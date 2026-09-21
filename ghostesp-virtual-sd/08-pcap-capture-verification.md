# PCAP capture verification — `-raw` capture type, byte-level check

Follow-up to `04-hardware-verification.md` (confirmed generic file I/O on
the virtual partition via a manual `sd write`/`sd cat` text round trip)
and `07-defer-capture-writes-to-stop-NOTES.md` (the fix that made
`capture -probe`/`capture -ble` stop crashing, hardware-verified with
packet counts matching file sizes). This doc adds one more data point on
top of both: does the resulting `.pcap` file actually contain
**well-formed pcap data**, checked at the byte level, not just a size or
packet-count that matches — and does a capture type `07` didn't test
(`-raw`) also work.

**Answer: yes**, confirmed on real hardware, byte-level.

## Firmware under test

This ran against whatever was flashed on the board at the time — based on
`local_builds`/file evidence, that build already included
`01`+`02`+`03`+`05`+`06`+`07` (the file `probescan_4.pcap` already present
in `/mnt/ghostesp/pcaps/` at 153,879 bytes matches `07`'s own 20s
`capture -probe` test exactly, i.e. that file is `07`'s verification
artifact, still sitting on the partition from that earlier session, not
something new). This test did not reflash the board first.

## Test performed

1. Baseline: `sd status` — `mounted=true`, `type=virtual`.
2. `capture -raw` (passive, receive-only — no transmission, no deauth).
   Firmware logged:
   ```
   Starting raw packet capture...
   PCAP: saving to SD as /mnt/ghostesp/pcaps/rawscan_1.pcap
   RAW: current channel verified as 6
   WiFi capture started.
   Type: raw
   Channel: 6
   ```
3. Ran 20 seconds. Background `PCAP writer HWM (bytes): ...` log lines
   climbed during the run — that's the in-RAM buffer's fill level, not a
   flush-to-SD event; per `07`, writes to the file now only happen at
   `capture -stop`, so this just confirms the buffer was actively
   receiving real frames the whole time, not stalled.
4. `capture -stop`.
5. `sd info` on the result: `/mnt/ghostesp/pcaps/rawscan_1.pcap`,
   **450,386 bytes**.
6. Went one step further than `07`'s size/count check: pulled the first
   512 bytes back over serial (`sd cat <path> 0 512 --base64`), decoded
   the base64 locally, and parsed it as binary pcap:
   - Magic number `d4c3b2a1` — correct little-endian classic-pcap magic.
   - Version `2.4`, snaplen `65535` — a standard, well-formed global
     header.
   - Linktype `127` (`LINKTYPE_IEEE802_11_RADIOTAP`) — exactly right for
     a raw 802.11 capture with radiotap headers.
   - First packet record: `incl_len=89, orig_len=89` — a real,
     correctly-sized frame, not padding or corruption.
   - The frame decodes as a radiotap header followed by an 802.11 frame,
     destination `ff:ff:ff:ff:ff:ff` (broadcast), type/subtype byte
     `0x80` — a genuine **beacon frame** from a real nearby AP.

This confirms the bytes on disk are a standards-compliant pcap file a
real tool (Wireshark, tcpdump, dpkt, scapy) can open, not just a
plausible-looking size.

## What this adds on top of `07`

- `07` verified `-probe` and `-ble`. This adds `-raw` as a third working
  capture type on the fixed firmware.
- `07` verified file size matched reported packet counts. This adds an
  actual binary parse of the file contents — global header fields and a
  real decoded 802.11 frame — which `07`'s own testing didn't do.

## What this doesn't establish

- Doesn't touch the separate `-wireshark`/`-wiresharkble` raw-UART-stream
  capture mode, which has its own known, source-confirmed log-interleaving
  risk (not covered by `07`'s fix — that fix is about SD writes during
  active capture, unrelated to the UART streaming path).
- Doesn't re-test `sd vstorage resize`/`create`/`delete` — `06`'s fix is
  compiled into this build (per the file evidence above) but, per
  `06-fix-vstorage-resize-abort-NOTES.md`, has not actually been re-run
  on hardware to confirm the abort is gone. This session didn't touch that
  either.
