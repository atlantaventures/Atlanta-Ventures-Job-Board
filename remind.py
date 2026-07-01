"""
Sends a Slack reminder for manually added jobs that are 3+ weeks old.
Piggybacked on run.sh — runs daily after the main scraper pipeline.

Jobs eligible for reminder:
  - WP Status == "manual"  (set by webhook approve_job)
  - Scraped At date is >= REMIND_AFTER_DAYS days ago
  - WP Status is NOT "expired", "removed", or "blocked"
"""
import os
import requests
from datetime import date, datetime, timedelta
from pathlib import Path

import gspread
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

SHEET_ID          = os.environ.get("SHEET_ID", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
CREDENTIALS_FILE  = Path(__file__).parent / "config" / "google_credentials.json"
REMIND_AFTER_DAYS = 21

# Column indices (0-based) matching the Jobs tab layout:
# Company | Job Title | Application URL | Function | Evergreen | Location | Scraped At | WP Status | Description
COL_COMPANY   = 0
COL_TITLE     = 1
COL_DATE      = 6
COL_WP_STATUS = 7


def _post_slack(blocks: list):
    if not SLACK_WEBHOOK_URL:
        print("remind.py: SLACK_WEBHOOK_URL not set — skipping Slack post")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
    except Exception as e:
        print(f"remind.py: Slack post failed — {e}")


def main():
    if not SHEET_ID:
        print("remind.py: SHEET_ID not set — skipping")
        return

    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    jobs_ws = sh.worksheet("Jobs")

    all_rows = jobs_ws.get_all_values()
    if len(all_rows) < 2:
        return

    today     = date.today()
    cutoff    = today - timedelta(days=REMIND_AFTER_DAYS)
    due       = []  # [{"company": str, "title": str, "row": int, "added": str}]

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= COL_WP_STATUS:
            continue
        wp_status = row[COL_WP_STATUS].strip().lower()
        if wp_status != "manual":
            continue

        raw_date = row[COL_DATE].strip() if len(row) > COL_DATE else ""
        try:
            added = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue

        if added <= cutoff:
            due.append({
                "company": row[COL_COMPANY].strip(),
                "title":   row[COL_TITLE].strip(),
                "row":     i,
                "added":   raw_date,
            })

    if not due:
        print("remind.py: no manual jobs due for review")
        return

    print(f"remind.py: {len(due)} manual job(s) due for review — posting Slack reminder")

    lines = "\n".join(
        f"• *{j['company']}* — {j['title']} (Row {j['row']}, added {j['added']})"
        for j in due
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "LinkedIn Jobs Due for Review"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":bell: These jobs were manually added 3+ weeks ago and may need removing if the role is filled:\n"
                    + lines
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_To remove: Jobs tab → select the row → Job Board → Remove job(s)_",
            },
        },
    ]
    _post_slack(blocks)


if __name__ == "__main__":
    main()
