"""
Syncs jobs from Google Sheets (Jobs tab) to WordPress.
Only posts jobs not already on the site. Marks each row WP Status = "posted" on success.

Two dedup layers:
  1. Sheet-side: skips rows already marked WP Status = "posted"
  2. WordPress-side: fetches all existing application_url values before posting

Usage:
    python3 sync/wp_sync.py
"""

import os
import time
from pathlib import Path

import gspread
import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

WP_URL           = os.environ["WP_URL"].rstrip("/")
WP_USERNAME      = os.environ["WP_USERNAME"]
WP_APP_PASSWORD  = os.environ["WP_APP_PASSWORD"]
SHEET_ID         = os.environ["SHEET_ID"]
CREDENTIALS_FILE = Path(__file__).parent.parent / "config" / "google_credentials.json"

AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Column indices in the Jobs tab (0-indexed)
COL_COMPANY   = 0
COL_TITLE     = 1
COL_URL       = 2
COL_FUNCTION  = 3
COL_EVERGREEN = 4
COL_LOCATION  = 5
COL_WP_STATUS = 7  # column 8 in sheet (1-indexed)


def get_existing_wp_urls() -> set:
    """Fetch all application_url values already on WordPress."""
    urls = set()
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "acf"},
            auth=AUTH,
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code == 400:
            break
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for job in batch:
            url = job.get("acf", {}).get("application_url", "")
            if url:
                urls.add(url.rstrip("/").lower())
        if len(batch) < 100:
            break
        page += 1
    return urls


def post_job(job: dict) -> bool:
    """POST a single job to WordPress. Returns True on success."""
    evergreen = str(job["evergreen"]).strip().lower() == "true"
    payload = {
        "title":  job["title"],
        "status": "publish",
        "acf": {
            "application_url": job["application_url"],
            "job_function":    job["function"],
            "job_location":    job["location"],
            "is_evergreen":    evergreen,
            "company":         job["company"],
        },
    }
    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/av_job",
        json=payload,
        auth=AUTH,
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.status_code in (200, 201)


def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    jobs_ws = sh.worksheet("Jobs")

    rows = jobs_ws.get_all_values()
    if len(rows) <= 1:
        print("No jobs in sheet.")
        return

    data = rows[1:]  # skip header
    print(f"Found {len(data)} jobs in sheet.")

    print("Fetching existing jobs from WordPress...")
    existing_urls = get_existing_wp_urls()
    print(f"Found {len(existing_urls)} jobs already on WordPress.\n")

    posted  = 0
    skipped = 0
    failed  = 0

    for i, row in enumerate(data, start=2):  # start=2 accounts for header row
        def col(idx):
            return row[idx].strip() if len(row) > idx else ""

        wp_status = col(COL_WP_STATUS)
        app_url   = col(COL_URL)
        title     = col(COL_TITLE)

        if wp_status.lower() == "posted":
            skipped += 1
            continue

        if app_url.rstrip("/").lower() in existing_urls:
            jobs_ws.update_cell(i, COL_WP_STATUS + 1, "posted")
            skipped += 1
            continue

        job = {
            "company":         col(COL_COMPANY),
            "title":           title,
            "application_url": app_url,
            "function":        col(COL_FUNCTION),
            "evergreen":       col(COL_EVERGREEN),
            "location":        col(COL_LOCATION),
        }

        if post_job(job):
            jobs_ws.update_cell(i, COL_WP_STATUS + 1, "posted")
            existing_urls.add(app_url.rstrip("/").lower())
            posted += 1
            print(f"  Posted:  {title}")
        else:
            failed += 1
            print(f"  FAILED:  {title}")
        time.sleep(2)

    print(f"\n{'=' * 40}")
    print(f"  Posted  : {posted}")
    print(f"  Skipped : {skipped}")
    print(f"  Failed  : {failed}")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
