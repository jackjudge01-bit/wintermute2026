# GhostESP dynamic virtual-storage sizing — notes

Scratch clone: `/tmp/ghostesp-work/GhostESP` (branch `Development-deki`).
This patch: `/tmp/ghostesp-virtual-storage-dynamic-sizing.patch` — a git diff
**layered on top of** the prior `/tmp/ghostesp-virtual-storage-generic-s3.patch`,
not a replacement for it. Neither patch is applied/committed/pushed anywhere
permanent; the scratch clone's working tree still holds both (prior patch
staged in the index, this one unstaged) exactly as `git status` shows.

**Verification done**: cloned the scratch repo's committed HEAD
(`0ed9c8d3`) fresh into `/tmp/ghostesp-verify`, applied
`ghostesp-virtual-storage-generic-s3.patch` then
`ghostesp-virtual-storage-dynamic-sizing.patch` in sequence with
`git apply --check` + `git apply`, both applied cleanly, and the resulting
tree was byte-identical (`diff -rq`) to my working copy. That verify clone
was then deleted. **I did not build or flash anything** — no ESP-IDF
toolchain was invoked, no compiler was run, nothing touched
`/dev/ttyACM0` or any real hardware. Everything below is source-level
review only.

## Part 1 — core sizing logic

New file pair, not added to `sd_card_manager.c` (which is already 2209
lines and file-per-concern is the established pattern in `main/managers/`
— e.g. `cloud_store_manager.c`, `fuel_gauge_manager.c`, one manager per
concern):

- `include/managers/sd_vstorage_manager.h`
- `main/managers/sd_vstorage_manager.c`

