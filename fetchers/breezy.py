import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug


def scrape_breezy(url: str) -> list:
    slug = extract_slug(r"([^./]+)\.breezy\.hr", url)
    if not slug:
        return []

    resp = requests.get(f"https://{slug}.breezy.hr/json", timeout=15)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []

    jobs = []
    for j in data:
        title    = j.get("name", "")
        dept     = j.get("department", "") or ""
        fn       = normalize_function(dept) or normalize_function(title)
        loc_obj  = j.get("location") or {}
        if loc_obj.get("is_remote"):
            location = "Remote"
        else:
            city     = loc_obj.get("city", "") or loc_obj.get("name", "") or ""
            location = normalize_location(city)
        jobs.append({
            "job_title":       title,
            "application_url": j.get("url", url),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    location,
        })
    return jobs
