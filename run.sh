#!/bin/bash
cd /Users/aidenfisher/dev/jobs
python3 scraper/playwright_extract.py && python3 sync/wp_sync.py
