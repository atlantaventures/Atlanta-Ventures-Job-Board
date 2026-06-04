"""
Restores all jobs from snapshot.json back to WordPress.
Run this after nuke.py to return the site to its prior state.

Usage:
    python3 sync/restore.py
"""

import json
import os
import time
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


def main():
    if not SNAPSHOT_FILE.exists():
        print("No snapshot.json found — run snapshot.py first.")
        return

    jobs = json.loads(SNAPSHOT_FILE.read_text())
    if not jobs:
        print("Snapshot is empty — nothing to restore.")
        return

    print(f"Restoring {len(jobs)} jobs from snapshot...")

    success = 0
    failed  = 0
    for job in jobs:
        payload = {
            "title":  job["title"]["rendered"],
            "status": job.get("status", "publish"),
            "acf":    job.get("acf", {}),
        }
        resp = requests.post(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            json=payload,
            auth=AUTH,
            headers=HEADERS,
            timeout=30,
        )
        title = job["title"]["rendered"]
        if resp.status_code in (200, 201):
            success += 1
            print(f"  Restored: {title}")
        else:
            failed += 1
            print(f"  FAILED:   {title} — {resp.status_code} {resp.text[:120]}")
        time.sleep(2)

    print(f"\nRestored {success}/{len(jobs)} jobs.")
    if failed:
        print(f"{failed} failed — check output above.")


if __name__ == "__main__":
    main()
