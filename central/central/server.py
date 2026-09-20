"""devicedb central service — test server.

System of record is Postgres (see schema.sql). Elasticsearch is NOT used here: it is
the intended denormalized READ MODEL, added once there is data worth searching.

Design commitments baked in:
  - Clients are the sole initiators. This server never distributes targets.
  - Contribution is opt-in; every record carries provenance (contributor + ledger id).
  - Identifiers are pseudonymized at ingest with a server-held secret; location is
    coarsened. Raw MACs never land here.

Endpoints:
  GET  /health
  POST /v1/contributors                  register a contributor
  POST /v1/contribute                    upload a batch of observations
  GET  /v1/fingerprint                   resolve a fingerprint -> model + confidence
  POST /v1/fingerprints                  contribute fingerprint -> model knowledge
  GET  /v1/models/{model_id}             capability KB lookup
  GET  /v1/search                        search observations
"""
import hashlib
import hmac
import json
import os
import resource
import time
from contextlib import contextmanager
from datetime import datetime

import psycopg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

DB_URL = os.environ.get(
    "CENTRAL_DB_URL",
    "postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central",
)
PSEUDO_SECRET = os.environ.get("PSEUDO_SECRET", "change-me-in-prod").encode()
SALT_EPOCH = int(os.environ.get("SALT_EPOCH", "1"))
LOCATION_DECIMALS = int(os.environ.get("LOCATION_DECIMALS", "3"))  # ~110 m at 3 dp

app = FastAPI(title="devicedb central (test)", version="0.1.0")


@contextmanager
def db():
    conn = psycopg.connect(DB_URL, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pseudo(value):
    """Pseudonymize an identifier. Deterministic within a salt epoch so the same MAC
    links across contributors; irreversible without the server secret."""
    if not value:
        return None
    return hmac.new(
        PSEUDO_SECRET, f"{SALT_EPOCH}:{value.strip().lower()}".encode(), hashlib.sha256
    ).hexdigest()


def coarsen(lat, lon):
    if lat is None or lon is None:
        return None, None
    return round(float(lat), LOCATION_DECIMALS), round(float(lon), LOCATION_DECIMALS)


# ---------------------------------------------------------------- input models
class ContributorIn(BaseModel):
    handle: str
    public_key: str | None = None


class ObservationIn(BaseModel):
    observed_at: datetime
    signal: str
    mac: str | None = None
    name: str | None = None
    rssi: int | None = None
    channel: str | None = None
    vendor: str | None = None
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    fingerprint: str | None = None
    extra: dict = Field(default_factory=dict)


class ContributeIn(BaseModel):
    handle: str
    signature: str | None = None
    observations: list[ObservationIn]


class FingerprintIn(BaseModel):
    fingerprint: str
    signal_type: str
    vendor: str | None = None
    model_name: str | None = None
    device_class: str | None = None
    capabilities: dict = Field(default_factory=dict)
    confidence: float = 0.5


# ---------------------------------------------------------------- helpers
def get_or_create_contributor(conn, handle, public_key=None):
    with conn.cursor() as cur:
        cur.execute("SELECT contributor_id FROM contributors WHERE handle=%s", (handle,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE contributors SET last_seen_at=now() WHERE contributor_id=%s",
                (row[0],),
            )
            return row[0]
        cur.execute(
            "INSERT INTO contributors (handle, public_key, sharing_enabled) "
            "VALUES (%s,%s,TRUE) RETURNING contributor_id",
            (handle, public_key),
        )
        return cur.fetchone()[0]


def resolve_device(conn, mac_hash, vendor, contributor_id, ts, name=None):
    """Attach a pseudonymized MAC to a device cluster, creating the cluster if new.
    This mirrors the local device-centric model so history survives centrally."""
    if not mac_hash:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT device_id FROM identities WHERE mac_hash=%s AND salt_epoch=%s",
            (mac_hash, SALT_EPOCH),
        )
        row = cur.fetchone()
        if row:
            did = row[0]
            cur.execute(
                "UPDATE devices SET last_seen=%s, vendor=COALESCE(vendor,%s) WHERE device_id=%s",
                (ts, vendor, did),
            )
            cur.execute(
                "UPDATE identities SET last_seen=%s, name=COALESCE(%s,name) "
                "WHERE mac_hash=%s AND salt_epoch=%s",
                (ts, name, mac_hash, SALT_EPOCH),
            )
            return did
        cur.execute(
            "INSERT INTO devices (vendor, first_seen, last_seen, contributor_count) "
            "VALUES (%s,%s,%s,1) RETURNING device_id",
            (vendor, ts, ts),
        )
        did = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO identities (device_id, mac_hash, salt_epoch, name, first_seen,"
            " last_seen, contributor_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (did, mac_hash, SALT_EPOCH, name, ts, ts, contributor_id),
        )
        return did


# ---------------------------------------------------------------- endpoints
_START = time.time()


def proc_stats():
    """This process's own resource usage, straight from /proc + resource."""
    out = {}
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        out["rss_mb"] = round(pages * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except Exception:
        pass
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("Threads:"):
                    out["threads"] = int(line.split()[1])
                    break
    except Exception:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF)
    out["cpu_time_s"] = round(ru.ru_utime + ru.ru_stime, 2)
    out["uptime_s"] = round(time.time() - _START, 1)
    return out


@app.get("/health")
def health():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM observations")
        obs = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
        )
        conns = cur.fetchone()[0]
    return {"status": "ok", "observations": obs, "connections": conns}


