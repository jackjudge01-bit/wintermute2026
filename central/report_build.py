#!/usr/bin/env python3
"""Build the project PDF report from a spec dict."""
import sys

sys.path.insert(0, "/path/to/pdf-skill/scripts")
from pdf_create import build_pdf  # noqa: E402

E = []
h = lambda t, lvl=1: E.append({"type": "heading", "text": t, "level": lvl})
p = lambda t: E.append({"type": "paragraph", "text": t})
tbl = lambda rows: E.append({"type": "table", "rows": rows, "header": True})
pb = lambda: E.append({"type": "pagebreak"})

h("Device Tracker — Project Report", 1)
p("Local agent, central service, and Elasticsearch read model.")
p("Host: wintermute (Kali Linux) &nbsp;|&nbsp; Date: 2026-09-17 &nbsp;|&nbsp; Status: working test system")

h("1. Summary", 2)
p("This project is a device-tracking system in two halves. The <b>local agent</b> runs on this "
  "laptop, sweeps the surrounding radio environment on a schedule, and records every device it "
  "encounters into a device-centric local database with per-device movement history. The "
  "<b>central service</b> is a shared server that serves a device/firmware capability knowledge "
  "base and receives opt-in contributed data from clients.")
p("The guiding model: <b>clients are the sole initiators</b>. The central service never "
  "distributes scan targets and never initiates anything; it is a data service. Contribution is "
  "optional. Identifiers are pseudonymized and location coarsened at the edge, before data moves.")
p("Both halves were built and verified in this session. The local agent was already in "
  "production (cron-driven); today it gained device-centric MAC clustering. The central service, "
  "its Postgres schema, the CQRS projector and the Elasticsearch + Kibana read model were built "
  "from scratch and are running now.")

h("2. Status at a glance", 2)
tbl([
    ["Component", "What it is", "Where", "State"],
    ["Local agent", "devicedb.py, SQLite, cron every 10 min", "~/devicedb", "running"],
    ["Central API", "FastAPI service, port 8090", "~/central/server.py", "running"],
    ["System of record", "Postgres 18, database devicedb_central, port 5432", "systemd/system", "online"],
    ["Read model", "Elasticsearch 9.5.4, port 9200", "systemd, enabled", "green"],
    ["Read model UI", "Kibana 9.5.4, port 5601", "systemd, enabled", "up"],
])

pb()

h("3. The local agent (device-centric)", 2)
p("The core problem: <b>MAC addresses are not device identity.</b> BLE devices rotate their MAC "
  "roughly every 15 minutes, and WiFi clients get a new MAC every time they roam. If you key on "
  "MAC, one physical device becomes hundreds of rows and its history is unusable.")
p("The implemented model keys on a synthetic <b>device_id</b> instead:")
tbl([
    ["Table", "Role"],
    ["devices", "one row per device cluster: synthetic device_id, representative MAC, name, vendor, kinds, first/last seen, location"],
    ["identities", "observed MAC to device_id alias table, with the fingerprint evidence that justified the join. UNIQUE(device_id, mac)"],
    ["observations", "append-only raw sightings; carries device_id so every sighting links to its cluster. Never updated"],
    ["runs", "one row per sweep, with location"],
    ["services", "open ports per IP"],
    ["v_inventory", "flat human-readable view with obs_count, alias_count, port_count"],
])
p("<b>Clustering is deliberately conservative</b> so it does not over-merge. The rules, in order: "
  "(1) if the MAC is already a known alias, reuse that cluster; (2) same name AND same "
  "manufacturer-data means merge; (3) same name owned by exactly one device means merge, but if "
  "the name is ambiguous, do NOT merge and create a new cluster. Rule 3's ambiguity guard exists "
  "because names collide - three speakers can all advertise the same model name and be three "
  "different physical devices.")
p("<b>Verified by test:</b> a new MAC with the same device name was fed to the clusterer; it "
  "resolved into the existing device (aliases went 1 to 2) instead of creating a new row. Case "
  "differences in the MAC hash identically, and the raw MAC is stored nowhere.")
p("Location is resolved once per sweep: GPS via gpsd if present, otherwise the public IP through "
  "a geo-IP lookup. Offline means NULL rather than a failed sweep. Current local state: "
  "2,797 devices, 2,477 identities, 17,885 observations, 1,304 runs, sweeping every 10 minutes.")

h("4. Central service", 2)
h("4.1 Storage decision: Postgres is the system of record", 3)
p("Elasticsearch is the right tool here, but <b>not as the system of record</b>. The hardest "
  "operation in this system is identity resolution and merging - a new MAC matching an existing "
  "device cluster by fingerprint, then merging and reassigning aliases. That needs transactions, "
  "uniqueness constraints, and idempotent upserts. Elasticsearch has no unique constraints, no "
  "transactions, and an update is a full document reindex, so merges churn the index. The core "
  "shape (devices to identities to observations, fingerprints to device_models) is relational, "
  "and ES has no joins.")
