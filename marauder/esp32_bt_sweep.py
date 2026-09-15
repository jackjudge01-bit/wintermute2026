#!/usr/bin/env python3
import serial, time, re, sys

PORT = "/dev/ttyACM0"
BAUD = 115200
SWEEP_SECONDS = 25

s = serial.Serial(PORT, BAUD, timeout=0.5)
time.sleep(0.3)
s.reset_input_buffer()

def send(cmd):
    s.write(cmd.encode() + b"\r")
    time.sleep(0.3)

print("stopping any running scan...", file=sys.stderr)
for _ in range(4):
    send("stopscan")
    time.sleep(0.8)
    s.reset_input_buffer()

# confirm the stream actually goes quiet before starting BT sniff
quiet_check = s.read(s.in_waiting or 1)
if quiet_check:
    print(f"WARNING: still {len(quiet_check)} bytes streaming after 4x stopscan, "
          f"proceeding anyway: {quiet_check[:100]!r}", file=sys.stderr)
s.reset_input_buffer()

print(f"starting BT sniff for {SWEEP_SECONDS}s...", file=sys.stderr)
send("sniffbt")

data = b""
deadline = time.time() + SWEEP_SECONDS
while time.time() < deadline:
    try:
        chunk = s.read(s.in_waiting or 1)
        if chunk:
            data += chunk
        else:
            time.sleep(0.05)
    except serial.SerialException:
        time.sleep(0.1)
        continue

send("stopscan")
time.sleep(0.5)
try:
    data += s.read(s.in_waiting or 1)
except serial.SerialException:
    pass
s.close()

text = data.decode("utf-8", errors="replace")
open("/home/jack/bluetooth_recon/esp32_bt_sweep_raw.txt", "w").write(text)

print(f"\n--- raw bytes: {len(data)} ---", file=sys.stderr)

devices = re.findall(r"(-?\d+)\s*Device:\s*(.*?)(?=-\d+\s*Device:|#info|>|[\r\n]|$)", text)
seen = {}
for rssi, name in devices:
    name = name.strip()
    r = int(rssi)
    if name not in seen or r > seen[name]:
        seen[name] = r

print(f"\n{len(seen)} unique BLE devices seen:\n")
for name, rssi in sorted(seen.items(), key=lambda kv: -kv[1]):
    print(f"  {rssi:>5} dBm  {name}")
