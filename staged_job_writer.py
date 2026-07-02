"""
Handles the two final steps for each company's scraped jobs:
  1. Claude relevance filter — decides keep/skip and confirms job_function
  2. Sheet writer — appends kept rows to Jobs tab, skipped rows to Skipped tab

Also handles evergreen company listings (one row per company, no Claude pass needed).
"""

import json
import re

import anthropic

from core.normalize import normalize_function, normalize_location, VALID_FUNCTIONS, VALID_LOCATIONS
from core.utils import sheets_write
from core.dedup import (
    is_generic_listing,
    job_key,
    expire_removed_jobs,
    expire_stale_evergreen_rows,
    normalize_url,
)


def _sanitize(job: dict) -> dict:
    """Coerce all job fields to safe types. Runs on every job before Claude sees it."""
    title    = str(job.get("job_title")    or "").strip()
    url      = str(job.get("application_url") or "").strip()
    fn       = str(job.get("job_function") or "").strip()
    loc      = str(job.get("job_location") or "").strip()
    evergreen = bool(job.get("is_evergreen", False))

    if fn not in VALID_FUNCTIONS:
        fn = normalize_function(fn)
    if loc not in VALID_LOCATIONS:
        loc = normalize_location(loc)

    return {
        "job_title":       title,
        "application_url": url,
        "job_function":    fn,
        "job_location":    loc,
        "is_evergreen":    evergreen,
    }


