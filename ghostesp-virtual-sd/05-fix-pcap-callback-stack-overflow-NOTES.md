# pcap capture-crash-with-SD-mounted fix — notes

`05-fix-pcap-callback-stack-overflow.patch` is layered on top of
`01-static-partition.patch` + `02-dynamic-sizing.patch` +
`03-fix-mbedtls-md5-idf61.patch`, in that order. It's independent of the
other three (it doesn't touch anything about the storage partition itself)
but was found *because of* them — you only hit it once the board actually
has a mounted SD (real or virtual) for capture to write to, which is exactly
what this series adds for boards that had no SD slot at all.

## The bug: any capture command panics the device once SD is mounted

Confirmed live on real hardware (the same board this whole series targets):
with `sd vstorage info` showing `board_mounts_virtual_storage=true`, running
`capture -probe` panics within ~2 seconds of the first packet:

```
Guru Meditation Error: Core  0 panic'ed (Double exception).
PC      : 0x4039741e  A1 (stack ptr): 0x60100000   <- not a valid RAM address
Backtrace: 0x4039741b:0x60100000 |<-CORRUPTED
```

followed by an automatic reboot (`esp_core_dump_flash` confirms a core dump
was saved and reloaded on the following boot). Without SD mounted, the same
command runs fine indefinitely — capture just streams to UART instead of a
file.

## Root cause: a too-small dedicated writer task, not the raw driver callback

The first hypothesis (WiFi driver's own internal RX-processing task, which
has no exposed stack-size config at all in ESP-IDF v6.1 — confirmed by
`grep -rn WIFI_TASK_STACK_SIZE` returning zero matches anywhere in the IDF
tree, and no `task_stack_size` field in `wifi_init_config_t`) turned out to
be wrong for this specific crash, on reading the actual call chain instead
of assuming it from the symptom. `main/core/commands/cmd_capture.c`'s
`-probe`/`-deauth`/`-beacon`/`-raw`/`-eapol`/`-pwn`/`-wps` handlers register
callbacks (`wifi_probe_scan_callback` etc., `main/core/callbacks.c`) that
were **already** doing the right thing: they only `memcpy` the packet into a
fixed pool slot and `xQueueSend` it — no filesystem call in the raw
promiscuous-callback context at all. That part of this codebase already
solved the "don't block the WiFi driver's callback" problem, with a comment
saying so (`// queued writer to avoid heavy work in promiscuous callback`).

The actual crash site is the *consumer* of that queue: `pcap_writer_task`
(`main/core/callbacks.c`), a dedicated FreeRTOS task GhostESP creates itself
via `xTaskCreate_psram(pcap_writer_task, "pcap_wr", 3072, ...)`. Its loop
calls `pcap_flush_buffer_to_file()` → `_pcap_flush_buffer_to_file_nolock()`
(`main/vendor/pcap.c`) → `fwrite()`/`fflush()` on the SD-mounted file. On the
virtual SD, that's the full FATFS → wear-levelling → `esp_partition` →
SPI-flash-erase/write call chain, executed synchronously on this task's own
3072-byte stack — and 3072 bytes isn't enough for it. The result is a stack
overflow that corrupts the task's own context, which is exactly what the
`Double exception` / `A1: 0x60100000` (a peripheral-space address, not a
valid stack pointer) / `Backtrace: ...|<-CORRUPTED` panic signature looks
like.

Unlike the WiFi driver's internal task, `pcap_writer_task`'s stack size *is*
a number GhostESP's own code controls directly — no ESP-IDF limitation here.

## Second problem found while tracing every caller: not everything uses the queue

`pcap_write_packet_to_buffer()` (the function `pcap_writer_task` calls
safely) also has direct callers that bypass the queue entirely and call it
straight from their own callback/event context — the exact pattern
`pcap_writer_task` exists to avoid:

- `main/managers/ble_manager.c` — the BLE capture path (`capture -ble`,
  `-wiresharkble`), called directly from the NimBLE host's GAP event
  callback.
- `main/core/callbacks.c` — BLE skimmer detection (`capture -skimmer`),
  same NimBLE callback context, and worse: it also called
  `pcap_flush_buffer_to_file()` immediately afterward, forcing the
  synchronous flash write inline instead of letting the writer task batch
  it.
- `main/core/callbacks.c` — `wifi_listen_probes_callback`, a separate
  "listen probes" feature (not the `capture` command family), called
  directly from the raw WiFi promiscuous callback.
- `main/managers/plugin_api_lowlevel.c` — `plugin_wifi_promisc_cb`, the
  plugin/scripting API's raw WiFi packet monitor, same raw-callback context.

All four are exposed to the same crash class as the one reproduced above,
by the same mechanism, just via a different, unsized-for-it calling context
instead of `pcap_writer_task`'s. Not individually reproduced on hardware —
inferred from being architecturally identical to the confirmed crash.

Not in scope here: `zigbee_capture_task` (`main/managers/zigbee_manager.c`)
calls `pcap_write_packet_to_buffer()` from its own dedicated task too
(4096-byte stack, `xTaskCreate`), same shape as `pcap_writer_task` but not
crashed or resized — it's ESP32-C5/C6-only (`#if defined(CONFIG_IDF_TARGET_ESP32C5)
|| defined(CONFIG_IDF_TARGET_ESP32C6)`) and this board is an S3, so it isn't
even compiled into this build.

## The fix

1. `pcap_writer_task`'s stack: 3072 → 8192 bytes. Still PSRAM-backed (via
   `xTaskCreate_psram`, already the case before this patch), and 8192 is
   the same value already used elsewhere in this codebase for comparable
   I/O-adjacent tasks (`arp_scan`, `port_scan`, several `eth_*` tasks, the
   `mdns_scan` task, etc.) — not an arbitrary number.
2. `enqueue_pcap_write_typed()` / `enqueue_pcap_write()` (the existing safe
   queue-write helpers) changed from `static inline` (file-local to
   `callbacks.c`) to ordinary external functions, declared in
   `include/core/callbacks.h`, so the other three call sites can use them.
3. All four direct-call sites above changed to call
   `enqueue_pcap_write_typed()` / `enqueue_pcap_write()` instead of
   `pcap_write_packet_to_buffer()` directly. The BLE skimmer path's forced
   `pcap_flush_buffer_to_file()` call was removed outright — the writer
   task's existing periodic/threshold auto-flush covers it.

No new task or queue was added — this reuses the pool/queue/task machinery
that was already in `callbacks.c`, sized correctly, and applied
consistently to every caller of `pcap_write_packet_to_buffer()`.

## Verification status — be clear-eyed about this

- **Established**: `idf.py build` (via `build.py --targets 46`, the
  "Generic ESP32-S3 16MB" entry) on `configs/sdkconfig.generic_esp32s3_16mb`
  with patches 01+02+03+05 all applied: exit code 0, "Project build
  complete." Only pre-existing, unrelated warnings elsewhere in the tree
  (`options_screen.c`, `wifi_manager.c` unused functions/variables), same
  as noted in `03`'s own notes.
- This patch file (`git apply --check`) was verified to apply cleanly, in
  order, on top of a fresh `01`+`02`+`03` checkout at `0ed9c8d3`.
- **Not yet established**: this fix has not been flashed to hardware and
  re-run against `capture -probe` (or `-ble`/`-skimmer`/the plugin API path)
  to confirm the panic is actually gone, as opposed to just being a
  plausible, well-evidenced fix that compiles. Flashing is a separate
  approval, same as the rest of this series (see `04-hardware-verification.md`
  for why build and flash are treated as two separate steps here).