p("So the split is: <b>Postgres = system of record</b> (registry, identity resolution, capability "
  "KB, fingerprints, contributor ledger); <b>Elasticsearch = read model</b>, a denormalized "
  "projection for search, aggregation and geo queries, fed from Postgres. This is CQRS.")
p("License note worth an explicit decision: Elasticsearch 8.16 and later is AGPLv3 again (after "
  "the SSPL / Elastic-2.0 detour), while Kibana is under the Elastic License 2.0. OpenSearch is "
  "Apache-2.0 and a drop-in if license entanglement is a concern for a commercial service.")

h("4.2 Schema (draft)", 3)
tbl([
    ["Table", "Role"],
    ["contributors", "client agents; provenance anchor; trust_score; public_key for signing"],
    ["device_models", "capability KB: vendor, model, class, capabilities JSONB, confidence, sources"],
    ["fingerprints", "fingerprint string to model plus confidence and votes (Fingerbank-shaped)"],
    ["devices", "device clusters (central registry), optional link to a model"],
    ["identities", "pseudonymized MAC to device_id, UNIQUE(mac_hash, salt_epoch)"],
    ["contributions", "ledger of every upload: contributor, count, payload hash, signature, status"],
    ["observations", "append-only sightings, provenance and coarsened location, linked device_id"],
])

h("4.3 Privacy built into the schema", 3)
p("Pseudonymization happens at ingest: mac_hash = HMAC(server_secret, epoch + mac). It is "
  "deterministic within a salt epoch so the same MAC links across contributors - which is the "
  "entire point - and irreversible without the server secret. Raw MACs never reach the database. "
  "Location is coarsened by rounding before storage; raw GPS never lands centrally. Every "
  "observation carries contributor and contribution provenance, and every upload is hashed and "
  "can be signed.")
p("The honest tradeoff, and an open decision: rotating the salt epoch deliberately breaks "
  "cross-contributor linking. That is a privacy-versus-utility dial that has to be chosen on "
  "purpose.")

h("4.4 Endpoints", 3)
tbl([
    ["Method and path", "Purpose"],
    ["GET /health", "liveness plus observation count"],
    ["GET /stats", "process RSS, threads, CPU time, uptime, DB size, row counts"],
    ["POST /v1/contributors", "register a contributor"],
    ["POST /v1/contribute", "upload a batch of observations (provenance-tagged)"],
    ["GET /v1/fingerprint", "resolve a fingerprint to model plus confidence (the Ring-doorbell flow)"],
    ["POST /v1/fingerprints", "contribute fingerprint to model knowledge"],
    ["GET /v1/models/{model_id}", "capability KB lookup"],
    ["GET /v1/search", "query observations (placeholder for the ES-backed search)"],
])

h("4.5 Verified behaviour", 3)
tbl([
    ["Check", "Result"],
    ["Same MAC in different case", "identical mac_hash, one device cluster, raw MAC absent from DB"],
    ["GPS 49.18361234, -123.11645678", "stored as 49.184, -123.116 (coarsened to about 110 m)"],
    ["Known fingerprint", "resolved to device model with full capability set"],
    ["Unknown fingerprint", "returned known:false - the needs-research path"],
    ["Contribution ledger", "row written with payload SHA-256 and signature"],
])

pb()

h("5. Identity research: fingerprint resolution", 2)
p("The mechanism for 'a client sees an unknown device, pulls what we know from central, and if it "
  "is unknown everywhere, researches it and stores it centrally' already exists as a product "
  "category: a <b>device-fingerprint resolution service</b>. The canonical implementation is "
  "<b>Fingerbank</b> - a client posts a fingerprint (DHCP option 55 list, DHCP vendor class, user "
  "agents) and receives a device name, a confidence <b>score</b>, and a device hierarchy. Unknown "
  "fingerprints are submitted by clients and become known for everyone. About 110,000 device "
  "models and 6 million fingerprints.")
p("Design ideas worth copying directly: key on a <b>fingerprint string</b>, not a device ID; "
  "return a <b>confidence score</b>, never a single definitive answer; and return a hierarchy "
  "(OS, vendor OS, model).")
p("Terminology note: 'first-seen identifier' is not a standard term. It is really two things - "
  "first-seen registration (the bookkeeping convention of timestamping when an identity first "
  "appears) and fingerprint resolution (the actual identification step). Keeping them separate "
  "clarifies the design.")
