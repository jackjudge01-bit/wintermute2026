#!/usr/bin/env python3
"""portapack_control.py — serial driver for a HackRF One + PortaPack running
Mayhem firmware, using the official USB serial console
(https://github.com/portapack-mayhem/mayhem-firmware/wiki/usb-serial-console).

Verified against the real device this skill was built for (2026-09-22):
port, baud, line ending, and every command wrapped below actually returned
the expected response, not assumed from the wiki alone.

## The one thing that will make this look completely broken if you miss it

The HackRF+PortaPack enumerates as ONE of two different USB PIDs depending
on which firmware mode it's currently in:

  - 0x6089 ("HackRF passthrough" / native HackRF firmware) - the Mayhem
    console is NOT listening in this mode. Every command times out with
    zero bytes back, silently - no error, just nothing, which looks
    exactly like a wrong port/baud/cable problem and isn't.
  - 0x6018 (Mayhem's own serial shell) - this is the mode this script
    needs. Confirm with `lsusb -d 1d50:` before debugging anything else if
    a command returns empty.

Also: the device's /dev/ttyACMn path is NOT stable across reconnects or
mode switches - it re-enumerates as a new tty each time. Don't hardcode a
port number; re-detect it (see find_port() below) or pass --port
explicitly after checking `ls /dev/ttyACM*` / dmesg yourself.

## Button 6 is DFU - do not simulate it

The `button` command's mapping (1=Right, 2=Left, 3=Down, 4=Up, 5=Select,
6=DFU, 7=Rotary Left, 8=Rotary Right) includes DFU (firmware update mode)
as button 6. There's no dedicated "back/home" button in this mapping -
navigate with the directional buttons instead, and never send `button 6`
looking for a shortcut back to a menu.

## setfreq isn't guaranteed to stick

Verified live: running `setfreq` while an app that manages its own
frequency is active (e.g. blerx, which channel-hops across BLE
advertising channels on its own schedule) returns `ok` but the frequency
shown by a subsequent `radioinfo` can still reflect the app's own value,
not what was just set. Don't assume a bare `ok` means the frequency
change actually took effect - read back with `radioinfo` and check.

Usage as a library:
    from portapack_control import PortaPack
    pp = PortaPack("/dev/ttyACM2")
    print(pp.applist())
    pp.appstart("blerx")
    print(pp.radioinfo())
    pp.close()

Usage from the CLI:
    python3 portapack_control.py --port /dev/ttyACM2 help
    python3 portapack_control.py --port /dev/ttyACM2 appstart blerx
    python3 portapack_control.py --port /dev/ttyACM2 radioinfo
"""
import argparse
import glob
import json
import os
import re
import sys
import time

import serial  # pyserial

BAUD = 115200  # USB CDC - the value barely matters but this is what was verified

# Ground truth for applist() parsing - see its docstring. Overridable via
# the known_ids_path argument if this catalog ever moves.
KNOWN_APP_IDS_PATH = os.path.expanduser("~/data/rf/mayhem_apps.json")

DANGEROUS_BUTTON_VALUES = {6}  # DFU


def find_port():
    """Best-effort: return the first /dev/ttyACMn that looks alive. Prefer
    passing --port explicitly once you know it - re-enumeration means this
    guess can be wrong, especially right after a mode switch."""
    candidates = sorted(glob.glob("/dev/ttyACM*"))
    return candidates[-1] if candidates else None


