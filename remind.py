"""
Runs daily after the main scraper pipeline (piggybacked on run.sh).

Two jobs:
  1. AUTO-EXPIRE: WP Status == "manual-auto" and Scraped At + 30 days <= today
     → mark "expired" in the sheet, delete from WordPress, post Slack alert
  2. REMIND: WP Status == "manual" and Scraped At + 21 days <= today
     → post Slack nudge asking for manual review
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
WP_URL            = os.environ.get("WP_URL", "").rstrip("/")
WP_USERNAME       = os.environ.get("WP_USERNAME", "")
WP_APP_PASSWORD   = os.environ.get("WP_APP_PASSWORD", "")
CREDENTIALS_FILE  = Path(__file__).parent / "config" / "google_credentials.json"

AUTO_EXPIRE_DAYS  = 30
REMIND_AFTER_DAYS = 21

# Column indices (0-based) matching the Jobs tab layout:
# Company | Job Title | Application URL | Function | Evergreen | Location | Scraped At | WP Status
COL_COMPANY   = 0
COL_TITLE     = 1
COL_URL       = 2
COL_DATE      = 6
COL_WP_STATUS = 7

WP_AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
WP_HEADERS = {"Content-Type": "application/json"}


def _find_wp_job_id(app_url: str) -> int | None:
    if not WP_URL or not WP_USERNAME:
        return None
    norm = app_url.rstrip("/").lower()
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "id,acf"},
            auth=WP_AUTH,
            headers=WP_HEADERS,
            timeout=30,
        )
        if not resp.ok:
            return None
        batch = resp.json()
        if not batch:
            return None
        for job in batch:
            if job.get("acf", {}).get("job_link", "").rstrip("/").lower() == norm:
                return job["id"]
        if len(batch) < 100:
            return None
        page += 1


def _delete_from_wp(app_url: str) -> bool:
    post_id = _find_wp_job_id(app_url)
    if post_id is None:
        return False
    resp = requests.delete(
        f"{WP_URL}/wp-json/wp/v2/av_job/{post_id}",
        params={"force": True},
        auth=WP_AUTH,
        headers=WP_HEADERS,
        timeout=30,
    )
    return resp.status_code == 200


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

    today          = date.today()
    expire_cutoff  = today - timedelta(days=AUTO_EXPIRE_DAYS)
    remind_cutoff  = today - timedelta(days=REMIND_AFTER_DAYS)

    to_expire = []
    to_remind = []

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= COL_WP_STATUS:
            continue
        wp_status = row[COL_WP_STATUS].strip().lower()
        if wp_status not in ("manual", "manual-auto"):
            continue

        raw_date = row[COL_DATE].strip() if len(row) > COL_DATE else ""
        try:
            added = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue

        job = {
            "company": row[COL_COMPANY].strip(),
            "title":   row[COL_TITLE].strip(),
            "url":     row[COL_URL].strip() if len(row) > COL_URL else "",
            "row":     i,
            "added":   raw_date,
        }

        if wp_status == "manual-auto" and added < expire_cutoff:
            to_expire.append(job)
        elif wp_status == "manual" and added <= remind_cutoff:
            to_remind.append(job)

    # ── Auto-expire ───────────────────────────────────────────────────────────
    expired = []
    for j in to_expire:
        print(f"remind.py: expiring {j['company']} — {j['title']} (row {j['row']})")
        deleted = _delete_from_wp(j["url"])
        new_status = "removed" if deleted else "expired"
        jobs_ws.update_cell(j["row"], COL_WP_STATUS + 1, new_status)
        expired.append({**j, "wp_deleted": deleted})

    if expired:
        lines = "\n".join(
            f"• *{j['company']}* — {j['title']} (added {j['added']})"
            + ("" if j["wp_deleted"] else " ⚠️ _not removed from website_")
            for j in expired
        )
        _post_slack([
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "LinkedIn Jobs Auto-Expired"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":wastebasket: These jobs reached their 30-day expiration and have been removed from the job board:\n"
                        + lines
                    ),
                },
            },
        ])

    # ── Remind ────────────────────────────────────────────────────────────────
    if to_remind:
        lines = "\n".join(
            f"• *{j['company']}* — {j['title']} (Row {j['row']}, added {j['added']})"
            for j in to_remind
        )
        _post_slack([
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
        ])

    if not expired and not to_remind:
        print("remind.py: nothing to expire or remind")


if __name__ == "__main__":
    main()
