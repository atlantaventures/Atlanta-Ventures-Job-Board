#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 setup_env.py
python3 job_loader.py
echo "Waiting for Sheets write quota to reset...."
sleep 60
python3 sync/wp_sync.py
python3 notify.py
