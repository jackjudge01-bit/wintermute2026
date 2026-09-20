# devicedb — unified device inventory for wintermute

Passive + active device collection on the Kali laptop, all sources merged into
one SQLite database (`inventory.db`) that you can query at leisure. Runs on
cron ("auto-pilot") so history accumulates without you driving it.

Location: `/path/to/devicedb/`
Database: `/path/to/devicedb/inventory.db`

---

## 1. What each sensor contributes

| Sensor | Medium | Identity it gives | How it's collected |
|---|---|---|---|
| **ESP32 Marauder** (`/dev/ttyACM0`) | Wi-Fi + BLE | BSSID, SSID, channel, RSSI, security, frames | Serial CLI: `scanall` → `list -a` → `info -a <idx>` per AP; `recon ble` → `list -b` |
| **BLEA** (`blea`/`ble` CLI) | BLE | MAC, name, RSSI, service UUIDs | `blea scan -j` JSON |
| **arpwatch** (systemd `arpwatch@wlan0`) | LAN/ARP | MAC ↔ IP pairing | journald live + `/var/lib/arpwatch/wlan0.dat` |
| **arp-scan** | LAN | MAC, IP, vendor | `arp-scan --localnet` |
| **avahi-browse** | mDNS/Cast | hostname, service, IP, port | `avahi-browse -a -t -p` |
| **nmap** | LAN | open ports/services | optional `--nmap` (slower) |
| **Zeek** (Docker) | LAN flows | DNS/SSL/conn metadata | `docker run zeek/zeek` → log dir |
| **Kismet** | Wi-Fi monitor | full 802.11 detail | **on-demand only** — drops `wlan0` |

Each observation is stored raw *and* rolled up into a per-device row, so the
same physical thing seen by Marauder and BLEA merges into one device entry
(see §4).

---

## 2. The radio situation (why the ESP32 matters)

The laptop has one Wi-Fi adapter (MediaTek MT7925 → `wlan0`). Putting it into
monitor mode for Kismet **takes you off the network** — no internet, no LAN
scanning.

The ESP32 Marauder has **its own Wi-Fi and BLE radios**, so it provides
Wi-Fi AP/client and BLE coverage with **zero disruption**. That's why it's the
primary wireless sensor and Kismet is on-demand only.

---

## 3. Verified source formats

### Marauder (firmware v1.16.0)

`list -a` (no BSSID here — index, channel, SSID, RSSI):

    [7][CH:9] ORBI65 -87

`info -a <idx>` (the real record — this is where the BSSID comes from):

       ESSID: ORBI65
       BSSID: 1A:59:C0:2D:FA:5A
     Channel: 9
        RSSI: -83
      Frames: 9
    Stations: 0
    Complete EAPOL: FALSE
    Security: WPA2

`list -b`:

    [3][RSSI:-69] LE_WH-1000XM4

Empty result: `0 selected`.

**Note:** `--machine` / `@MARAUDER:{json}` output exists but **only for
`protocolinfo` and spiffs backup commands** — the list commands ignore it, so
plain-text parsing is the supported path.

A hidden-SSID AP shows its MAC in the SSID slot; the code detects that via
`norm_mac()` and marks `hidden: true`.

### arpwatch

Data file is **per-interface**: `/var/lib/arpwatch/wlan0.dat`, format
`<mac>\t<ip>\t<unix_epoch>\t<hostname|empty>\t<iface>`.

**The data file is only written on exit (SIGTERM)** — while running it stays
0 bytes. Live events only appear in journald:

    new station 192.168.1.254 54:b7:bd:c7:71:85 wlan0
    changed station ...
    ethernet mismatch ...

So the collector reads **both**: journal for live events, `.dat` for state.
(`journalctl -o cat` strips the `arpwatch[pid]:` prefix — the regex allows for
both.) It also does not create its own data file; the systemd unit pre-touches
it, and arpwatch silently records nothing if it's missing.

### BLEA

`blea scan -j` → JSON array with `identifier` (MAC), `name`, `rssi`,
`serviceUUIDs`.

### Zeek — Docker only

The Kali `zeek` .deb **cannot be installed here**: it depends on
`libc6 (< 2.38)` but the host has `2.42`. Working route is the official image:

```bash
sudo mkdir -p /var/lib/devicedb/zeek
sudo timeout -k 5 60 docker run --rm --network host \
  --cap-add=NET_RAW --cap-add=NET_ADMIN \
  -v /var/lib/devicedb/zeek:/out -w /out zeek/zeek:latest zeek -i wlan0 -C
```

