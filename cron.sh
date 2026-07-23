#!/bin/bash
set -e
python3 -c "
import os, requests, sys

# This is the only thing that starts a run — if this script errors out silently,
# the scraper never runs and nothing else in the repo would ever know. Alert
# directly to Slack here rather than relying on notify.py, which only runs
# *after* a successful trigger.
webhook_url = os.environ.get('SLACK_WEBHOOK_URL', '')

def alert_slack(text):
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}]},
            timeout=10,
        )
    except Exception:
        pass

try:
    resp = requests.post(
        os.environ['WEBHOOK_BASE'].rstrip('/') + '/run',
        headers={'X-Secret': os.environ['WEBHOOK_SECRET'], 'Content-Type': 'application/json'},
        json={},
        timeout=10,
    )
except requests.RequestException as e:
    alert_slack(
        f'<!channel> :x: *Job board cron could not reach the scraper service* — {e}\\n'
        f'_The scraper never started this run. Check that the Railway service is running._'
    )
    sys.exit(1)

print(resp.status_code, resp.text)
if resp.status_code not in (200, 409):
    alert_slack(
        f'<!channel> :x: *Job board cron trigger failed* — HTTP {resp.status_code}\\n{resp.text[:300]}'
    )
sys.exit(0 if resp.status_code in (200, 409) else 1)
"
