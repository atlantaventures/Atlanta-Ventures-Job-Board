import re
import time

import gspread


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
    if "docs.google.com/document" in url:
        return "googledoc"
    if "linkedin.com/company" in url:
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
