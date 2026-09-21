# `sd vstorage create` / `sd vstorage delete` — hardware verification

Closes out the last untested corner flagged in `09-resize-and-capture-retest.md`:
`create` and `delete` go through the same `vstorage_commit_new_table()` →
write-to-`0x8000` path as `resize` (fixed in `06`), but had not been
separately exercised on hardware. Both now are.

Starting state: the 5MB partition left over from `09`'s resize test,
mounted.

## `delete`

`sd vstorage delete -y`:

```
W SD_VStorage: Deleting 'storage' partition at 0x400000 (5242880 bytes)
W SD_VStorage: Scratch copy verified OK. Committing to live partition table at 0x8000 -- do not power off now.
W SD_VStorage: Live partition table updated. A reboot is required for it to take effect.
SD:VSTORAGE:DELETE:OK
SD:VSTORAGE:REBOOT_REQUIRED:1
```

No `abort()`. Rebooted (`reboot` command) — clean boot, no panic. Boot log
correctly reflected the partition being gone:

```
E SD_Card_Manager: Storage partition not found
W SD_Card_Manager: Virtual storage mount failed (ESP_ERR_NOT_FOUND), falling back to physical SD card
W SD_Card_Manager: SPI host probe failed for SPI3_HOST / SPI2_HOST: ESP_ERR_INVALID_ARG
```

That fallback-to-physical-SD failing is expected and correct — this board
has no physical SD card or SD pins configured, so falling back to
"nothing" is the right outcome, and it's a graceful log/warning path, not
a crash.

Confirmed via CLI: `sd vstorage info` → `storage_exists=false`. `sd
status` → `mounted=false`. `free_bytes` correctly grew from ~7MB to
~12.6MB (the freed 5MB partition space returned to "unallocated"), and
`cap_bytes` (80% of free) grew correspondingly to ~9MB.

## `create`

With no storage partition existing, `sd vstorage create 4`:

```
W SD_VStorage: Scratch copy verified OK. Committing to live partition table at 0x8000 -- do not power off now.
W SD_VStorage: Live partition table updated. A reboot is required for it to take effect.
SD:VSTORAGE:CREATE:OK size_mb=4
SD:VSTORAGE:REBOOT_REQUIRED:1
```

No `abort()`. No `-y` required — and that's correct behavior per
`cmd_sd.c`'s own destructive-action gate: a `create` when nothing already
exists isn't destroying anything, so it doesn't demand confirmation the
way `resize` (always) or `create` (when a partition already exists) does.

Rebooted — clean boot, no "Storage partition not found" error this time;
virtual storage mounted with no errors on the first try.

Confirmed via CLI: `sd vstorage info` → `storage_exists=true`,
`storage_size_bytes=4194304` (exactly 4MB, matching the request). `sd
status` → `mounted=true, type=virtual`.

Functional check, not just a size check: wrote 18 bytes to
`/mnt/ghostesp/create_test.txt`, read back `"Create test worked"`
byte-identical via `sd cat`, `sd info` confirmed `size=18`.

## Result

All three operations in the `sd vstorage` family — `create`, `resize`
(`09`), and `delete` — are now individually confirmed working on real
hardware with `06`'s fix applied: no `abort()` in any of them, correct
state after reboot in each case, and a working read/write filesystem
after each transition. The device was left in a clean 4MB state (the
original documented default size) at the end of this test.

## What's still not covered

- The specific "create when a partition already exists" confirmation gate
  (`sd vstorage create <MB>` without `-y` when one already exists, which
  `cmd_sd.c` should refuse with `SD:ERR:confirmation_required`) — not
  exercised; only the "nothing exists yet" create path and the "already
  exists, resize with -y" path were tested.
- Everything else already listed as open in `09-resize-and-capture-retest.md`
  (the `-wireshark` UART log-interleaving bug, power-loss-mid-write
  recovery) is still open — unrelated to this doc.
