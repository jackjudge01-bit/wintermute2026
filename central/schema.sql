-- devicedb central service — SYSTEM OF RECORD (Postgres)
-- Draft schema. Freeze pending: pseudonymization scheme, query patterns.
--
-- Design note: Postgres is the system of record (registry + identity resolution +
-- capability KB). Elasticsearch, when added, is a DENORMALIZED READ MODEL fed from
-- here — never the source of truth.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- Contributors = client agents. Provenance anchor for everything they send.
CREATE TABLE IF NOT EXISTS contributors (
    contributor_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle          TEXT UNIQUE NOT NULL,
    public_key      TEXT,                     -- Ed25519 pubkey for record signing
    trust_score     REAL NOT NULL DEFAULT 0,
    sharing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'active',   -- active|suspended
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ
);

-- Capability knowledge base — the thing clients pull down and browse.
CREATE TABLE IF NOT EXISTS device_models (
    model_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor       TEXT,
    model_name   TEXT NOT NULL,
    device_class TEXT,                        -- phone|laptop|iot|ap|camera|sdr|...
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,  -- bands rx/tx, firmware, abilities, weaknesses
    confidence   REAL NOT NULL DEFAULT 0,
    sources      JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor, model_name)
);

-- Fingerprint -> model resolution (Fingerbank-shaped: string in, model + confidence out).
CREATE TABLE IF NOT EXISTS fingerprints (
    fp_id           BIGSERIAL PRIMARY KEY,
    fingerprint     TEXT NOT NULL,            -- "1,33,3,6,12,15,28" | "oui:ac:12:34" | "sni:ring.com"
    signal_type     TEXT NOT NULL,            -- dhcp55|dhcp60|ble_mfr|ble_uuid|oui|sni|ie|ja3|hostname
    device_model_id UUID REFERENCES device_models(model_id) ON DELETE SET NULL,
    confidence      REAL NOT NULL DEFAULT 0,
    votes           INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fingerprint, signal_type)
);

-- Device clusters (central registry). Identity resolution merges into these.
CREATE TABLE IF NOT EXISTS devices (
    device_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor            TEXT,
    device_class      TEXT,
    model_id          UUID REFERENCES device_models(model_id) ON DELETE SET NULL,
    first_seen        TIMESTAMPTZ,
    last_seen         TIMESTAMPTZ,
    confidence        REAL NOT NULL DEFAULT 0,
    contributor_count INTEGER NOT NULL DEFAULT 0
);

-- Observed MAC (pseudonymized) -> device cluster.
CREATE TABLE IF NOT EXISTS identities (
    identity_id    BIGSERIAL PRIMARY KEY,
    device_id      UUID NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    mac_hash       TEXT NOT NULL,             -- hmac(secret, epoch || mac)
    salt_epoch     INTEGER NOT NULL,
    name           TEXT,
    mfr_data       TEXT,
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    contributor_id UUID REFERENCES contributors(contributor_id),
    UNIQUE (mac_hash, salt_epoch)
);
CREATE INDEX IF NOT EXISTS identities_device_idx ON identities(device_id);

-- Ledger of uploads — provenance / audit for every contribution.
CREATE TABLE IF NOT EXISTS contributions (
    contribution_id BIGSERIAL PRIMARY KEY,
    contributor_id  UUID NOT NULL REFERENCES contributors(contributor_id),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_count    INTEGER NOT NULL DEFAULT 0,
    payload_sha256  TEXT,
    signature       TEXT,
    status          TEXT NOT NULL DEFAULT 'accepted'   -- accepted|rejected|partial
);

-- Append-only contributed sightings.
CREATE TABLE IF NOT EXISTS observations (
    obs_id          BIGSERIAL PRIMARY KEY,
    contributor_id  UUID NOT NULL REFERENCES contributors(contributor_id),
    contribution_id BIGINT REFERENCES contributions(contribution_id) ON DELETE CASCADE,
    observed_at     TIMESTAMPTZ NOT NULL,
    signal          TEXT NOT NULL,            -- ble|wifi|lan|sdr
    mac_hash        TEXT,
    name            TEXT,
    rssi            INTEGER,
    channel         TEXT,
    vendor          TEXT,
    lat             REAL,                     -- COARSENED, never raw
    lon             REAL,
    place           TEXT,
    fingerprint     TEXT,
    device_id       UUID REFERENCES devices(device_id) ON DELETE SET NULL,
    extra           JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS obs_mac_idx    ON observations(mac_hash);
CREATE INDEX IF NOT EXISTS obs_time_idx   ON observations(observed_at);
CREATE INDEX IF NOT EXISTS obs_device_idx ON observations(device_id);
