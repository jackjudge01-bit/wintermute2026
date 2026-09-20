#!/bin/bash
# devicedb central test server — admin helper
# usage: ./admin.sh {status|start|stop|restart|logs [n]|stats|db|sql "..."|backup
#                    |es {status|start|stop|restart}|indices|project|kibana}
#
# Uses a PID file rather than pkill -f: pattern matching on "uvicorn server:app"
# also matches any shell whose command line mentions it, which kills the caller.
set -u

PORT="${CENTRAL_PORT:-8090}"
DIR="$HOME/central"
LOG="$DIR/server.log"
PIDFILE="$DIR/server.pid"
VENV="$DIR/venv"
ES_URL="http://127.0.0.1:9200"
KB_URL="http://127.0.0.1:5601"
DB_URL="postgresql://devicedb:devicedb@127.0.0.1:5432/devicedb_central"
export PSEUDO_SECRET="${PSEUDO_SECRET:-test-secret-please-rotate}"


running_pid() {
    [ -f "$PIDFILE" ] || return 1
    local p
    p=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [ -n "$p" ] || return 1
    kill -0 "$p" 2>/dev/null || return 1
    grep -qa "uvicorn" "/proc/$p/cmdline" 2>/dev/null || return 1
    echo "$p"
}


case "${1:-status}" in
  status)
    if pid=$(running_pid); then
      echo "api:      RUNNING (pid $pid)"
    else
      echo "api:      STOPPED"
    fi
    if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
      echo "port $PORT: listening"
    else
      echo "port $PORT: not listening"
    fi
    echo -n "health:   "
    curl -s --max-time 3 "http://127.0.0.1:$PORT/health" || echo "(no response)"
    echo
    if pg_lsclusters 2>/dev/null | grep -q online; then
      echo "postgres: online"
    else
      echo "postgres: DOWN  (start: sudo pg_ctlcluster 18 main start)"
    fi
    es_status=$(curl -s --max-time 3 "$ES_URL/_cluster/health" 2>/dev/null)
    if [ -n "$es_status" ]; then
      echo "elastic:  $(echo "$es_status" | grep -o '"status":"[a-z]*"' | head -1)"
    else
      echo "elastic:  DOWN  (start: sudo systemctl start elasticsearch)"
    fi
    if curl -fsS --max-time 3 "$KB_URL/api/status" >/dev/null 2>&1; then
      echo "kibana:   up   ($KB_URL)"
    else
      echo "kibana:   DOWN  (start: sudo systemctl start kibana)"
    fi
    ;;

  start)
    if running_pid >/dev/null; then
      echo "already running (pid $(running_pid))"; exit 0
    fi
    pg_lsclusters 2>/dev/null | grep -q online || sudo pg_ctlcluster 18 main start
    cd "$DIR" || exit 1
    nohup "$VENV/bin/uvicorn" server:app --host 127.0.0.1 --port "$PORT" >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "started pid $(cat "$PIDFILE")  (log: $LOG)"
    sleep 3
    curl -s --max-time 3 "http://127.0.0.1:$PORT/health" || echo "(no health yet — check: $0 logs)"
    echo
    ;;

  stop)
    if pid=$(running_pid); then
      kill "$pid" && echo "stopped pid $pid"
      sleep 1
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    else
      echo "not running"
    fi
    rm -f "$PIDFILE"
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  logs)
    tail -n "${2:-40}" "$LOG"
    ;;

  stats)
    echo -n "health: "
    curl -s --max-time 3 "http://127.0.0.1:$PORT/health" || echo "(no response)"
    echo
    echo "--- service resource usage (api) ---"
    if pid=$(running_pid); then
      ps -o pid,%cpu,%mem,rss,vsz,etime,cmd -p "$pid"
      echo "(rss/vsz in KB)"
    else
      echo "api not running"
    fi
    echo
    echo "--- elasticsearch ---"
    curl -s "$ES_URL/_cluster/health?pretty" 2>/dev/null | grep -E '"status"|"number_of_nodes"' || echo "not reachable"
    curl -s "$ES_URL/_cat/indices/devicedb-*?v&h=index,docs.count,store.size" 2>/dev/null
    echo
    echo "--- database ---"
    PGPASSWORD=CHANGE_ME psql -h 127.0.0.1 -U devicedb -d devicedb_central -c "
      SELECT relname AS table, n_live_tup AS rows,
             pg_size_pretty(pg_total_relation_size(relid)) AS size
        FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;" 2>/dev/null
    PGPASSWORD=CHANGE_ME psql -h 127.0.0.1 -U devicedb -d devicedb_central -tAc \
      "SELECT 'database total: '||pg_size_pretty(pg_database_size('devicedb_central'));" 2>/dev/null
    ;;

  es)
    case "${2:-status}" in
      status)
        curl -s "$ES_URL/_cluster/health?pretty" | head -8
        curl -s "$ES_URL/_cat/indices?v&h=index,health,docs.count,store.size"
        ;;
      start)   sudo systemctl start elasticsearch  && echo "elasticsearch started" ;;
      stop)    sudo systemctl stop elasticsearch   && echo "elasticsearch stopped" ;;
      restart) sudo systemctl restart elasticsearch && echo "elasticsearch restarted" ;;
      *) echo "usage: $0 es {status|start|stop|restart}" ;;
    esac
    ;;

  kibana)
    case "${2:-status}" in
      status)  curl -s "$KB_URL/api/status" | head -c 200; echo ;;
      start)   sudo systemctl start kibana && echo "kibana started" ;;
      stop)    sudo systemctl stop kibana  && echo "kibana stopped" ;;
      restart) sudo systemctl restart kibana && echo "kibana restarted" ;;
      *) echo "usage: $0 kibana {status|start|stop|restart}" ;;
    esac
    ;;

  indices)
    curl -s "$ES_URL/_cat/indices?v&h=index,health,status,docs.count,store.size"
    ;;

  project)
    "$VENV/bin/python" "$DIR/es/project.py"
    ;;

  db)
    PGPASSWORD=CHANGE_ME psql "$DB_URL"
    ;;

  sql)
    shift
    PGPASSWORD=CHANGE_ME psql -h 127.0.0.1 -U devicedb -d devicedb_central -c "$*"
    ;;

  backup)
    mkdir -p "$DIR/backups"
    OUT="$DIR/backups/central-$(date +%Y%m%d-%H%M%S).sql"
    PGPASSWORD=CHANGE_ME pg_dump -h 127.0.0.1 -U devicedb devicedb_central > "$OUT"
    echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
    ;;

  *)
    echo "usage: $0 {status|start|stop|restart|logs [n]|stats|db|sql \"...\"|backup"
    echo "          |es {status|start|stop|restart}|kibana {status|start|stop}|indices|project}"
    ;;
esac
