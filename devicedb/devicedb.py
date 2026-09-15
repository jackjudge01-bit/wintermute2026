#!/usr/bin/env python3
"""
devicedb — unified device inventory for wintermute.

Collects passive + active observations of every device this laptop can see
(Bluetooth/BLE, Wi-Fi, LAN/IP, open ports, mDNS/Cast) into one SQLite database
with timestamps, so history can be queried later.

Sources:
  blea      -> BLE scan JSON      (Bluetooth Low Energy)
  kismet    -> kismet .kismet DB  (Wi-Fi / BT / Zigbee / RF)
  zeek      -> zeek log dir       (passive LAN: conn/dhcp/dns)
  arpwatch  -> arp.dat            (passive MAC<->IP)
  arp-scan  -> text output        (active ARP sweep)
  nmap      -> XML output         (live hosts + open ports + services)

Usage:
  devicedb.py init
  devicedb.py sweep [--ble] [--arp] [--portscan]
  devicedb.py ingest-blea FILE.json
  devicedb.py ingest-kismet FILE.kismet
  devicedb.py ingest-zeek DIR
  devicedb.py ingest-arpwatch /var/lib/arpwatch/arp.dat
  devicedb.py ingest-nmap FILE.xml
  devicedb.py devices [--since 7d] [--kind bt] [--search X]
  devicedb.py ports [--ip 1.2.3.4]
  devicedb.py show MAC
  devicedb.py changes [--since 24h]
  devicedb.py stats
  devicedb.py runs
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "inventory.db")
RAW = os.path.join(BASE, "raw")
SCHEMA = os.path.join(BASE, "schema.sql")

OUI_FILES = [
    "/usr/share/ieee-data/oui.txt",
    "/usr/share/nmap/nmap-mac-prefixes",
    "/var/lib/ieee-data/oui.txt",
]

# ---------------------------------------------------------------- helpers

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def norm_mac(m):
    """Normalise a MAC to UPPER:CO:LO:N form. Returns None if not MAC-like."""
    if not m:
        return None
    h = re.sub(r"[^0-9a-fA-F]", "", str(m))
    if len(h) != 12:
        return None
    h = h.upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def is_local_mac(mac):
    """True if the MAC is locally administered (e.g. randomised / privacy)."""
    try:
        first = int(mac.split(":")[0], 16)
        return bool(first & 0x02)
    except Exception:
        return False


_oui_cache = None

def load_oui():
    global _oui_cache
    if _oui_cache is not None:
        return _oui_cache
    _oui_cache = {}
    for path in OUI_FILES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # ieee-data: "AABBCC     (base 16)\t\tVendor Name"  /  "(hex)"
                    m = re.match(
                        r"^([0-9A-Fa-f]{6})\s+(?:\((?:hex|base\s*16)\)\s*)?(.+)$", line)
                    if m:
                        _oui_cache[m.group(1).upper()] = m.group(2).strip()
                        continue
                    # nmap-mac-prefixes: "AABBCC Vendor"
                    m = re.match(r"^([0-9A-Fa-f]{6})\s+(.+)$", line)
                    if m:
                        _oui_cache[m.group(1).upper()] = m.group(2).strip()
        except Exception:
            pass
        if _oui_cache:
            break
    return _oui_cache


def vendor_for(mac):
    if not mac:
        return None
    oui = load_oui()
    return oui.get(mac.replace(":", "").upper()[:6])


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(RAW, exist_ok=True)
    if not os.path.exists(SCHEMA):
        raise SystemExit(f"missing schema: {SCHEMA}")
    conn = get_db()
    with open(SCHEMA) as fh:
        conn.executescript(fh.read())
    conn.commit()
    print(f"initialised {DB}")


# ---------------------------------------------------------------- writes

def upsert_device(conn, mac=None, ip=None, hostname=None, kind=None,
                  name=None, ts=None, source=None):
    """Create or update a device row. Matches on MAC if present, else IP."""
    ts = ts or now_iso()
    mac = norm_mac(mac) if mac else None

    row = None
    if mac:
        row = conn.execute("SELECT * FROM devices WHERE mac=?", (mac,)).fetchone()
    if row is None and ip:
        row = conn.execute(
            "SELECT * FROM devices WHERE ip=? AND (mac IS NULL OR mac='')", (ip,)
        ).fetchone()

    if row is None:
        kinds = kind or "unknown"
        srcs = source or ""
        conn.execute(
            """INSERT INTO devices (mac, ip, hostname, vendor, kinds, first_seen,
                                    last_seen, source, randomized)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (mac, ip, hostname or name, vendor_for(mac), kinds, ts, ts, srcs,
             1 if (mac and is_local_mac(mac)) else 0),
        )
        return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]

    did = row["device_id"]
    kinds = set(filter(None, (row["kinds"] or "").split(",")))
    if kind:
        kinds.add(kind)
    srcs = set(filter(None, (row["source"] or "").split(",")))
    if source:
        srcs.add(source)
    conn.execute(
        """UPDATE devices
             SET ip       = COALESCE(?, ip),
                 hostname = COALESCE(?, hostname),
                 vendor   = COALESCE(?, vendor),
                 kinds    = ?,
                 source   = ?,
                 last_seen= ?
           WHERE device_id = ?""",
        (ip, hostname, vendor_for(mac), ",".join(sorted(kinds)),
         ",".join(sorted(srcs)), ts, did),
    )
    return did


