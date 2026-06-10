import requests

from core.utils import extract_slug
from fetchers.web_scraper import extract_jobs_with_claude


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
    if not text:
        return []
    return extract_jobs_with_claude(client, company, f"PAGE TEXT:\n{text[:15000]}\n\nALL PAGE LINKS:")
