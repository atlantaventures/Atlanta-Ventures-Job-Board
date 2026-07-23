import requests

from core.normalize import normalize_function
from core.utils import expect_key


_WORKPLACE_MAP = {
    "remote":  "Remote",
    "hybrid":  "Hybrid",
    "onsite":  "In Person",
}


def scrape_ashby(url: str) -> list:
    slug = url.rstrip("/").split("/")[-1]
    if not slug:
        return []
    resp = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        timeout=10,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    jobs = []
    for j in expect_key(resp.json(), "jobs", "Ashby"):
        title    = j.get("title", "")
        team     = j.get("team", "")
        dept     = j.get("department", "")
        fn       = normalize_function(team) or normalize_function(dept) or normalize_function(title)
        wt       = (j.get("workplaceType") or "").lower()
        location = _WORKPLACE_MAP.get(wt, "In Person")
        jobs.append({
            "job_title":       title,
            "application_url": j.get("jobUrl", url),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    location,
        })
    return jobs