Picked up automatically by `main/CMakeLists.txt`'s `file(GLOB_RECURSE
app_sources "${CMAKE_SOURCE_DIR}/main/*.c" ...)` — no CMakeLists edit
needed, though per that file's own comment a new source file needs an
`idf.py reconfigure` before it's picked up by an existing build directory.

Functions (all in the new file, signatures exactly matching the task):

- `calc_free_flash_space(uint64_t *out_free_bytes)` — reads
  `esp_flash_get_size()`, reconstructs the live partition table via
  `esp_partition_find(ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY,
  NULL)` (iterating with `esp_partition_get`/`esp_partition_next`), sums
  every partition's size, adds one sector (`SD_VSTORAGE_SECTOR_SIZE` =
  0x1000) for the partition-table region itself and one more sector this
  module reserves as a scratch-write area (see "safety model" below), and
  subtracts that total from the chip size. Nothing here is hardcoded to
  16MB or to any particular existing layout — it re-derives everything
  from what's actually on the chip at call time, so it should work on any
  GhostESP board/flash size, not just Jack's.
- `size_cap_bytes(uint64_t *out_cap_bytes)` — `(free_bytes / 5) * 4`,
  i.e. 80% as an exact integer floor (deliberately not floating point).
- `sd_vstorage_get_info(sd_vstorage_info_t *out_info)` — the above plus
  the current "storage" partition's offset/size via
  `esp_partition_find_first(DATA, FAT, "storage")`, for `sd vstorage info`.
- `create_or_resize_storage_partition(uint32_t size_bytes, bool
  *out_reboot_required)` — validates against `size_cap_bytes()` and a
  64KB floor, reads the current table (again via `esp_partition_find`,
  not a raw-flash parse), either resizes the existing "storage" entry in
  place (offset unchanged, size changed — destructive, caller must have
  already confirmed) or appends a new one after the highest end-offset in
  the table (sector-aligned), checks for range overlap against every
  other partition either way, then hands the new entry list to a shared
  commit routine. Never mounts/formats anything. Sets
  `*out_reboot_required = true` on success and nothing else — no
  attempt to remount, matching the task's explicit instruction that
  ESP-IDF caches the table once at (lazy, first-use) boot and the caller
  (CLI) must tell the user to reboot.
- `delete_storage_partition(bool *out_reboot_required)` — same idea,
  removes the "storage" entry and shifts the rest down.

### The scratch-then-commit write path (the risky part)

Shared by all three mutating operations via a private
`vstorage_commit_new_table()`:

1. Build the full new table in RAM (`vstorage_build_table_blob`).
2. Erase + write it to a **scratch sector at the very top of the flash
   chip** (`chip_size - 0x1000`), then read it back and `memcmp` it
   against what was meant to be written. If that fails, stop — the live
   table at `CONFIG_PARTITION_TABLE_OFFSET` (0x8000 by default) is never
   touched.
3. Only if the scratch copy verified correctly, erase + write the same
   bytes to the live table sector, then read that back and verify too.

The scratch sector is not a discardable afterthought — `calc_free_flash_space()`
permanently reserves it (one sector, subtracted from every free-space
calculation), and `create_or_resize_storage_partition()` never lets the
storage partition's range reach into it. So the "safety" mechanism the
task asked for is structurally guaranteed to have somewhere to write to.

**What this does and does not protect against**, stated plainly because
this is the part I'm least able to fully de-risk without real hardware:
- It DOES catch a corrupt/garbled table (bad RAM, a bug in this code, a
  transient flash glitch on the scratch write) before it ever reaches the
  live sector — the device's current, working table is never overwritten
  with something unverified.
- It does NOT make the live-sector write itself atomic or power-loss-safe.
  Stock ESP-IDF (confirmed by reading the actual v6.1 source, see below)
  has no backup/alternate partition-table slot for the ESP32-S3 target —
  that redundant-table mechanism exists in newer IDF versions
  (`PART_TYPE_PARTITION_TABLE` / `ESP_PRIMARY_PARTITION_TABLE_OFFSET`,
  new in the v5.1→v6.1 diff I pulled) but is for a different secure-boot
  scheme, not something this patch wires up or relies on. If power is cut
  between the live-sector erase and the write completing, the device will
  not boot (bootloader's own `esp_partition_table_verify()` will find no
  valid entries) and recovery requires reflashing over serial (esptool).
  This is an inherent limitation of the platform for this chip/board, not
  something I found a way to close in software — I want to be explicit
  that "verified before commit" is not the same as "commit cannot fail."

## Part 2 — CLI commands

All added inside the existing `handle_sd_cmd()` dispatcher in
`main/core/commands/cmd_sd.c`, as a new `vstorage` branch alongside
`status`/`list`/`info`/etc. — `sd` is already registered as a single
command (`register_command("sd", handle_sd_cmd)` in
`main/core/commandline.c`) that does its own subcommand string matching;
I matched that exact pattern rather than registering new top-level
commands, and reused the file's existing `SD:...` / `glog()`
machine-parseable output convention.

- `sd vstorage info` — chip total / used / free / 80% cap (bytes and MB),
  current storage partition's offset+size if any, and whether this
  firmware build actually mounts virtual storage at all
  (`sd_card_virtual_storage_supported()`, see below).
- `sd vstorage create <MB>` — validates the MB arg (1–4095, to stay well
  inside `uint32_t` bytes), calls `create_or_resize_storage_partition`,
  prints `SD:VSTORAGE:REBOOT_REQUIRED:1` plus a human sentence on
  success.
- `sd vstorage resize <MB> -y` — same, but refuses without `-y` /
  `--yes` / `--confirm` present, printing a destructive-action warning
  naming the existing partition's current size/offset first. Without the
  confirm flag it never calls into the core logic at all.
- `sd vstorage delete -y` — same confirm-flag gate, then
  `delete_storage_partition`.

**One judgment call beyond the literal spec, flagged rather than done
quietly**: the task only explicitly asked for the `-y` confirmation gate
on `resize`. I also require it for `create` in the one case where `create`
would itself be destructive — i.e. when a "storage" partition already
exists and `create_or_resize_storage_partition()` would resize it in
place. A bare `create` on a board with no existing storage partition
still needs no confirmation. I did this because it's the same principle
Jack stated for `resize` ("don't make destructive resize a single
accidental command") applied to the one place `create` can silently hit
the same destructive path; happy to remove it if that's not wanted. I did
**not** add a confirm requirement to `delete` beyond what's obviously
warranted by it being destructive by definition — that one was already
implied by "removes the storage partition" being inherently destructive,
and I judged it should get the same `-y` gate for the same reason; flagging
this too in case Jack wants `delete` to run without confirmation.

Small supporting addition in `main/managers/sd_card_manager.c` /
`include/managers/sd_card_manager.h`: `sd_card_virtual_storage_supported()`,
a one-line compile-time capability check (true iff
`CONFIG_IS_S3TWATCH`/`CONFIG_IS_ATOMS3R`/`CONFIG_IS_GENERIC_ESP32S3_16MB`
is defined) alongside the existing `sd_card_is_virtual_storage()`
(runtime-mounted check). This lets `sd vstorage info/create/resize` warn
when a storage partition is being created on a board whose firmware
doesn't include the mount code at all — otherwise the user could create a
partition that nothing will ever mount, with no explanation why. This
felt like necessary plumbing for Part 2 to be honest with the user rather
than scope creep; flagging it as a small addition beyond the literal three
listed functions.

## Part 3 — build.py / CI registration

Exactly the gap the prior patch's NOTES.md called out as left undone:

- `build.py`, `get_build_targets()`: added
  `{"name": "Generic ESP32-S3 16MB", "idf_target": "esp32s3",
  "sdkconfig_file": "configs/sdkconfig.generic_esp32s3_16mb", "zip_name":
  "Generic_ESP32S3_16MB.zip"}`, appended after the existing last entry
  (`Banshee C5`), matching the dict shape every other entry uses.
- `.github/workflows/compile_all.yml`, the `strategy.matrix.target` list:
  added the equivalent YAML entry (`name`/`idf_target`/`sdkconfig_file`/
  `zip_name`, no `ota:`/`board_key:` fields since this board's partition
  table is non-OTA single-app0, matching how `M5Stack AtomS3R`'s entry is
  written — no OTA fields either), appended after `Marauder Pancake`.

Both use the human-readable name "Generic ESP32-S3 16MB" (existing board
names mix conventions — `S3TWatch`, `M5Stack AtomS3R`, `ghostboard` — so a
readable multi-word name is normal), and I did not invent any new
`ota_slot_size`/`board_key` values since the existing AtomS3R entry (the
closest analog: also virtual-storage, also no OTA) doesn't use them
either.

## File-by-file summary of this patch (delta only, on top of the S3/generic patch)

| File | Change |
|---|---|
| `include/managers/sd_vstorage_manager.h` | new, 150 lines — types, constants, the 6 function declarations, and the safety-model doc comment |
| `main/managers/sd_vstorage_manager.c` | new, 464 lines — the implementation described above |
| `include/managers/sd_card_manager.h` | +1 line — `sd_card_virtual_storage_supported()` declaration |
| `main/managers/sd_card_manager.c` | +14 lines — `sd_card_virtual_storage_supported()` definition, right after `sd_card_is_virtual_storage()` |
| `main/core/commands/cmd_sd.c` | +167 lines — `#include "managers/sd_vstorage_manager.h"`, updated usage text, and the full `vstorage` subcommand branch (`info`/`create`/`resize`/`delete`) |
| `build.py` | +1 line — new board-matrix entry |
| `.github/workflows/compile_all.yml` | +1 line — new board-matrix entry |

