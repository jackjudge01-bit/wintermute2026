#!/bin/bash
# devicedb auto-pilot sweep. Installed by cron every 15 min.
# Stop with: crontab -e   (comment out the devicedb line)
cd /path/to/devicedb || exit 1
mkdir -p logs
L=logs/sweep-$(date +%Y%m).log
{
  echo "===== $(date -Is) ====="
  # passive: arpwatch journal + arp-scan LAN + BLEA BLE
  timeout 300 python3 devicedb.py sweep \
      --arpwatch --arp --ble \
      --marauder-seconds 15 --marauder-ble-seconds 10
  echo "sweep exit=$?"
  # push new observations to central (Postgres -> ES read model)
  timeout 60 python3 devicedb.py contribute --since 1h
  echo "contribute exit=$?"
} >> "$L" 2>&1

# keep logs to ~2 months
find logs -name 'sweep-*.log' -mtime +60 -delete 2>/dev/null
