-- devicedb schema — unified device inventory
-- SQLite. All timestamps are ISO8601 UTC strings ('YYYY-MM-DDTHH:MM:SS').

-- One row per unique observed device identity.
CREATE TABLE IF NOT EXISTS devices (
    device_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    mac         TEXT UNIQUE,            -- normalised UPPER:CO:LO:N, NULL if ip-only
    ip          TEXT,
    hostname    TEXT,
    vendor      TEXT,                   -- OUI lookup
    kinds       TEXT,                   -- csv: ble,bt,wifi,eth,zigbee,rf,dns,cast
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    source      TEXT,                   -- csv of sources that observed it
    randomized  INTEGER DEFAULT 0,      -- locally-administered MAC (privacy)
    place       TEXT,                   -- where the sweep that saw it ran
    lat         REAL,
    lon         REAL,
    notes       TEXT
);

-- Append-only raw sightings. Never updated; this is the history.
CREATE TABLE IF NOT EXISTS observations (
    obs_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    source   TEXT NOT NULL,             -- blea|marauder|kismet|zeek|arpwatch|arp-scan|nmap
    mac      TEXT,
    ip       TEXT,
    kind     TEXT,
    name     TEXT,                      -- ssid / device name / hostname
    rssi     INTEGER,
    channel  TEXT,
    extra    TEXT,                      -- JSON blob
    run_id   INTEGER REFERENCES runs(run_id),
    place    TEXT,                      -- location at time of sighting
    lat      REAL,
    lon      REAL,
    device_id INTEGER                   -- device cluster this sighting belongs to
);

-- Observed MACs -> device cluster. The device-centric core: a physical device
-- that rotates its MAC (BLE, mobile) maps to ONE device_id across all its MACs.
CREATE TABLE IF NOT EXISTS identities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL REFERENCES devices(device_id),
    mac        TEXT,
    name       TEXT,
    mfr_data   TEXT,                    -- manufacturer-data fingerprint (JSON)
    first_seen TEXT,
    last_seen  TEXT,
    source     TEXT,
    UNIQUE(device_id, mac)
);

-- Open ports / services seen on a host.
CREATE TABLE IF NOT EXISTS services (
    ip         TEXT NOT NULL,
    port       INTEGER NOT NULL,
    proto      TEXT NOT NULL,
    service    TEXT,
    banner     TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (ip, port, proto)
);

-- Audit log of every collection run (so you can tell gaps from absence).
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    tool       TEXT NOT NULL,
    args       TEXT,
    status     TEXT,                    -- running|ok|error
    records    INTEGER DEFAULT 0,
    artifact   TEXT,                    -- path to raw output kept in raw/
    error      TEXT,
    place      TEXT                     -- where this run's sweep ran
);

CREATE INDEX IF NOT EXISTS idx_devices_mac      ON devices(mac);
CREATE INDEX IF NOT EXISTS idx_devices_ip       ON devices(ip);
CREATE INDEX IF NOT EXISTS idx_devices_lastseen ON devices(last_seen);
CREATE INDEX IF NOT EXISTS idx_obs_ts           ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_mac          ON observations(mac);
CREATE INDEX IF NOT EXISTS idx_obs_source       ON observations(source);
CREATE INDEX IF NOT EXISTS idx_svc_ip           ON services(ip);

-- Convenience view: a flat human-readable inventory.
CREATE VIEW IF NOT EXISTS v_inventory AS
SELECT
    d.mac,
    d.ip,
    d.hostname,
    d.vendor,
    d.kinds,
    d.randomized,
    d.first_seen,
    d.last_seen,
    (SELECT COUNT(*) FROM observations o WHERE o.mac = d.mac) AS obs_count,
    (SELECT COUNT(*) FROM identities i WHERE i.device_id = d.device_id) AS alias_count,
    (SELECT COUNT(*) FROM services s WHERE s.ip = d.ip)       AS port_count
FROM devices d
ORDER BY d.last_seen DESC;