## Honest confidence assessment — read this before trusting the binary write path

**What I did to de-risk this**, beyond recalling the format from memory:
I fetched the actual ESP-IDF source for the exact tag this repo's own CI
pins (`git clone -b v6.1 ... esp-idf` in `compile_all.yml`) — not a
guessed/nearby version — specifically:
- `components/bootloader_support/include/esp_flash_partitions.h` (the
  `esp_partition_info_t` struct, magic constants, offsets)
- `components/bootloader_support/src/flash_partitions.c`
  (`esp_partition_table_verify`, the function the bootloader itself uses)
- `components/esp_partition/partition.c` (`load_partitions()`, the
  app-side loader that runs after boot — this is what actually determines
  whether my table is accepted post-reboot)
- `components/partition_table/gen_esp32part.py` (the canonical
  CSV→binary tool; its `PartitionDefinition.STRUCT_FORMAT =
  b'<2sBBLL16sL'` and `PartitionTable.to_binary()` are the ground truth
  for what a "correct" table looks like on disk)

I also diffed those same files against the `v5.1` tag to see what changed
between versions, specifically to catch anything version-specific (result:
the core 32-byte entry format, magic values, MD5 entry layout, and
0xC00-byte table region are unchanged between v5.1 and v6.1; v6.1 adds a
`readonly` flag bit and new partition types for a different feature I
don't touch).

**What I'm confident about** (cross-checked against real source, not
recalled):
- The 32-byte `esp_partition_info_t` layout (`uint16 magic, uint8 type,
  uint8 subtype, uint32 offset, uint32 size, uint8 label[16], uint32
  flags` — no padding, confirmed both by reading the struct and by the
  fact that `ESP_PARTITION_TABLE_MAX_LEN / sizeof(...)` is defined to
  equal exactly 96 in the real header) and using it directly instead of
  hand-rolling an equivalent struct.