def filter_jobs_with_claude(client: anthropic.Anthropic, company: str, jobs: list) -> tuple:
    """
    Batch-evaluate all jobs from a company in one Claude call.
    Handles both relevance filtering and function assignment.
    Returns (kept, skipped) where skipped items include a 'skip_reason' key.
    """
    if not jobs:
        return [], []

    job_list = "\n".join(f"{i+1}. {j.get('job_title', '')}" for i, j in enumerate(jobs))

    prompt = f"""You are reviewing job listings from {company} for a professional business job board.

For each job, decide whether to KEEP or SKIP it, and assign a function category.

SKIP if the role is a hands-on trade, manual labor, or consumer-facing hourly job (cook, driver, cleaner, barista, server, retail associate, warehouse worker, security guard, HVAC technician, repair technician, etc.)
KEEP if the role is a professional business position. When in doubt, KEEP.
KEEP all support, customer success, and technical support roles — these are professional Operations positions, not consumer service work.
KEEP technician roles that are R&D, lab, research, robotics, or engineering-adjacent — these are professional technical positions, not trade work.

Function must be exactly one of these 5 values:
- "Engineering" — software, data, AI/ML, infrastructure, product, design, QA, robotics, hardware, mechanical, electrical, embedded, solutions/data architect, analytics, research scientist, manufacturing, programmer
- "Sales" — AEs, SDRs, BDRs, account management, account executive, revenue, business development
- "Marketing" — demand gen, brand, content, growth, SEO, communications, PR, public relations, social media, advertising
- "Finance" — accounting, FP&A, financial analysis, treasury, controller, audit, tax, payroll
- "Operations" — everything else: HR, recruiting, talent acquisition, legal, customer success, project management, program management, procurement, supply chain, logistics, facilities, general management, events, implementation, compliance, specialist roles

Use "" only if the title gives no useful signal at all.

Jobs:
{job_list}

Return a JSON array with one entry per job in the same order:
[{{"keep": true, "reason": "brief reason", "function": "Engineering"}}, ...]

Return ONLY the JSON array, no explanation, no markdown."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        verdicts = json.loads(text)
    except json.JSONDecodeError:
        print(f"    Claude returned invalid JSON for relevance filter — keeping all {len(jobs)} jobs")
        return jobs, []

    if len(verdicts) != len(jobs):
        print(f"    WARNING: Claude returned {len(verdicts)} verdicts for {len(jobs)} jobs — adjusting")
        while len(verdicts) < len(jobs):
            verdicts.append({"keep": True, "reason": "verdict missing", "function": ""})
        verdicts = verdicts[:len(jobs)]

    kept    = []
    skipped = []
    for job, verdict in zip(jobs, verdicts):
        # Claude's assignment takes priority; normalize_function is a safety net for
        # slight variations (e.g. "Ops" → "Operations"); fall back to whatever was
        # already set on the job (from ATS keyword match or prior Claude extraction).
        fn = normalize_function(verdict.get("function", "")) or job.get("job_function", "")
        if verdict.get("keep", True):
            kept.append({**job, "job_function": fn})
        else:
            skipped.append({**job, "job_function": fn, "skip_reason": verdict.get("reason", "")})
    return kept, skipped


def process_company_jobs(
    client, jobs_ws, skipped_ws, companies_ws,
    company: str, url: str, sheet_row: int,
    jobs: list, existing_keys: set, all_job_rows: list,
    today: str, skip_expiry: bool = False,
) -> dict:
    """
    Dedup, filter, and write jobs for a regular (non-evergreen) company.
    Returns stats: {"kept": int, "skipped": int, "dupes": int}.
    Mutates existing_keys and all_job_rows in place.
    """
    # Apply careers URL as fallback application URL before dedup so key is consistent across runs
    for j in jobs:
        if not j.get("application_url"):
            j["application_url"] = url

    jobs = [_sanitize(j) for j in jobs]

    # Mark jobs no longer on the careers page as expired (guard: only if scrape returned results)
    # Skip expiry if any URL fetch failed — partial results would wrongly expire jobs from failed URLs
    current_keys  = {job_key(company, j) for j in jobs}
    expired_titles = [] if skip_expiry else expire_removed_jobs(jobs_ws, company, current_keys, all_job_rows)
    if expired_titles:
        print(f"    {len(expired_titles)} job(s) marked expired")

    # Drop anything already processed in a prior run
    unseen = [j for j in jobs if job_key(company, j) not in existing_keys]
    dupes  = len(jobs) - len(unseen)

    if not unseen:
        return {"kept": 0, "skipped": 0, "dupes": dupes, "added_jobs": [], "removed_jobs": expired_titles}

    # Deterministic pre-filter: catch generic talent pool listings before Claude
    pre_skipped = [j for j in unseen if is_generic_listing(j.get("job_title", ""))]
    unseen      = [j for j in unseen if not is_generic_listing(j.get("job_title", ""))]
    for j in pre_skipped:
        j["skip_reason"] = "Generic talent pool / always-open listing"

    kept, claude_skipped = filter_jobs_with_claude(client, company, unseen)
    all_skipped = pre_skipped + claude_skipped

    if kept:
        new_rows = [
            [
                company,
                j.get("job_title", ""),
                j.get("application_url", ""),
                j.get("job_function", ""),
                j.get("is_evergreen", False),
                j.get("job_location", ""),
                today,
                "",  # WP Status — blank until wp_sync.py runs
            ]
            for j in kept
        ]
        sheets_write(jobs_ws.append_rows, new_rows)
        all_job_rows.extend(new_rows)
        for j in kept:
            existing_keys.add(job_key(company, j))

    if all_skipped:
        sheets_write(skipped_ws.append_rows, [
            [
                company,
                j.get("job_title", ""),
                j.get("application_url", ""),
                j.get("skip_reason", ""),
                today,
            ]
            for j in all_skipped
        ])
        for j in all_skipped:
            existing_keys.add(job_key(company, j))

    sheets_write(companies_ws.update_cell, sheet_row, 4, today)

    added_titles = [j.get("job_title", "") for j in kept]
    return {"kept": len(kept), "skipped": len(all_skipped), "dupes": dupes, "added_jobs": added_titles, "removed_jobs": expired_titles}


def process_evergreen_company(
    jobs_ws, companies_ws,
    company: str, url: str, sheet_row: int,
    row: dict, existing_keys: set, all_job_rows: list,
    today: str,
) -> str:
    """
    Handle evergreen company listing — one Jobs tab row per company pointing to their careers page.
    Returns a dict: {"action": str, ...}.
    "updated" includes "old_title" and "new_title" keys.
    Mutates existing_keys and all_job_rows in place.
    """
    key          = normalize_url(url)
    custom_title = row.get("Custom Title", "").strip()
    description  = row.get("Description", "").strip()
    title        = custom_title if custom_title else "View All Openings"

    n_stale = expire_stale_evergreen_rows(jobs_ws, company, key, all_job_rows)
    if n_stale:
        print(f"    {n_stale} stale URL row(s) marked expired (Careers URL changed)")

    # Expire any individual job rows left over from before this company was evergreen
    old_jobs_expired = []
    for i, jrow in enumerate(all_job_rows[1:], start=2):
        if not jrow or jrow[0] != company:
            continue
        is_evergreen_row = str(jrow[4]).strip().lower() == "true" if len(jrow) > 4 else False
        wp_status        = jrow[7].strip().lower() if len(jrow) > 7 else ""
        if is_evergreen_row or wp_status in ("expired", "removed", "blocked", "manual", "manual-auto"):
            continue
        sheets_write(jobs_ws.update_cell, i, 8, "expired")
        all_job_rows[i - 1][7] = "expired"
        old_jobs_expired.append(jrow[1].strip() if len(jrow) > 1 else "")
    if old_jobs_expired:
        print(f"    {len(old_jobs_expired)} individual job row(s) expired (company switched to evergreen)")

    if key in existing_keys:
        # Find the active (non-expired/removed) row to compare against.
        # Expired rows are skipped — comparing against them causes false updates.
        active_row = None
        for j, jrow in enumerate(all_job_rows[1:], start=2):
            row_key   = normalize_url(jrow[2]) if len(jrow) > 2 else ""
            wp_status = jrow[7] if len(jrow) > 7 else ""
            if jrow[0] == company and row_key == key:
                if wp_status.lower() in ("expired", "removed", "blocked", "manual", "manual-auto"):
                    continue
                active_row = (j, jrow)
                break

        if active_row:
            j, jrow       = active_row
            current_title = jrow[1] if len(jrow) > 1 else ""
            current_desc  = jrow[8] if len(jrow) > 8 else ""
            if current_title != title or current_desc != description:
                # Title or description changed — expire old row and write a fresh one
                sheets_write(jobs_ws.update_cell, j, 8, "expired")
                all_job_rows[j - 1][7] = "expired"
                new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
                sheets_write(jobs_ws.append_rows, [new_row])
                all_job_rows.append(new_row)
                sheets_write(companies_ws.update_cell, sheet_row, 4, today)
                return {"action": "updated", "old_title": current_title, "new_title": title, "evicted_jobs": old_jobs_expired}
            else:
                return {"action": "no-change", "evicted_jobs": old_jobs_expired}
        else:
            # All prior rows expired/removed — re-add fresh
            new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
            sheets_write(jobs_ws.append_rows, [new_row])
            all_job_rows.append(new_row)
            sheets_write(companies_ws.update_cell, sheet_row, 4, today)
            return {"action": "re-added", "title": title, "evicted_jobs": old_jobs_expired}
    else:
        new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
        sheets_write(jobs_ws.append_rows, [new_row])
        all_job_rows.append(new_row)
        existing_keys.add(key)
        sheets_write(companies_ws.update_cell, sheet_row, 4, today)
        return {"action": "added", "title": title, "evicted_jobs": old_jobs_expired}
