#!/bin/bash
set -e
python3 -c "
import os, requests, sys
resp = requests.post(
    os.environ['WEBHOOK_BASE'].rstrip('/') + '/run',
    headers={'X-Secret': os.environ['WEBHOOK_SECRET'], 'Content-Type': 'application/json'},
    json={},
    timeout=10,
)
print(resp.status_code, resp.text)
sys.exit(0 if resp.status_code in (200, 409) else 1)
"
