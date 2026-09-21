# GhostESP virtual-storage patch for generic ESP32-S3 board — notes

Scratch clone: `/tmp/ghostesp-work/GhostESP` (branch `Development-deki`, fresh
clone, not one of Jack's project directories).
Patch file: `/tmp/ghostesp-virtual-storage-generic-s3.patch` (git diff,
**not applied/committed/pushed** — produced with `git add -A && git diff
--cached`, working tree in the scratch clone still holds the changes but
nothing was committed).

## What I verified in the real source before writing anything

- The mount-gate macro is `#if defined(CONFIG_IS_S3TWATCH) ||
  defined(CONFIG_IS_ATOMS3R)`, and it appears at exactly **5** places in
  `main/managers/sd_card_manager.c` (lines 548, 688, 1415, 2080, 2111 in the
  fresh clone): the `mount_virtual_storage()`/`unmount_virtual_storage()`
  definitions, the call site in `sd_card_init()`, the unmount path in
  `sd_card_unmount_with_context()`, the status print in
  `sd_card_print_config()`, and `sd_card_is_virtual_storage()`. The actual
  mount call is `esp_vfs_fat_spiflash_mount_rw_wl("/mnt", "storage",
  &mount_config, &s_wl_handle)` at line 578, looking up a partition named
  `"storage"` of type `ESP_PARTITION_TYPE_DATA` / subtype
  `ESP_PARTITION_SUBTYPE_DATA_FAT`.
- S3TWatch uses `partitions_ota_s3twatch.csv` (OTA A/B, storage fixed 4MB at
  0x400000). AtomS3R uses `partitions_atoms3r.csv` (single factory app slot,
  storage 1MB at 0x400000). Both are wired per-board via
  `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME` / `CONFIG_PARTITION_TABLE_FILENAME`
  in their own `configs/sdkconfig.S3TWatch` / `configs/sdkconfig.atoms3r` —
  there is no CMake-level indirection, it's a plain per-sdkconfig ESP-IDF
  Kconfig setting, one board = one sdkconfig file = one partition CSV.
- `main/Kconfig.projbuild` confirms `IS_S3TWATCH`/`IS_ATOMS3R` wording exactly
  as given in the task brief, both `default n`.

## Which existing config is "the generic ESP32-S3 DevKitC-1 board"

`configs/sdkconfig.default.esp32s3` is registered in `build.py` /
`.github/workflows/compile_all.yml` under the board name `esp32s3-generic`
— it is the closest existing match to "plain ESP32-S3 DevKitC-1, no display,
no persona." **However**, as shipped it assumes a 4MB-flash, no-PSRAM part
(`CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y`, `# CONFIG_SPIRAM is not set`, and it
points at the plain 4MB `partitions.csv`). Jack's actual hardware (confirmed
via `esptool flash_id`) is 16MB flash / 8MB PSRAM, so that shared generic
config does not reflect his board's real capacity, and it is also the one
target every other minimal/no-persona ESP32-S3 board builder currently uses
— editing it in place to add a fixed 16MB-flash storage partition would
silently break anyone building `esp32s3-generic` for an actual 4MB part.

## Deviation from the plan: new board-identity flag instead of reusing the config in place

Per the task's fallback instruction ("propose the smallest reasonable
alternative... rather than forcing a bad fit"), I did **not** just extend
the `IS_S3TWATCH`/`IS_ATOMS3R` macro guard to fire unconditionally for
`esp32s3-generic`, and I did not edit `sdkconfig.default.esp32s3` in place.
Instead, mirroring exactly how S3TWatch and AtomS3R are each their own
board identity + sdkconfig + partition table:

1. **New Kconfig flag** `CONFIG_IS_GENERIC_ESP32S3_16MB` in
   `main/Kconfig.projbuild`, same shape/wording as `IS_ATOMS3R`.
2. **New partition table** `partitions_generic_esp32s3_16mb.csv` (repo
   root, alongside `partitions_atoms3r.csv` etc.), copied from
   `partitions_atoms3r.csv`'s non-OTA layout (single `app0` factory slot,
   no OTA) and scaled to 16MB:
   ```
   nvs,        data, nvs,     0x9000,  0x7000,
   app0,       app,  factory, 0x10000, 0x3D0000,
   coredump,   data, coredump,0x3E0000,0x20000,
   storage,    data, fat,     0x400000,0x400000,
   ```
   `storage` = **4MB** at offset 0x400000 (same offset S3TWatch/AtomS3R
   use). This leaves 0x800000–0x1000000 (**8MB**) completely unused on the
   16MB chip, on top of the ~12MB that was unused before this patch minus
   the 4MB now claimed.
