# central — build notes (append-only)

## 2026-09-20 — role change: ES-first ingest, Postgres parked

- **Decision (Jack):** devicedb now writes observations DIRECTLY to
  Elasticsearch. Postgres + the FastAPI server are no longer in the ingest
  path. Device/location histories are built later in post-processing.
- **Live now:** Elasticsearch 9.5.4 (:9200) + Kibana (:5601). Index
  `devicedb-obs-000001` receives devicedb's data (bulk, _id = local obs_id).
- **Parked:** FastAPI server (:8090) + Postgres 18 — kept installed, not
  running. The `/v1/contribute` endpoint and Postgres schema remain for
  reference / future multi-client use, but nothing writes to them.
- **ES boot fix:** /usr/share/elasticsearch/{logs,data} were missing
  (AccessDenied on boot). Created + chowned to elasticsearch:elasticsearch.
- **Projection:** `es/project.py` (Postgres -> ES) is now unused for ingest;
  kept for reference. The projection cron was removed.
- **Future:** this is the natural home for the RF data project (ES index for
  RF captures + signal analysis), alongside the device data.

## 2026-09-20 — Kibana data views on the ES store

- Two Kibana data views now exist over index `devicedb-obs-000001`:
  `devicedb-obs` (timeFieldName `received_at`) and `devicedb-obs-observed`
  (timeFieldName `observed_at`). The old view broke because observed_at was
  naive UTC; devicedb now writes tz-aware timestamps. Kibana Discover time
  filtering works against both fields.
