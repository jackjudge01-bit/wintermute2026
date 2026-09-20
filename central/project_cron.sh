#!/bin/bash
# Run the central -> ES projection. Reads the DB password from a file so it
# never sits in crontab. Called by cron every 15 min.
cd /path/to/central || exit 1
DBPASS=$(cat /path/to/central/.dbpass 2>/dev/null)
[ -z "$DBPASS" ] && DBPASS="devicedb"
CENTRAL_DB_URL="postgresql://devicedb:${DBPASS}@127.0.0.1:5432/devicedb_central" \
  venv/bin/python es/project.py >> /path/to/central/projection.log 2>&1
