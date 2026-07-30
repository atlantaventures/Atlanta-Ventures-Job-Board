import requests

from core.normalize import normalize_function, normalize_location
from core.utils import extract_slug, expect_list


def scrape_greenhouse(url: str) -> list:
    slug = extract_slug(r"greenhouse\.io/([^/?#]+)", url)
    if not slug:
        return []
    resp = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        timeout=10,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    jobs = []
    for j in expect_list(resp.json(), "Greenhouse"):
        title = j.get("title", "")
        departments = j.get("departments") or []
        dept  = departments[0].get("name", "") if departments else ""
        fn    = normalize_function(dept) or normalize_function(title)
        jobs.append({
            "job_title":       title,
            "application_url": j.get("absolute_url", ""),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    normalize_location((j.get("location") or {}).get("name", "")),
        })
    return jobs
