#!/usr/bin/env python3
"""Module 1 — passive WiFi AP/station recon.

Single pyserial session (open -> scan -> scan -> close), matching the
pattern this whole toolchain has to use: the ESP32's UART is exclusively
held by whichever process has it open, so commands and results have to
flow through one connection, not a command-sender plus a separate
listener fighting over the port.

Purely passive: only listens to what's already being broadcast (beacon
frames for APs, association-derived station lists). Never transmits
anything, never joins a network, never touches a specific device. Safe
to run against your own vicinity without per-target authorization, the
same way any WiFi scanner (survey tool, `iwlist scan`, etc.) is.

What it does:
1. `scanap <seconds>` - broad AP scan (SSID, BSSID, channel, RSSI).
2. `scansta` - station scan (finds MACs associated with each AP by
   listening to data-frame source/dest addressing) - left running for
   `--sta-seconds`, then `stopscan` to end it.
3. `list -s` - the detailed per-station listing (MAC, vendor, associated
   AP, AP BSSID/vendor) - only the stop event gives a count, this is
   what gives the actual list to parse.
4. Groups stations by their associated AP and ranks APs by client count.

Output: JSON to stdout (and optionally a file) - one entry per AP, with
its scan fields plus a `clients` list and `client_count`, sorted by
client_count descending. This ranking is what Module 2 uses to decide
which APs are worth the heavier per-target work.

Usage:
    python3 module1_passive_scan.py --port /dev/ttyACM0 --ap-seconds 10 --sta-seconds 15
    python3 module1_passive_scan.py --out /tmp/scan.json
"""
import argparse
import json
import re
import sys
import time

import serial  # pyserial


def open_port(port, baud):
    ser = serial.Serial(port, baud, timeout=1)
    time.sleep(1)
    if ser.in_waiting:
        ser.read(ser.in_waiting)  # drain boot/prompt noise
    return ser


def send(ser, cmd):
    ser.reset_input_buffer()
    ser.write(cmd.encode() + b"\r")


def read_for(ser, seconds):
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            out += chunk
    return out.decode("utf-8", errors="replace")


# --- Parsers, matched against the exact formats GhostESP's CLI prints
# (main/scans/wifi/ap_scan.c and station_scan.c) - not guessed. ---

AP_RE = re.compile(
    r"\[(\d+)\]\s*SSID:\s*(?P<ssid>.*?),\s*"
    r"BSSID:\s*(?P<bssid>[0-9A-Fa-f:]{17}),\s*"
    r"RSSI:\s*(?P<rssi>-?\d+),\s*"
    r"Channel:\s*(?P<channel>\d+),?"
    r"(?:\s*Vendor:\s*(?P<vendor>[^\r\n]+))?",
    re.DOTALL,
)

STATION_RE = re.compile(
    r"\[(\d+)\]\s*Station MAC:\s*(?P<sta_mac>[0-9A-Fa-f:]{17}),\s*"
    r"Station Vendor:\s*(?P<sta_vendor>[^,]*),\s*"
    r"Associated AP:\s*(?P<ssid>[^,]*),\s*"
    r"AP BSSID:\s*(?P<ap_bssid>[0-9A-Fa-f:]{17}),\s*"
    r"AP Vendor:\s*(?P<ap_vendor>[^\r\n]+)",
    re.DOTALL,
)

STOP_COUNT_RE = re.compile(r"Station Scan Stopped\. Found (\d+) stations\.")


def parse_aps(text):
    aps = {}
    for m in AP_RE.finditer(text):
        bssid = m.group("bssid").upper()
        aps[bssid] = {
            "ssid": m.group("ssid").strip(),
            "bssid": bssid,
            "rssi": int(m.group("rssi")),
            "channel": int(m.group("channel")),
            "vendor": (m.group("vendor") or "").strip() or None,
            "clients": [],
        }
    return aps


def parse_stations(text):
    stations = []
    for m in STATION_RE.finditer(text):
        stations.append({
            "station_mac": m.group("sta_mac").upper(),
            "station_vendor": m.group("sta_vendor").strip(),
            "ap_ssid": m.group("ssid").strip(),
            "ap_bssid": m.group("ap_bssid").upper(),
        })
    return stations


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--ap-seconds", type=int, default=10, help="AP scan duration")
    ap.add_argument("--sta-seconds", type=int, default=15, help="station scan duration before stopping it")
    ap.add_argument("--out", default=None, help="also write JSON here")
    args = ap.parse_args()

    print(f"Opening {args.port} @ {args.baud}...", file=sys.stderr)
    ser = open_port(args.port, args.baud)

    try:
        print(f"AP scan ({args.ap_seconds}s)...", file=sys.stderr)
        send(ser, f"scanap {args.ap_seconds}")
        ap_text = read_for(ser, args.ap_seconds + 3)
        aps = parse_aps(ap_text)
        print(f"  {len(aps)} APs seen", file=sys.stderr)

        print(f"Station scan ({args.sta_seconds}s)...", file=sys.stderr)
        send(ser, "scansta")
        read_for(ser, args.sta_seconds)
        send(ser, "stopscan")
        stop_text = read_for(ser, 3)
        m = STOP_COUNT_RE.search(stop_text)
        reported_count = int(m.group(1)) if m else None

        print("Fetching station detail (list -s)...", file=sys.stderr)
        send(ser, "list -s")
        list_text = read_for(ser, 4)
        stations = parse_stations(list_text)
        print(f"  {len(stations)} stations parsed"
              + (f" (device reported {reported_count})" if reported_count is not None else ""),
              file=sys.stderr)

        # Group stations under their AP by BSSID.
        unmatched = []
        for sta in stations:
            ap_entry = aps.get(sta["ap_bssid"])
            if ap_entry is not None:
                ap_entry["clients"].append(sta)
            else:
                unmatched.append(sta)  # AP not in this scan window's results

        ranked = sorted(aps.values(), key=lambda a: len(a["clients"]), reverse=True)
        for a in ranked:
            a["client_count"] = len(a["clients"])

        result = {
            "ap_scan_seconds": args.ap_seconds,
            "station_scan_seconds": args.sta_seconds,
            "ap_count": len(ranked),
            "station_count_parsed": len(stations),
            "station_count_device_reported": reported_count,
            "unmatched_stations": unmatched,
            "access_points": ranked,
        }

        out_json = json.dumps(result, indent=2)
        print(out_json)
        if args.out:
            with open(args.out, "w") as f:
                f.write(out_json)
            print(f"Wrote {args.out}", file=sys.stderr)

    finally:
        ser.close()


if __name__ == "__main__":
    main()
