import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug


def scrape_lever(url: str) -> list:
    slug = extract_slug(r"lever\.co/([^/?#]+)", url)
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
        title     = p.get("text", "")
        team      = p.get("categories", {}).get("team", "")
        fn        = normalize_function(team) or normalize_function(title)
        workplace = p.get("workplaceType", "").lower()
        if workplace == "remote":
            loc = "Remote"
        elif workplace == "hybrid":
            loc = "Hybrid"
        elif workplace == "on-site":
            loc = "In Person"
        else:
            loc = normalize_location(p.get("categories", {}).get("location", ""))
        jobs.append({
            "job_title":       title,
            "application_url": p.get("hostedUrl", ""),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    loc,
        })
    return jobs