class PortaPack:
    def __init__(self, port=None, baud=BAUD, timeout=1.0):
        self.port_path = port or find_port()
        if not self.port_path:
            raise RuntimeError("No /dev/ttyACM* device found - is it plugged in "
                                "and in Mayhem serial-shell mode (USB PID 0x6018, "
                                "not 0x6089)? Check with: lsusb -d 1d50:")
        self.ser = serial.Serial(self.port_path, baud, timeout=timeout)
        time.sleep(1)
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def close(self):
        self.ser.close()

    def send(self, cmd, wait=1.5):
        self.ser.reset_input_buffer()
        self.ser.write(cmd.encode() + b"\r\n")
        out = b""
        end = time.time() + wait
        while time.time() < end:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                out += chunk
        text = out.decode("utf-8", errors="replace")
        # Every response echoes the command back on its own first line,
        # and ends with the "ch> " prompt - strip both for a cleaner value,
        # but keep raw available for callers that want to check for "ok".
        lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
        if lines and lines[0].strip() == cmd:
            lines = lines[1:]
        if lines and lines[-1].strip().rstrip(">").strip() in ("ch", ""):
            lines = lines[:-1]
        return "\n".join(lines).strip(), text

    # --- Thin, explicit wrappers for the commands this skill actually
    # needs day to day. Anything else in `help`'s output can be sent via
    # send() directly - this isn't trying to wrap all ~60 commands. ---

    def help(self):
        clean, _ = self.send("help", wait=1.5)
        return clean

    def applist(self, known_ids_path=KNOWN_APP_IDS_PATH):
        """Returns list of {id, name, category} dicts.

        The device packs multiple apps per line with no reliable delimiter
        between one entry's (possibly multi-word) name and the next
        entry's id - "recon Recon capture Capture lookingglass Looking
        Glass" has no punctuation marking where "Looking Glass" ends and
        the next id begins. A naive regex gets this wrong (verified: it
        split "Looking Glass" into a fake extra entry). The reliable fix
        is to use the known app-id list (~/data/rf/mayhem_apps.json on
        this box) as ground truth for where entries start, since ids are
        always a single lowercase/underscore token and never collide with
        a display name. Falls back to the fragile whitespace-only split
        if that file isn't available, and says so.
        """
        # wait=4.0, not less: verified live that 2.5s truncates the full
        # list at 115200 baud (~2600 chars takes longer than that to
        # arrive) and silently drops trailing entries with no error.
        clean, _ = self.send("applist", wait=4.0)
        tokens = clean.split()

        known_ids = None
        if known_ids_path:
            try:
                with open(known_ids_path) as f:
                    known_ids = set(json.load(f).keys())
            except (OSError, json.JSONDecodeError):
                known_ids = None

        apps = []
        if known_ids:
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok in known_ids:
                    app_id = tok
                    i += 1
                    name_parts = []
                    category = None
                    while i < len(tokens) and tokens[i] not in known_ids:
                        t = tokens[i]
                        if t.startswith("[") and t.endswith("]"):
                            category = t.strip("[]")
                        else:
                            name_parts.append(t)
                        i += 1
                    apps.append({"id": app_id, "name": " ".join(name_parts), "category": category})
                else:
                    i += 1  # token didn't match a known id or a name in progress - skip
        else:
            # Degraded fallback - known to mis-split multi-word names.
            for tok in re.finditer(r"(\S+)\s+([A-Za-z0-9 .\-+/]+?)(?:\s*\[(\w+)\])?(?=\s+\S+\s+[A-Za-z]|\s*$)", clean):
                apps.append({"id": tok.group(1), "name": tok.group(2).strip(), "category": tok.group(3)})
        return apps

    def appstart(self, app_id):
        clean, raw = self.send(f"appstart {app_id}", wait=2.0)
        return "ok" in clean.lower(), clean

    def setfreq(self, hz):
        clean, raw = self.send(f"setfreq {int(hz)}", wait=1.0)
        return "ok" in clean.lower(), clean

    def radioinfo(self):
        clean, _ = self.send("radioinfo", wait=1.5)
        info = {}
        for line in clean.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
        return info

    def sysinfo(self):
        clean, _ = self.send("sysinfo", wait=1.5)
        return clean

    def button(self, n):
        n = int(n)
        if n in DANGEROUS_BUTTON_VALUES:
            raise ValueError(
                f"button {n} is mapped to DFU (firmware update mode) - refusing to "
                "send it. If you genuinely want DFU mode, use the documented `dfu` "
                "command directly and be sure that's actually what you want."
            )
        clean, _ = self.send(f"button {n}", wait=1.0)
        return clean

    def touch(self, x, y):
        clean, _ = self.send(f"touch {int(x)} {int(y)}", wait=1.0)
        return clean

    def keyboard(self, text):
        hex_str = text.encode().hex()
        clean, _ = self.send(f"keyboard {hex_str}", wait=1.0)
        return clean

    def screenshot(self):
        """Triggers a screenshot saved to the device's SD card (per the
        console command) - this does not transfer the image over serial.
        Pull it off afterward the same way any file gets pulled off SD
        (see ghostesp-file-transfer for the equivalent problem on the
        other board - the PortaPack's own file commands (ls/fread/frb)
        are the analogous path here, not implemented as a convenience
        wrapper in this script yet)."""
        clean, _ = self.send("screenshot", wait=1.5)
        return clean


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None, help="e.g. /dev/ttyACM2 - re-detected if omitted, but check first")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="raw console command and args, e.g.: appstart blerx")
    args = ap.parse_args()

    if not args.command:
        ap.error("give a console command, e.g.: appstart blerx")

    pp = PortaPack(args.port, args.baud)
    print(f"Connected: {pp.port_path}", file=sys.stderr)
    try:
        clean, raw = pp.send(" ".join(args.command), wait=2.0)
        print(clean)
    finally:
        pp.close()


if __name__ == "__main__":
    main()