def add_observation(conn, mac=None, ip=None, kind=None, name=None, rssi=None,
                    channel=None, extra=None, source=None, ts=None, run_id=None):
    conn.execute(
        """INSERT INTO observations (ts, source, mac, ip, kind, name, rssi,
                                     channel, extra, run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ts or now_iso(), source, norm_mac(mac) if mac else None, ip, kind,
         name, rssi, channel, json.dumps(extra) if extra else None, run_id),
    )


def upsert_service(conn, ip, port, proto, service=None, banner=None, ts=None):
    ts = ts or now_iso()
    conn.execute(
        """INSERT INTO services (ip, port, proto, service, banner, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(ip, port, proto) DO UPDATE SET
             service   = COALESCE(excluded.service, services.service),
             banner    = COALESCE(excluded.banner, services.banner),
             last_seen = excluded.last_seen""",
        (ip, port, proto, service, banner, ts, ts),
    )


def start_run(conn, tool, args=None):
    conn.execute("INSERT INTO runs (started_at, tool, args, status) VALUES (?,?,?,'running')",
                 (now_iso(), tool, args))
    return conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]


def finish_run(conn, run_id, status="ok", records=0, artifact=None, error=None):
    conn.execute(
        """UPDATE runs SET ended_at=?, status=?, records=?, artifact=?, error=?
           WHERE run_id=?""",
        (now_iso(), status, records, artifact, error, run_id),
    )


# ---------------------------------------------------------------- ingesters

def ingest_blea(conn, path_or_obj, run_id=None):
    data = path_or_obj
    if isinstance(data, str):
        with open(data) as fh:
            data = json.load(fh)
    n = 0
    ts = now_iso()
    for dev in data.get("devices", []):
        mac = dev.get("identifier")
        name = dev.get("name") or dev.get("local_name")
        rssi = dev.get("rssi")
        upsert_device(conn, mac=mac, kind="ble", name=name, ts=ts, source="blea")
        add_observation(conn, mac=mac, kind="ble", name=name, rssi=rssi,
                        source="blea", ts=ts, run_id=run_id,
                        extra={"service_uuids": dev.get("service_uuids"),
                               "manufacturer_data": dev.get("manufacturer_data"),
                               "tx_power": dev.get("tx_power")})
        n += 1
    return n


def ingest_kismet(conn, path, run_id=None):
    """Read a Kismet .kismet sqlite DB. Devices are stored as JSON blobs."""
    if not os.path.exists(path):
        return 0
    k = sqlite3.connect(path)
    k.row_factory = sqlite3.Row
    try:
        rows = k.execute("SELECT device FROM devices").fetchall()
    except sqlite3.Error:
        return 0
    n = 0
    for r in rows:
        try:
            d = json.loads(r["device"])
        except Exception:
            continue
        mac = d.get("kismet.device.base.macaddr")
        name = d.get("kismet.device.base.name") or d.get("kismet.device.base.commonname")
        dtype = (d.get("kismet.device.base.type") or "").lower()
        first = d.get("kismet.device.base.first_time")
        last = d.get("kismet.device.base.last_time")
        sig = d.get("kismet.common.signal.last_signal_dbm")
        kind = "wifi"
        if "bluetooth" in dtype:
            kind = "ble" if "le" in dtype else "bt"
        elif "zigbee" in dtype:
            kind = "zigbee"
        elif "rf" in dtype:
            kind = "rf"
        ts = (datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
              if last else now_iso())
        upsert_device(conn, mac=mac, kind=kind, name=name, ts=ts, source="kismet")
        add_observation(conn, mac=mac, kind=kind, name=name, rssi=sig,
                        source="kismet", ts=ts, run_id=run_id,
                        extra={"kismet_type": dtype,
                               "first_time": first, "last_time": last})
        n += 1
    k.close()
    return n


def ingest_zeek(conn, logdir, run_id=None):
    """Parse zeek logs (TSV with #fields header). We care about dhcp/dns/conn."""
    n = 0
    mapping = {"dhcp.log": "dhcp", "dns.log": "dns", "conn.log": "conn"}
    for fname, kind in mapping.items():
        path = os.path.join(logdir, fname)
        if not os.path.exists(path):
            continue
        fields = None
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                if line.startswith("#fields"):
                    fields = line.strip().split("\t")[1:]
                    continue
                if line.startswith("#") or not fields:
                    continue
                vals = line.rstrip("\n").split("\t")
                if len(vals) != len(fields):
                    continue
                rec = dict(zip(fields, vals))
                ts = rec.get("ts")
                iso = (datetime.fromtimestamp(float(ts), timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%S") if ts else now_iso())
                if kind == "dhcp" and rec.get("mac"):
                    upsert_device(conn, mac=rec.get("mac"),
                                  ip=rec.get("assigned_addr") or rec.get("client_addr"),
                                  hostname=rec.get("host_name") or None,
                                  kind="eth", ts=iso, source="zeek")
                    add_observation(conn, mac=rec.get("mac"), kind="eth",
                                    name=rec.get("host_name"), source="zeek",
                                    ts=iso, run_id=run_id,
                                    extra={"dhcp": rec.get("msg_types")})
                    n += 1
                elif kind == "dns" and rec.get("query"):
                    # record mDNS/Cast-ish names as observations keyed by query
                    add_observation(conn, name=rec.get("query"), kind="dns",
                                    source="zeek", ts=iso, run_id=run_id,
                                    extra={"qtype": rec.get("qtype_name"),
                                           "answers": rec.get("answers")})
                    n += 1
                elif kind == "conn":
                    orig = rec.get("id.orig_h")
                    resp = rec.get("id.resp_h")
                    if orig:
                        upsert_device(conn, ip=orig, kind="eth", ts=iso, source="zeek")
                    if resp and not resp.startswith("224.") and not resp.startswith("239."):
                        upsert_device(conn, ip=resp, kind="eth", ts=iso, source="zeek")
                    n += 1
    return n


def refresh_vendors(conn):
    """Recompute vendor for every device from the current OUI cache.

    Fixes rows ingested before an OUI-parsing fix (historical data otherwise
    keeps whatever string the old parser produced).
    """
    n = 0
    for (mac,) in conn.execute("SELECT mac FROM devices WHERE mac IS NOT NULL"):
        v = vendor_for(mac)
        if v:
            cur = conn.execute("UPDATE devices SET vendor=? WHERE mac=?", (v, mac))
            n += cur.rowcount
    conn.commit()
    return n


def ingest_arpwatch(conn, path, run_id=None):
    """arpwatch data file. VERIFIED format (arpwatch 2.1a15, systemd unit):
         <mac>\t<ip>\t<unix_epoch>\t<hostname|empty>\t<iface>
       e.g.  c8:99:b2:0d:6d:4a\t192.168.1.41\t1789391557\t\twlan0
    NOTE: arpwatch only writes this file on exit (SIGTERM) — while running it
    stays empty. Use ingest_arpwatch_journal() for live events.
    """
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            mac, ip = parts[0].strip(), parts[1].strip()
            if not norm_mac(mac):
                continue
            ts = None
            if len(parts) > 2 and parts[2].strip().isdigit():
                ts = datetime.fromtimestamp(int(parts[2]), timezone.utc)\
                    .strftime("%Y-%m-%dT%H:%M:%S")
            host = parts[3].strip() if len(parts) > 3 else None
            host = host or None
            upsert_device(conn, mac=mac, ip=ip, hostname=host, kind="eth",
                          ts=ts or now_iso(), source="arpwatch")
            add_observation(conn, mac=mac, ip=ip, kind="eth", name=host,
                            source="arpwatch", ts=ts or now_iso(), run_id=run_id)
            n += 1
    return n


RE_ARP_JOURNAL = re.compile(
    r"(?:arpwatch\[\d+\]:\s*)?(new station|changed station|ethernet mismatch)\s+"
    r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})(?:\s+(\S+))?")


def ingest_arpwatch_journal(conn, since="2h", run_id=None):
    """Live arpwatch events from journald (the only real-time channel).

    Lines look like:
      arpwatch[699457]: new station 192.168.1.41 c8:99:b2:0d:6d:4a wlan0
    """
    rc, out, err = run_cmd(["sudo", "-n", "journalctl", "-t", "arpwatch",
                            "--since", f"-{since}", "-o", "cat", "--no-pager"],
                           timeout=60)
    if rc != 0 or not out:
        return 0
    n = 0
    ts = now_iso()
    for line in out.splitlines():
        m = RE_ARP_JOURNAL.search(line)
        if not m:
            continue
        event, ip, mac, iface = m.group(1), m.group(2), m.group(3), m.group(4)
        upsert_device(conn, mac=mac, ip=ip, kind="eth", ts=ts, source="arpwatch")
        add_observation(conn, mac=mac, ip=ip, kind="eth", source="arpwatch",
                        ts=ts, run_id=run_id,
                        extra={"event": event, "iface": iface})
        n += 1
    return n


def ingest_nmap(conn, path, run_id=None):
    """Parse nmap XML (-oX): hosts, addresses, hostnames, open ports/services."""
    if not os.path.exists(path):
        return 0
    tree = ET.parse(path)
    root = tree.getroot()
    n = 0
    ts = now_iso()
    for host in root.findall("host"):
        status = host.find("status")
        if status is not None and status.get("state") != "up":
            continue
        ip, mac = None, None
        for addr in host.findall("address"):
            if addr.get("addrtype") in ("ipv4", "ipv6"):
                ip = ip or addr.get("addr")
            elif addr.get("addrtype") == "mac":
                mac = addr.get("addr")
        hostname = None
        hn = host.find("hostnames/hostname")
        if hn is not None:
            hostname = hn.get("name")
        upsert_device(conn, mac=mac, ip=ip, hostname=hostname, kind="eth",
                      ts=ts, source="nmap")
        add_observation(conn, mac=mac, ip=ip, kind="eth", name=hostname,
                        source="nmap", ts=ts, run_id=run_id,
                        extra={"state": "up"})
        n += 1
        for port in host.findall("ports/port"):
            state = port.find("state")
            if state is None or state.get("state") != "open":
                continue
            svc = port.find("service")
            sname = svc.get("name") if svc is not None else None
            ver = " ".join(filter(None, [
                svc.get("product") if svc is not None else None,
                svc.get("version") if svc is not None else None,
            ])) or None
            if ip:
                upsert_service(conn, ip, int(port.get("portid")),
                               port.get("protocol"), sname, ver, ts)
    return n


# ---------------------------------------------------------------- marauder
#
# ESP32 Marauder over USB serial (/dev/ttyACM0). Its own radios, so it scans
# Wi-Fi + BLE without disturbing wlan0 or hci0.
#
# Confirmed output formats (firmware v1.16.0):
#   APs:   [7][CH:9] ORBI65 -87          -> [idx][CH:chan] <ssid|mac> -rssi
#   BLE:   [3][RSSI:-69] LE_WH-1000XM4   -> [idx][RSSI:-r] <mac|name>
#   empty: "0 selected"
# NOTE: --machine JSON exists but only for protocolinfo/spiffs commands,
#       NOT for list -a/-b/-s. Parsing text is the supported path.

MARAUDER_PORT = "/dev/ttyACM0"
MARAUDER_BAUD = 115200

RE_AP = re.compile(r"^\[(\d+)\]\[CH:(\d+)\]\s+(.+?)\s+(-?\d+)\s*$")
RE_BLE = re.compile(r"^\[(\d+)\]\[RSSI:(-?\d+)\]\s+(.+?)\s*$")
RE_INFO = {
    "essid": re.compile(r"^\s*ESSID:\s*(.*?)\s*$", re.M),
    "bssid": re.compile(r"^\s*BSSID:\s*(.*?)\s*$", re.M),
    "channel": re.compile(r"^\s*Channel:\s*(\d+)", re.M),
    "rssi": re.compile(r"^\s*RSSI:\s*(-?\d+)", re.M),
    "frames": re.compile(r"^\s*Frames:\s*(\d+)", re.M),
    "stations": re.compile(r"^\s*Stations:\s*(\d+)", re.M),
    "security": re.compile(r"^\s*Security:\s*(.*?)\s*$", re.M),
    "brand": re.compile(r"^\s*Brand:\s*(.*?)\s*$", re.M),
}


def parse_ap_info(block):
    """Parse the output of `info -a <idx>` into a dict. None if no BSSID."""
    rec = {}
    for key, rx in RE_INFO.items():
        m = rx.search(block)
        rec[key] = m.group(1) if m else None
    if not rec.get("bssid"):
        return None
    for k in ("channel", "rssi", "frames", "stations"):
        try:
            rec[k] = int(rec[k]) if rec[k] is not None else None
        except (TypeError, ValueError):
            rec[k] = None
    return rec


def _marauder_cmd(s, cmd, wait):
    s.reset_input_buffer()
    s.write(cmd.encode() + b"\r\n")
    time.sleep(wait)
    return s.read(65536).decode(errors="replace")


def collect_marauder(conn, seconds=20, ble_seconds=12, run_id=None,
                     info_wait=0.6, max_info=60, port=MARAUDER_PORT):
    """Drive Marauder through a scan cycle and ingest APs/stations/BLE."""
    try:
        import serial
    except ImportError:
        return {"error": "pyserial not installed"}
    if not os.path.exists(port):
        return {"error": f"{port} not present"}

    out_lines = []
    try:
        s = serial.Serial(port, MARAUDER_BAUD, timeout=1)
    except Exception as e:
        return {"error": f"open failed: {e}"}

    try:
        time.sleep(0.4)
        s.reset_input_buffer()
        _marauder_cmd(s, "stopscan", 1.0)

        # Wi-Fi: APs + stations
        _marauder_cmd(s, "scanall", 1.0)
        time.sleep(seconds)
        _marauder_cmd(s, "stopscan", 1.0)
        aps_txt = _marauder_cmd(s, "list -a", 2.5)
        sta_txt = _marauder_cmd(s, "list -s", 2.0)

        # list -a has no BSSID; `info -a <idx>` does. Pull full records so APs
        # can be uniquely keyed (otherwise SSID-only rows collide).
        ap_info = {}
        for line in aps_txt.splitlines():
            mm = RE_AP.match(line.strip())
            if not mm or int(mm.group(1)) >= max_info:
                continue
            blk = _marauder_cmd(s, f"info -a {mm.group(1)}", info_wait)
            rec = parse_ap_info(blk)
            if rec:
                ap_info[mm.group(1)] = rec

        # BLE
        _marauder_cmd(s, "recon ble", 1.0)
        time.sleep(ble_seconds)
        _marauder_cmd(s, "recon stop", 1.0)
        ble_txt = _marauder_cmd(s, "list -b", 2.5)
    except Exception as e:
        s.close()
        return {"error": f"serial error: {e}"}
    finally:
        try:
            s.close()
        except Exception:
            pass

    ts = now_iso()
    n_ap = n_sta = n_ble = 0
    raw = {"aps": [], "stations": [], "ble": []}

    for line in aps_txt.splitlines():
        m = RE_AP.match(line.strip())
        if not m:
            continue
        idx, ch, label, rssi = m.group(1), m.group(2), m.group(3).strip(), int(m.group(4))
        rec = ap_info.get(idx) or {}
        # BSSID is the real identity key; fall back to a MAC-shaped SSID slot
        mac = norm_mac(rec.get("bssid") or label)
        ssid = rec.get("essid") or label
        hidden = norm_mac(ssid) is not None
        if hidden:
            ssid = None
        ch = rec.get("channel") or ch
        rssi = rec.get("rssi") if rec.get("rssi") is not None else rssi
        raw["aps"].append({"idx": idx, "bssid": rec.get("bssid"), "ch": ch,
                           "ssid": ssid, "rssi": rssi})
        upsert_device(conn, mac=mac, kind="wifi", name=ssid, ts=ts, source="marauder")
        add_observation(conn, mac=mac, kind="wifi", name=ssid,
                        rssi=rssi, channel=ch, source="marauder", ts=ts,
                        run_id=run_id,
                        extra={"ssid": ssid, "hidden": hidden,
                               "bssid": rec.get("bssid"),
                               "security": rec.get("security"),
                               "frames": rec.get("frames")})
        n_ap += 1

    for line in sta_txt.splitlines():
        m = RE_AP.match(line.strip())
        if not m:
            continue
        label, rssi, ch = m.group(3).strip(), int(m.group(4)), m.group(2)
        raw["stations"].append({"ch": ch, "label": label, "rssi": rssi})
        mac = norm_mac(label)
        upsert_device(conn, mac=mac, kind="wifi", ts=ts, source="marauder")
        add_observation(conn, mac=mac, kind="wifi", rssi=rssi, channel=ch,
                        source="marauder", ts=ts, run_id=run_id,
                        extra={"role": "station"})
        n_sta += 1

    for line in ble_txt.splitlines():
        m = RE_BLE.match(line.strip())
        if not m:
            continue
        rssi, label = int(m.group(2)), m.group(3).strip()
        raw["ble"].append({"label": label, "rssi": rssi})
        mac = norm_mac(label)
        upsert_device(conn, mac=mac, kind="ble",
                      name=None if mac else label, ts=ts, source="marauder")
        add_observation(conn, mac=mac, kind="ble",
                        name=label if not mac else None, rssi=rssi,
                        source="marauder", ts=ts, run_id=run_id)
        n_ble += 1

    art = os.path.join(RAW, f"marauder-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    try:
        with open(art, "w") as fh:
            json.dump(raw, fh, indent=1)
    except Exception:
        art = None

    return {"aps": n_ap, "stations": n_sta, "ble": n_ble, "artifact": art}


# ---------------------------------------------------------------- sweep

def run_cmd(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -2, "", "not found"


def sweep(args):
    conn = get_db()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = {}

    # 1. BLE (Bluetooth Low Energy) via BLEA
    if args.ble:
        out = os.path.join(RAW, f"blea-{stamp}.json")
        rc, so, se = run_cmd(["blea", "scan", "--timeout", str(args.ble_seconds),
                              "--json"], timeout=args.ble_seconds + 120)
        rid = start_run(conn, "blea", f"scan --timeout {args.ble_seconds}")
        if rc == 0 and so.strip():
            with open(out, "w") as fh:
                fh.write(so)
            n = ingest_blea(conn, out, rid)
            finish_run(conn, rid, "ok", n, out)
            results["blea"] = n
        else:
            finish_run(conn, rid, "error", 0, None, (se or "no output")[:400])
            results["blea"] = f"error: {(se or 'no output')[:80]}"

    # 2. Active ARP sweep of the local subnet
    if args.arp:
        out = os.path.join(RAW, f"arpscan-{stamp}.txt")
        rc, so, se = run_cmd(["sudo", "-n", "arp-scan", "--localnet"], timeout=180)
        rid = start_run(conn, "arp-scan", "--localnet")
        if rc == 0 and so.strip():
            with open(out, "w") as fh:
                fh.write(so)
            n = 0
            ts = now_iso()
            for line in so.splitlines():
                m = re.match(r"^(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})\s*(.*)$", line)
                if m:
                    ip, mac, vendor_txt = m.group(1), m.group(2), m.group(3).strip()
                    upsert_device(conn, mac=mac, ip=ip, kind="eth", ts=ts,
                                  source="arp-scan")
                    add_observation(conn, mac=mac, ip=ip, kind="eth",
                                    source="arp-scan", ts=ts, run_id=rid,
                                    extra={"vendor": vendor_txt})
                    n += 1
            finish_run(conn, rid, "ok", n, out)
            results["arp-scan"] = n
        else:
            finish_run(conn, rid, "error", 0, None, (se or "no output")[:400])
            results["arp-scan"] = f"error: {(se or 'no output')[:80]}"

    # 3. Optional port scan of live hosts
    if args.portscan:
        out = os.path.join(RAW, f"nmap-{stamp}.xml")
        rc, so, se = run_cmd(["sudo", "-n", "nmap", "-sn", "-oG", "-",
                              args.subnet], timeout=300)
        live = []
        if rc == 0:
            for line in so.splitlines():
                if line.startswith("Host:") and "Status: Up" in line:
                    live.append(line.split()[1])
        if live:
            rc2, so2, se2 = run_cmd(
                ["sudo", "-n", "nmap", "-sT", "--top-ports", str(args.top_ports),
                 "-oX", out, *live], timeout=900)
            rid = start_run(conn, "nmap", f"top-ports {args.top_ports} on {len(live)} hosts")
            if rc2 == 0 and os.path.exists(out):
                n = ingest_nmap(conn, out, rid)
                finish_run(conn, rid, "ok", n, out)
                results["nmap"] = f"{n} hosts / {len(live)} scanned"
            else:
                finish_run(conn, rid, "error", 0, None, (se2 or "")[:400])
                results["nmap"] = f"error: {(se2 or '')[:80]}"
        else:
            results["nmap"] = "no live hosts"

    # 3.5 ESP32 Marauder — its own Wi-Fi + BLE radios (no disruption)
    if args.marauder:
        rid = start_run(conn, "marauder",
                        f"scanall {args.marauder_seconds}s + recon ble {args.marauder_ble_seconds}s")
        res = collect_marauder(conn, seconds=args.marauder_seconds,
                               ble_seconds=args.marauder_ble_seconds, run_id=rid)
        if "error" in res:
            finish_run(conn, rid, "error", 0, None, res["error"])
            results["marauder"] = f"error: {res['error']}"
        else:
            n = res["aps"] + res["stations"] + res["ble"]
            finish_run(conn, rid, "ok", n, res.get("artifact"))
            results["marauder"] = f"aps={res['aps']} sta={res['stations']} ble={res['ble']}"

    # 4. Passive sources — just ingest whatever the daemons have written
    if args.arpwatch:
        rid = start_run(conn, "arpwatch",
                        f"{args.arpwatch_path} + journal {args.arpwatch_journal_since}")
        n1 = ingest_arpwatch(conn, args.arpwatch_path, rid)
        n2 = ingest_arpwatch_journal(conn, args.arpwatch_journal_since, rid)
        finish_run(conn, rid, "ok", n1 + n2, args.arpwatch_path)
        results["arpwatch"] = f"file={n1} journal={n2}"
    if args.zeek:
        rid = start_run(conn, "zeek", args.zeek_path)
        n = ingest_zeek(conn, args.zeek_path, rid)
        finish_run(conn, rid, "ok", n, args.zeek_path)
        results["zeek"] = n
    if args.kismet:
        rid = start_run(conn, "kismet", args.kismet_path)
        n = ingest_kismet(conn, args.kismet_path, rid)
        finish_run(conn, rid, "ok", n, args.kismet_path)
        results["kismet"] = n

    conn.commit()
    print(json.dumps({"sweep": stamp, "results": results}, indent=2))
    conn.close()


# ---------------------------------------------------------------- queries

def parse_since(s):
    if not s:
        return None
    m = re.match(r"^(\d+)([hdwm])$", s.strip().lower())
    if not m:
        return s
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n),
             "w": timedelta(weeks=n), "m": timedelta(days=30 * n)}[unit]
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S")


def q_devices(conn, a):
    sql = "SELECT * FROM devices WHERE 1=1"
    p = []
    if a.since:
        sql += " AND last_seen >= ?"; p.append(parse_since(a.since))
    if a.kind:
        sql += " AND kinds LIKE ?"; p.append(f"%{a.kind}%")
    if a.search:
        sql += " AND (ip LIKE ? OR hostname LIKE ? OR mac LIKE ? OR vendor LIKE ?)"
        p += [f"%{a.search}%"] * 4
    sql += " ORDER BY last_seen DESC LIMIT ?"
    p.append(a.limit)
    rows = conn.execute(sql, p).fetchall()
    if not rows:
        print("no devices match"); return
    print(f"{'MAC':18} {'IP':16} {'KINDS':10} {'VENDOR':22} {'NAME/HOST':24} LAST SEEN")
    print("-" * 120)
    for r in rows:
        print(f"{r['mac'] or '-':18} {r['ip'] or '-':16} {(r['kinds'] or '-')[:10]:10} "
              f"{(r['vendor'] or '-')[:22]:22} {(r['hostname'] or '-')[:24]:24} {r['last_seen']}")


def q_ports(conn, a):
    sql = "SELECT * FROM services"
    p = []
    if a.ip:
        sql += " WHERE ip=?"; p.append(a.ip)
    sql += " ORDER BY ip, port"
    rows = conn.execute(sql, p).fetchall()
    if not rows:
        print("no services recorded"); return
    cur = None
    for r in rows:
        if r["ip"] != cur:
            cur = r["ip"]
            print(f"\n{r['ip']}")
        print(f"   {r['port']:>6}/{r['proto']:<5} {(r['service'] or '-'):18} "
              f"{(r['banner'] or '')[:40]:40} last {r['last_seen']}")


def q_show(conn, a):
    mac = norm_mac(a.mac) or a.mac
    dev = conn.execute("SELECT * FROM devices WHERE mac=? OR ip=?",
                       (mac, a.mac)).fetchone()
    if not dev:
        print("device not found"); return
    print("DEVICE")
    for k in dev.keys():
        print(f"  {k:12} {dev[k]}")
    svc = conn.execute("SELECT * FROM services WHERE ip=? ORDER BY port",
                       (dev["ip"],)).fetchall()
    if svc:
        print("\nSERVICES")
        for s in svc:
            print(f"  {s['port']}/{s['proto']:<5} {s['service'] or '-'} "
                  f"first {s['first_seen']} last {s['last_seen']}")
    obs = conn.execute(
        "SELECT * FROM observations WHERE mac=? OR ip=? ORDER BY ts DESC LIMIT ?",
        (dev["mac"], dev["ip"], a.limit)).fetchall()
    print(f"\nOBSERVATIONS (latest {len(obs)})")
    for o in obs:
        print(f"  {o['ts']} {o['source']:9} {o['kind'] or '-':6} "
              f"rssi={o['rssi'] if o['rssi'] is not None else '-':>5} {o['name'] or ''}")


def q_changes(conn, a):
    since = parse_since(a.since) or parse_since("24h")
    rows = conn.execute(
        """SELECT mac, ip, hostname, first_seen, last_seen, kinds, source
             FROM devices WHERE first_seen >= ? ORDER BY first_seen DESC""",
        (since,)).fetchall()
    print(f"NEW devices since {since}: {len(rows)}")
    for r in rows:
        print(f"  {r['first_seen']}  {r['mac'] or '-':18} {r['ip'] or '-':16} "
              f"{r['kinds'] or '-':8} {r['hostname'] or ''}")
    rows2 = conn.execute(
        """SELECT ip, port, proto, service, last_seen FROM services
            WHERE first_seen >= ? ORDER BY first_seen DESC""", (since,)).fetchall()
    print(f"\nNEW open ports since {since}: {len(rows2)}")
    for r in rows2:
        print(f"  {r['last_seen']}  {r['ip']}:{r['port']}/{r['proto']} {r['service'] or ''}")


def q_stats(conn, a):
    for label, sql in [
        ("devices", "SELECT COUNT(*) c FROM devices"),
        ("observations", "SELECT COUNT(*) c FROM observations"),
        ("services", "SELECT COUNT(*) c FROM services"),
        ("runs", "SELECT COUNT(*) c FROM runs"),
    ]:
        print(f"{label:14} {conn.execute(sql).fetchone()['c']}")
    print("\nby kind:")
    for r in conn.execute("""SELECT kinds, COUNT(*) c FROM devices
                             GROUP BY kinds ORDER BY c DESC LIMIT 15"""):
        print(f"  {r['kinds'] or '-':20} {r['c']}")
    print("\ntop vendors:")
    for r in conn.execute("""SELECT vendor, COUNT(*) c FROM devices
                             WHERE vendor IS NOT NULL
                             GROUP BY vendor ORDER BY c DESC LIMIT 10"""):
        print(f"  {r['vendor'][:34]:34} {r['c']}")
    print("\nfirst/last activity:")
    r = conn.execute("SELECT MIN(first_seen) a, MAX(last_seen) b FROM devices").fetchone()
    print(f"  {r['a']}  ->  {r['b']}")


def q_runs(conn, a):
    for r in conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (a.limit,)):
        print(f"[{r['run_id']}] {r['started_at']} {r['tool']:10} {r['status']:8} "
              f"records={r['records']} {r['artifact'] or ''} {r['error'] or ''}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="unified device inventory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    s = sub.add_parser("sweep")
    s.add_argument("--ble", action="store_true", default=True)
    s.add_argument("--no-ble", dest="ble", action="store_false")
    s.add_argument("--ble-seconds", type=int, default=10)
    s.add_argument("--arp", action="store_true", default=True)
    s.add_argument("--no-arp", dest="arp", action="store_false")
    s.add_argument("--portscan", action="store_true", default=False)
    s.add_argument("--top-ports", type=int, default=200)
    s.add_argument("--subnet", default="192.168.1.0/24")
    s.add_argument("--marauder", action="store_true", default=True)
    s.add_argument("--no-marauder", dest="marauder", action="store_false")
    s.add_argument("--marauder-seconds", type=int, default=20)
    s.add_argument("--marauder-ble-seconds", type=int, default=12)
    s.add_argument("--arpwatch", action="store_true", default=False)
    s.add_argument("--arpwatch-path", default="/var/lib/arpwatch/wlan0.dat")
    s.add_argument("--arpwatch-journal-since", default="2h")
    s.add_argument("--zeek", action="store_true", default=False)
    s.add_argument("--zeek-path", default="/var/lib/devicedb/zeek")
    s.add_argument("--kismet", action="store_true", default=False)
    s.add_argument("--kismet-path", default=os.path.expanduser("~/.kismet/kismet.db"))

    p = sub.add_parser("marauder")
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--ble-seconds", type=int, default=12)
    sub.add_parser("refresh-vendors")

    p = sub.add_parser("ingest-blea"); p.add_argument("path")
    p = sub.add_parser("ingest-kismet"); p.add_argument("path")
    p = sub.add_parser("ingest-zeek"); p.add_argument("path")
    p = sub.add_parser("ingest-arpwatch"); p.add_argument("path")
    p = sub.add_parser("ingest-nmap"); p.add_argument("path")

    p = sub.add_parser("devices")
    p.add_argument("--since"); p.add_argument("--kind"); p.add_argument("--search")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("ports"); p.add_argument("--ip")

    p = sub.add_parser("show"); p.add_argument("mac"); p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("changes"); p.add_argument("--since", default="24h")

    sub.add_parser("stats")
    p = sub.add_parser("runs"); p.add_argument("--limit", type=int, default=20)

    a = ap.parse_args()
    if a.cmd == "init":
        init_db(); return

    conn = get_db()
    if a.cmd == "sweep":            sweep(a)
    elif a.cmd == "refresh-vendors":
        print("vendors updated:", refresh_vendors(conn))
    elif a.cmd == "marauder":
        rid = start_run(conn, "marauder", f"scanall {a.seconds}s + recon ble {a.ble_seconds}s")
        res = collect_marauder(conn, seconds=a.seconds, ble_seconds=a.ble_seconds, run_id=rid)
        if "error" in res:
            finish_run(conn, rid, "error", 0, None, res["error"])
        else:
            finish_run(conn, rid, "ok", res["aps"] + res["stations"] + res["ble"],
                       res.get("artifact"))
        conn.commit()
        print(json.dumps(res, indent=1))
    elif a.cmd == "ingest-blea":    print("ingested", ingest_blea(conn, a.path)); conn.commit()
    elif a.cmd == "ingest-kismet":  print("ingested", ingest_kismet(conn, a.path)); conn.commit()
    elif a.cmd == "ingest-zeek":    print("ingested", ingest_zeek(conn, a.path)); conn.commit()
    elif a.cmd == "ingest-arpwatch":print("ingested", ingest_arpwatch(conn, a.path)); conn.commit()
    elif a.cmd == "ingest-nmap":    print("ingested", ingest_nmap(conn, a.path)); conn.commit()
    elif a.cmd == "devices":        q_devices(conn, a)
    elif a.cmd == "ports":          q_ports(conn, a)
    elif a.cmd == "show":           q_show(conn, a)
    elif a.cmd == "changes":        q_changes(conn, a)
    elif a.cmd == "stats":          q_stats(conn, a)
    elif a.cmd == "runs":           q_runs(conn, a)
    conn.close()


if __name__ == "__main__":
    main()
