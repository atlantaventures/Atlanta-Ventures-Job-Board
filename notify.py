"""
Run after job_loader.py and wp_sync.py to send a summary notification.
Prints to stdout (visible in Railway logs) always.
Posts to Slack only if SLACK_WEBHOOK_URL is set.
"""
import json
import os
import requests
from pathlib import Path

STATS_FILE        = Path("/tmp/run_stats.json")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def main():
    if not STATS_FILE.exists():
        msg = "Job Board Run — ERROR: scraper crashed before writing stats"
        print(msg)
        _post_slack(f":x: *{msg}*")
        return

    stats = json.loads(STATS_FILE.read_text())
    STATS_FILE.unlink(missing_ok=True)

    scraper_ok = stats.get("scraper_ok", False)
    wp_failed  = stats.get("wp_failed", 0) + stats.get("wp_delete_failed", 0)
    wp_ok      = stats.get("wp_ok", False)
    icon       = ":white_check_mark:" if scraper_ok and wp_ok else ":warning:"

    lines = [
        f"{icon} *Job Board Run — {stats.get('date', 'unknown')}*",
        "",
        "*Scraper*",
        f"  Companies scraped:   {stats.get('companies_scraped', '?')}",
        f"  New jobs found:      {stats.get('new_jobs_found', '?')}",
        f"  Filtered out:        {stats.get('filtered_out', '?')}",
        f"  Duplicates skipped:  {stats.get('duplicates_skipped', '?')}",
        f"  Errors:              {stats.get('errors', '?')}",
        "",
        "*WordPress Sync*",
        f"  Posted:              {stats.get('wp_posted', '?')}",
        f"  Removed:             {stats.get('wp_removed', '?')}",
        f"  Skipped:             {stats.get('wp_skipped', '?')}",
        f"  Failed:              {wp_failed}",
    ]

    message = "\n".join(lines)
    print(message)
    _post_slack(message)


def _post_slack(text):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    except Exception as e:
        print(f"Slack notification failed: {e}")


if __name__ == "__main__":
    main()
