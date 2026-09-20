#!/bin/bash
# devicedb central service — one-shot deploy script.
# Reproduces the working stack on a clean Debian/Kali box:
#   Postgres 18 (system of record) + FastAPI service + Elasticsearch + Kibana (read model)
#
# Usage:
#   ./deploy.sh                 # full deploy (idempotent; safe to re-run)
#   ./deploy.sh --no-es         # skip Elasticsearch + Kibana (API + Postgres only)
#   ./deploy.sh --no-api        # skip the FastAPI service (Postgres + ES only)
#   ./deploy.sh --no-start      # install/configure but do not start anything
#   ./deploy.sh --help
#
# Idempotent: safe to re-run; already-done steps are skipped.
set -u

# ---- config (override with env vars) -------------------------------------
DB_USER="${DB_USER:-devicedb}"
DB_PASS="${DB_PASS:-devicedb}"
DB_NAME="${DB_NAME:-devicedb_central}"
API_PORT="${API_PORT:-8090}"
ES_PORT="${ES_PORT:-9200}"
KB_PORT="${KB_PORT:-5601}"
PSEUDO_SECRET="${PSEUDO_SECRET:-test-secret-please-rotate}"
ES_VERSION="${ES_VERSION:-9.x}"
HEAP_MB="${HEAP_MB:-1024}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"          # where this script + schema/server live
# Debian sudo resets $HOME to the target user's home, so derive the app dir from the
# INVOKING user, not $HOME — otherwise a `sudo ./deploy.sh` installs into /root/central.
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
APP_DIR="${APP_DIR:-$REAL_HOME/central}"          # where the service is installed

DO_ES=1; DO_API=1; DO_START=1
for a in "$@"; do
  case "$a" in
    --no-es)   DO_ES=0 ;;
    --no-api)  DO_API=0 ;;
    --no-start) DO_START=0 ;;
    --help|-h) sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a"; exit 1 ;;
  esac
done

