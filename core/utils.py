import re
import time

import gspread


class ScrapeShapeError(Exception):
    """Raised when a fetcher gets a successful (200 OK) response but the body isn't
    shaped as expected — e.g. a renamed/missing key, or a list where a dict was
    expected. This signals an upstream API or extraction change, not "zero jobs
    found," so callers (job_loader.py) must treat it as a scrape failure rather
    than a legitimate empty result — otherwise a silent shape change looks
    identical to a company with no open roles, and can wrongly expire real jobs
    from other URLs for the same company."""


def expect_list(value, context: str) -> list:
    """Assert `value` is a list. A present empty list is a legitimate "no jobs"
    result; anything else (a string, dict, None, ...) means the response shape
    changed, so raise instead of silently returning []."""
    if not isinstance(value, list):
        raise ScrapeShapeError(f"{context}: expected a list, got {type(value).__name__}")
    return value


def expect_key(data, key: str, context: str) -> list:
    """Assert `data` is a dict containing `key`, and that its value is a list.
    A present-but-empty list is a legitimate "no jobs" result; a missing key,
    a non-dict body, or a wrong-shaped value means the response shape changed."""
    if not isinstance(data, dict):
        raise ScrapeShapeError(f"{context}: expected a JSON object, got {type(data).__name__}")
    if key not in data:
        raise ScrapeShapeError(f"{context}: response is missing expected key '{key}'")
    return expect_list(data[key], f"{context}.{key}")


def compute_platform_wide_breaks(platform_attempts: dict, platform_failures: dict) -> list:
    """
    A platform where every attempted company failed this run is a much stronger
    signal than an isolated failure — almost always the vendor's ATS API/response
    shape changed, not that N unrelated companies all took their careers pages
    down at once. Require at least 2 attempts so a single company's bad URL
    doesn't get misread as a platform-wide break.
    """
    return sorted(
        platform for platform, attempts in platform_attempts.items()
        if attempts >= 2 and len(platform_failures.get(platform, ())) == attempts
    )


def sheets_write(fn, *args, **kwargs):
    """Call a gspread write method, retrying up to 3 times on 429 quota errors."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            if "429" in str(e) and attempt < 2:
                print("    Sheets rate limit — waiting 60s...")
                time.sleep(60)
            else:
                raise


def detect_platform(url: str) -> str:
    """Route a careers URL to the appropriate fetcher."""
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "ashbyhq.com" in url:
        return "ashby"
    if "aft" in url:
        return "workable"
    if "smartrecruiters.com" in url:
        return "smartrecruiters"
    if "breezy.hr" in url:
        return "breezy"
    if "recruitee.com" in url:
        return "recruitee"
    if "docs.google.com/document" in url:
        return "googledoc"
    if "linkedin.com" in url:
        return "linkedin"
    if re.search(r"drive\.google\.com/(file/d/|uc\?)", url):
        return "pdf"
    lower = url.lower()
    if lower.endswith(".pdf") or re.search(r"\.pdf[?#]", lower):
        return "pdf"
    return "custom"


def extract_slug(pattern: str, url: str) -> str:
    """Pull a capture group from a URL using a regex pattern."""
    match = re.search(pattern, url)
    return match.group(1) if match else ""
