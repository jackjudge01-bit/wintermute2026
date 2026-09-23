#!/usr/bin/env python3
"""Module 2 — focused, channel-locked probe-request harvesting.

Takes Module 1's ranked AP list and, for the top N APs by client count,
does a longer, channel-locked listen on each AP's own channel using
`capture -wireshark` (streams raw pcap frames straight over the same
serial connection this script already has open - no separate file/SD
step needed, same single-pyserial-session reasoning as Module 1).

Still fully passive - "aggressive" here means more focused and
longer-running than Module 1's broad sweep, not that anything gets
transmitted. This never sends a deauth, never joins a network, never
touches an individual device - it only listens longer, on a specific
channel, to what's already being broadcast. Every WiFi client
periodically broadcasts probe-request frames asking "is <SSID> here?"
for networks it remembers - the same passive behavior a phone's WiFi
scanner produces constantly, whether or not that phone is one of the
specific stations Module 1 saw associated to the target AP. That's the
real limitation to be upfront about: this captures whichever devices are
transmitting on that channel during the listen window, which will
usually include (but isn't guaranteed to be limited to) Module 1's
client list for that AP.

What it extracts per probe-request frame: the transmitting client's MAC
and the SSID it's asking about (empty string = broadcast/wildcard probe,
common on newer phones with randomized-MAC privacy features - that's a
real signal too: it means the device wasn't willing to reveal what it's
looking for).

What it does NOT have: signal strength (RSSI) per probe. GhostESP emits
a stub radiotap header in this stream (a fixed 8 bytes declaring no
present fields, not real radio metadata) - there is no RSSI in this raw
data to extract. Module 1's per-AP RSSI (from the beacon-based AP scan)
is the only signal-strength data this toolchain has; don't claim
otherwise.

This is the deliberate stopping point of this toolchain - see the
skill's SKILL.md for why it goes no further (no deauth, no forced
reconnects, no joining/probing individual client devices on their own
network) without separate, explicit authorization for a specific target.

Usage:
    python3 module2_aggressive_probe.py --port /dev/ttyACM0 \\
        --module1-json /tmp/scan.json --top 3 --seconds-per-ap 20
"""
import argparse
import json
import re
import sys
import time

import serial  # pyserial

PCAP_GLOBAL_HEADER_LEN = 24
PCAP_RECORD_HEADER_LEN = 16
RADIOTAP_HEADER_LEN = 8  # GhostESP emits a fixed stub, verified in main/vendor/pcap.c
MGMT_HEADER_LEN = 24     # FC(2) + Duration(2) + Addr1(6) + Addr2(6) + Addr3(6) + SeqCtl(2)

PCAP_MAGIC = b"\xd4\xc3\xb2\xa1"


def open_port(port, baud):
    ser = serial.Serial(port, baud, timeout=1)
    time.sleep(1)
    if ser.in_waiting:
        ser.read(ser.in_waiting)
    return ser


def send(ser, cmd):
    ser.reset_input_buffer()
    ser.write(cmd.encode() + b"\r")


def read_raw_for(ser, seconds):
    """Read raw bytes (no text decode - this stream is mixed binary pcap
    frames and CLI status text; decoding here would corrupt the binary
    part, same lesson as the earlier serial-transfer work this session
    did)."""
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            out += chunk
    return out


