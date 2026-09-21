# capture-crash-with-SD-mounted — actual fix — notes

`07-defer-capture-writes-to-stop.patch` is layered on top of `01`+`02`+`03`+`05`+`06`.
This is the fix that actually worked, after two attempts (both shipped as
part of `05`, see below) that didn't. **Hardware-verified**: flashed and
re-tested on the real board this whole series targets, not just build-verified.

## Recap: two prior attempts on this same crash, both failed on hardware

`05-fix-pcap-callback-stack-overflow.patch` (already committed/pushed)
contains two changes that were built and tested as fixes for this exact
crash and did not work:

1. Bumped `pcap_writer_task`'s FreeRTOS stack 3072→8192 bytes, on the theory
   the FATFS/wear-levelling call chain was overflowing it.
2. (Applied afterward, in a later session on this same investigation, not
   its own patch number since it didn't work either) moved the one-time
   queue/pool/task creation out of its lazy first-call-inside-the-RX-callback
   path into `pcap_init()`, on the theory that setup itself was overflowing
   whatever callback called it first.

Both were flashed to real hardware and re-tested with `capture -probe` —
crashed identically both times (`Guru Meditation Error: Core 0 panic'ed
(Double exception)`, `A0: 0xbad00bad`, `A1: 0x60100000`, `EXCCAUSE: 0x2`
Instruction Fetch Error). A later coredump analysis (pulled via `sd read`
of the SD-saved copy, since GhostESP's own boot code erases the flash
coredump partition after copying it — a separate tooling gap that had to be
worked around to get a real decode) showed the crashed task's own stack
usage at panic time was `1024/7156` bytes used/free — nowhere near
overflow. **That directly disproves the stack-overflow theory both prior
attempts were built on.** Root cause of the instruction-fetch fault itself
was never conclusively identified; a PSRAM/flash SPI-bus-contention
hypothesis was raised (this board is a third-party "Aideepen" clone, not an
official Espressif module, and reviewers report other nonstandard hardware
quirks on it) but not confirmed before this patch made the question moot in
practice.

## The actual fix: stop writing to SD while capture is still receiving

Every previous attempt assumed the crash was *how* the write to the
SD-backed file happened (stack size, setup timing) and left *when* it
happened untouched: writes to the mounted file were still happening while
WiFi/BLE RX was actively running, triggered either by the in-RAM buffer
filling up (`pcap_write_packet_to_buffer()`, `main/vendor/pcap.c`) or by two
separate periodic-flush timers (`pcap_writer_task`'s every-32-packets and
idle-500ms auto-flush in `main/core/callbacks.c`; a dedicated 1s/200ms
`esp_timer` in `main/managers/ble_manager.c` for BLE captures).

This patch stops flushing to SD during active capture entirely, regardless
of why the earlier flushes were crashing:

- **`PCAP_BUFFER_SIZE`** (`include/vendor/pcap.h`): 8192 → 1MB (PSRAM). The
  old size filled in ~2s of real probe-request traffic in a normal
  environment — meaning practically every capture longer than a couple
  seconds was *already* triggering an in-capture flush before any of this
  session's fixes even got a chance to matter. (Independent corroboration
  found searching GhostESP-Revival's own issue tracker: issue #158, a
  different unrelated SD-write bug on CYD hardware, was fixed by
  "increasing buffer size" in this same `pcap.h` — a real, previously
  effective lever on this exact file.)
- **`pcap_write_packet_to_buffer()`** (`main/vendor/pcap.c`): when the
  (now much bigger) buffer does fill, packets are dropped
  (`packets_dropped` stat, already existed) instead of triggering an
  in-capture flush.
- **`pcap_writer_task()`** (`main/core/callbacks.c`): the two periodic/idle
  auto-flush calls removed.
- **`pcap_flush_timer_cb()`** (`main/managers/ble_manager.c`): made a
  no-op; the periodic BLE flush timer keeps running (harmless) but no
  longer touches the file.

The only remaining call to `_pcap_flush_buffer_to_file_nolock()` in the
write-while-capturing path is gone. Every other existing call to
`pcap_flush_buffer_to_file()` was already at genuine capture-stop time
(`pcap_file_close()`, and the various "Final flush" calls in
`ble_manager.c` / `plugin_api_lowlevel.c`) and is untouched — those already
ran with WiFi/BLE RX disabled (`cmd_capture.c`'s `-stop` handler calls
`wifi_manager_stop_monitor_mode()` before `pcap_file_close()`), so they were
never part of the problem. Capture length is now bounded by the in-RAM
buffer rather than SD free space; a very long capture will start dropping
packets once the buffer fills instead of flushing mid-capture. That's the
accepted tradeoff of this approach, not an oversight.

## Verification — on real hardware, not just compiled

- `idf.py build` (`build.py --targets 46`) on `configs/sdkconfig.generic_esp32s3_16mb`
  with `01`+`02`+`03`+`05`+`06`+`07` all applied: clean, no errors.
- Flashed to the actual board. `capture -probe` run for 20s: **no crash**,
  `Capture stats: seen=801 written=801 dropped=0`. Confirmed the file
  actually landed on the SD-backed mount: `sd info` on the resulting
  `.pcap` reported `size=153879` bytes, matching the packet count.
- Repeated with a 30s `capture -probe` run: **no crash**, `seen=1064
  written=1064 dropped=0`.
- `capture -ble` run for 15s: **no crash**, `seen=400 written=400
  dropped=0`, file written correctly.
- Both prior (failed) fixes' underlying diagnosis attempts remain applied
  underneath this patch (the `pcap_writer_task` 8192-byte stack, the
  `pcap_init()` queue warm-up) — they're harmless, just not what actually
  fixed it, and weren't reverted since there was no reason to.

## What this doesn't establish

The actual hardware/software mechanism behind the original instruction-fetch
fault is still not identified — this patch works around it by never
triggering it, not by understanding and correcting it. If a future capture
type or code path reintroduces a flush-to-SD during active RX (directly
calling `_pcap_flush_buffer_to_file_nolock()`/`pcap_flush_buffer_to_file()`
from a live callback instead of going through the queue and waiting for
stop), the same crash would likely resurface. Worth keeping in mind for any
future capture-related changes to this codebase.
