import html
import re

import requests

from core.normalize import normalize_function, normalize_location

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# LinkedIn title tag format: "{Company} hiring {Job Title} in {Location} | LinkedIn"
_TITLE_RE = re.compile(r"^(.+?) hiring (.+) in [^|]+ \| LinkedIn$")


def _extract_job_id(url: str) -> str | None:
    # /jobs/view/4430589214  OR  /jobs/view/ai-engineer-at-adpipe-4430589214
    m = re.search(r"/jobs/view/(?:[^/?#]+-)?(\d+)(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    return None


def _location_from_description(desc: str) -> str:
    """
    LinkedIn's meta description snippet often contains the actual work arrangement.
    Scan for Remote/Hybrid keywords before falling back to the title's location string.
    """
    desc_lower = desc.lower()
    if "remote" in desc_lower:
        return "Remote"
    if "hybrid" in desc_lower:
        return "Hybrid"
    return ""


def fetch_linkedin_job(url: str) -> dict:
    """
    Fetch a single LinkedIn job posting from its public page.
    Returns a job dict with an extra '_company' key.
    Raises ValueError with a human-readable message on any failure.
    """
    job_id = _extract_job_id(url)
    if not job_id:
        raise ValueError("could not extract job ID from URL")

    fetch_url = f"https://www.linkedin.com/jobs/view/{job_id}"

    try:
        resp = requests.get(fetch_url, headers=_HEADERS, timeout=15, allow_redirects=True)
    except requests.exceptions.Timeout:
        raise ValueError("request timed out")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"network error — {e}")

    if resp.status_code == 404:
        raise ValueError("job not found (404) — may be expired or removed")
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}")

    if "linkedin.com/login" in resp.url or "authwall" in resp.url:
        raise ValueError("login required — job is members-only")

    title_tag = re.search(r"<title>(.*?)</title>", resp.text)
    if not title_tag:
        raise ValueError("no page title found — login may be required")

    m = _TITLE_RE.match(html.unescape(title_tag.group(1)))
    if not m:
        raise ValueError("unexpected page title format — job may be expired or removed")

    company   = m.group(1).strip()
    job_title = m.group(2).strip()

    desc_tag  = re.search(r'<meta name="description" content="([^"]+)"', resp.text)
    desc_text = desc_tag.group(1) if desc_tag else ""
    location  = _location_from_description(desc_text) or normalize_location("")

    return {
        "job_title":       job_title,
        "application_url": fetch_url,
        "job_function":    normalize_function(""),
        "job_location":    location,
        "is_evergreen":    False,
        "_company":        company,
    }