Then: `python3 devicedb.py ingest-zeek /var/lib/devicedb/zeek`

Logs are tab-separated with `#separator`/`#fields`/`#types` headers
(`-` = unset); the parser reads headers dynamically so column-count changes
between versions are harmless. Produces `conn.log`, `dns.log`, `ssl.log`,
`weird.log`, `packet_filter.log`, `reporter.log` (and `dhcp.log` when DHCP
traffic occurs). Add `LogAscii::use_json=T` for JSON instead.

---

## 4. Database schema

- **`devices`** — one row per identity: `mac`, `ip`, `hostname`, `vendor`,
  `kinds` (comma list), `first_seen`, `last_seen`, `source`, `randomized`.
  Keyed by MAC where known; SSID-only APs key on the SSID as `hostname`.
- **`observations`** — every sighting: `mac`, `ip`, `kind`, `name`, `rssi`,
  `channel`, `source`, `ts`, `run_id`, `extra` (JSON: security, frames,
  service UUIDs, event type…).
- **`services`** — open ports per device (from nmap).
- **`runs`** — one row per collection run: source, started/finished, status,
  count, artifact. Useful for "what did the sweep see at 03:00".
- **`oui`** — MAC vendor cache, populated from `/usr/share/ieee-data/oui.txt`
  at runtime. Note that file carries **both** `08-F0-1E (hex)` and
  `08F01E (base 16)` lines; the parser handles both and stores
  `08F01E → eero inc.`

Two sources seeing the same MAC merge into one `devices` row with `kinds`
growing (`wifi,ble`) — cross-source corroboration is automatic.

Locally-administered MACs (second hex digit is 2/6/A/E, e.g. `A2:`, `42:`)
are virtual BSSIDs from mesh/extender radios and legitimately have **no
vendor** — not a parsing failure.

---

## 5. Commands

```bash
cd /path/to/devicedb

python3 devicedb.py init                    # create/upgrade schema
python3 devicedb.py sweep                   # full sweep (all enabled sources)
python3 devicedb.py sweep --arpwatch --arp --ble        # passive only
python3 devicedb.py sweep --no-marauder                 # skip the ESP32
python3 devicedb.py marauder --seconds 20 --ble-seconds 12
python3 devicedb.py refresh-vendors         # re-resolve vendors from OUI cache

# queries
python3 devicedb.py stats                   # counts, kinds, top vendors, span
python3 devicedb.py devices --kind wifi --limit 20
python3 devicedb.py devices --since 1h
python3 devicedb.py show <mac>              # one device + its recent sightings
python3 devicedb.py changes                 # what's new since a given time
python3 devicedb.py ports                   # recorded open ports/services
python3 devicedb.py runs                    # collection history

# offline ingest
python3 devicedb.py ingest-blea  <file.json>
python3 devicedb.py ingest-kismet <kismet.db>
python3 devicedb.py ingest-zeek  <logdir>
```

---

## 6. Auto-pilot

`/path/to/devicedb/sweep.sh` runs a full sweep and appends to
`logs/sweep-YYYYMM.log` (logs pruned after 60 days). Installed in the user
crontab:

    */15 * * * * /path/to/devicedb/sweep.sh

Manage it:

```bash
crontab -l                      # view
crontab -e                      # edit / comment out
tail -f logs/sweep-$(date +%Y%m).log
```

**Conflict note:** each cycle grabs `/dev/ttyACM0` for ~45 s. If you're driving
Marauder manually (serial console or web UI) at that moment, it will clash —
comment out the cron line while you're using it, or run the sweep with
`--no-marauder`.

Kismet is deliberately **not** in the cron job: it needs `wlan0` in monitor
mode, which disconnects you. Run it by hand when you want a deep 802.11 look.

---

## 7. Known limits

- **Marauder stations (`list -s`) come back empty** — client/station capture
  needs a longer or differently-targeted scan; APs and BLE work reliably.
- Marauder's `info -a` costs ~0.6 s per AP (capped at 60 APs per run), so a
  sweep takes ~45 s wall clock.
- arpwatch email alerts are broken on this box (`/usr/lib/sendmail` missing) —
  harmless, but journald is the only alert channel.
- Zeek logs written from Docker are root-owned; `ingest-zeek` reads them with
  sudo where needed.
- No GPS module on the ESP32 (confirmed) — wardrive logs SSIDs without
  coordinates.