log()  { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m %s\n' "$*"; }
skip() { printf '  \033[33mskip\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

# ---- preflight ------------------------------------------------------------
log "preflight"
[ "$(id -u)" = 0 ] || die "run as root (sudo $0)"
command -v apt-get >/dev/null || die "apt-get not found (Debian/Kali required)"
command -v curl >/dev/null || { apt-get install -y -qq curl >/dev/null 2>&1; }
command -v python3 >/dev/null || die "python3 not found"
free -g | awk 'NR==2 && $7 < 2 { print "  WARN: <2GB RAM available" }'
df -h / | awk 'NR==2 && $4+0 < 2 { print "  WARN: <2GB disk free" }'

# ---- Phase 1: Postgres ----------------------------------------------------
log "Postgres (system of record)"
if ! command -v psql >/dev/null; then
  apt-get install -y -qq postgresql || die "postgres install failed"
  ok "installed postgresql"
else
  skip "postgresql already installed"
fi
if ! pg_lsclusters 2>/dev/null | grep -q online; then
  sudo pg_ctlcluster 18 main start 2>/dev/null || systemctl start postgresql
  ok "started postgres"
else
  skip "postgres already online"
fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';" \
  && ok "role $DB_USER ready"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" \
  && ok "database $DB_NAME ready"

# ---- Phase 2: FastAPI service ---------------------------------------------
if [ "$DO_API" = 1 ]; then
  log "FastAPI central service"
  mkdir -p "$APP_DIR"
  # copy schema + server from the deploy dir if present, else expect them in APP_DIR
  for f in schema.sql server.py; do
    if [ -f "$SRC_DIR/$f" ]; then cp "$SRC_DIR/$f" "$APP_DIR/$f"; fi
  done
  [ -f "$APP_DIR/schema.sql" ] || die "schema.sql missing (put it next to deploy.sh or in $APP_DIR)"
  [ -f "$APP_DIR/server.py" ]  || die "server.py missing (put it next to deploy.sh or in $APP_DIR)"

  # copy the ES read-model files (templates + projector) if present
  if [ -d "$SRC_DIR/es" ]; then
    mkdir -p "$APP_DIR/es"
    cp "$SRC_DIR"/es/* "$APP_DIR/es/" 2>/dev/null
  fi

  if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv" || die "venv create failed"
    "$APP_DIR/venv/bin/pip" install -q fastapi "uvicorn[standard]" "psycopg[binary]" \
      || die "pip install failed"
    ok "venv + deps ready"
  else
    skip "venv already present"
  fi

  PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" \
    -v ON_ERROR_STOP=1 -f "$APP_DIR/schema.sql" >/dev/null 2>&1 \
    && ok "schema applied" || skip "schema already applied (or errored — check manually)"
fi

# ---- Phase 3: Elasticsearch + Kibana --------------------------------------
if [ "$DO_ES" = 1 ]; then
  log "Elasticsearch + Kibana (read model)"
    if [ ! -x /usr/share/elasticsearch/bin/elasticsearch ]; then
      apt-get install -y -qq apt-transport-https ca-certificates curl gnupg >/dev/null 2>&1
      rm -f /usr/share/keyrings/elasticsearch-keyring.gpg
      curl -fsSL https://artifacts.elastic.co/GPG-KEY-elasticsearch \
        | gpg --batch --yes --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg \
        || die "failed to add Elastic GPG key"
      echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/$ES_VERSION/apt stable main" \
        > /etc/apt/sources.list.d/elastic-$ES_VERSION.list
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq elasticsearch kibana \
        || die "elasticsearch/kibana install failed"
      ok "installed elasticsearch + kibana"
    else
      skip "elasticsearch already installed"
    fi

  # ES config
  cat > /etc/elasticsearch/elasticsearch.yml <<EOF
cluster.name: devicedb
node.name: devicedb-es01
discovery.type: single-node
network.host: 127.0.0.1
http.port: $ES_PORT
xpack.security.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
xpack.security.enrollment.enabled: false
EOF
  mkdir -p /etc/elasticsearch/jvm.options.d
  printf -- "-Xms%sm\n-Xmx%sm\n" "$HEAP_MB" "$HEAP_MB" > /etc/elasticsearch/jvm.options.d/heap.options
  ok "elasticsearch configured (loopback, heap ${HEAP_MB}MB, security off)"

  # Kibana config
  KB_KEY=$(openssl rand -hex 32 2>/dev/null || echo "0000000000000000000000000000000000000000000000000000000000000000")
  cat > /etc/kibana/kibana.yml <<EOF
server.host: "127.0.0.1"
server.port: $KB_PORT
server.name: "devicedb-kibana"
elasticsearch.hosts: ["http://127.0.0.1:$ES_PORT"]
xpack.encryptedSavedObjects.encryptionKey: "$KB_KEY"
EOF
  ok "kibana configured (loopback, port $KB_PORT)"
fi

# ---- Phase 4: start everything --------------------------------------------
if [ "$DO_START" = 1 ]; then
  log "starting services"
  if [ "$DO_ES" = 1 ]; then
    systemctl daemon-reload
    systemctl enable --now elasticsearch >/dev/null 2>&1 && ok "elasticsearch enabled+started"
    systemctl enable --now kibana >/dev/null 2>&1 && ok "kibana enabled+started"
  fi
  if [ "$DO_API" = 1 ]; then
    # stop any previous instance
    [ -f "$APP_DIR/server.pid" ] && kill "$(cat "$APP_DIR/server.pid")" 2>/dev/null
    cd "$APP_DIR"
    PSEUDO_SECRET="$PSEUDO_SECRET" nohup ./venv/bin/uvicorn server:app \
      --host 127.0.0.1 --port "$API_PORT" >> "$APP_DIR/server.log" 2>&1 &
    echo $! > "$APP_DIR/server.pid"
    ok "api started (pid $(cat "$APP_DIR/server.pid"), port $API_PORT)"
  fi
fi

# ---- Phase 4.5: ES read model (templates + projection) ---------------------
if [ "$DO_ES" = 1 ] && [ "$DO_API" = 1 ] && [ "$DO_START" = 1 ]; then
  log "ES read model (templates + projection)"
  if [ -x "$APP_DIR/es/setup.sh" ]; then
    "$APP_DIR/es/setup.sh" >/dev/null 2>&1 && ok "index templates applied" || skip "templates (check manually)"
  fi
  if [ -f "$APP_DIR/es/project.py" ]; then
    "$APP_DIR/venv/bin/python" "$APP_DIR/es/project.py" --full >/dev/null 2>&1 \
      && ok "projection run" || skip "projection (check manually)"
  fi
fi

# ---- Phase 5: verify -------------------------------------------------------
log "verification"
if [ "$DO_API" = 1 ]; then
  sleep 3
  echo -n "  api health:  "
  curl -s --max-time 5 "http://127.0.0.1:$API_PORT/health" || echo "(no response)"
  echo
fi
if [ "$DO_ES" = 1 ]; then
  # ES/Kibana can take a few seconds to come up after a fresh start — retry.
  es_up=""; kb_up=""
  for i in $(seq 1 12); do
    [ -z "$es_up" ] && es_up=$(curl -s --max-time 3 "http://127.0.0.1:$ES_PORT/_cluster/health" 2>/dev/null | grep -o '"status":"[a-z]*"' | head -1)
    [ -z "$kb_up" ] && kb_up=$(curl -s --max-time 3 "http://127.0.0.1:$KB_PORT/api/status" 2>/dev/null | grep -o '"level":"[a-z]*"' | head -1)
    [ -n "$es_up" ] && [ -n "$kb_up" ] && break
    sleep 5
  done
  echo "  elastic:     ${es_up:-no response}"
  echo "  kibana:      ${kb_up:-no response}"
fi

log "done"
echo "  API:        http://127.0.0.1:$API_PORT"
echo "  Elastic:    http://127.0.0.1:$ES_PORT"
echo "  Kibana:     http://127.0.0.1:$KB_PORT"
echo "  Postgres:   postgresql://$DB_USER@127.0.0.1:5432/$DB_NAME"
echo "  App dir:    $APP_DIR"
echo "  Log:        $APP_DIR/server.log"
echo
echo "  NOTE: PSEUDO_SECRET is '$PSEUDO_SECRET' — change it before exposing beyond loopback."
echo "  NOTE: ES security is DISABLED (loopback test build)."
