# Hardware verification — generic ESP32-S3 16MB, patches 01+02+03

This is the first point in this series where the firmware built from
`01-static-partition.patch` + `02-dynamic-sizing.patch` +
`03-fix-mbedtls-md5-idf61.patch` was actually flashed to and run on real
hardware — the same board described in the main `README.md`'s "What was
verified on real hardware before writing any code" section (16MB SPI
flash, 8MB embedded PSRAM, no physical SD card).

Not flashed until explicitly asked for, separately from the build/compile
step — build and flash were treated as two separate approvals.

## Flash

```
idf.py -p /dev/ttyACM0 flash
```

`esptool` connected to an ESP32-S3 (QFN56, revision v0.2) on
`/dev/ttyACM0`, MAC `14:c1:9f:d1:47:24`, 8MB embedded PSRAM. Wrote
bootloader, partition table, and app image; verified each write's hash
against the source; hard-reset the device via RTS. Exit code 0, no errors,
no retries needed.

## Serial verification — `chipinfo`

Read over the USB-Serial/JTAG console at 115200 baud, right after the
post-flash reset:

```
[CHIPINFO_START]
Chip Information:
  Firmware: GhostESP Revival v2.2 BETA
  Git Commit: 0ed9c8d3
  Model: ESP32-S3
  Revision: v0.2
  CPU Cores: 2
  Features: WiFi/BLE
  Free Heap: 8426303 bytes
  Min Heap: 8387068 bytes
  IDF Version: v6.1
  Build Config: generic_esp32s3_16mb

  Enabled Features:
    NFC
    GPS
    USB Keyboard (Host)
    SD Card (SPI)
    Core Dump
[CHIPINFO_END]
```

Firmware boots clean (no panic/crash in the boot buffer), and confirms it
was actually built with `IDF Version: v6.1` and
`Build Config: generic_esp32s3_16mb` — i.e. this is genuinely the target
config, not a stale/wrong image.

## Serial verification — `sd vstorage info`

```
SD:VSTORAGE:INFO:chip_total_bytes=16777216
SD:VSTORAGE:INFO:chip_total_mb=16
SD:VSTORAGE:INFO:used_bytes=8359936
SD:VSTORAGE:INFO:used_mb=7
SD:VSTORAGE:INFO:free_bytes=8417280
SD:VSTORAGE:INFO:free_mb=8
SD:VSTORAGE:INFO:cap_bytes=6733824
SD:VSTORAGE:INFO:cap_mb=6
SD:VSTORAGE:INFO:storage_exists=true
SD:VSTORAGE:INFO:storage_offset=0x400000
SD:VSTORAGE:INFO:storage_size_bytes=4194304
SD:VSTORAGE:INFO:storage_size_mb=4
SD:VSTORAGE:INFO:board_mounts_virtual_storage=true
SD:OK
```

This confirms, on real hardware, that:

- The new `CONFIG_IS_GENERIC_ESP32S3_16MB` board identity is recognized
  and mounts virtual storage (`board_mounts_virtual_storage=true`).
- The static 4MB `storage` partition from `01-static-partition.patch`
  exists exactly as built: offset `0x400000`, size `4194304` bytes (4MB) —
  matching the `gen_esp32part.py` dump of the build output byte-for-byte.
- `02-dynamic-sizing.patch`'s `size_cap_bytes()` (80% of genuinely free
  flash) is computing a real, sane number on this chip: `cap_bytes=6733824`
  (~6MB) against `free_bytes=8417280` (~8MB) — i.e. ~80%, as designed.

## Read/write verification

The `sd` command family (`main/core/commands/cmd_sd.c`) exposes a
write → read → verify loop against the mounted virtual storage over the
same serial console:

```
sd status
sd write /mnt/ghostesp/test.txt R2hvc3RFU1AgdnN0b3JhZ2UgdGVzdA==
sd cat /mnt/ghostesp/test.txt
sd info /mnt/ghostesp/test.txt
sd list
sd rm /mnt/ghostesp/test.txt
```

(`R2hvc3RFU1AgdnN0b3JhZ2UgdGVzdA==` is base64 for the 23-byte string
`GhostESP vstorage test`; `sd write` takes a path and base64 payload,
`sd cat` returns the raw decoded bytes, `sd info` reports the stored size.)

Jack ran this sequence manually over the serial console and confirmed it
round-trips correctly — write, then read back byte-identical content, on
the flash-backed FAT partition these patches create. That manual run's
exact terminal transcript wasn't captured into this repo; the command
sequence above is the reproducible procedure, not a transcript of a
specific run.

## What this does and does not establish

**Established**: the patch series compiles clean against ESP-IDF v6.1 with
`03`'s fix applied, flashes and boots on real ESP32-S3 hardware, correctly
identifies the new board config, creates the static partition at the
right offset/size, computes a sane dynamic-sizing cap from live free-flash
numbers, and supports at least one manual write/read round trip through
the standard `sd` file commands.

**Not established**: the partition-table *resize* path
(`sd vstorage create|resize|delete`, the genuinely risky part flagged in
`02-dynamic-sizing-NOTES.md` — writing a new partition table to a running
device and surviving the required reboot) has not been exercised here.
That remains unverified, same as before this session.
