# Administering the central test server

Port **8090**. Postgres 18 on 5432. Everything below is run as `jack`.

## The admin tool

```
~/central/admin.sh status          # is it up? port? health? postgres?
~/central/admin.sh start           # start server (+ postgres if down)
~/central/admin.sh stop            # stop server
~/central/admin.sh restart         # stop + start
~/central/admin.sh logs [n]        # last n lines of the server log (default 40)
~/central/admin.sh stats           # resource usage: api + ES indices + database
~/central/admin.sh db              # interactive psql shell
~/central/admin.sh sql "SELECT ..."  # one-off SQL
~/central/admin.sh backup          # pg_dump to ~/central/backups/
~/central/admin.sh es {status|start|stop|restart}      # Elasticsearch service
~/central/admin.sh kibana {status|start|stop|restart}  # Kibana service
~/central/admin.sh indices         # ES index list + doc counts
~/central/admin.sh project         # re-run Postgres -> ES projection
```

Start/stop use a **PID file** (`~/central/server.pid`), not `pkill -f`. Pattern
matching on "uvicorn server:app" also matches any shell whose command line mentions
it — which kills the caller.

Typical check:

```
$ ~/central/admin.sh status
server:   RUNNING (pid 904537 )
port 8090: listening
health:   {"status":"ok","observations":2,"connections":1}
postgres: online
```

## The two layers

| layer    | what                        | how it starts                                  |
|----------|-----------------------------|------------------------------------------------|
| app      | `uvicorn server:app` :8090  | `admin.sh start` (nohup, logs to server.log)   |
| database | postgres 18 on 5432         | `sudo pg_ctlcluster 18 main start`             |

Note: port 8080 belongs to **Open WebUI** — don't take it. The test server is on 8090.

## Configuration

Set these env vars before starting (all optional except the secret):

```
CENTRAL_DB_URL      default postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central
PSEUDO_SECRET       HMAC key for MAC pseudonymization  ← MUST be a real secret in prod
SALT_EPOCH          pseudonymization epoch (int, default 1)
LOCATION_DECIMALS   GPS rounding, default 3 (≈110 m)
CENTRAL_PORT        port admin.sh manages, default 8090
```

## Logs

```
~/central/admin.sh logs          # tail
tail -f ~/central/server.log     # follow live
```

Every request the server handles is logged there (uvicorn access log).

## Database

```
# interactive
~/central/admin.sh db

# useful queries
~/central/admin.sh sql "SELECT count(*) FROM observations;"
~/central/admin.sh sql "SELECT handle, trust_score FROM contributors;"
~/central/admin.sh sql "SELECT fingerprint, signal_type, confidence FROM fingerprints;"
~/central/admin.sh sql "SELECT vendor, model_name, capabilities FROM device_models;"

# backup / restore
~/central/admin.sh backup
PGPASSWORD=CHANGE_ME psql -h 127.0.0.1 -U devicedb -d devicedb_central < backups/central-TIMESTAMP.sql
```

## API (what the server exposes)

```
GET  /health
GET  /stats                        resource usage + row counts (self-reported)
POST /v1/contributors              register a contributor
POST /v1/contribute                upload a batch of observations
GET  /v1/fingerprint?fp=&signal=   resolve fingerprint -> model + confidence
POST /v1/fingerprints              contribute fingerprint -> model knowledge
GET  /v1/models/{model_id}         capability KB lookup
GET  /v1/search                    query observations
```

`/stats` reports this process's RSS, thread count, CPU time, uptime, plus the
database size and per-table row counts — the same numbers as `admin.sh stats`, from
the service's own point of view.

## Tools available on this box

Installed and useful here:

```
htop              process/resource TUI            htop
top, ps           process view                    ps aux --sort=-%mem | head
lsof              open files / sockets            lsof -p <pid>
ss                listening sockets / connections ss -ltnp | grep 8090
systemd-cgtop     per-cgroup resource usage       systemd-cgtop
journalctl        system logs                     journalctl -f
sqlite3           local agent DB (inventory.db)   sqlite3 ~/devicedb/inventory.db
psql              postgres                        admin.sh db
pg_dump/pg_restore   backup / restore             admin.sh backup
```

**Not installed** (would need `apt install`): `nvtop` (GPU), `btop`/`glances`
(nicer monitoring TUI), `netdata`/`cockpit` (web dashboards), `pg_top`/`pg_activity`
/`pgbadger`/`pgadmin4` (postgres tooling). The box has htop, lsof, ss, psql, and the
`admin.sh` wrapper — enough to run this by hand.

## Elasticsearch + Kibana (installed natively 2026-09-17)

Installed via Elastic's apt repo — **no containers**. Versions 9.5.4.

```
elasticsearch.service   :9200   systemd, enabled
kibana.service          :5601   systemd, enabled
Kibana UI               http://127.0.0.1:5601
```

Role in the design: **read model only.** Postgres stays the system of record; ES
receives a denormalized projection for search / aggregation / geo. Never write to
ES as truth.

Config:

```
/etc/elasticsearch/elasticsearch.yml   cluster devicedb, single-node, 127.0.0.1:9200
/etc/elasticsearch/jvm.options.d/heap.options   -Xms1g -Xmx1g  (box also runs ComfyUI)
/etc/kibana/kibana.yml                 server.host 127.0.0.1, ES at 127.0.0.1:9200
*.orig files next to them              the shipped configs, backed up
```

Access, indices and data views:

```
devicedb-obs-000001      observations projection   (time field: observed_at, geo_point: location)
devicedb-models-000001   device/firmware capability KB
Kibana data views        "devicedb observations", "devicedb device models"
```

Feeding ES — the projection (CQRS):

```
~/central/admin.sh project          # Postgres -> ES, idempotent (obs_id == ES _id)
~/central/es/project.py             # the projector
~/central/es/setup.sh               # applies index templates + creates indices
~/central/es/*-template.json        # mappings: geo_point, flattened extra, keyword/text
```

**Security is DISABLED** (`xpack.security.enabled: false`) because this is a
loopback-only test read model. The generated `elastic` superuser password is stored
at `/etc/elasticsearch/.elastic_superuser` (root, 600) for when security goes back
on. Do not expose :9200 or :5601 beyond loopback in this state.

## Not there yet (real gaps, before this is a real service)

- **No authentication or authorization.** Anything that can reach :8090 can write.
- **No systemd unit** — it's a nohup'd process, so it does not survive a reboot or
  restart itself on crash. (A `systemd --user` unit is the fix.)
- **No TLS** — plain HTTP on loopback.
- **`PSEUDO_SECRET` is the literal test string** — rotate before any real data.
- **No rate limiting, no request size caps, no CORS policy.**
- **Schema is still DRAFT** — see `DESIGN.md` open questions.