- Entry magic `0x50AA`, MD5-entry magic `0xEBEB`, MD5 digest offset 16
  bytes into that entry, MD5 computed over the concatenation of all real
  entries' raw bytes (not including the MD5 entry itself) — matches both
  the Python generator and the C verifier byte-for-byte.
- That an all-`0xFF` (erased) tail is a valid, sufficient terminator —
  I don't write an explicit end-of-table sentinel record, I just leave
  unwritten bytes at their post-erase `0xFF` value, exactly like
  `gen_esp32part.py`'s own `to_binary()` does.
- The partition-table offset (`CONFIG_PARTITION_TABLE_OFFSET`, 0x8000 by
  default) and that `CONFIG_PARTITION_TABLE_MD5=y` is set in both
  `sdkconfig.default.esp32s3` and the new
  `sdkconfig.generic_esp32s3_16mb` — meaning an MD5 entry is **not
  optional** for this specific board; getting it wrong is a hard
  boot-failure, not a soft degradation, which is why I verified this one
  particularly carefully.
- That ESP-IDF's app-side `esp_partition` module loads/caches the table
  once, lazily, on first use, and never re-reads it — confirmed by
  reading `ensure_partitions_loaded()`/`load_partitions()` directly,
  which is why `create_or_resize_storage_partition()` correctly never
  tries to remount anything and just reports "reboot required."

**Where my confidence is lower, stated explicitly**:
- I derived the new/reconstructed table entries from the **public
  `esp_partition_t` API** (`esp_partition_find`/`esp_partition_get`/
  `.encrypted`/`.readonly`) rather than parsing the raw on-flash bytes
  myself. I believe this is the safer choice (it reuses ESP-IDF's own
  already-boot-verified parse instead of duplicating that logic), and for
  the two flag bits ESP-IDF currently defines (`PART_FLAG_ENCRYPTED`,
  `PART_FLAG_READONLY`) it's a complete round-trip — but I have **not**
  run this against a real device, so I have not empirically confirmed
  that reconstructing entries this way and rewriting them produces a
  table that a real bootloader accepts. Static/source-level reasoning
  says it should; I have not seen it work.
- The scratch-sector placement (`chip_size - 0x1000`, the very top of the
  chip) assumes nothing else ever needs that region and that
  `esp_flash_erase_region`/`esp_flash_write`/`esp_flash_read` with a
  `NULL` chip pointer (meaning "default chip") behave the same way this
  close to the top of a real flash chip as anywhere else. I have no
  reason to think otherwise from the API docs, but again, untested on
  hardware.
- I have **not** compiled this. There is no ESP-IDF toolchain in this
  environment and I was explicitly told not to build with the real
  toolchain or touch hardware, so this is source-level review only (brace
  balancing, signature matching between the header and the `.c` file,
  and manual re-reading) — not a compiler-verified result. I'd treat a
  real `idf.py build` (and ideally a bench test on a spare/non-critical
  board before ever pointing this at your live hardware) as a hard
  prerequisite before trusting the write path, not a formality.

**Net assessment**: I'm confident the binary format itself is correct —
it's checked against the exact IDF source this project builds against,
not recollection. I'm considerably less confident about the *runtime*
behavior of the flash write sequence (erase/write/read timing, whether
`esp_flash_get_size()`/`esp_flash_erase_region()` behave exactly as
documented at the very top of a real 16MB chip, whether
`mbedtls_md5()` is linked in the exact form I assumed) simply because
none of that can be verified without building and running it. Given this
writes to the live partition-table sector of real hardware, I'd want that
build+bench-test step done deliberately, on a device Jack is fine
temporarily bricking if something's off, before this touches
`/dev/ttyACM0`.

## Part 4 confirmation

I did **not** attempt Part 4 (LVGL touchscreen UI for any of this). No
files under `main/gui/` or similar were touched, and nothing in this
patch depends on or assumes a display exists. That's intentionally out of
scope for this pass, per the task.
