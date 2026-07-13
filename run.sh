#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 setup_env.py
python3 job_loader.py || true
echo "Waiting for Sheets write quota to reset...."
sleep 60
python3 remind.py || true
python3 sync/wp_sync.py || true
python3 notify.py
