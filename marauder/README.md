# marauder — ESP32 Marauder serial control probes

Python scripts for driving an ESP32 Marauder (Wi-Fi/BLE attack board) over
its USB serial CLI. These were written to reverse-engineer the command
protocol and output formats needed to script the board automatically —
knowledge that later became the Marauder collector in `devicedb/`.

## Hardware / connection

- ESP32 Marauder on `/dev/ttyACM0`, 115200 baud
- Plain text CLI over serial: send `command\r\n`, read the response
- pyserial (`pip install pyserial`)

## What each script does

| Script | Purpose |
|---|---|
| `marauder_probe.py` | Minimal hello: opens the port, sends a bare newline, dumps whatever the board emits. Verifies connectivity + that the CLI prompt responds. |
| `marauder_probe2.py` | Protocol discovery: `protocolinfo`, `protocolinfo --machine 1` (machine-readable mode), `gps`, `settings -r`. |
| `marauder_probe3.py` | Output format discovery: `list -a/-b/-s --machine 1` (AP/BLE/station lists in machine mode), plus a short live `scanall` to compare plain vs machine output with real data. |
| `marauder_info.py` | Per-AP detail dump: runs `scanall` for ~14 s, stops, then `info -a 0/1/2` to show what per-device records look like (BSSID, SSID, channel, RSSI, security). |

## Key findings baked into these probes

- `--machine 1` flag switches list output to a parseable format (semicolon
  delimited) — the reliable way to ingest data programmatically
- A `scanall` needs ~15 s before `stopscan` + `list -a` returns useful results
- Always `reset_input_buffer()` before sending: the board emits asynchronous
  status chatter (LED/serial spam) that will otherwise pollute your reads
- Per-AP detail is only available via `info -a <index>` after a scan; the
  index refers to the last `list -a` ordering

## The driving pattern that works

```python
import serial, time

s = serial.Serial("/dev/ttyACM0", 115200, timeout=1)
time.sleep(0.4)

def cmd(c, wait):
    s.reset_input_buffer()
    s.write(c.encode() + b"\r\n")
    time.sleep(wait)
    return s.read(65536).decode(errors="replace")

cmd("stopscan", 1.0)          # clear any running scan state
cmd("scanall", 1.0)
time.sleep(14)                # let it see the neighbourhood
cmd("stopscan", 1.0)
aps = cmd("list -a --machine 1", 2.5)   # parseable AP list
```

## Legal

These scripts only exercise the board's own scanning/listing commands —
passive observation of nearby wireless traffic, same as any scanner app.
Use of attack features (beacon spam, deauth, etc.) is on you and your local
laws and authorization.
