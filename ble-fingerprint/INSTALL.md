# INSTALL — ble-fingerprint

## Prerequisites

- Linux with BlueZ (standard on Kali/Debian), a working Bluetooth adapter
- Python 3.9+

```bash
python3 -m venv .venv
.venv/bin/pip install bleak
```

## Permissions

`bleak`'s BlueZ backend talks to `bluetoothd` over D-Bus — no root needed
for scanning or connecting (unlike raw `hcitool`/`btmon`, which do need
root). If scans return nothing, check the adapter itself first:

```bash
systemctl is-active bluetooth   # should be 'active'
bluetoothctl show | grep Powered  # should be 'Powered: yes'
```

## Usage

```bash
# Find a device by (partial) name, get MAC + services + vendor
.venv/bin/python ble_fingerprint.py scan --name "JEREMY" --timeout 10

# See everything nearby
.venv/bin/python ble_fingerprint.py scan --timeout 15

# Full GATT enumeration of a specific device
.venv/bin/python ble_fingerprint.py inspect AA:BB:CC:DD:EE:FF
```

## Verification

```bash
.venv/bin/python ble_fingerprint.py scan --timeout 5
```
Expect at least one nearby device to print with an address, RSSI, and
either a name or `name=None`. If literally nothing prints, the adapter
isn't powered/discovering — see Permissions above, not a script issue.

For `inspect`, expect either a services/characteristics dump, or
`'<addr>' not seen in a 10s scan` (address rotated/out of range) — a
`BleakError` other than that from a device you *did* just see in a scan
means that specific device is refusing connections (normal for some
advertise-only peripherals, see README.md).