p("<b>The critical constraint for this project:</b> Fingerbank's strongest signals are LAN-side "
  "(DHCP options, mDNS, UPnP) and this system does not capture them - our sensors are BLE "
  "advertisements and WiFi frames. Over the air, the useful signals are the IEEE OUI (MAC prefix "
  "to vendor), BLE manufacturer data and service UUIDs, WiFi information elements, and - only in "
  "monitor mode - DNS queries and TLS SNI, which reveal the cloud hostnames a device talks to. "
  "That SNI signal is the single most valuable over-the-air identification signal, and it is why "
  "monitor-mode WiFi capture is on the critical path rather than a nice-to-have.")

h("6. Elasticsearch + Kibana read model", 2)
p("Installed natively through Elastic's apt repository - <b>no containers</b>, as required. "
  "Elasticsearch 9.5.4 and Kibana 9.5.4, both systemd units and enabled, so they survive reboot.")
tbl([
    ["Setting", "Value"],
    ["Elasticsearch", "9.5.4, http://127.0.0.1:9200, cluster devicedb, single node"],
    ["Kibana", "9.5.4, http://127.0.0.1:5601"],
    ["Heap", "pinned to 1 GB (jvm.options.d/heap.options) - the box also runs ComfyUI"],
    ["Binding", "loopback only"],
    ["Security", "disabled (xpack.security.enabled: false) - loopback test only"],
    ["Shipped configs", "backed up alongside as .orig"],
])

h("6.1 Mappings", 3)
p("Two index templates define the read model: <b>devicedb-obs-*</b> for the observation "
  "projection and <b>devicedb-models-*</b> for the capability knowledge base. The observation "
  "mapping includes a real <b>geo_point</b> for location, a <b>flattened</b> field for arbitrary "
  "per-signal extras, dual keyword-plus-text on vendor and name, and explicit date fields. Kibana "
  "data views were created for both, with observed_at as the time field.")

h("6.2 The projection (CQRS)", 3)
p("The projector copies Postgres to Elasticsearch. It uses the ES bulk <b>index</b> action with an "
  "explicit document id, which is ES's create-or-replace - so re-running a projection overwrites "
  "a document rather than duplicating it. That makes the projection idempotent and safe to re-run "
  "at any time.")
p("Runs are <b>incremental</b> by default. A watermark of the last projected obs_id and "
  "model updated_at is stored beside the script, so each run only touches rows added or changed "
  "since the previous one. The <b>--full</b> flag ignores the watermark and rebuilds everything.")
tbl([
    ["Run", "Result"],
    ["project --full", "indexed 2 observations, 1 model; watermark set to obs_id 2"],
    ["project (again, nothing changed)", "indexed 0 observations, 0 models"],
    ["new observation via API, then project", "indexed exactly 1 observation; watermark to obs_id 3"],
])
p("<b>Refresh semantics, worth knowing:</b> the bulk uses refresh=false, so a freshly indexed "
  "document lands in the translog and is not <i>searchable</i> until a refresh happens. The "
  "template sets refresh_interval to 5 seconds, so it appears almost immediately; an explicit "
  "refresh is available via the --refresh flag. Refreshing on every bulk would destroy "
  "throughput, so this is deliberate.")
p("<b>Documented tradeoff:</b> the observation document denormalizes fields joined from the "
  "devices table (device_class, model_id). If a device row changes later, already-projected "
  "observations are not refreshed automatically - the watermark only advances on observations. "
  "Correcting that needs a --full run or an update-by-query. That is the real cost of "
  "denormalizing the join, and it is the thing to revisit as the corpus grows.")

pb()

h("7. Administration", 2)
p("One wrapper manages everything: <b>~/central/admin.sh</b>.")
tbl([
    ["Command", "Purpose"],
    ["admin.sh status", "API, port, health, Postgres, Elasticsearch, Kibana at a glance"],
    ["admin.sh start | stop | restart", "the API service"],
    ["admin.sh logs [n]", "tail the API log"],
    ["admin.sh stats", "process resource usage, ES indices, database table sizes"],
    ["admin.sh es {status|start|stop|restart}", "Elasticsearch service"],
    ["admin.sh kibana {status|start|stop|restart}", "Kibana service"],
    ["admin.sh indices", "ES index list and document counts"],
    ["admin.sh project", "re-run the Postgres to ES projection"],
    ["admin.sh db | sql | backup", "interactive psql, one-off SQL, pg_dump backup"],
])
p("<b>Tools installed on the box:</b> htop, top, ps, lsof, ss, systemd-cgtop, journalctl, "
  "sqlite3, psql, pg_dump and pg_restore. <b>Not installed:</b> nvtop (GPU), btop or glances, "
  "netdata or cockpit (web dashboards), and the Postgres tooling pg_top, pg_activity, pgbadger "
  "and pgadmin4. Kibana itself now serves as the visualisation layer.")

