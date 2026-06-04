"""
Saves all current av_job posts from WordPress to snapshot.json.
Run this before testing so you can restore the site to its current state.

Usage:
    python3 sync/snapshot.py
"""

import json
import os
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

WP_URL          = os.environ["WP_URL"].rstrip("/")
WP_USERNAME     = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
SNAPSHOT_FILE   = Path(__file__).parent / "snapshot.json"

AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def get_all_jobs():
    jobs = []
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "id,title,status,acf"},
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
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return jobs


def main():
    print("Fetching all jobs from WordPress...")
    jobs = get_all_jobs()
    SNAPSHOT_FILE.write_text(json.dumps(jobs, indent=2))
    print(f"Saved {len(jobs)} jobs to {SNAPSHOT_FILE}")


if __name__ == "__main__":
    main()
