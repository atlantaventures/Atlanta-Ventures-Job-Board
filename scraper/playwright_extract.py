"""
Job Scraper
Reads the company list from Google Sheets, scrapes each career page,
and writes job listings back to the Jobs tab.

Run:
    python scraper/playwright_extract.py
"""

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import anthropic
import gspread
import requests
from dotenv import find_dotenv, load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv(find_dotenv())

_REQUIRED_VARS = ["ANTHROPIC_API_KEY", "SHEET_ID"]
_missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    sys.exit(1)

# ── CONFIG ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SHEET_ID          = os.environ["SHEET_ID"]
CREDENTIALS_FILE  = Path(__file__).parent.parent / "config" / "google_credentials.json"
COMPANIES_TAB     = "Companies"
JOBS_TAB          = "Jobs"
DELAY_SECONDS     = 2
PAGE_TIMEOUT      = 15000
# ─────────────────────────────────────────────────────────────────────────────


# ── VALUE NORMALIZATION ───────────────────────────────────────────────────────
# These ensure every value written to the sheet matches what WordPress accepts exactly.

VALID_FUNCTIONS = {"Engineering", "Sales", "Marketing", "Operations", "Finance"}
VALID_LOCATIONS = {"Remote", "In Person", "Hybrid"}


def normalize_function(raw: str) -> str:
    """Map a raw job function/team string to one of the 5 allowed values, or ''."""
    if not raw:
        return ""
    r = raw.lower().strip()
    # Exact match
    for f in VALID_FUNCTIONS:
        if f.lower() == r:
            return f
    # Finance first — must precede Engineering to catch "Financial Analyst", "Accounting", etc.
    if any(k in r for k in ["financ", "accounting", "invest", "treasury", "fp&a", "controller"]):
        return "Finance"
    # "developer" not "develop" — avoids matching "Sales Development Representative"
    if any(k in r for k in ["engineer", "developer", "software", "data", "product", "design", "qa", "devops", "infra", "security", "techni", "tech lead", "tech manager", "ai", "ml", "machine learn", "platform", "ux", "research"]):
        return "Engineering"
    if any(k in r for k in ["sale", "business dev", "account exec", "revenue", "bdr", "sdr"]):
        return "Sales"
    if any(k in r for k in ["market", "growth", "brand", "content", "seo", "demand"]):
        return "Marketing"
    if any(k in r for k in ["operat", "people", "human res", "recruit", "admin", "customer success", "support", "legal", "compli", "implement", "general manager", "event", "relation", "coordinator", "specialist"]):
        return "Operations"
    return ""


def normalize_location(raw: str) -> str:
    """Map a raw location string to Remote / Hybrid / In Person. Defaults to In Person."""
    r = (raw or "").lower().strip()
    if "remote" in r:
        return "Remote"
    if "hybrid" in r:
        return "Hybrid"
    return "In Person"


# ── ATS PLATFORM DETECTION ───────────────────────────────────────────────────

def detect_platform(url: str) -> str:
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    return "custom"


def _slug(pattern: str, url: str) -> str:
    match = re.search(pattern, url)
    return match.group(1) if match else ""


# ── ATS API SCRAPERS ─────────────────────────────────────────────────────────

def scrape_greenhouse(url: str) -> list:
    slug = _slug(r"greenhouse\.io/([^/?#]+)", url)
    if not slug:
        return []
    resp = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        timeout=10,
    )
    resp.raise_for_status()
    jobs = []
    for j in resp.json().get("jobs", []):
        title = j.get("title", "")
        dept  = j.get("departments", [{}])[0].get("name", "") if j.get("departments") else ""
        fn    = normalize_function(dept) or normalize_function(title)
        jobs.append({
            "job_title":       title,
            "application_url": j.get("absolute_url", ""),
            "job_function":    fn,
            "job_description": "",
            "is_evergreen":    False,
            "job_location":    normalize_location(j.get("location", {}).get("name", "")),
        })
    return jobs