def extract_probe_requests(raw):
    """Scan a raw capture -wireshark byte stream for probe-request frames.
    Returns a list of {mac, ssid} dicts. Best-effort: finds the pcap
    global header's magic to locate the start of real frame data, then
    walks forward looking for well-formed probe-request records rather
    than trusting every byte offset (the stream has CLI text status lines
    interleaved with binary frames, so a strict single-pass record walk
    would desync on the first stray text byte)."""
    results = []
    start = raw.find(PCAP_MAGIC)
    if start < 0:
        return results  # no pcap data in this window at all

    pos = start + PCAP_GLOBAL_HEADER_LEN
    n = len(raw)
    while pos + PCAP_RECORD_HEADER_LEN <= n:
        incl_len = int.from_bytes(raw[pos + 8:pos + 12], "little")
        # Sanity bound: a real 802.11 frame (+ the 8-byte stub radiotap)
        # is at most ~2340 bytes; anything outside that range means we've
        # desynced (probably hit interleaved CLI text) - resync by
        # scanning forward for the next plausible record start instead of
        # trusting this offset.
        if not (RADIOTAP_HEADER_LEN + MGMT_HEADER_LEN <= incl_len <= 2340):
            pos += 1
            continue

        frame_start = pos + PCAP_RECORD_HEADER_LEN
        frame_end = frame_start + incl_len
        if frame_end > n:
            break  # rest of this frame hasn't arrived yet in this read window

        frame = raw[frame_start:frame_end]
        dot11 = frame[RADIOTAP_HEADER_LEN:]
        if len(dot11) >= MGMT_HEADER_LEN and dot11[0] == 0x40:  # mgmt, probe request
            client_mac = dot11[10:16]
            body = dot11[MGMT_HEADER_LEN:]
            ssid = None
            if len(body) >= 2 and body[0] == 0x00:  # tag 0 = SSID
                tag_len = body[1]
                if len(body) >= 2 + tag_len:
                    raw_ssid = body[2:2 + tag_len]
                    try:
                        ssid = raw_ssid.decode("utf-8")
                    except UnicodeDecodeError:
                        ssid = raw_ssid.decode("latin-1")
            results.append({
                "client_mac": ":".join(f"{b:02X}" for b in client_mac),
                "probed_ssid": ssid if ssid else "(broadcast/wildcard)",
            })
            pos = frame_end
        else:
            pos += 1  # not a probe request at this offset, keep resyncing

    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--module1-json", required=True, help="output file from module1_passive_scan.py")
    ap.add_argument("--top", type=int, default=3, help="how many top (by client count) APs to focus on")
    ap.add_argument("--seconds-per-ap", type=int, default=20)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.module1_json) as f:
        m1 = json.load(f)
    targets = sorted(m1["access_points"], key=lambda a: a["client_count"], reverse=True)[:args.top]
    if not targets:
        print("No APs in module1 JSON to target.", file=sys.stderr)
        sys.exit(1)

    print(f"Opening {args.port} @ {args.baud}...", file=sys.stderr)
    ser = open_port(args.port, args.baud)

    results = []
    try:
        for ap_entry in targets:
            ssid, ch, bssid = ap_entry["ssid"], ap_entry["channel"], ap_entry["bssid"]
            print(f"Focusing channel {ch} ({ssid} / {bssid}, "
                  f"{ap_entry['client_count']} known clients) for {args.seconds_per_ap}s...",
                  file=sys.stderr)
            send(ser, f"capture -wireshark -channel {ch}")
            raw = read_raw_for(ser, args.seconds_per_ap)
            send(ser, "capture -stop")
            read_raw_for(ser, 2)  # drain the stop acknowledgement before the next command

            probes = extract_probe_requests(raw)
            seen_macs = {p["client_mac"] for p in probes}
            print(f"  {len(probes)} probe-request frames, {len(seen_macs)} distinct client MACs",
                  file=sys.stderr)

            results.append({
                "target_ap": {"ssid": ssid, "bssid": bssid, "channel": ch,
                               "known_client_count": ap_entry["client_count"]},
                "seconds_listened": args.seconds_per_ap,
                "probe_requests_seen": len(probes),
                "distinct_client_macs": sorted(seen_macs),
                "probes": probes,
            })

        out_json = json.dumps({"targets_probed": len(results), "results": results}, indent=2)
        print(out_json)
        if args.out:
            with open(args.out, "w") as f:
                f.write(out_json)
            print(f"Wrote {args.out}", file=sys.stderr)

    finally:
        ser.close()


if __name__ == "__main__":
    main()
