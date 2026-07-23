import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug, expect_key


def scrape_recruitee(url: str) -> list:
    slug = (
        extract_slug(r"([^./]+)\.recruitee\.com", url)
        or extract_slug(r"recruitee\.com/o/([^/?#]+)", url)
    )
    if not slug:
        return []

    resp = requests.get(
        f"https://api.recruitee.com/c/{slug}/offers",
        timeout=15,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()

    jobs = []
    for j in expect_key(resp.json(), "offers", "Recruitee"):
        title = j.get("title", "")
        dept  = j.get("department", "") or ""
        fn    = normalize_function(dept) or normalize_function(title)
        if j.get("remote"):
            location = "Remote"
        else:
            location = normalize_location(j.get("city", "") or "")
        jobs.append({
            "job_title":       title,
            "application_url": j.get("careers_url", url),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    location,
        })
    return jobs
