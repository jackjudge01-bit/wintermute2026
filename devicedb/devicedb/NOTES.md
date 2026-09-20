# devicedb — build notes

Running notes on how we build this application. Append-only; no editorializing.

## Direction (2026-09-17)

- Target: **Linux** — the current platform. **Android target PARKED (2026-09-17)**,
  user said "scrub the android requirement, we will come back to it." Design
  Linux-native; don't contort the core for a target we're not building yet.
- Radio capture hardware is the **ESP32 Marauder** (serial), plus host WiFi/BLE.

## What the application is

Local device-tracking system:
- Recon the local area (BLE + WiFi + LAN) on a schedule.
- Record every encounter into a **device-centric** database.
- Track how a device moves over time; maintain history.
- Defeat MAC randomization via fingerprint clustering ("who are you across
  bluetooth and wifi").

## Architecture as built (Linux, current)

- `~/devicedb/devicedb.py` — single-file Python app, stdlib + sqlite3.
- Storage: SQLite, WAL mode, at `~/devicedb/inventory.db`.
- Tables:
  - `devices` — one row per **device cluster** (synthetic `device_id`; representative
    MAC; hostname/name; vendor; kinds; first/last seen; source; randomized flag;
    place/lat/lon).
  - `identities` — observed MAC -> device_id alias table + fingerprint evidence
    (name, mfr_data). The device-centric join. UNIQUE(device_id, mac).
  - `observations` — append-only raw sightings; carries `device_id` to link each
    sighting to its cluster. Never updated (history).
  - `runs` — one row per sweep; carries location.
  - `services` — open ports per IP (from portscan).
  - `v_inventory` — flat human-readable view (obs_count, alias_count, port_count).
- Clustering: `find_cluster` — conservative. MAC alias -> name+mfr_data ->
  unambiguous-name. BLE-only for now.
- Location: GPS via gpsd if present, else public-IP geo (ip-api.com); resolved once
  per sweep, cached; NULL when offline.
- Schedule: cron `*/10 * * * * ~/devicedb/sweep.sh`.
- Collectors: blea (BLE), marauder (ESP32 serial), kismet, zeek, arpwatch,
  arp-scan, nmap portscan.

## Parked: Android target (revisit later)

Deferred 2026-09-17 (user: "scrub the android requirement, peg it we will come back
to it"). Kept for reference — when we pick it back up, these are the portability
gaps to close:

- [ ] Serial transport: `/dev/ttyACM0` hardcoded -> configurable device path;
      consider USB-OTG naming.
- [ ] Scheduling: cron -> needs an in-process scheduler or Android equivalent
      (no cron on stock Android).
- [ ] gpsd: absent on Android -> rely on Android/geo-IP location provider.
- [ ] Collector availability: kismet/zeek/arpwatch/nmap likely absent ->
      make collectors pluggable/optional and degrade gracefully.
- [ ] DB path: `~/devicedb` -> app-private storage path.
- [ ] Long-running process model: cron sweep -> resident daemon optional.

## Known gaps / open questions

- WiFi **client** MAC capture missing (Marauder `list -s` empty) — needs monitor
  mode / Kismet / 2nd adapter. `identities` is built to absorb it when available.
- Probe-SSID fingerprinting (strongest WiFi identity signal) unavailable without
  monitor mode.
- Fingerprint cluster != guaranteed identity — honest ceiling, documented.

## Conventions

- Back up `inventory.db` before schema migrations.
- Migrations are idempotent and run in `get_db()`.
- Keep new abstractions only where a real device differs, not speculatively.

## Hardware onboarding & capability probe (spec)

At **installation**, the app scans the platform for usable devices and onboards
them. Three stages:

1. **Scan** the platform for available hardware — onboard WiFi, USB WiFi/BT
   adapters, serial devices (ESP32 Marauder), GPS, etc. Enumerate via
   `/dev/serial/by-id`, `lsusb`, `nmcli`/`iw`, `hcitool`/BlueZ, gpsd.
2. **Onboard** each detected device: record it in an **internal DB of onboarded
   devices, with its abilities and weaknesses** (what it can do: monitor mode,
   packet injection, BLE, 2.4/5 GHz, serial baud; what it can't: no monitor mode,
   no injection, single-band, no GPS, etc.).
3. **Tailor the service profile** to the hardware: the set of scans/services the
   app offers is derived from the onboarded capabilities. No monitor-mode adapter
   -> no probe-SSID capture offered; no GPS -> geo-IP location only; etc.

**Ring-fencing:** the user is offered the chance to ring-fence certain onboard
devices (e.g. the ONBOARD WIFI) so they are **excluded from being co-opted into
scans**. Ring-fenced devices are recorded but not used as scan radios.

### Onboarded-device registry (DB)

Each onboarded device row carries:
- identity (path / USB id / interface name / MAC)
- class (wifi / bluetooth / serial / gps / ...)
- abilities (capability flags)
- weaknesses / limitations
- ring-fence state (in-use vs reserved by user)
- source of discovery, first/last seen

### Consequences for the core

- Capability flags gate which collectors are offered/run.
- Ring-fence state gates which devices may be used as scan radios.
- Collector set becomes **dynamic** (derived from onboarding), not a fixed list.

## Device/firmware capability knowledge base (spec)

Present the user with the **capabilities of their devices**:
- what bands they can RX / TX (2.4 / 5 / 6 GHz, sub-GHz, BLE, etc.)
- what **firmware** they have loaded.

Maintain an **up-to-date DB bundled with the app** of:
- hardware capabilities of popular devices
- capabilities of their firmware.

Update path: **monitor the vendors'/projects' GitHub repos** (firmware releases,
changelogs) to keep the bundled capability DB current. This is a
**capabilities + firmware lineage** catalog, shipped alongside the app.

Flagged by the user as a **major project**.

## Identity: fingerprint resolution + first-seen registration (research, 2026-09-17)

Mechanism for "client sees an unknown device -> pull what we know from central;
if unknown everywhere -> research/enumerate it, store centrally."

**Prior art to copy — Fingerbank** (fingerbank.org): central device-fingerprint API.
Client POSTs a fingerprint (dhcp_fingerprint, dhcp_vendor, user_agents) -> returns
`device_name` + `score` + `version` + a device hierarchy. Unknown fingerprints are
SUBMITTED by clients and become known for everyone. ~110K device models, 6M+
fingerprints. This IS the crowdsourced first-seen flow, at scale. Key ideas to
steal: key on a **fingerprint string** (not a device ID), return a **confidence
score**, return a **hierarchy** (OS -> vendor OS -> model). DEFCON 19 talk exists.

**Terminology**: "first-seen identifier" is not a standard term. The two real halves:
- *first-seen / last-seen* = the bookkeeping convention (timestamp an identity first
  appears) — universal in monitoring/ITSEC (Defender, Elastic, NetFlow, fraud).
- *fingerprint resolution* = the actual identification step (Fingerbank, JA3/JA4,
  p0f, OUI).

**Signal set**, ranked, split by what our sensors can see:

  LAN-side (Fingerbank's signals — we do NOT capture these):
    DHCP option 55 (ordered param request list) — strong
    DHCP option 60 (vendor class) — device self-declares vendor/product
    DHCP hostname, LLDP, mDNS/DNS-SD, SSDP/UPnP description XML
    HTTP User-Agent, TLS JA3/JA4 client fingerprint

  Over-the-air (what our BLE + WiFi sensors actually see):
    IEEE OUI (MAC prefix -> vendor; Amazon prefix = Ring/Echo class)
    BLE: Bluetooth SIG Company Identifier (manufacturer data), advertised service
         UUIDs, device name/local name, GATT services on connect
    WiFi: probe-SSID sets, information-element fingerprint, WPS, supported rates
    Monitor mode only: DNS queries + TLS SNI -> reveals cloud hostnames (e.g.
         "ring.com") — the strongest over-the-air IoT-identification signal

  => The Ring example cannot be asked "who are you" over the air; it's inferred from
     OUI (Amazon) + SNI/hostnames + traffic shape. Needs monitor mode. Without it,
     OUI + BLE manufacturer data is the ceiling.

**Proposed central structure** (two tables, not one):

  fingerprints   (fingerprint_string, signal_type, created_at, submitter, votes)
      signal_type = dhcp55 | ble_mfr | ble_uuid | oui | sni | ie | ja3 | ...
  device_models  (model_id, vendor, class, capabilities_json, confidence, sources)

**Resolution flow** (client): compute fingerprint -> local cache -> central lookup ->
on double-miss create provisional record marked NEEDS_RESEARCH + upload evidence ->
client or central research worker enumerates capabilities -> device_model row written
-> everyone resolves it from cache thereafter. First observer pays the research cost
once; all benefit after. (This is the crowd-sourcing incentive.)

**"Enumerate its capabilities" per class**:
  LAN device:   port scan, UPnP/SSDP description, mDNS TXT, HTTP title, TLS cert
  BLE device:   connect + GATT enumeration (services/characteristics) = capability manifest
  WiFi AP:      beacon/probe IEs (rates, bands, WPS, security)
  Our own gear: USB descriptor + firmware handshake (the onboarding probe)
  => Foreign devices' capabilities come from interrogating THEM; our own hardware's
     capabilities come from the onboarding scan + the GitHub-monitored firmware KB.
     Two pipelines, same central store.

**Pitfalls**:
  - Fingerprint collisions (shared fingerprints) -> always return best-guess + score,
    never a single definitive answer.
  - Poisoning: crowdsourced capability data is attacker-writable -> per-field
    votes/reputation/provenance + a confidence floor before serving to all.
  - Signal drift: JA3 fading (JA4), MAC randomization standard -> version the
    fingerprint schema so it can be swapped.
  - Privacy: model knowledge is benign; the same DB keyed to WHERE a device was seen
    is not -> keep model knowledge and location in strictly separate stores.



## Model: clients as info-gathering agents (CORRECTED 2026-09-17)

**User correction**: "each user runs their own scans on their own terms, we never
initiate anything from cnc, they have the option to share their data."

So the real model:
- Each client runs **its own scans, its own targets, on its own terms and its own
  authorization**. The user is the sole initiator.
- The **central server NEVER initiates anything** — no target distribution, no C2.
  It is a *data service*: it serves the shared datasets (fingerprint / capability
  knowledge base) and receives contributed data.
- "using data from our servers" = clients **enrich their own scans** with our data
  (lookup: what is this device, what can it do), NOT that we hand them targets.
- Sharing is **OPTIONAL** — opt-in data contribution, not a gate on using the app.

**Revised assessment** (the earlier botnet/liability review was based on a
misreading; those concerns do not apply to this model):
- This is a clean **federated / crowdsourced data co-op**. No C2, no distributed
  liability, no target dictation. The user's own scans are the user's own business.

What still genuinely matters under this (correct) model:
1. **Privacy of third-party data** — shared runs contain *other people's* devices and
   locations. The contributor opting in is not the observed party consenting. Data
   minimization / pseudonymization at upload is the core ethical + legal task.
2. **Corpus quality / poisoning** — contributed data can be wrong or malicious.
   Provenance + trust weighting + signing on every record (unchanged, still needed).
3. **Access model** — if sharing is optional, read access cannot be gated on sharing.
   The incentive must be a carrot (richer/earlier access, contributor score,
   reciprocal depth), not a lock. Need to define what sharing buys.
4. **Coverage bias** — contributors are a self-selected sample, so the corpus is
   sparse and biased. That is a data-quality property to design around, not fix.
5. **Central integrity still matters** — as a pure data service its integrity and
   availability are the whole product; there is no control plane to secure.

## Distributed-system model (2026-09-17)

Q: is the local-DB + central-server design a model for a distributed system?
A: **Yes — local-first, hub-and-spoke, store-and-forward aggregation with eventual
consistency.** Those five words are the model.

Framing: it is ES's own architecture at coarse grain — many nodes each owning a
shard (source-of-truth for that shard), a coordinating layer that aggregates,
eventual consistency, no cross-node transactions, merges handled app-side. Here the
"shards" are users' machines and the "network" is the open internet with nodes that
come and go.

Why the shape fits: nodes must work offline/independently (mobile laptop) ->
local-first is mandatory; local workload is append-heavy/read-light; eventual
consistency is acceptable (a late sighting hurts nobody); the valuable asset is the
aggregate -> a hub makes sense.

Already right as distributed foundations:
- eventual consistency by design
- `observed_at` (client clock) + `received_at` (server clock) — no trustworthy global clock
- provenance on every record (contributor + ledger) — seed of Byzantine tolerance
- pseudonymize/coarsen at the edge — privacy enforced before data moves
- local DB is self-sufficient -> a node never depends on the hub to function

What a REAL distributed version still needs:
1. Topology: ours is a star = SPOF + bottleneck + single chokepoint of power.
   Options: federate the hub (Usenet/Git/Matrix style replication), multi-tier
   (edge -> regional -> global), or full P2P (hardest, rarely worth it).
2. Conflict resolution: two nodes disagree about one device -> merge rules
   (CRDT-ish / LWW + version vectors / per-field voting).
3. Who is source of truth: node owns its sightings locally; demanding one canonical
   global record makes the hub SoR and inherits partitions/split-brain/stale reads.
4. Idempotency: at-least-once delivery -> dedup key (contributor + local record id).
5. Watermarks / offline catch-up: track last-sent per node so a week-offline node
   resumes without resending everything.
6. Byzantine nodes: signing + reputation + spot-check quorum.
7. Partitioning + schema versioning: shard central by time/region; version payloads
   so a mixed-version fleet can't corrupt ingest.

When it's wrong: needing strong consistency / one canonical real-time truth means the
hub becomes SoR and you are writing a distributed DB (hard). A politically unacceptable
hub forces federation or P2P — where the real pain lives.

Key decisions to go from test server -> real distributed system: TOPOLOGY,
CONFLICT RESOLUTION, and HOW MUCH YOU DISTRUST THE NODES.

## Central service (test server built 2026-09-17)

Location `~/central/`. See `~/central/DESIGN.md` for the full design.
- **Storage decision**: Postgres = system of record (registry, identity resolution,
  capability KB). Elasticsearch = intended READ MODEL (search/aggregations/geo),
  added later as a CQRS projection — NOT the source of truth. Reasons: ES has no
  unique constraints / no transactions / no joins, and identity merging needs all
  three. ES 8.16+ is AGPLv3; OpenSearch is Apache-2.0 (license decision pending).
- Files: `schema.sql` (draft), `server.py` (FastAPI), `test_client.py`, `DESIGN.md`.
- DB `devicedb_central` on local Postgres 18; role `devicedb`.
- Verified: deterministic MAC pseudonymization (HMAC + salt epoch), location
  coarsening, device clustering, fingerprint resolve (known/unknown), contribution
  ledger, capability KB lookup, search.
- Open before schema freeze: pseudonymization rotation scheme, per-sensor field sets,
  query patterns, retention, access model (what sharing buys).
- **Elasticsearch + Kibana installed natively** (2026-09-17, no containers): ES 9.5.4
  :9200, Kibana 9.5.4 :5601, both systemd + enabled, 1 GB heap, loopback only,
  security disabled (loopback test). Read model only — Postgres stays SoR.
  Projection: `~/central/es/project.py` (idempotent, obs_id == ES _id); templates in
  `~/central/es/`; Kibana data views for observations + models. Admin via
  `~/central/admin.sh {es|kibana|indices|project}`.

## Product / architecture direction (dictated, in progress)

Captured as stated; user still dictating.

- **Scheduled scans, user-configurable**: the app scans whatever the user wants to
  look at on a user-set schedule.
- **Passive by default**: out of the box it also does passive scans of local
  Bluetooth, local WiFi networks, and neighbours on any connected WiFi.
- **Default cadence**: runs every **ten minutes**.
- **Local database**: results stored locally, building **timelines for every
  recorded device**.
- **Crowdsourced shared DB**: local DB updates are sent up to a **central DB**;
  **all users have read access** to it and **can query whatever they like in
  return for sharing their own data** — a data-sharing co-op.



