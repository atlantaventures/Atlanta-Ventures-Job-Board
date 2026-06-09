#!/bin/bash
cd /Users/aidenfisher/dev/jobs
python3 scraper/playwright_extract.py || exit 1
echo "Waiting for Sheets write quota to reset..."
sleep 60
python3 sync/wp_sync.py
