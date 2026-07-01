import re

from core.utils import sheets_write

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


def normalize_url(url: str) -> str:
    """Normalize a URL so the same job posting always produces the same key."""
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0].rstrip("/")
    url = url.lower()
    url = re.sub(r"^(https?://)www\.", r"\1", url)
    return url


def job_key(company: str, job: dict) -> str:
    """Primary key is the normalized URL; fall back to company+title if no URL or if the URL
    is a Google Doc (shared across all jobs from that doc, so not a unique identifier)."""
    app_url = job.get("application_url", "")
    url = normalize_url(app_url)
    if url and "docs.google.com" not in app_url:
        return url
    return f"{company}|{job.get('job_title', '')}".lower()


def load_existing_keys(*worksheets) -> set:
    """
    Load all job keys already processed (Jobs tab + Skipped tab).
    Prevents re-adding or re-evaluating anything seen in a prior run.
    Application URL is always column C (index 2) in both tabs.

    Expired and auto-removed rows are excluded so that jobs which disappeared
    and later reappear on the careers page can be re-scraped and re-synced.
    "blocked" rows (manually removed via the App Script) are kept — that exclusion
    is permanent until the user explicitly clears the row.
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
            is_gdoc   = "docs.google.com" in app_url
            url_norm  = normalize_url(app_url) if not is_gdoc else ""
            keys.add(url_norm if url_norm else f"{company}|{job_title}".lower())
    return keys


def expire_removed_jobs(jobs_ws, company: str, current_keys: set, all_job_rows: list) -> list:
    """
    Mark Jobs tab rows 'expired' for jobs no longer found on the careers page.
    Only called when the fresh scrape returned at least one result.
    Mutates all_job_rows in place to keep the in-memory copy current.
    Returns a list of expired job titles.
    """
    expired_titles = []
    for i, row in enumerate(all_job_rows[1:], start=2):
        if not row or row[0] != company:
            continue
        wp_status = row[7].strip().lower() if len(row) > 7 else ""
        if wp_status in ("expired", "removed", "blocked"):
            continue
        app_url = row[2].strip() if len(row) > 2 else ""
        if "linkedin.com" in app_url.lower():
            continue
        title   = row[1].strip() if len(row) > 1 else ""
        is_gdoc = "docs.google.com" in app_url
        key = (normalize_url(app_url) if (app_url and not is_gdoc) else f"{company}|{title}".lower())
        if key and key not in current_keys:
            sheets_write(jobs_ws.update_cell, i, 8, "expired")
            all_job_rows[i - 1][7] = "expired"
            expired_titles.append(title)
    return expired_titles


def expire_deleted_companies(jobs_ws, active_companies: set, all_job_rows: list) -> list:
    """
    Mark Jobs tab rows as expired for companies that no longer exist in the Companies tab.
    Returns a list of {"company": str, "title": str} for every row expired.
    """
    active_lower = {c.lower() for c in active_companies}
    expired = []
    for i, row in enumerate(all_job_rows[1:], start=2):
        if not row:
            continue
        company   = row[0].strip() if len(row) > 0 else ""
        title     = row[1].strip() if len(row) > 1 else ""
        app_url   = row[2].strip() if len(row) > 2 else ""
        wp_status = row[7].strip().lower() if len(row) > 7 else ""
        if not company or wp_status in ("expired", "removed", "blocked"):
            continue
        if "linkedin.com" in app_url.lower():
            continue
        if company.lower() not in active_lower:
            sheets_write(jobs_ws.update_cell, i, 8, "expired")
            all_job_rows[i - 1][7] = "expired"
            expired.append({"company": company, "title": title})
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
        if wp_status in ("expired", "removed", "blocked"):
            continue
        row_key = normalize_url(row[2]) if len(row) > 2 else ""
        if row_key and row_key != current_key:
            sheets_write(jobs_ws.update_cell, i, 8, "expired")
            all_job_rows[i - 1][7] = "expired"
            expired += 1
    return expired