def scrape_lever(url: str) -> list:
    slug = _slug(r"lever\.co/([^/?#]+)", url)
    if not slug:
        return []
    resp = requests.get(
        f"https://api.lever.co/v0/postings/{slug}?mode=json",
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    jobs = []
    for p in data:
        title = p.get("text", "")
        team  = p.get("categories", {}).get("team", "")
        fn    = normalize_function(team) or normalize_function(title)
        jobs.append({
            "job_title":       title,
            "application_url": p.get("hostedUrl", ""),
            "job_function":    fn,
            "job_description": "",
            "is_evergreen":    False,
            "job_location":    normalize_location(p.get("categories", {}).get("location", "")),
        })
    return jobs


# ── PLAYWRIGHT + CLAUDE (custom pages) ───────────────────────────────────────

def get_page_content(page, url: str) -> str:
    page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    for _ in range(5):
        clicked = False
        for selector in [
            "button:has-text('Load More')", "button:has-text('Show More')",
            "button:has-text('View More')", "button:has-text('See More')",
            "a:has-text('Load More')",      "a:has-text('Show More')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    page.wait_for_timeout(2000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break

    links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a'))
            .map(a => ({ text: a.innerText.trim(), href: a.href }))
            .filter(a => a.text && a.href && a.href.startsWith('http'))
    """)
    text = page.inner_text("body")

    # Prefer links whose path looks job-related so nav/footer links don't crowd out job URLs
    job_path_hints = ["/job", "/career", "/opening", "/position", "/apply", "/role", "/vacancy", "/hire", "/posting", "/opportunity"]
    job_links = [l for l in links if any(p in l["href"].lower() for p in job_path_hints)]
    display_links = job_links if job_links else links
    links_formatted = "\n".join(f"LINK: {l['text']} -> {l['href']}" for l in display_links if l["text"])

    return f"PAGE TEXT:\n{text[:15000]}\n\nALL PAGE LINKS:\n{links_formatted[:10000]}"


def extract_jobs_with_claude(client: anthropic.Anthropic, company: str, content: str) -> list:
    prompt = f"""You are a data extraction assistant. Below is content from {company}'s careers page.

Extract all job listings and return a JSON array where each item has exactly these fields:
- "job_title": title of the role (string)
- "application_url": direct URL to that specific job posting — match job titles to links in ALL PAGE LINKS. Use "" if not found. (string)
- "job_location": MUST be exactly one of: "Remote", "Hybrid", "In Person". Default to "In Person" if not explicitly mentioned. (string)
- "job_function": MUST be exactly one of: "Engineering", "Sales", "Marketing", "Operations", "Finance". Use "" if none fits. (string)
- "is_evergreen": true ONLY for generic "always hiring" / "send us your resume" listings with no specific headcount — e.g. "General Application", "Join our talent pool". If there is a real job title and/or a direct URL to the posting, this is false. Default to false. (boolean)

Rules:
- Return ONLY a valid JSON array, no explanation, no markdown, no code fences
- job_location and job_function must use the exact strings listed above or ""
- If no jobs found, return []
- No duplicates

Page content:
{content}
"""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"    Claude returned invalid JSON for job extraction — skipping")
        return []
    if not isinstance(result, list):
        return []
    for job in result:
        job["job_location"] = normalize_location(job.get("job_location", ""))
        job["job_function"] = normalize_function(job.get("job_function", ""))
        job.pop("job_description", None)
        # A job with a direct URL is never evergreen — override Claude's guess
        if job.get("application_url"):
            job["is_evergreen"] = False
    return result


# ── GENERIC LISTING FILTER ───────────────────────────────────────────────────

_GENERIC_PATTERNS = [
    "talent network", "talent pool", "talent community",
    "general application", "open application", "general interest",
    "future opportunities", "future openings", "future roles",
    "join our team", "stay connected", "get in touch",
    "not seeing a fit", "don't see a fit", "don't see your role",
]

def is_generic_listing(title: str) -> bool:
    """Return True for broad 'always open' listings that aren't real job postings."""
    t = title.lower().strip()
    return any(p in t for p in _GENERIC_PATTERNS)


# ── RELEVANCE FILTER ─────────────────────────────────────────────────────────

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

SKIP if the role is a service, trade, manual labor, or part-time non-professional role (cook, driver, cleaner, barista, server, retail associate, warehouse worker, security guard, etc.)
KEEP if the role is a professional business position. When in doubt, KEEP.

Function must be exactly one of these 5 values:
- "Engineering" — software, data, AI/ML, infrastructure, product, design, QA
- "Sales" — AEs, SDRs, BDRs, account management, revenue
- "Marketing" — demand gen, brand, content, growth, SEO
- "Finance" — accounting, FP&A, financial analysis, treasury, controller
- "Operations" — everything else: HR, recruiting, legal, customer success, project/program management, general management, events, implementation, compliance, specialist roles

Use "" only if the title gives no useful signal at all.

Jobs:
{job_list}

Return a JSON array with one entry per job in the same order:
[{{"keep": true, "reason": "brief reason", "function": "Engineering"}}, ...]

Return ONLY the JSON array, no explanation, no markdown."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
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


# ── DEDUPLICATION ────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Normalize a URL so the same job posting always produces the same key."""
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0].rstrip("/")
    url = url.lower()
    url = re.sub(r"^(https?://)www\.", r"\1", url)
    return url


def job_key(company: str, job: dict) -> str:
    """Primary key is the normalized URL; fall back to company+title if no URL."""
    url = normalize_url(job.get("application_url", ""))
    if url:
        return url
    return f"{company}|{job.get('job_title', '')}".lower()


def load_existing_keys(*worksheets) -> set:
    """
    Load all job keys already processed (Jobs tab + Skipped tab).
    Prevents re-adding or re-evaluating anything seen in a prior run.
    Application URL is always column C (index 2) in both tabs.

    Expired and removed rows are excluded so that jobs which disappeared
    and later reappear on the careers page can be re-scraped and re-synced.
    The Skipped tab has no WP Status column, so its rows are always included.
    """
    keys = set()
    for ws in worksheets:
        rows = ws.get_all_values()
        for row in rows[1:]:
            wp_status = row[7].strip().lower() if len(row) > 7 else ""
            if wp_status in ("expired", "removed"):
                continue
            company   = row[0] if len(row) > 0 else ""
            job_title = row[1] if len(row) > 1 else ""
            app_url   = row[2] if len(row) > 2 else ""
            url_norm  = normalize_url(app_url)
            keys.add(url_norm if url_norm else f"{company}|{job_title}".lower())
    return keys


# ── EXPIRY ───────────────────────────────────────────────────────────────────

def expire_removed_jobs(jobs_ws, company: str, current_keys: set, all_job_rows: list) -> int:
    """
    Mark Jobs tab rows 'expired' for jobs no longer found on the careers page.
    Only called when the fresh scrape returned at least one result.
    Mutates all_job_rows in place to keep the in-memory copy current.
    Returns the number of rows marked expired.
    """
    expired = 0
    for i, row in enumerate(all_job_rows[1:], start=2):
        if not row or row[0] != company:
            continue
        wp_status = row[7].strip().lower() if len(row) > 7 else ""
        if wp_status in ("expired", "removed"):
            continue
        app_url = row[2].strip() if len(row) > 2 else ""
        title   = row[1].strip() if len(row) > 1 else ""
        key = normalize_url(app_url) if app_url else f"{company}|{title}".lower()
        if key and key not in current_keys:
            jobs_ws.update_cell(i, 8, "expired")
            all_job_rows[i - 1][7] = "expired"
            expired += 1
    return expired


def expire_stale_evergreen_rows(jobs_ws, company: str, current_key: str, all_job_rows: list) -> int:
    """
    Mark active Jobs tab rows for an evergreen company as expired when their
    URL no longer matches the current Careers URL in the Companies tab.
    This handles the case where a user updates the URL for an evergreen company —
    without this, the old WP post would be orphaned and never cleaned up.
    Mutates all_job_rows in place to keep the in-memory copy current.
    Returns the number of rows marked expired.
    """
    expired = 0
    for i, row in enumerate(all_job_rows[1:], start=2):
        if not row or row[0] != company:
            continue
        wp_status = row[7].strip().lower() if len(row) > 7 else ""
        if wp_status in ("expired", "removed"):
            continue
        row_key = normalize_url(row[2]) if len(row) > 2 else ""
        if row_key and row_key != current_key:
            jobs_ws.update_cell(i, 8, "expired")
            all_job_rows[i - 1][7] = "expired"
            expired += 1
    return expired


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    companies_ws = sh.worksheet(COMPANIES_TAB)
    jobs_ws      = sh.worksheet(JOBS_TAB)
    skipped_ws   = sh.worksheet("Skipped")

    all_rows      = companies_ws.get_all_records()
    today         = date.today().isoformat()
    claude        = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    existing_keys = load_existing_keys(jobs_ws, skipped_ws)
    all_job_rows  = jobs_ws.get_all_values()

    print(f"{len(existing_keys)} job(s) already processed — will skip duplicates\n")

    # sheet_row is 1-indexed; +2 accounts for header row + 0-indexed offset
    to_scrape = [
        (sheet_row, row)
        for sheet_row, row in enumerate(all_rows, start=2)
        if row.get("Careers URL", "").strip()
    ]

    print(f"Found {len(to_scrape)} companies with URLs\n")

    success_count   = 0
    fail_count      = 0
    error_count     = 0
    total_jobs      = 0
    duplicate_jobs  = 0
    filtered_jobs   = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        for i, (sheet_row, row) in enumerate(to_scrape, start=1):
            company            = row["Company"]
            url                = row["Careers URL"].strip()
            evergreen_company  = str(row.get("Evergreen", "")).strip().lower() == "true"
            platform           = detect_platform(url)

            print(f"[{i}/{len(to_scrape)}] {company}  ({'evergreen' if evergreen_company else platform})")

            try:
                if evergreen_company:
                    key          = normalize_url(url)
                    custom_title = row.get("Custom Title", "").strip()
                    description  = row.get("Description", "").strip()
                    title        = custom_title if custom_title else "View All Openings"

                    n_stale = expire_stale_evergreen_rows(jobs_ws, company, key, all_job_rows)
                    if n_stale:
                        print(f"    {n_stale} stale URL row(s) marked expired (Careers URL changed)")

                    if key in existing_keys:
                        # Find the active (non-expired/removed) row to compare against.
                        # Expired rows are skipped — comparing against them causes false updates.
                        active_row = None
                        for j, jrow in enumerate(all_job_rows[1:], start=2):
                            row_key   = normalize_url(jrow[2]) if len(jrow) > 2 else ""
                            wp_status = jrow[7] if len(jrow) > 7 else ""
                            if jrow[0] == company and row_key == key:
                                if wp_status.lower() in ("expired", "removed"):
                                    continue
                                active_row = (j, jrow)
                                break

                        updated = False
                        if active_row:
                            j, jrow       = active_row
                            current_title = jrow[1] if len(jrow) > 1 else ""
                            current_desc  = jrow[8] if len(jrow) > 8 else ""
                            if current_title != title or current_desc != description:
                                jobs_ws.update_cell(j, 8, "expired")
                                all_job_rows[j - 1][7] = "expired"
                                new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
                                jobs_ws.append_rows([new_row])
                                all_job_rows.append(new_row)
                                total_jobs += 1
                                success_count += 1
                                updated = True
                                print(f"    Updated evergreen listing\n")
                        else:
                            # All prior rows expired/removed — re-add fresh
                            new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
                            jobs_ws.append_rows([new_row])
                            all_job_rows.append(new_row)
                            total_jobs += 1
                            success_count += 1
                            updated = True
                            print(f"    Re-added evergreen listing\n")

                        if not updated:
                            print(f"    Already processed (no changes)\n")
                    else:
                        new_row = [company, title, url, "Engineering", True, "In Person", today, "", description]
                        jobs_ws.append_rows([new_row])
                        all_job_rows.append(new_row)
                        existing_keys.add(key)
                        total_jobs += 1
                        success_count += 1
                        print(f"    Added evergreen listing\n")
                    companies_ws.update_cell(sheet_row, 4, today)
                    if i < len(to_scrape):
                        time.sleep(DELAY_SECONDS)
                    continue

                if platform == "greenhouse":
                    jobs = scrape_greenhouse(url)
                elif platform == "lever":
                    jobs = scrape_lever(url)
                else:
                    content = get_page_content(page, url)
                    if not content.strip():
                        print(f"    Empty page — skipping\n")
                        fail_count += 1
                        continue
                    jobs = extract_jobs_with_claude(claude, company, content)

                # Apply careers URL fallback before dedup so the key is consistent across runs
                for j in jobs:
                    if not j.get("application_url"):
                        j["application_url"] = url

                # Mark jobs no longer on the careers page as expired (guard: only if scrape returned results)
                if jobs:
                    current_keys = {job_key(company, j) for j in jobs}
                    n_expired = expire_removed_jobs(jobs_ws, company, current_keys, all_job_rows)
                    if n_expired:
                        print(f"    {n_expired} job(s) marked expired")

                # Drop anything already processed in a prior run
                unseen   = [j for j in jobs if job_key(company, j) not in existing_keys]
                dupes    = len(jobs) - len(unseen)
                if dupes:
                    duplicate_jobs += dupes

                if not unseen:
                    print(f"    No new jobs ({dupes} already processed)\n" if dupes else "    No jobs found\n")
                    fail_count += 1
                    continue

                # Deterministic pre-filter: catch generic talent pool listings before Claude
                pre_skipped = [j for j in unseen if is_generic_listing(j.get("job_title", ""))]
                unseen      = [j for j in unseen if not is_generic_listing(j.get("job_title", ""))]
                for j in pre_skipped:
                    j["skip_reason"] = "Generic talent pool / always-open listing"

                # Relevance filter
                kept, skipped = filter_jobs_with_claude(claude, company, unseen)
                skipped = pre_skipped + skipped
                filtered_jobs += len(skipped)

                summary = f"    {len(kept)} relevant"
                if dupes:
                    summary += f"  ({dupes} already processed)"
                if skipped:
                    summary += f"  ({len(skipped)} filtered out)"
                print(summary + "\n")

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
                            "",  # WP Status — blank until sync script runs
                        ]
                        for j in kept
                    ]
                    jobs_ws.append_rows(new_rows)
                    all_job_rows.extend(new_rows)
                    for j in kept:
                        existing_keys.add(job_key(company, j))
                    total_jobs += len(kept)
                    success_count += 1

                if skipped:
                    skipped_ws.append_rows([
                        [
                            company,
                            j.get("job_title", ""),
                            j.get("application_url", ""),
                            j.get("skip_reason", ""),
                            today,
                        ]
                        for j in skipped
                    ])
                    for j in skipped:
                        existing_keys.add(job_key(company, j))

                companies_ws.update_cell(sheet_row, 4, today)

            except Exception as e:
                print(f"    FAILED — {e}\n")
                fail_count  += 1
                error_count += 1

            if i < len(to_scrape):
                time.sleep(DELAY_SECONDS)

        browser.close()

    print("=" * 50)
    print(f"DONE")
    print(f"  Companies with relevant jobs   : {success_count}")
    print(f"  Companies with errors/no jobs  : {fail_count}")
    print(f"  New jobs written to Jobs tab   : {total_jobs}")
    print(f"  Irrelevant jobs filtered out   : {filtered_jobs}")
    print(f"  Duplicates skipped             : {duplicate_jobs}")
    if error_count:
        print(f"  Script errors                  : {error_count}")
    print("=" * 50)
    if error_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
