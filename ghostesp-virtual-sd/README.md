# ghostesp-virtual-sd

Patch series adding a real, user-sizeable "virtual SD card" (internal-flash
FAT storage) to [GhostESP](https://github.com/GhostESP-Revival/GhostESP) for
boards that have no physical SD slot — starting with a plain ESP32-S3
DevKitC-1-style board (16MB flash, 8MB PSRAM). **Status: compiled, flashed to
real hardware, and verified over serial** — see `04-hardware-verification.md`
for the full result. Six patches, applied in order: `01` (static
partition), `02` (dynamic sizing), `03` (a one-file fix required to compile
`02` against ESP-IDF v6.1 — see `03-fix-mbedtls-md5-idf61-NOTES.md`), `05`
(two attempted fixes for a device panic that mounting this virtual SD
exposes in GhostESP's own pcap capture code — see
`05-fix-pcap-callback-stack-overflow-NOTES.md`; **neither attempt actually
fixed it, see `07`**), `06` (fixes the `sd vstorage resize/create/delete`
abort() that `02`'s own notes flagged as unverified and risky — see
`06-fix-vstorage-resize-abort-NOTES.md`; layered on `02` directly, since
it's the commit-the-new-partition-table step `02` added that was aborting),
`07` (the fix that actually resolved the capture-panic `05` attempted twice
and didn't — see `07-defer-capture-writes-to-stop-NOTES.md`. **Hardware-verified**,
unlike `05`'s two attempts). A follow-up verification pass (`08-pcap-capture-verification.md`,
no patch of its own) confirms `-raw` capture also works on the `07`-fixed
firmware, and goes one step further than `07`'s own testing by parsing the
resulting `.pcap` file's bytes directly (magic number, header fields, a
real decoded 802.11 beacon frame) rather than just checking size/packet
count.

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

## The patches

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

### `03-fix-mbedtls-md5-idf61.patch` (layered on top of 01+02)
One-file fix, found by actually compiling this against ESP-IDF v6.1:
`02`'s new `sd_vstorage_manager.c` included `mbedtls/md5.h` and called
`mbedtls_md5()`, but ESP-IDF v6.1's mbedtls component (mbedtls 4.x /
TF-PSA-Crypto) no longer ships that header publicly. Swapped it for
ESP-IDF's own `esp_rom_md5.h` API — the same one
`components/bootloader_support/src/flash_partitions.c` uses to verify this
exact MD5 partition-table-checksum entry, so the digest matches what the
bootloader checks on boot. See `03-fix-mbedtls-md5-idf61-NOTES.md` for the
full root-cause writeup.

## Verification status — be clear-eyed about this

- The binary partition-table format used in `02` was cross-checked directly
  against the actual ESP-IDF source at the exact version this repo's CI pins
  (`v6.1`) — `esp_flash_partitions.h`, `flash_partitions.c`,
  `partition.c`, `gen_esp32part.py` — not recalled from memory. High
  confidence the on-disk format itself is correct.
- **All three patches have been compiled, and the result flashed to and run
  on real hardware.** `01` + `02` apply cleanly in sequence onto a fresh
  clone; `02` alone does not compile against ESP-IDF v6.1 without `03`
  (see `03-fix-mbedtls-md5-idf61-NOTES.md` for why — a header ESP-IDF v6.1's
  mbedtls no longer ships publicly). With `03` applied, `idf.py build` on
  `configs/sdkconfig.generic_esp32s3_16mb` succeeds cleanly.
- Flashed to the actual 16MB-flash/8MB-PSRAM ESP32-S3 board this series
  targets. `chipinfo` and `sd vstorage info` over serial confirm the new
  board identity, the static 4MB `storage` partition at the right
  offset/size, and a sane dynamic-sizing cap computed from live free-flash
  numbers. A manual `sd write` / `sd cat` round trip through the mounted
  virtual storage was also confirmed working. Full detail and exact serial
  output in `04-hardware-verification.md`.
- **Update**: the partition-table *resize* path (`sd vstorage
  create|resize|delete`) — the genuinely risky part flagged above and in
  `02-dynamic-sizing-NOTES.md` — has since been exercised on hardware, in a
  separate session: `sd vstorage resize 5 -y` reliably `abort()`s the device
  (cleanly recovers on reboot, live table left untouched) because the commit
  write lands in esp_flash's protected-region guard
  (`CONFIG_SPI_FLASH_DANGEROUS_WRITE_ABORTS=y` in this build). Root-caused
  and fixed in `06-fix-vstorage-resize-abort.patch` — see
  `06-fix-vstorage-resize-abort-NOTES.md`. That patch is build-verified only;
  the actual resize has not yet been re-run on hardware with the fix
  applied.
- **Update**: the capture-panic-with-SD-mounted bug `05` attempted to fix
  (twice, see above) is now actually fixed and **hardware-verified** —
  `07-defer-capture-writes-to-stop.patch`. `capture -probe` (20s and 30s
  runs) and `capture -ble` (15s) all completed with zero crashes on the
  real board, packets written to SD matching the reported counts exactly.
  See `07-defer-capture-writes-to-stop-NOTES.md` for what actually worked
  (deferring all SD writes to capture-stop, not the stack/timing fixes `05`
  tried first) and why the earlier attempts didn't.
- **Update**: `capture -raw` also confirmed working on the `07`-fixed
  firmware — `sd info` showed a 450,386-byte `.pcap`, and this time the
  bytes themselves were pulled back and parsed (not just the size):
  correct pcap magic number, correct global-header fields, correct
  `LINKTYPE_IEEE802_11_RADIOTAP`, and a real decoded 802.11 beacon frame as
  the first record. See `08-pcap-capture-verification.md`.

## Applying

```bash
git clone --branch Development-deki --single-branch \
  https://github.com/GhostESP-Revival/GhostESP.git
cd GhostESP
git apply /path/to/01-static-partition.patch
git apply /path/to/02-dynamic-sizing.patch
git apply /path/to/03-fix-mbedtls-md5-idf61.patch   # required for ESP-IDF v6.1
git apply /path/to/05-fix-pcap-callback-stack-overflow.patch   # required before 07 (queue infra); does not by itself fix the capture panic
git apply /path/to/06-fix-vstorage-resize-abort.patch          # sd vstorage resize/create/delete aborts without this
git apply /path/to/07-defer-capture-writes-to-stop.patch       # the actual capture+SD-mounted panic fix (05 alone doesn't fix it)
# then build configs/sdkconfig.generic_esp32s3_16mb via GBT / idf.py
```

See `04-hardware-verification.md` for the build/flash/serial-verification
results and the exact `sd` commands used to test read/write.
`07-defer-capture-writes-to-stop-NOTES.md` is hardware-verified.
`06-fix-vstorage-resize-abort-NOTES.md` is build-verified only, not yet
flashed/re-tested on hardware.
