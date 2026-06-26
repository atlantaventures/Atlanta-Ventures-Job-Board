"""
Syncs jobs from Google Sheets (Jobs tab) to WordPress.
Only posts jobs not already on the site. Marks each row WP Status = "posted" on success.
Expired rows get deleted from WP and marked "removed".

Dedup layers:
  1. Sheet-side: skips rows already marked WP Status = "posted" or "removed"
  2. WordPress-side: fetches all existing job_link values before posting

Posting strategy:
  - Attempts batch posts (up to BATCH_SIZE per HTTP request) via /wp-json/batch/v1
  - Falls back to individual posts with a 2s delay if batch endpoint is unavailable

Usage:
    python3 sync/wp_sync.py
"""

import os
import sys
import time
from pathlib import Path

import gspread
import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_REQUIRED_VARS = ["WP_URL", "WP_USERNAME", "WP_APP_PASSWORD", "SHEET_ID"]
_missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    sys.exit(1)

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

BATCH_SIZE = 10

# Column indices in the Jobs tab (0-indexed)
COL_COMPANY     = 0
COL_TITLE       = 1
COL_URL         = 2
COL_FUNCTION    = 3
COL_EVERGREEN   = 4
COL_LOCATION    = 5
COL_WP_STATUS   = 7  # column 8 in sheet (1-indexed)
COL_DESCRIPTION = 8  # column 9 — only populated for evergreen companies


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _sheets_write(fn, *args, **kwargs):
    """Call a gspread write method, retrying once on 429 quota errors."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            if "429" in str(e) and attempt < 2:
                print("    Sheets rate limit — waiting 60s...")
                time.sleep(60)
            else:
                raise


def get_existing_wp_jobs() -> dict:
    """Fetch all av_job posts from WordPress. Returns {normalized_url: post_id}."""
    jobs = {}
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "id,acf"},
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
            url = job.get("acf", {}).get("job_link", "")
            if url:
                jobs[url.rstrip("/").lower()] = job["id"]
        if len(batch) < 100:
            break
        page += 1
    return jobs


def delete_job(post_id: int) -> bool:
    """Permanently delete a single WP job post. Returns True on success."""
    resp = requests.delete(
        f"{WP_URL}/wp-json/wp/v2/av_job/{post_id}",
        params={"force": True},
        auth=AUTH,
        headers=HEADERS,
        timeout=30,
    )
    return resp.status_code == 200


def _build_payload(job: dict) -> dict:
    evergreen = str(job["evergreen"]).strip().lower() == "true"
    return {
        "title":  job["title"],
        "status": "publish",
        "acf": {
            "job_link":        job["application_url"],
            "job_function":    job["function"],
            "job_location":    job["location"],
            "is_evergreen":    evergreen,
            "job_company":     job["company"],
            "job_description": job.get("description", ""),
        },
    }


def post_job(job: dict) -> bool:
    """POST a single job to WordPress. Returns True on success."""
    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/av_job",
        json=_build_payload(job),
        auth=AUTH,
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.status_code in (200, 201)


def post_jobs_batch(jobs: list):
    """
    POST a batch of jobs via /wp-json/batch/v1.
    Returns a list of booleans (True = success) in input order,
    or None if the batch endpoint is unavailable.
    """
    payload = {
        "requests": [
            {"method": "POST", "path": "/wp/v2/av_job", "body": _build_payload(j)}
            for j in jobs
        ]
    }
    resp = requests.post(
        f"{WP_URL}/wp-json/batch/v1",
        json=payload,
        auth=AUTH,
        headers=HEADERS,
        timeout=60,
    )
    if resp.status_code == 404:
        return None  # endpoint not available — caller falls back to individual posts
    resp.raise_for_status()
    responses = resp.json().get("responses", [])
    return [r.get("status", 500) in (200, 201) for r in responses]


def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    companies_ws = sh.worksheet("Companies")
    jobs_ws      = sh.worksheet("Jobs")

    active_companies = {
        r["Company"].strip()
        for r in companies_ws.get_all_records()
        if r.get("Company", "").strip() and r.get("Careers URL", "").strip()
    }

    rows = jobs_ws.get_all_values()
    if len(rows) <= 1:
        print("No jobs in sheet.")
        return

    data = rows[1:]  # skip header
    print(f"Found {len(data)} jobs in sheet.")

    # Mark jobs for deleted companies as expired so the main loop removes them from WP
    for i, row in enumerate(data, start=2):
        company   = row[COL_COMPANY].strip()  if len(row) > COL_COMPANY   else ""
        wp_status = row[COL_WP_STATUS].strip() if len(row) > COL_WP_STATUS else ""
        if company and company not in active_companies and wp_status.lower() not in ("expired", "removed"):
            _sheets_write(jobs_ws.update_cell,i, COL_WP_STATUS + 1, "expired")
            row[COL_WP_STATUS] = "expired"

    print("Fetching existing jobs from WordPress...")
    existing_jobs = get_existing_wp_jobs()
    print(f"Found {len(existing_jobs)} jobs already on WordPress.\n")

    posted        = 0
    skipped       = 0
    failed        = 0
    deleted       = 0
    delete_failed = 0

    # First pass: handle expired deletions and collect rows that need posting
    pending = []  # list of (sheet_row_index, job_dict, title)

    for i, row in enumerate(data, start=2):
        def col(idx, r=row):
            return r[idx].strip() if len(r) > idx else ""

        wp_status = col(COL_WP_STATUS)
        app_url   = col(COL_URL)
        title     = col(COL_TITLE)

        if wp_status.lower() == "expired":
            post_id = existing_jobs.get(app_url.rstrip("/").lower())
            if post_id:
                if delete_job(post_id):
                    _sheets_write(jobs_ws.update_cell,i, COL_WP_STATUS + 1, "removed")
                    del existing_jobs[app_url.rstrip("/").lower()]
                    deleted += 1
                    print(f"  Removed: {title}")
                else:
                    delete_failed += 1
                    print(f"  DELETE FAILED: {title}")
                time.sleep(2)
            else:
                # Already gone from WP — just clean up the sheet row
                _sheets_write(jobs_ws.update_cell,i, COL_WP_STATUS + 1, "removed")
            continue

        if wp_status.lower() in ("posted", "removed"):
            skipped += 1
            continue

        if app_url.rstrip("/").lower() in existing_jobs:
            _sheets_write(jobs_ws.update_cell,i, COL_WP_STATUS + 1, "posted")
            skipped += 1
            continue

        pending.append((i, {
            "company":         col(COL_COMPANY),
            "title":           title,
            "application_url": app_url,
            "function":        col(COL_FUNCTION),
            "evergreen":       col(COL_EVERGREEN),
            "location":        col(COL_LOCATION),
            "description":     col(COL_DESCRIPTION),  # only set for evergreen; ignored for others
        }, title))

    # Second pass: post pending jobs in batches, fall back to individual if needed
    if pending:
        print(f"Posting {len(pending)} new job(s)...\n")

    use_batch = True
    for chunk in _chunks(pending, BATCH_SIZE):
        chunk_jobs  = [j for _, j, _ in chunk]
        chunk_meta  = [(i, t) for i, _, t in chunk]

        if use_batch:
            results = post_jobs_batch(chunk_jobs)
            if results is None:
                print("  Batch endpoint unavailable — switching to individual posts")
                use_batch = False
            else:
                for (row_i, title), success in zip(chunk_meta, results):
                    if success:
                        _sheets_write(jobs_ws.update_cell,row_i, COL_WP_STATUS + 1, "posted")
                        posted += 1
                        print(f"  Posted:  {title}")
                    else:
                        failed += 1
                        print(f"  FAILED:  {title}")
                time.sleep(2)
                continue

        # Individual fallback
        for (row_i, title), job in zip(chunk_meta, chunk_jobs):
            if post_job(job):
                _sheets_write(jobs_ws.update_cell,row_i, COL_WP_STATUS + 1, "posted")
                posted += 1
                print(f"  Posted:  {title}")
            else:
                failed += 1
                print(f"  FAILED:  {title}")
            time.sleep(2)

    print(f"\n{'=' * 40}")
    print(f"  Posted  : {posted}")
    print(f"  Removed : {deleted}")
    print(f"  Skipped : {skipped}")
    print(f"  Failed  : {failed}")
    if delete_failed:
        print(f"  Del fail: {delete_failed}")
    print(f"{'=' * 40}")

    import json
    stats_path = Path("/tmp/run_stats.json")
    existing   = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    existing.update({
        "wp_posted":       posted,
        "wp_removed":      deleted,
        "wp_skipped":      skipped,
        "wp_failed":       failed,
        "wp_delete_failed": delete_failed,
        "wp_ok":           not (failed or delete_failed),
    })
    stats_path.write_text(json.dumps(existing))

    if failed or delete_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
