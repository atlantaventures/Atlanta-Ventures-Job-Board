#!/bin/bash
set -e
cd "$(dirname "$0")"

LOCK_FILE="/tmp/scraper.lock"

if [ -f "$LOCK_FILE" ]; then
  echo "Another scraper run is already in progress — exiting."
  exit 0
fi

trap "rm -f $LOCK_FILE" EXIT
touch "$LOCK_FILE"

python3 setup_env.py
python3 job_loader.py
echo "Waiting for Sheets write quota to reset...."
sleep 60
python3 sync/wp_sync.py
python3 notify.py
