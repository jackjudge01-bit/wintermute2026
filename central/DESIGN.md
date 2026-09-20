# devicedb central service — design

Status: **test server built and verified** (2026-09-17). Schema is a DRAFT.

## What it is

The central service for the data co-op. Clients run their own scans on their own
terms; central never initiates anything. Central's two jobs:

1. Serve the shared knowledge: device/firmware capability KB, fingerprint resolution.
2. Receive contributed data (opt-in) and keep the device-centric registry.

## Storage decision: Postgres = system of record, Elasticsearch = read model

**Decision: Elasticsearch yes, but NOT as the system of record.**

The hardest operation in this system is **identity resolution and merging** — a new
MAC matching an existing device cluster by fingerprint, then merging and reassigning
aliases. That needs transactions, uniqueness constraints, and idempotent upserts:

- ES has **no unique constraints** and **no transactions**.
- An ES update is a full document reindex (delete + index) — merges churn the index.
- The core shape (`devices` <- `identities` <- `observations`, `fingerprints` ->
  `device_models`) is relational; ES has no joins.

So:

```
  Postgres       = system of record: registry, identity resolution/merge,
                   capability KB, fingerprints, contributor/contribution ledger
  Elasticsearch  = read model (to add): denormalized projection for search,
                   aggregations, geo queries, Kibana. Fed FROM Postgres. CQRS.
```

**License note**: ES 8.16+ is AGPLv3 again (Aug 2024 re-add after the SSPL/Elastic-2.0
detour). Running modified AGPL as a network service has copyleft implications.
OpenSearch is Apache-2.0 and a drop-in. Decide deliberately for a commercial service.

**ES is now installed natively** (9.5.4, systemd, :9200; Kibana :5601) — no
containers. Heap pinned to 1 GB because the box also runs ComfyUI. Security is
disabled for the loopback test build. The CQRS projection is live:
`~/central/es/project.py` copies Postgres -> ES idempotently (obs_id == ES `_id`).
Indices: `devicedb-obs-000001`, `devicedb-models-000001`; Kibana data views created
for both. See `ADMIN.md` for the elasticsearch section.

## Schema (draft)

| table           | role |
|-----------------|------|
| `contributors`  | client agents; provenance anchor; trust_score; public_key for signing |
| `device_models` | capability KB — vendor, model, class, capabilities JSONB, confidence, sources |
| `fingerprints`  | fingerprint string -> model + confidence + votes (Fingerbank-shaped) |
| `devices`       | device clusters (central registry), optional model_id link |
| `identities`    | pseudonymized MAC -> device_id; UNIQUE (mac_hash, salt_epoch) |
| `contributions` | ledger of every upload: contributor, count, payload hash, signature, status |
| `observations`  | append-only sightings, provenance + coarsened location, linked device_id |

## Privacy commitments baked into the schema

- **Pseudonymization at ingest**: `mac_hash = HMAC(server_secret, epoch || mac)`.
  Deterministic within a salt epoch so the same MAC links across contributors (that
  is the whole point), and irreversible without the server secret. Raw MACs are
  never stored. Served by `salt_epoch` column + rotation.
- **Location coarsening**: lat/lon rounded (`LOCATION_DECIMALS`, default 3 dp ≈ 110 m)
  before storage. Raw GPS never lands centrally.
- **Provenance**: every observation carries `contributor_id` +
  `contribution_id`; every upload is hashed and can be signed.
- Model knowledge (benign) and location (sensitive) are separate tables/stores.

## Endpoints (test server)

```
GET  /health
POST /v1/contributors              register a contributor
POST /v1/contribute                upload a batch of observations (provenance-tagged)
GET  /v1/fingerprint?fp=&signal=   resolve fingerprint -> model + confidence
POST /v1/fingerprints              contribute fingerprint -> model knowledge
GET  /v1/models/{model_id}         capability KB lookup
GET  /v1/search                    placeholder query surface (ES replaces this)
```

## Verified behaviour (2026-09-17)

- Case-different MACs (`AA:BB:CC:..` / `aa:bb:cc:..`) hash identically -> one device
  cluster. Raw MAC absent from the DB.
- GPS `49.18361234, -123.11645678` stored as `49.184, -123.116`.
- Known fingerprint resolves to model + capabilities; unknown returns `known:false`
  (the "needs research" path).
- Contribution ledger row written with payload sha256 + signature.

## Open — needed before freezing the schema

1. **Pseudonymization scheme finalisation**: rotation cadence, key custody, whether
   epoch rotation breaks cross-contributor linking (it does — that's the tradeoff).
2. **Per-sensor field sets**: exact fields per sensor; what stays in `extra` JSONB.
3. **Query patterns**: what users actually search — decides ES mappings.
4. **Upload granularity + retention**: raw sightings vs summaries; retention policy.
5. **Access model**: what sharing buys (determines contributor/access tables).

## Running the test server

```
sudo pg_ctlcluster 18 main start
cd ~/central && ./venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090
# DB: postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central
# env: PSEUDO_SECRET (set in prod), SALT_EPOCH, LOCATION_DECIMALS
python test_client.py     # end-to-end check
```
