#!/usr/bin/env python3
"""Project Postgres (system of record) -> Elasticsearch (read model).

Upsert semantics: obs_id / model_id are the ES _id, and the bulk `index` action is
ES's create-or-replace. Re-projecting a row overwrites its document instead of
duplicating it, so the projection is safe to re-run at any time.

Runs are INCREMENTAL by default: a watermark of the last projected obs_id /
updated_at means each run only touches rows added or changed since the last one.
Use --full to ignore the watermark and rebuild everything (e.g. after a mapping
change or if the projection is suspected stale).

Known tradeoff: the observation document denormalizes fields joined from `devices`
(device_class, model_id). If a device row changes, already-projected observations
for it are NOT automatically refreshed — that needs a --full run or an
update-by-query. Fine while the corpus is small; revisit when it is not.
"""
import argparse
import json
import os
import urllib.request

import psycopg

DB = os.environ.get(
    "CENTRAL_DB_URL", "postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central"
)
ES = os.environ.get("ES_URL", "http://127.0.0.1:9200")
OBS_INDEX = "devicedb-obs-000001"
MODELS_INDEX = "devicedb-models-000001"
WM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".watermark")
BATCH = 2000
EPOCH = "1970-01-01T00:00:00+00:00"


def load_wm():
    try:
        with open(WM_FILE) as f:
            return json.load(f)
    except Exception:
        return {"obs_id": 0, "models_updated_at": EPOCH}


def save_wm(wm):
    tmp = WM_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(wm, f)
    os.replace(tmp, WM_FILE)


def es(method, path, body=None, ndjson=False):
    if body is None:
        data = None
    elif ndjson:
        data = body.encode()
    else:
        data = json.dumps(body).encode()
    ctype = "application/x-ndjson" if ndjson else "application/json"
    req = urllib.request.Request(
        ES + path, data=data, method=method, headers={"Content-Type": ctype}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def bulk(index, docs):
    """docs: list of (id, body). Returns (indexed, errors)."""
    if not docs:
        return 0, 0
    lines = []
    for _id, body in docs:
        lines.append(json.dumps({"index": {"_index": index, "_id": _id}}))
        lines.append(json.dumps(body))
    res = es("POST", "/_bulk?refresh=false", "\n".join(lines) + "\n", ndjson=True)
    return len(docs), sum(1 for i in res.get("items", []) if i["index"].get("error"))


OBS_SQL = """
    SELECT o.obs_id, o.observed_at, o.received_at, o.signal, o.mac_hash,
           o.name, o.rssi, o.channel, o.vendor, o.lat, o.lon, o.place,
           o.fingerprint, o.device_id, o.extra,
           c.handle, d.device_class, d.model_id
      FROM observations o
      LEFT JOIN contributors c ON c.contributor_id = o.contributor_id
      LEFT JOIN devices d      ON d.device_id = o.device_id
     WHERE o.obs_id > %s
     ORDER BY o.obs_id
     LIMIT %s
"""


def project_observations(conn, since, batch=BATCH):
    """Index observations past `since`. Returns (indexed, errors, last_id)."""
    total = errs = 0
    last = since
    while True:
        with conn.cursor() as cur:
            cur.execute(OBS_SQL, (last, batch))
            rows = cur.fetchall()
        if not rows:
            break
        docs = []
        for r in rows:
            doc = {
                "obs_id": r[0],
                "observed_at": r[1].isoformat() if r[1] else None,
                "received_at": r[2].isoformat() if r[2] else None,
                "signal": r[3], "mac_hash": r[4], "name": r[5], "rssi": r[6],
                "channel": r[7], "vendor": r[8], "place": r[11],
                "fingerprint": r[12],
                "device_id": str(r[13]) if r[13] else None,
                "extra": r[14] or {}, "contributor": r[15],
                "device_class": r[16], "model_id": str(r[17]) if r[17] else None,
            }
            if r[9] is not None and r[10] is not None:
                doc["location"] = {"lat": r[9], "lon": r[10]}
            docs.append((r[0], doc))
        n, e = bulk(OBS_INDEX, docs)
        total += n
        errs += e
        last = rows[-1][0]
        if len(rows) < batch:
            break
    return total, errs, last


MODELS_SQL = """
    SELECT model_id, vendor, model_name, device_class, capabilities,
           confidence, sources, created_at, updated_at
      FROM device_models
     WHERE updated_at > %s
     ORDER BY updated_at
     LIMIT %s
"""


def project_models(conn, since, batch=BATCH):
    """Index device models changed since `since`. Returns (indexed, errors, last_ts)."""
    total = errs = 0
    last = since
    while True:
        with conn.cursor() as cur:
            cur.execute(MODELS_SQL, (last, batch))
            rows = cur.fetchall()
        if not rows:
            break
        docs = []
        for r in rows:
            docs.append((str(r[0]), {
                "model_id": str(r[0]), "vendor": r[1], "model_name": r[2],
                "device_class": r[3], "capabilities": r[4] or {},
                "confidence": r[5], "sources": r[6] or [],
                "created_at": r[7].isoformat() if r[7] else None,
                "updated_at": r[8].isoformat() if r[8] else None,
            }))
        n, e = bulk(MODELS_INDEX, docs)
        total += n
        errs += e
        last = rows[-1][8].isoformat()
        if len(rows) < batch:
            break
    return total, errs, last


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="ignore the watermark and rebuild everything")
    ap.add_argument("--refresh", action="store_true",
                    help="refresh indices immediately after indexing")
    a = ap.parse_args()

    wm = {"obs_id": 0, "models_updated_at": EPOCH} if a.full else load_wm()

    with psycopg.connect(DB) as conn:
        o, oe, obs_last = project_observations(conn, wm["obs_id"])
        m, me, models_last = project_models(conn, wm["models_updated_at"])

    if o:
        wm["obs_id"] = obs_last
    if m:
        wm["models_updated_at"] = models_last
    save_wm(wm)

    if a.refresh:
        es("POST", f"/{OBS_INDEX},{MODELS_INDEX}/_refresh")

    print(json.dumps({
        "mode": "full" if a.full else "incremental",
        "observations_indexed": o, "obs_errors": oe,
        "models_indexed": m, "model_errors": me,
        "watermark": wm,
    }, indent=2))
