#!/bin/bash
cd "$(dirname "$0")"
python3 setup_env.py || exit 1
python3 job_loader.py || exit 1
echo "Waiting for Sheets write quota to reset...."
sleep 60
python3 sync/wp_sync.py