h("8. Decisions locked in", 2)
tbl([
    ["Decision", "Rationale"],
    ["MAC is not identity", "MACs rotate; clusters are keyed on synthetic device_id via the identities alias table"],
    ["Conservative clustering", "MAC alias, then name plus manufacturer-data, then unambiguous name only - avoids over-merging"],
    ["Postgres is the system of record", "identity merging needs transactions, uniqueness and joins, which ES lacks"],
    ["Elasticsearch is a read model", "search, aggregation and geo; fed by a CQRS projection, never written as truth"],
    ["Pseudonymize and coarsen at the edge", "raw MACs and raw GPS never leave the client"],
    ["Provenance on every record", "contributor plus contribution ledger row"],
    ["Clients initiate; central never distributes targets", "keeps the system a data service, not a control plane"],
    ["Linux only", "the Android target is parked"],
])

h("9. Open questions (before the schema freezes)", 2)
p("1. <b>Pseudonymization rotation scheme</b> - rotating the salt epoch breaks cross-contributor "
  "linking; that tradeoff must be chosen deliberately. This is the decision that most reshapes "
  "the schema.")
p("2. <b>Per-sensor field sets</b> - exactly which fields each sensor emits, and what stays in the "
  "JSONB extras.")
p("3. <b>Query patterns</b> - what users actually search for, which decides ES mappings and index "
  "design.")
p("4. <b>Upload granularity and retention</b> - raw sightings versus summaries, and a retention "
  "policy.")
p("5. <b>Access model</b> - what sharing buys. Since sharing is optional, read access cannot be "
  "gated on it, so the incentive must be a carrot: richer or earlier access, a contributor score, "
  "reciprocal depth.")
p("6. <b>License</b> - AGPLv3 Elasticsearch versus Apache-2.0 OpenSearch.")
p("7. <b>Distributed topology</b> - one hub or federated hubs, conflict resolution, and how much "
  "the nodes are distrusted.")

h("10. Findings and fixes from this session", 2)
tbl([
    ["Finding", "Resolution"],
    ["pkill -f on 'uvicorn server:app' also matched the calling shell and killed it",
     "admin.sh now uses a PID file for start and stop instead of pattern matching"],
    ["The projector re-read and re-indexed the entire table on every run",
     "replaced with a watermark-driven incremental upsert; --full rebuilds on demand"],
    ["The central test server was squatting port 8080, which Open WebUI uses",
     "moved to port 8090; 8080 left free"],
    ["Bulk indexing with refresh=false made a new document non-searchable immediately",
     "documented as expected behaviour; template refresh_interval is 5 s"],
    ["The migration populated identities but not their names, so name-based clustering silently found nothing",
     "migration now copies the device name and backfills; verified by test"],
])

h("11. File map", 2)
tbl([
    ["Path", "Contents"],
    ["~/devicedb/devicedb.py", "the local agent (single file, stdlib plus sqlite3)"],
    ["~/devicedb/NOTES.md", "running notes: direction, product model, research, open questions"],
    ["~/devicedb/CHECKPOINT.md", "the resume document - state, restart commands, decisions"],
    ["~/central/server.py", "the FastAPI central service"],
    ["~/central/schema.sql", "Postgres schema (draft)"],
    ["~/central/DESIGN.md", "central service design, storage decision, endpoints"],
    ["~/central/ADMIN.md", "administration guide"],
    ["~/central/admin.sh", "the admin wrapper"],
    ["~/central/es/project.py", "the CQRS projector (incremental)"],
    ["~/central/es/setup.sh", "applies ES index templates"],
    ["~/central/es/*-template.json", "ES index mappings"],
    ["~/checkpoints/device-tracker-2026-09-17/", "frozen snapshot, DB dumps, tarball"],
])

h("12. Quick reference", 2)
p("<b>Local agent:</b> python3 ~/devicedb/devicedb.py sweep | stats | cluster")
p("<b>Central service:</b> ~/central/admin.sh status | start | stop | stats | project")
p("<b>Elasticsearch:</b> http://127.0.0.1:9200 &nbsp;&nbsp; <b>Kibana:</b> http://127.0.0.1:5601")
p("<b>Postgres:</b> postgresql://devicedb@127.0.0.1:5432/devicedb_central &nbsp;&nbsp; "
  "<b>API:</b> http://127.0.0.1:8090")
p("<b>Note:</b> PSEUDO_SECRET is still the literal test string and ES security is disabled. Both "
  "must be changed before this is exposed beyond loopback.")

spec = {
    "title": "Device Tracker — Project Report",
    "author": "Vesper (on wintermute)",
    "page_size": "A4",
    "page_numbers": True,
    "elements": E,
}

out = "/path/to/central/device-tracker-report.pdf"
rc = build_pdf(spec, out)
print("build_pdf rc:", rc)
print("out:", out)