3. **New sdkconfig** `configs/sdkconfig.generic_esp32s3_16mb`, seeded as a
   copy of `configs/sdkconfig.default.esp32s3` (so it keeps all the
   "no display / no persona" settings of the generic target) with only the
   deltas needed for this specific hardware and feature:
   - `CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y` (was 4MB)
   - `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME` / `_FILENAME` →
     `partitions_generic_esp32s3_16mb.csv`
   - `CONFIG_BUILD_CONFIG_TEMPLATE="generic_esp32s3_16mb"`
   - `CONFIG_IS_GENERIC_ESP32S3_16MB=y` added next to the (already-present)
     `# CONFIG_IS_S3TWATCH is not set` line
   - `CONFIG_SPIRAM=y` plus the full "SPI RAM config" block (octal PSRAM,
     40MHz, boot-init, malloc-in-PSRAM, etc.) and
     `CONFIG_ESP32S3_SPIRAM_SUPPORT=y` — copied verbatim from
     `configs/sdkconfig.ghostlink_p1_core`, the one other config in this
     repo that already combines 16MB flash with 8MB octal PSRAM on ESP32-S3.
     `sdkconfig.atoms3r` was not usable as the SPIRAM reference because its
     board is only 8MB flash.
   Everything else in the file — the "no display", "no persona" block
   (`WITH_SCREEN`, `USE_CARDPUTER`, `USE_TDISPLAY_S3`, LED options, etc.) —
   is untouched, i.e. identical to `sdkconfig.default.esp32s3`.
4. **`sd_card_manager.c`**: extended all 5 occurrences of
   `#if defined(CONFIG_IS_S3TWATCH) || defined(CONFIG_IS_ATOMS3R)` to
   `... || defined(CONFIG_IS_GENERIC_ESP32S3_16MB)` via one `replace_all`
   edit (confirmed all 5 now read identically).

## Other observations, not acted on (out of scope per the task's explicit step 3)

- `build.py` (`get_build_targets()`) and `.github/workflows/compile_all.yml`
  both maintain a board matrix (`name` → `idf_target` → `sdkconfig_file` →
  `zip_name`) that GBT/CI use to expose a board by name. **This patch does
  not add an entry for `generic_esp32s3_16mb` to either file.** Without
  that, the new sdkconfig/partition table exist and are internally
  consistent, but GBT has no named target that points at them yet — you'd
  currently have to reference `configs/sdkconfig.generic_esp32s3_16mb`
  directly. I left this out because the task's step 3 scoped the patch to
  (a) the partition table and (b) the mount-call conditional only, and I
  was told to do exactly what was asked, no more. Say the word if you want
  a follow-up patch that also registers the board in `build.py` and the CI
  matrix.
- `sdkconfig.default.esp32s3` itself appears to predate the `IS_ATOMS3R`
  Kconfig option (grep found no `IS_ATOMS3R` line in it at all, whereas
  `IS_S3TWATCH` is present as `# ... is not set`) — i.e. it looks stale
  relative to current `main/Kconfig.projbuild`. Harmless for a boolean
  default-`n` option (an absent line just means "use the Kconfig default"),
  and not something this patch needed to fix, but worth knowing it's not
  perfectly in sync.
- I did not touch `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` (off in the
  generic default, on in both S3TWatch and AtomS3R configs). It's an
  OTA-rollback safety feature unrelated to the storage partition itself,
  and the new partition table here is non-OTA (single `app0`, matching
  AtomS3R's pattern) — left as-is to keep the diff minimal.

## Size choice

Picked **4MB** for `storage` (not 8MB): it matches the pattern used by
S3TWatch (4MB) rather than AtomS3R (1MB, on a smaller 8MB chip), while
deliberately leaving a full 8MB of the chip completely free above it —
this is explicitly a **placeholder** size, called out in a comment at the
top of `partitions_generic_esp32s3_16mb.csv`, since real user-driven sizing
of this partition is separate, future work per the task brief.

## Patch contents summary

| File | Change |
|---|---|
| `main/Kconfig.projbuild` | +10 lines: new `IS_GENERIC_ESP32S3_16MB` bool option |
| `main/managers/sd_card_manager.c` | 5 lines changed: macro guard extended at all 5 gate sites |
| `partitions_generic_esp32s3_16mb.csv` | new file, 19 lines: partition table with 4MB `storage` |
| `configs/sdkconfig.generic_esp32s3_16mb` | new file, ~3408 lines (full sdkconfig, copied from `sdkconfig.default.esp32s3` with the targeted deltas listed above) |

Not done, per explicit task scope: no `build.py`/CI board-matrix
registration, no commit, no push, no PR, no flashing, no touching
`/dev/ttyACM0` or any of Jack's existing files/directories.
