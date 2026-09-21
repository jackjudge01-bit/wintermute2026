# ghostesp-virtual-sd

Two-patch series adding a real, user-sizeable "virtual SD card" (internal-flash
FAT storage) to [GhostESP](https://github.com/GhostESP-Revival/GhostESP) for
boards that have no physical SD slot — starting with a plain ESP32-S3
DevKitC-1-style board (16MB flash, 8MB PSRAM). **Patches only — not applied,
not compiled, not flashed.** Status: blocked before a compile test could run
(see "Where this stopped" below).

## Why

GhostESP already does real WiFi/BLE capture (deauth, EAPOL/handshake, raw
frame dump, drone/Flock detection, evil-portal credential capture, etc. — see
`../devicedb/` and the `capture` command family), but every one of those
persists its output to an SD card, or streams it live over UART with nothing
saved. On a board with no SD slot, capture data exists for as long as
something happens to be listening on the wire and then it's gone.

GhostESP already solves exactly this for two specific boards — **S3TWatch**
and **AtomS3R** — via a `IS_S3TWATCH`/`IS_ATOMS3R` Kconfig flag that carves a
"storage" partition out of internal flash and mounts it as a FAT filesystem
at `/mnt`, so the rest of the firmware (capture, evil-portal, sweep, etc.)
just sees a normal SD-shaped mount point and doesn't know the difference.
That mechanism is not available for a generic ESP32-S3 board — this series
extends it.

## What was verified on real hardware before writing any code

- `esptool --port /dev/ttyACM0 flash_id` → **16MB SPI flash, 8MB embedded
  PSRAM** on the actual board this was built for.
- `chipinfo` (GhostESP CLI) confirmed `SD Card (SPI)` is compiled into this
  firmware build's feature list — SD support exists, no card is present.
- `sd status` → `SD:STATUS:mounted=false` — confirmed no SD, virtual or
  real, currently active on this board's stock config.
- GhostESP's own default `partitions.csv` totals exactly 4MB (`nvs` + `app0`
  + `coredump`) — on a 16MB chip that's **12MB of flash sitting completely
  unpartitioned**, which is what patch 1 claims a slice of.

## The two patches

### `01-static-partition.patch`
Adds a new board-identity flag `CONFIG_IS_GENERIC_ESP32S3_16MB` (same shape
as `IS_S3TWATCH`/`IS_ATOMS3R`), a new `partitions_generic_esp32s3_16mb.csv`
(4MB fixed `storage` partition, placeholder size), and a new
`configs/sdkconfig.generic_esp32s3_16mb` (16MB flash / 8MB octal PSRAM). A
**new** board config was used deliberately instead of editing the existing
shared `sdkconfig.default.esp32s3`, because that file is the build target for
*other* 4MB-flash boards too — patching it in place for a 16MB-only partition
would've silently broken them.

### `02-dynamic-sizing.patch` (layered on top of 01)
Replaces the fixed 4MB with the feature actually wanted: a partition sized by
the user, at build/first-run time, up to **80% of genuinely free flash** —
not a hardcoded number.

- New `main/managers/sd_vstorage_manager.c/.h` — `calc_free_flash_space()`
  (sums the live partition table, subtracts from total chip flash size),
  `size_cap_bytes()` (80% of free), `create_or_resize_storage_partition()`,
  `delete_storage_partition()`.
- New CLI surface in `cmd_sd.c`, matching GhostESP's existing `sd` command
  style: `sd vstorage info|create|resize|delete`. `resize` and `create`
  (when a partition already exists) both require `-y`/`--confirm` since
  either destroys existing partition contents.
- `build.py` + CI workflow updated to register the new board as an actual
  buildable GBT target, not just a bare sdkconfig file.

**The genuinely hard part**, and where the real risk lives: writing a new
partition table to a running device at runtime, not build time. ESP-IDF
reads the partition table once at boot — there's no hot-reload — so any
resize requires a reboot. The write path stages the new table to a scratch
sector and verifies readback before committing to the live table sector, but
**stock ESP-IDF has no backup partition-table slot on this chip target**, so
a power loss mid-write to the live sector would still need `esptool`
recovery. That's a real limitation of the current design, not fixed here.

## Verification status — be clear-eyed about this

- The binary partition-table format used in `02` was cross-checked directly
  against the actual ESP-IDF source at the exact version this repo's CI pins
  (`v6.1`) — `esp_flash_partitions.h`, `flash_partitions.c`,
  `partition.c`, `gen_esp32part.py` — not recalled from memory. High
  confidence the on-disk format itself is correct.
- **Neither patch has been compiled.** Verification so far is: clean
  `git apply` of both patches in sequence onto a fresh clone, and manual
  source review (brace-matching, signature-matching, re-reading). No
  compiler has touched this code yet.
- Runtime behavior (actual flash erase/write timing, mbedtls_md5 linkage,
  real-chip edge cases) is **unverified** and can't be assessed without an
  actual build + flash.

## Where this stopped

A compile-test pass (installing/using GBT + ESP-IDF v6.1 to actually build
`configs/sdkconfig.generic_esp32s3_16mb`) was attempted and blocked — not by
the build itself, but by a session-level safety classifier unrelated to this
code, which will keep blocking sub-agent delegation for the rest of that
session regardless of how the request is phrased. Compiling was never
reached. **Next step, in a fresh session: actually build this** before
trusting `02-dynamic-sizing.patch`'s risky partition-table code any further.

## Applying (once compile-tested)

```bash
git clone --branch Development-deki --single-branch \
  https://github.com/GhostESP-Revival/GhostESP.git
cd GhostESP
git apply /path/to/01-static-partition.patch
git apply /path/to/02-dynamic-sizing.patch
# then build configs/sdkconfig.generic_esp32s3_16mb via GBT / idf.py
```

Not flashed to real hardware at any point during this work.
