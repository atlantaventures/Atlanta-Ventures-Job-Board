"""
Permanently deletes all av_job posts from WordPress.
FOR TESTING ONLY — run snapshot.py first to preserve the current state.

Usage:
    python3 sync/nuke.py
"""

import os
import time

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

WP_URL          = os.environ["WP_URL"].rstrip("/")
WP_USERNAME     = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

BATCH_SIZE = 10


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_all_job_ids():
    jobs = []
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "id,title"},
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


def delete_batch(job_ids: list):
    """
    DELETE a batch of jobs via /wp-json/batch/v1.
    Returns a list of booleans (True = success) in input order,
    or None if the batch endpoint is unavailable.
    """
    payload = {
        "requests": [
            {"method": "DELETE", "path": f"/wp/v2/av_job/{job_id}?force=true"}
            for job_id in job_ids
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
        return None
    resp.raise_for_status()
    responses = resp.json().get("responses", [])
    return [r.get("status", 500) == 200 for r in responses]


def main():
    print("Fetching all jobs from WordPress...")
    jobs = get_all_job_ids()

    if not jobs:
        print("No jobs found — nothing to delete.")
        return

    print(f"Found {len(jobs)} jobs.")
    confirm = input(f"\nType DELETE to permanently remove all {len(jobs)} jobs: ").strip()
    if confirm != "DELETE":
        print("Aborted.")
        return

    deleted   = 0
    failed    = 0
    use_batch = True

    for chunk in _chunks(jobs, BATCH_SIZE):
        ids    = [j["id"] for j in chunk]
        titles = [j["title"]["rendered"] for j in chunk]

        if use_batch:
            results = delete_batch(ids)
            if results is None:
                print("  Batch endpoint unavailable — switching to individual deletes")
                use_batch = False
            else:
                for title, success in zip(titles, results):
                    if success:
                        deleted += 1
                        print(f"  Deleted: {title}")
                    else:
                        failed += 1
                        print(f"  FAILED:  {title}")
                time.sleep(2)
                continue

        # Individual fallback
        for job_id, title in zip(ids, titles):
            resp = requests.delete(
                f"{WP_URL}/wp-json/wp/v2/av_job/{job_id}",
                params={"force": True},
                auth=AUTH,
                headers=HEADERS,
                timeout=30,
            )
            if resp.status_code == 200:
                deleted += 1
                print(f"  Deleted: {title}")
            else:
                failed += 1
                print(f"  FAILED:  {title} — {resp.status_code}")
            time.sleep(2)

    print(f"\nDeleted {deleted}/{len(jobs)} jobs.")
    if failed:
        print(f"{failed} failed — check output above.")


if __name__ == "__main__":
    main()
