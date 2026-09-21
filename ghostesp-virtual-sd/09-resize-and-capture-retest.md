# Resize fix hardware re-test, plus a fresh capture re-check on the resized partition

Follow-up to `06-fix-vstorage-resize-abort-NOTES.md`, which fixed the
`sd vstorage resize/create/delete` `abort()` (root cause:
`CONFIG_SPI_FLASH_DANGEROUS_WRITE_ABORTS=y` firing on the live
partition-table commit write) but explicitly flagged itself as
**build-verified only, not yet flashed/re-tested on hardware**. This doc
closes that gap, and re-confirms capture still works afterward.

## What was done

1. Rebuilt from the working tree with `01`+`02`+`03`+`05`+`06`+`07` all
   applied (confirmed present: `esp_flash_set_dangerous_write_protection`
   in `sd_vstorage_manager.c`, `PCAP_BUFFER_SIZE (1024*1024)` in
   `pcap.h`) — `idf.py build` on `configs/sdkconfig.generic_esp32s3_16mb`:
   exit code 0.
2. Flashed to the real board (`idf.py -p /dev/ttyACM0 flash`) — clean,
   hash-verified.
3. Baseline: `sd vstorage info` — original 4MB `storage` partition,
   mounted.
4. Ran the exact command that previously `abort()`ed the device:
   `sd vstorage resize 5 -y`.

   ```
   W SD_VStorage: Resizing existing 'storage' partition at 0x400000: 4194304 -> 5242880 bytes (contents destroyed)
   W SD_VStorage: Scratch copy verified OK. Committing to live partition table at 0x8000 -- do not power off now.
   W SD_VStorage: Live partition table updated. A reboot is required for it to take effect.
   SD:VSTORAGE:RESIZE:OK size_mb=5
   SD:VSTORAGE:REBOOT_REQUIRED:1
   ```

   **No `abort()`.** The exact commit-to-`0x8000` step that crashed before
   now completes cleanly.
5. `reboot`. Device came back up normally — no panic, no coredump.
   FatFS logged "No filesystem detected, retrying after format" on first
   mount, which is expected: resize explicitly destroys old contents, so
   the differently-sized partition needs a fresh format, and the firmware
   does that automatically.
6. Post-reboot: `sd vstorage info` → `storage_size_bytes=5242880` (exactly
   5MB, matching the request). `sd status` → `mounted=true, type=virtual`,
   freshly formatted (`used_pct=2`).
7. Functional check, not just a size check: wrote a 20-byte test file to
   the resized partition and read it back byte-identical
   (`sd write`/`sd cat`/`sd info` round trip).

**Result: the resize fix works end-to-end on real hardware** — resize,
reboot, new size takes effect, filesystem mounts and is fully read/write
functional. `create`/`delete` were not separately tested (same
`vstorage_commit_new_table()` code path, no reason to expect a different
result, but not directly exercised).

## Capture re-checked on the resized, reformatted partition

Since the resize destroys and reformats the storage partition, capture
(verified working pre-resize in `07`/`08`) was re-run afterward to confirm
it wasn't affected by the partition change:

- `capture -raw`, 20s, passive/receive-only.
- **No crash.** `Capture stats: seen=1759 written=1759 dropped=0` — zero
  drops.
- `rawscan_0.pcap`: 280,354 bytes on the new 5MB partition.
- Pulled the first 512 bytes back and parsed them: valid pcap magic
  (`d4c3b2a1`), version 2.4, `LINKTYPE_IEEE802_11_RADIOTAP`, first record a
  real 89-byte 802.11 beacon frame (broadcast destination, type/subtype
  `0x80`) — same clean structure as `08`'s earlier check.

So on the current flash, both the resize fix (`06`) and the capture fix
(`07`) are now independently confirmed on real hardware, and confirmed to
still work together in sequence (resize → reboot → capture), not just each
in isolation.

## What's still not covered

- `sd vstorage create` and `sd vstorage delete` — not run this session.
- The separate `-wireshark`/`-wiresharkble` raw-UART-streaming capture
  mode and its known log-interleaving risk — unrelated to any of `05`/`06`/
  `07`, not addressed by this series at all yet.
- Power-loss-mid-write recovery for the partition-table commit — still
  only mitigated by the scratch-then-verify-then-commit design, not
  hardware-tested (and not really testable without deliberately cutting
  power mid-write, which wasn't attempted).
