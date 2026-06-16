import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug


def scrape_breezy(url: str) -> list:
    slug = extract_slug(r"([^./]+)\.breezy\.hr", url)
    if not slug:
        return []

    resp = requests.get(f"https://{slug}.breezy.hr/json", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []

    jobs = []
    for j in data:
        title      = j.get("name", "")
        dept       = j.get("department", "") or ""
        fn         = normalize_function(dept) or normalize_function(title)
        loc_name   = (j.get("location") or {}).get("name", "")
        location   = normalize_location(loc_name)
        friendly   = j.get("friendly_id", "") or j.get("id", "")
        jobs.append({
            "job_title":       title,
            "application_url": f"https://{slug}.breezy.hr/p/{friendly}" if friendly else url,
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    location,
        })
    return jobs
