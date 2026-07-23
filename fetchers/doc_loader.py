import requests

from core.utils import extract_slug, ScrapeShapeError
from fetchers.web_scraper import extract_jobs_with_claude

# A doc genuinely listing zero jobs still has real body text (headers, a note, etc).
# Anything this short is almost always a Google "you need permission" / "not found"
# placeholder page rather than actual content — feeding that to Claude risks a
# confidently wrong answer instead of a visible failure, so raise instead.
_MIN_DOC_CHARS = 80


def scrape_google_doc(client, company: str, url: str) -> list:
    """Export a Google Doc as plain text and extract jobs via Claude."""
    doc_id = extract_slug(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not doc_id:
        return []
    resp = requests.get(
        f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
        timeout=15,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if len(text) < _MIN_DOC_CHARS:
        raise ScrapeShapeError(
            f"Google Doc export for {company} returned only {len(text)} chars — "
            f"likely a permission-denied or removed-doc page, not real content: {text[:200]!r}"
        )
    return extract_jobs_with_claude(client, company, f"PAGE TEXT:\n{text[:15000]}\n\nALL PAGE LINKS:")
