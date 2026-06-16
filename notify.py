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
        print("Job Board Run — ERROR: scraper crashed before writing stats")
        _post_slack_blocks(_crash_blocks())
        return

    stats = json.loads(STATS_FILE.read_text())
    STATS_FILE.unlink(missing_ok=True)

    scraper_ok = stats.get("scraper_ok", False)
    wp_failed  = stats.get("wp_failed", 0) + stats.get("wp_delete_failed", 0)
    wp_ok      = stats.get("wp_ok", False)
    all_ok     = scraper_ok and wp_ok and wp_failed == 0

    new_jobs   = stats.get("new_jobs_found", 0)
    removed    = stats.get("wp_removed", 0)
    errors     = stats.get("errors", 0)
    companies  = stats.get("companies_scraped", 0)
    date       = stats.get("date", "unknown")

    # Plain-text version for Railway logs
    print(f"Job Board Run — {date}")
    print(f"  {'OK' if all_ok else 'WARNING'} | {companies} companies | {new_jobs} new | {removed} removed | {errors} errors")

    _post_slack_blocks(_summary_blocks(date, new_jobs, removed, errors, wp_failed, all_ok))


def _summary_blocks(date, new_jobs, removed, errors, wp_failed, all_ok):
    status_icon = ":white_check_mark:" if all_ok else ":warning:"

    if new_jobs == 0 and removed == 0:
        activity = "No changes — the job board is up to date."
    else:
        parts = []
        if new_jobs:
            parts.append(f"*{new_jobs}* new job{'s' if new_jobs != 1 else ''} added")
        if removed:
            parts.append(f"*{removed}* job{'s' if removed != 1 else ''} removed")
        activity = " and ".join(parts) + "."

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Atlanta Ventures Job Board — {date}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{status_icon}  <!channel> {activity}"},
        },
    ]

    if errors > 0 or wp_failed > 0:
        problems = []
        if errors:
            problems.append(f"{errors} company scrape{'s' if errors != 1 else ''} failed")
        if wp_failed:
            problems.append(f"{wp_failed} website update{'s' if wp_failed != 1 else ''} failed")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":warning:  *Issues:* " + ", ".join(problems) + ". Check Railway logs."},
        })

    return blocks


def _crash_blocks():
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Atlanta Ventures Job Board — Run Failed"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": ":x:  The scraper crashed before completing. Check Railway logs for details."},
        },
    ]


def _post_slack_blocks(blocks):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10)
    except Exception as e:
        print(f"Slack notification failed: {e}")


if __name__ == "__main__":
    main()
