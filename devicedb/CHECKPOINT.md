# devicedb / device-tracker — CHECKPOINT 2026-09-17

The single "come back to this" document. If you read one file, read this one.

## What this project is

A local device-tracking system, evolving into a distributed crowdsourced data
co-op. Two halves:

1. **The local agent** (`~/devicedb`) — runs on this laptop, sweeps the local area
   (BLE, WiFi, LAN) every 10 minutes, stores everything in a local, device-centric
   SQLite database with per-device timelines. **Built and running.**
2. **The central service** (`~/central`) — the shared server: serves the device /
   firmware capability knowledge base and fingerprint resolution, and receives
   opt-in contributed data. **Test server built and verified today.**

Guiding model: clients are the sole initiators; central never initiates anything;
contribution is optional. Pseudonymize and coarsen at the edge.

## Where everything lives

```
~/devicedb/                     local agent (PRODUCTION — cron runs it)
  devicedb.py                   the app (single file, stdlib + sqlite3)
  schema.sql                    local SQLite schema
  NOTES.md                      ★ running notes — ALL decisions & research
  README.md                     project readme
  sweep.sh                      the 10-minute sweep wrapper
  inventory.db                  LIVE local database
  inventory.db.bak-20260917-120749   backup taken before the cluster migration

~/central/                      central service (TEST SERVER)
  server.py                     FastAPI service
  schema.sql                    Postgres schema (DRAFT)
  DESIGN.md                     ★ full central design + ES decision
  test_client.py                end-to-end test
  venv/                         python venv (fastapi, uvicorn, psycopg)

~/checkpoints/device-tracker-2026-09-17/
  CHECKPOINT.md                 ← this file
  snapshot/                     frozen copies of all source files
  db/inventory_local.sql        local DB dump (schema + data)
  db/central_postgres.sql       central DB dump (schema + data)

~/checkpoints/device-tracker-2026-09-17.tar.gz    the whole checkpoint, one file
```

## Current state (as of this checkpoint)

Local agent:
```
devices       2797      observations  17885
identities    2477      runs           1304
cron          */10 * * * * /path/to/devicedb/sweep.sh
```

Central test server:
```
contributors 1   device_models 1   fingerprints 1
devices 1        identities 1      observations 2   contributions 1
DB:  postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central
```

Running right now:
```
127.0.0.1:8090   uvicorn (central test server; pid changes each start)
127.0.0.1:5432   postgres 18
0.0.0.0:8188     ComfyUI
```

## How to come back / restart everything

```bash
# 1. local agent — nothing to do, cron does it. Manual sweep:
cd ~/devicedb && python3 devicedb.py sweep
python3 devicedb.py stats        # numbers
python3 devicedb.py cluster      # device clusters under multiple MACs

# 2. central service
sudo pg_ctlcluster 18 main start
cd ~/central
PSEUDO_SECRET="test-secret-please-rotate" \
  ./venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090
./venv/bin/python test_client.py          # end-to-end check
curl -s http://127.0.0.1:8090/health

# 3. restore a database from this checkpoint if ever needed
sqlite3 ~/devicedb/inventory.db < ~/checkpoints/device-tracker-2026-09-17/db/inventory_local.sql
PGPASSWORD=CHANGE_ME psql -h 127.0.0.1 -U devicedb -d devicedb_central \
  < ~/checkpoints/device-tracker-2026-09-17/db/central_postgres.sql
```

## Decisions locked in

- **Device identity**: MAC is not identity (it rotates). `devices` = cluster keyed on
  a synthetic `device_id`; `identities` = observed pseudonymized MAC -> cluster;
  `observations.device_id` links every sighting to its cluster. Clustering is
  conservative (MAC alias -> name+mfr_data -> unambiguous name), BLE-only for now.
- **Location**: GPS via gpsd if present, else public-IP geo; resolved once per sweep.
- **Central storage**: **Postgres = system of record; Elasticsearch = read model**
  (CQRS projection, added later). ES has no unique constraints / transactions /
  joins, and identity merging needs all three.
- **Privacy**: pseudonymize MACs at ingest (HMAC + salt epoch); coarsen GPS;
  provenance on every record (contributor + contribution ledger).
- **Model**: clients initiate everything; central never distributes targets;
  sharing is optional.
- **Targets**: Linux. **Android parked** (see NOTES.md "Parked" section).

## Open questions (decide before freezing the central schema)

1. Pseudonymization rotation scheme (rotating the epoch breaks cross-contributor
   linking — a conscious tradeoff).
2. Per-sensor field sets (what stays in the JSONB `extra`).
3. Query patterns — what users actually search (decides ES mappings).
4. Upload granularity + retention policy.
5. Access model — what sharing buys (carrot, since read can't be gated on sharing).
6. License: ES 8.16+ is AGPLv3; OpenSearch is Apache-2.0.
7. Distributed topology: one hub vs federated hubs; conflict resolution; node trust.

## Next concrete step

Wire the local agent's contribute path: after each sweep, `devicedb.py` posts its
findings to the central service (`POST /v1/contribute`), so real sweeps start
feeding the central dataset.

## Doc index

- `~/devicedb/NOTES.md` — running notes: direction, product model, fingerprint
  research, distributed-system model, all open questions.
- `~/central/DESIGN.md` — central service design, ES decision, schema, endpoints.
- `~/checkpoints/device-tracker-2026-09-17/` — this frozen snapshot.
