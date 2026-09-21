# vstorage resize/create/delete abort() fix — notes

`06-fix-vstorage-resize-abort.patch` is layered on top of `01`+`02`+`03`
(and independent of `05` — different file, no interaction; order between
`05` and `06` doesn't matter). It fixes the exact risk `02-dynamic-sizing-NOTES.md`
and the main `README.md` flagged as unverified and "the genuinely risky
part": writing a new partition table to a running device.

## The bug, as reported and reproduced

`sd vstorage resize 5 -y` aborts the device instead of resizing the
partition:

```
W SD_VStorage: Resizing existing 'storage' partition at 0x400000: 4194304 -> 5242880 bytes (contents destroyed)
W SD_VStorage: Scratch copy verified OK. Committing to live partition table at 0x8000 -- do not power off now.
abort() was called at PC 0x403951d2 on core 0
```

Backtrace decoded against the built ELF:

```
abort → esp_flash_erase_region (esp_flash_api.c:649)
      ← vstorage_write_sector_verified (sd_vstorage_manager.c:63)
      ← vstorage_commit_new_table (sd_vstorage_manager.c:339)
      ← create_or_resize_storage_partition (sd_vstorage_manager.c:428)
      ← handle_sd_cmd (cmd_sd.c:877)
```

The device recovers cleanly on its own: self-reboots, CLI comes back, and
`sd vstorage info` afterward shows the partition unchanged at its original
4MB — the abort fires before any byte of the live sector is touched, so
nothing gets corrupted. `create` and `delete` go through the same
`vstorage_commit_new_table()` → write-to-`0x8000` path and would abort the
same way; not separately exercised, no reason to expect a different result.

## Root cause (independently verified against the ESP-IDF v6.1 source, not just the report)

- `configs/sdkconfig.generic_esp32s3_16mb` genuinely has
  `CONFIG_SPI_FLASH_DANGEROUS_WRITE_ABORTS=y` (confirmed in both the config
  file and the generated `sdkconfig`).
- `esp_flash_api.c`'s `CHECK_WRITE_ADDRESS` macro, present at the exact
  cited line (649, inside `esp_flash_erase_region`), matches verbatim:
  "fail writes which land in the bootloader, partition table, or running
  application region" — and with `DANGEROUS_WRITE_ABORTS` on, a hit calls
  `abort()` instead of returning an error code.
- `sd_vstorage_manager.c:339` (`vstorage_commit_new_table`) does write to
  `ESP_PARTITION_TABLE_OFFSET` (`0x8000`) via `vstorage_write_sector_verified()`
  (line 63: `esp_flash_erase_region(NULL, offset, ...)`), with no protection
  toggle around it — so every commit write into the live partition table
  hits the guard and aborts. This isn't hardware flakiness or a race, it's
  ESP-IDF's own safety mechanism firing exactly as designed against exactly
  what this code does: rewrite the live partition table sector from a
  running app.

## The fix: use ESP-IDF's own sanctioned bypass for this exact case

`esp_flash_set_dangerous_write_protection(esp_flash_t *chip, bool protect)`
(`esp_private/esp_flash_internal.h`) exists specifically to let code
intentionally write a protected region when it means to. It's not a hack
reaching around the guard — ESP-IDF's own OTA component
(`components/app_update/esp_ota_ops.c`) calls this same function for the
same category of operation (writing a protected region from running app
code).

Bracketed *only* the live-table commit write (the scratch-sector write
earlier in the same function isn't in a protected region and doesn't need
this):

```c
esp_flash_set_dangerous_write_protection(esp_flash_default_chip, false);
st = vstorage_write_sector_verified(ESP_PARTITION_TABLE_OFFSET, blob, ESP_PARTITION_TABLE_MAX_LEN);
esp_flash_set_dangerous_write_protection(esp_flash_default_chip, true);
```

Protection is re-enabled unconditionally right after, regardless of whether
the write succeeded, so nothing else gets an unintended extra window with
the guard off. `esp_flash_default_chip` was already reachable (`esp_flash.h`,
already included); the only new include needed was
`esp_private/esp_flash_internal.h` for the function declaration itself.

## Related, not yet fixed here

`main/managers/self_ota_manager.c` (lines 223/226) does the identical
unguarded `esp_flash_erase_region()`/`esp_flash_write()` straight to
`ESP_PARTITION_TABLE_OFFSET`, with no protection toggle. If GhostESP's
self-OTA ever actually exercises that code path, it would abort the exact
same way. Out of scope for this patch (different subsystem, not what was
reported crashing) but worth knowing about if self-OTA is ever relied on.

## Verification status — be clear-eyed about this

- **Established**: `idf.py build` (via `build.py --targets 46`) on
  `configs/sdkconfig.generic_esp32s3_16mb` with `01`+`02`+`03`+`05`+`06` all
  applied: exit code 0, "Project build complete." The link step
  (`Linking CXX executable Ghost_ESP_IDF.elf`) completing with no undefined-
  reference errors confirms `esp_flash_set_dangerous_write_protection` and
  the private header are actually reachable from this component, not just
  syntactically accepted.
- This patch file was verified (`git apply --check`, then applied for real)
  to apply cleanly, in order, on top of a fresh `01`+`02`+`03`+`05` checkout
  at `0ed9c8d3`.
- **Not yet established**: not flashed to hardware, so `sd vstorage resize`
  (or `create`/`delete`) has not actually been re-run to confirm the write
  now succeeds instead of aborting. The original crash report's own
  recovery behavior (clean reboot, table unchanged) is good evidence the
  scratch+verify-before-commit design is otherwise sound; this patch only
  targets the specific guard that was aborting the commit step.
