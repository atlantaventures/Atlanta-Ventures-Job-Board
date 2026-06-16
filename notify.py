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

    new_jobs     = stats.get("new_jobs_found", 0)
    removed      = stats.get("wp_removed", 0)
    errors       = stats.get("errors", 0)
    companies    = stats.get("companies_scraped", 0)
    date         = stats.get("date", "unknown")
    added_jobs        = stats.get("added_jobs", [])
    removed_jobs      = stats.get("removed_jobs", [])
    failed_companies  = stats.get("failed_companies", [])

    # Plain-text version for Railway logs
    print(f"Job Board Run — {date}")
    print(f"  {'OK' if all_ok else 'WARNING'} | {companies} companies | {new_jobs} new | {removed} removed | {errors} errors")

    _post_slack_blocks(_summary_blocks(date, new_jobs, removed, errors, wp_failed, all_ok, added_jobs, removed_jobs, failed_companies))


_MAX_JOB_LINES = 30   # cap per section so the message doesn't become a wall of text


def _job_list_text(jobs: list, cap: int = _MAX_JOB_LINES) -> str:
    """Format a list of {company, title} dicts as bullet lines, grouped by company."""
    from collections import defaultdict
    by_company = defaultdict(list)
    for j in jobs:
        by_company[j["company"]].append(j["title"])

    lines = []
    for company, titles in by_company.items():
        for title in titles:
            lines.append(f"• *{company}* — {title}")
            if len(lines) >= cap:
                remaining = sum(len(t) for t in by_company.values()) - cap
                lines.append(f"_… and {remaining} more_")
                return "\n".join(lines)
    return "\n".join(lines)


def _summary_blocks(date, new_jobs, removed, errors, wp_failed, all_ok, added_jobs=None, removed_jobs=None, failed_companies=None):
    added_jobs       = added_jobs       or []
    removed_jobs     = removed_jobs     or []
    failed_companies = failed_companies or []

    n_added   = len(added_jobs)
    n_removed = len(removed_jobs)
    has_errors = errors > 0 or wp_failed > 0

    # ── Verdict line ──────────────────────────────────────────────────────────
    if all_ok:
        verdict_icon = ":white_check_mark:"
        verdict_text = "*Jobs Synced Successfully*"
    else:
        verdict_icon = ":warning:"
        verdict_text = "*Errors Occurred on this Run*"

    # Activity summary (how many added/removed)
    if n_added == 0 and n_removed == 0:
        activity = "No changes — the job board is up to date."
    else:
        parts = []
        if n_added:
            parts.append(f"*{n_added}* new job{'s' if n_added != 1 else ''} added")
        if n_removed:
            parts.append(f"*{n_removed}* job{'s' if n_removed != 1 else ''} removed")
        activity = " and ".join(parts) + "."

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Atlanta Ventures"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Job Board — {date}*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{verdict_icon}  <!channel> {verdict_text}\n{activity}"},
        },
    ]

    # ── Error details (shown only when there are errors) ──────────────────────
    if has_errors:
        error_lines = []
        if failed_companies:
            names = ", ".join(failed_companies)
            error_lines.append(f"• *Scrape failed* — {names}")
        elif errors:
            error_lines.append(f"• *{errors}* company scrape{'s' if errors != 1 else ''} failed — see Railway logs")
        if wp_failed:
            error_lines.append(f"• *{wp_failed}* website sync{'s' if wp_failed != 1 else ''} failed — see Railway logs")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(error_lines)},
        })

    # ── Job detail lists ──────────────────────────────────────────────────────
    if added_jobs:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*New Jobs Added*\n{_job_list_text(added_jobs)}"},
        })

    if removed_jobs:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Jobs Removed*\n{_job_list_text(removed_jobs)}"},
        })

    return blocks


def _crash_blocks():
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Atlanta Ventures"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Job Board — Run Failed*"},
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
