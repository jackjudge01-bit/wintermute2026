#!/bin/bash
# devicedb ES read model — apply index templates and verify.
# Run once Elasticsearch is up:  ./setup.sh
set -u
ES="${ES_URL:-http://127.0.0.1:9200}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== cluster health ==="
curl -s "$ES/_cluster/health?pretty" || { echo "ES not reachable at $ES"; exit 1; }

echo
echo "=== apply index templates ==="
curl -s -X PUT "$ES/_index_template/devicedb-obs" \
  -H 'Content-Type: application/json' \
  --data-binary "@$DIR/observations-template.json" | head -c 200; echo
curl -s -X PUT "$ES/_index_template/devicedb-models" \
  -H 'Content-Type: application/json' \
  --data-binary "@$DIR/models-template.json" | head -c 200; echo

echo
echo "=== create initial indices ==="
curl -s -X PUT "$ES/devicedb-obs-000001" | head -c 200; echo
curl -s -X PUT "$ES/devicedb-models-000001" | head -c 200; echo

echo
echo "=== verify mappings ==="
curl -s "$ES/devicedb-obs-000001/_mapping?pretty" | head -40

echo
echo "=== indices ==="
curl -s "$ES/_cat/indices?v&h=index,health,docs.count,store.size"
