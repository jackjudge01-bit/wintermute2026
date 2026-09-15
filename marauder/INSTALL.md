# INSTALL — marauder probes

How to get the ESP32 Marauder serial probes running. What each script
does and the protocol findings are in [README.md](README.md).

## Prerequisites

- Linux (Kali/Debian), Python 3.6+
- An ESP32 Marauder (e.g. board with native USB) flashed with Marauder
  firmware, connected over USB
- Serial device appears as `/dev/ttyACM0` (the scripts hardcode this
  path and 115200 baud)

## Install

```bash
sudo apt update
sudo apt install python3-serial
```

(or in a venv: `pip install pyserial`)

Verify pyserial is importable:

```bash
python3 -c "import serial; print(serial.__version__)"
```

## Device setup (serial permissions)

Access to `/dev/ttyACM0` requires membership in `dialout`:

```bash
sudo usermod -aG dialout "$USER"
# log out and back in, then:
groups | grep -q dialout && echo ok
```

Plug the board in and confirm it enumerates:

```bash
ls -l /dev/ttyACM0
dmesg | tail -5        # should show a CDC ACM device
```

If your board shows up as `/dev/ttyUSB0` instead (some USB-serial
chips), either edit the `port = "/dev/ttyACM0"` line in each script or
create a udev symlink.

## Verification

```bash
python3 marauder_probe.py
```

Expected: `=== after newline ===` followed by the Marauder CLI banner or
prompt output, then `=== after 'help' ===` followed by the command list.
`OPEN FAILED: [Errno 13] Permission denied` means you skipped the dialout
step; `[Errno 2]` means the device is not at `/dev/ttyACM0`.

## First run

```bash
cd marauder/
python3 marauder_probe.py       # connectivity check (no side effects)
python3 marauder_probe2.py      # protocolinfo / gps / settings queries
python3 marauder_probe3.py      # list formats + a short live scanall
python3 marauder_info.py         # full scan (~14 s) then per-AP info dump
python3 esp32_bt_sweep.py        # BLE sweep (~25s), unique devices by RSSI
```

`marauder_probe3.py` and `marauder_info.py` run a real `scanall` (~15 s)
so they actually observe nearby Wi-Fi/BLE traffic. Leave ~15 s for a
scan to accumulate results before `list -a` returns anything useful.

## Troubleshooting

- **Permission denied** — dialout group not applied yet (re-login), or
  another process (serial console, devicedb cron sweep) holds the port.
- **Empty output** — always `reset_input_buffer()` before sending (the
  scripts do this); if you get nothing, press the board's reset button
  and rerun.
- **Baud garbage** — firmware expects 115200; check with the Marauder
  web/serial console first.
