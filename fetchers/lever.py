import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug, expect_list


def scrape_lever(url: str) -> list:
    slug = extract_slug(r"lever\.co/([^/?#]+)", url)
    if not slug:
        return []
    resp = requests.get(
        f"https://api.lever.co/v0/postings/{slug}?mode=json",
        timeout=10,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = expect_list(resp.json(), "Lever")
    jobs = []
    for p in data:
        title     = p.get("text", "")
        categories = p.get("categories") or {}
        team      = categories.get("team", "")
        fn        = normalize_function(team) or normalize_function(title)
        workplace = (p.get("workplaceType") or "").lower()
        if workplace == "remote":
            loc = "Remote"
        elif workplace == "hybrid":
            loc = "Hybrid"
        elif workplace == "on-site":
            loc = "In Person"
        else:
            loc = normalize_location(categories.get("location", ""))
        jobs.append({
            "job_title":       title,
            "application_url": p.get("hostedUrl", ""),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    loc,
        })
    return jobs