@app.get("/stats")
def stats():
    """Resource usage + row counts — the service's own admin view."""
    tables = ("contributors", "device_models", "fingerprints", "devices",
              "identities", "observations", "contributions")
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        db_size = cur.fetchone()[0]
        counts = {}
        for t in tables:
            cur.execute(f"SELECT count(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"
        )
        counts["active_connections"] = cur.fetchone()[0]
    return {"process": proc_stats(), "db_size": db_size, "db_rows": counts}


@app.post("/v1/contributors")
def create_contributor(body: ContributorIn):
    with db() as conn:
        cid = get_or_create_contributor(conn, body.handle, body.public_key)
    return {"contributor_id": str(cid), "handle": body.handle}


@app.post("/v1/contribute")
def contribute(body: ContributeIn):
    payload_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    with db() as conn:
        cid = get_or_create_contributor(conn, body.handle)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contributions (contributor_id, record_count, payload_sha256,"
                " signature, status) VALUES (%s,%s,%s,%s,'accepted') RETURNING contribution_id",
                (cid, len(body.observations), payload_hash, body.signature),
            )
            contrib_id = cur.fetchone()[0]

        stored = 0
        for o in body.observations:
            mh = pseudo(o.mac)
            lat, lon = coarsen(o.lat, o.lon)
            did = resolve_device(conn, mh, o.vendor, cid, o.observed_at, o.name)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO observations (contributor_id, contribution_id, observed_at,
                           signal, mac_hash, name, rssi, channel, vendor, lat, lon, place,
                           fingerprint, device_id, extra)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        cid, contrib_id, o.observed_at, o.signal, mh, o.name, o.rssi,
                        o.channel, o.vendor, lat, lon, o.place, o.fingerprint, did,
                        json.dumps(o.extra),
                    ),
                )
            stored += 1
    return {"contribution_id": contrib_id, "stored": stored, "status": "accepted"}


