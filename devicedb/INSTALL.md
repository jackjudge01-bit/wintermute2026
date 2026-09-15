# INSTALL — devicedb

How to set up the unified device inventory on a fresh Kali/Debian box.
What each sensor contributes, the schema, and known limits are in
[README.md](README.md) — read that first; this file only gets you running.

## Prerequisites

- Linux (Kali/Debian), Python 3.8+ (stdlib only: sqlite3, argparse, etc.)
- `sudo` access — several collectors are invoked with `sudo -n`
  (non-interactive sudo; see below)

## Core install (always required)

```bash
sudo apt update
sudo apt install ieee-data nmap arp-scan arpwatch
```

- `ieee-data` provides `/usr/share/ieee-data/oui.txt` (MAC vendor lookup).
  `nmap` also provides an acceptable fallback (`nmap-mac-prefixes`).
- Nothing else is needed for the core: `devicedb.py init` and the offline
  `ingest-*` commands work with Python alone.

Initialize:

```bash
cd devicedb/
python3 devicedb.py init
```

## Optional per-source dependencies

Only install what you plan to use:

| Source | Needs | Note |
|---|---|---|
| `--arp` (arp-scan) | `arp-scan` | run via `sudo -n` |
| `--arpwatch` | `arpwatch` | journal + `/var/lib/arpwatch/wlan0.dat`; needs the `arpwatch@wlan0` systemd unit and `sudo -n journalctl` |
| `--portscan` (nmap) | `nmap` | run via `sudo -n` |
| `--marauder` (default on) | `pyserial` + ESP32 Marauder on `/dev/ttyACM0` | `sudo apt install python3-serial`; skip with `--no-marauder` |
| `--ble` (default on) | `blea` CLI on PATH | not a distro package — install per your own setup; skip with `--no-ble` |
| `--kismet` | kismet | on-demand only (takes `wlan0` into monitor mode); install kismet per your distro |
| `--zeek` | Docker + `zeek/zeek` image | the Kali zeek .deb may conflict with newer libc; use the Docker route documented in README.md |

Docker route for Zeek:

```bash
sudo apt install docker.io
sudo docker pull zeek/zeek:latest
```

## sudo setup

Collectors call `sudo -n arp-scan`, `sudo -n nmap`, and
`sudo -n journalctl -t arpwatch`. Passwordless (NOPASSWD) sudo for those
specific commands is required for cron use; with a normal interactive
sudo the sweeps will still prompt, which breaks `sweep.sh`. Configure with
`sudo visudo` if you want the cron auto-pilot, e.g.:

```
youruser ALL=(root) NOPASSWD: /usr/sbin/arp-scan, /usr/bin/nmap, /usr/bin/journalctl
```

(Adjust paths to your distro: `command -v arp-scan nmap journalctl`.)

## Verification

```bash
python3 devicedb.py init
python3 devicedb.py stats
```

`stats` should print (empty) counts without errors — that proves the
schema was created and the DB is reachable. Check the OUI cache resolves:

```bash
python3 -c "import sys; sys.path.insert(0,'.'); import devicedb; print(devicedb.vendor_for('54:B7:BD:C7:71:85'))"
```

Any non-None vendor string means `ieee-data` is installed correctly.

## First run

```bash
cd devicedb/
python3 devicedb.py sweep --arp --no-marauder --no-ble   # LAN only, no extra hardware
python3 devicedb.py devices
python3 devicedb.py runs
```

With an ESP32 Marauder attached (see ../marauder/INSTALL.md):

```bash
python3 devicedb.py sweep                     # everything default: marauder + ble + arp
```

## Auto-pilot (optional)

```bash
crontab -e
# add:
*/15 * * * * /path/to/devicedb/sweep.sh
```

Then check it is firing:

```bash
tail -f logs/sweep-$(date +%Y%m).log
```

Notes: each sweep holds `/dev/ttyACM0` for ~45 s (conflicts with manual
Marauder use — see README.md §6), and Kismet is deliberately not in the
cron job.
