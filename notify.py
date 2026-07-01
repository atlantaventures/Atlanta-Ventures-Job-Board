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

    try:
        stats = json.loads(STATS_FILE.read_text())
    except Exception:
        print("Job Board Run — ERROR: stats file corrupted or unreadable")
        _post_slack_blocks(_crash_blocks())
        return
    STATS_FILE.unlink(missing_ok=True)

    scraper_ok       = stats.get("scraper_ok", False)
    wp_post_failed   = stats.get("wp_failed", 0)
    wp_delete_failed = stats.get("wp_delete_failed", 0)
    wp_failed        = wp_post_failed + wp_delete_failed
    wp_ok            = stats.get("wp_ok", False)
    all_ok           = scraper_ok and wp_ok and wp_failed == 0

    errors           = stats.get("errors", 0)
    companies        = stats.get("companies_scraped", 0)
    filtered_out     = stats.get("filtered_out", 0)
    date             = stats.get("date", "unknown")
    added_jobs       = stats.get("added_jobs", [])
    removed_jobs     = stats.get("removed_jobs", [])
    updated_jobs     = stats.get("updated_jobs", [])
    failed_companies = stats.get("failed_companies", [])

    # Plain-text version for Railway logs
    print(f"Job Board Run — {date}")
    print(f"  {'OK' if all_ok else 'WARNING'} | {companies} companies | {len(added_jobs)} new | {len(removed_jobs)} removed | {errors} errors")

    _post_slack_blocks(_summary_blocks(date, errors, wp_post_failed, wp_delete_failed, all_ok, companies, filtered_out, added_jobs, removed_jobs, updated_jobs, failed_companies))


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
                if remaining > 0:
                    lines.append(f"_… and {remaining} more_")
                return "\n".join(lines)
    return "\n".join(lines)


def _summary_blocks(date, errors, wp_post_failed, wp_delete_failed, all_ok, companies=0, filtered_out=0, added_jobs=None, removed_jobs=None, updated_jobs=None, failed_companies=None):
    added_jobs       = added_jobs       or []
    removed_jobs     = removed_jobs     or []
    updated_jobs     = updated_jobs     or []
    failed_companies = failed_companies or []

    n_added    = len(added_jobs)
    n_removed  = len(removed_jobs)
    n_updated  = len(updated_jobs)
    has_wp_issues    = wp_post_failed > 0 or wp_delete_failed > 0
    has_scrape_errors = errors > 0
    has_errors        = has_scrape_errors or has_wp_issues

    # ── Verdict line — three severity tiers ───────────────────────────────────
    if all_ok:
        verdict_icon = ":white_check_mark:"
        verdict_text = "*Jobs Synced Successfully*"
    elif has_wp_issues:
        verdict_icon = ":x:"
        verdict_text = "<!channel> *Website Sync Failed*"
    else:
        verdict_icon = ":warning:"
        verdict_text = "*Minor Issues — Run Completed*"

    # ── Activity summary ──────────────────────────────────────────────────────
    companies_note = f"_{companies} companies checked_" if companies else ""

    if n_added == 0 and n_removed == 0 and n_updated == 0:
        activity = (
            f"No changes — all {companies} companies checked, job board is up to date."
            if companies else
            "No changes — the job board is up to date."
        )
    else:
        parts = []
        if n_added:
            parts.append(f"*{n_added}* new job{'s' if n_added != 1 else ''} added")
        if n_removed:
            parts.append(f"*{n_removed}* job{'s' if n_removed != 1 else ''} removed")
        if n_updated:
            parts.append(f"*{n_updated}* listing{'s' if n_updated != 1 else ''} updated")
        activity = ", ".join(parts) + "."
        if companies_note:
            activity += f"  {companies_note}"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Atlanta Ventures Job Board — {date}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{verdict_icon} {verdict_text}\n{activity}"},
        },
    ]

    # ── Error details ─────────────────────────────────────────────────────────
    if has_errors:
        error_lines = []
        if failed_companies:
            names = ", ".join(failed_companies)
            error_lines.append(
                f"• *Scrape failed* — {names}\n"
                f"  _The careers page may be temporarily down, or the URL in the Companies tab may need updating._"
            )
        elif has_scrape_errors:
            n = errors
            error_lines.append(
                f"• *{n}* compan{'ies' if n != 1 else 'y'} couldn't be scraped. "
                f"The careers page may be temporarily down, or the URL in the Companies tab may need updating."
            )
        if wp_post_failed:
            n = wp_post_failed
            error_lines.append(
                f"• *{n}* job{'s' if n != 1 else ''} {'were' if n != 1 else 'was'} found but couldn't be posted to the website. "
                f"{'They' if n != 1 else 'It'} will be retried automatically on the next scrape."
            )
        if wp_delete_failed:
            n = wp_delete_failed
            error_lines.append(
                f"• *{n}* expired job{'s' if n != 1 else ''} couldn't be removed from the website and "
                f"may still be visible on the job board. Running the scraper again should clear {'them' if n != 1 else 'it'}."
            )
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
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Jobs Removed*\n"
                    f"_These jobs no longer appeared on the company's careers page and were automatically removed._\n"
                    f"{_job_list_text(removed_jobs)}"
                ),
            },
        })

    if updated_jobs:
        lines = "\n".join(
            f"• *{j['company']}* — _{j['old_title']}_ → _{j['new_title']}_"
            for j in updated_jobs
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Listings Updated*\n{lines}"},
        })

    if filtered_out:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_{filtered_out} job{'s' if filtered_out != 1 else ''} found but filtered as not relevant to this board._",
            },
        })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "<https://www.atlantaventures.com/jobs/|View Job Board>"},
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
            "text": {
                "type": "mrkdwn",
                "text": (
                    "<!channel> :x: *The scraper stopped unexpectedly and did not finish.*\n"
                    "*What to try:* Run it again from Job Board → Run Scraper Now. "
                    "If it keeps failing, restart the service in Railway."
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "<https://www.atlantaventures.com/jobs/|View Job Board>"},
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
