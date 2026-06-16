import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug


def scrape_greenhouse(url: str) -> list:
    slug = extract_slug(r"greenhouse\.io/([^/?#]+)", url)
    if not slug:
        return []
    resp = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        timeout=10,
    )
    resp.raise_for_status()
    jobs = []
    for j in resp.json().get("jobs", []):
        title = j.get("title", "")
        dept  = j.get("departments", [{}])[0].get("name", "") if j.get("departments") else ""
        fn    = normalize_function(dept) or normalize_function(title)
        jobs.append({
            "job_title":       title,
            "application_url": j.get("absolute_url", ""),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    normalize_location((j.get("location") or {}).get("name", "")),
        })
    return jobs