@app.get("/v1/fingerprint")
def resolve_fingerprint(
    fp: str = Query(..., description="fingerprint string"),
    signal: str | None = Query(None, description="signal_type filter"),
):
    """Fingerbank-shaped resolution: fingerprint in -> model + confidence out."""
    with db() as conn, conn.cursor() as cur:
        if signal:
            cur.execute(
                """SELECT f.fingerprint, f.signal_type, f.confidence, f.votes,
                          m.model_id, m.vendor, m.model_name, m.device_class, m.capabilities
                     FROM fingerprints f LEFT JOIN device_models m ON m.model_id=f.device_model_id
                    WHERE f.fingerprint=%s AND f.signal_type=%s""",
                (fp, signal),
            )
        else:
            cur.execute(
                """SELECT f.fingerprint, f.signal_type, f.confidence, f.votes,
                          m.model_id, m.vendor, m.model_name, m.device_class, m.capabilities
                     FROM fingerprints f LEFT JOIN device_models m ON m.model_id=f.device_model_id
                    WHERE f.fingerprint=%s""",
                (fp,),
            )
        rows = cur.fetchall()
    if not rows:
        return {"fingerprint": fp, "known": False, "matches": []}
    matches = [
        {
            "signal_type": r[1],
            "confidence": r[2],
            "votes": r[3],
            "model_id": str(r[4]) if r[4] else None,
            "vendor": r[5],
            "model_name": r[6],
            "device_class": r[7],
            "capabilities": r[8],
        }
        for r in rows
    ]
    return {"fingerprint": fp, "known": True, "matches": matches}


@app.post("/v1/fingerprints")
def submit_fingerprint(body: FingerprintIn):
    """Contribute device knowledge: fingerprint -> model (creates/links the model)."""
    with db() as conn, conn.cursor() as cur:
        model_id = None
        if body.model_name:
            cur.execute(
                """INSERT INTO device_models (vendor, model_name, device_class, capabilities,
                       confidence, sources)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (vendor, model_name) DO UPDATE
                      SET updated_at=now(),
                          capabilities = device_models.capabilities || EXCLUDED.capabilities
                   RETURNING model_id""",
                (
                    body.vendor, body.model_name, body.device_class,
                    json.dumps(body.capabilities), body.confidence, json.dumps(["api"]),
                ),
            )
            model_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO fingerprints (fingerprint, signal_type, device_model_id,
                   confidence, votes)
               VALUES (%s,%s,%s,%s,1)
               ON CONFLICT (fingerprint, signal_type) DO UPDATE
                  SET votes = fingerprints.votes + 1,
                      confidence = GREATEST(fingerprints.confidence, EXCLUDED.confidence),
                      device_model_id = COALESCE(EXCLUDED.device_model_id, fingerprints.device_model_id)
               RETURNING fp_id, votes, confidence""",
            (body.fingerprint, body.signal_type, model_id, body.confidence),
        )
        fp_id, votes, confidence = cur.fetchone()
    return {
        "fp_id": fp_id,
        "model_id": str(model_id) if model_id else None,
        "votes": votes,
        "confidence": confidence,
    }


@app.get("/v1/models/{model_id}")
def get_model(model_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT model_id, vendor, model_name, device_class, capabilities,
                      confidence, sources, updated_at
                 FROM device_models WHERE model_id=%s""",
            (model_id,),
        )
        r = cur.fetchone()
    if not r:
        raise HTTPException(404, "model not found")
    return {
        "model_id": str(r[0]), "vendor": r[1], "model_name": r[2], "device_class": r[3],
        "capabilities": r[4], "confidence": r[5], "sources": r[6], "updated_at": r[7],
    }


@app.get("/v1/search")
def search(
    vendor: str | None = None,
    signal: str | None = None,
    name: str | None = None,
    limit: int = Query(20, le=200),
):
    """Placeholder query surface. This is what Elasticsearch will serve later."""
    clauses, params = [], []
    if vendor:
        clauses.append("vendor ILIKE %s")
        params.append(f"%{vendor}%")
    if signal:
        clauses.append("signal = %s")
        params.append(signal)
    if name:
        clauses.append("name ILIKE %s")
        params.append(f"%{name}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT obs_id, observed_at, signal, name, vendor, rssi, place, device_id
                  FROM observations {where} ORDER BY observed_at DESC LIMIT %s""",
            params,
        )
        rows = cur.fetchall()
    return {
        "count": len(rows),
        "results": [
            {
                "obs_id": r[0], "observed_at": r[1], "signal": r[2], "name": r[3],
                "vendor": r[4], "rssi": r[5], "place": r[6],
                "device_id": str(r[7]) if r[7] else None,
            }
            for r in rows
        ],
    }
