import re


def detect_platform(url: str) -> str:
    """Route a careers URL to the appropriate fetcher."""
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "docs.google.com/document" in url:
        return "googledoc"
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
