import requests

from core.normalize import normalize_function
from core.utils import extract_slug


def scrape_smartrecruiters(url: str) -> list:
    slug = extract_slug(r"smartrecruiters\.com/([^/?#]+)", url)
    if not slug:
        return []

    jobs  = []
    limit = 100
    offset = 0
    while True:
        resp = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": limit, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        data    = resp.json()
        content = data.get("content", [])
        if not content:
            break

        for j in content:
            title = j.get("name", "")
            dept  = (j.get("department") or {}).get("label", "") or ""
            fn    = normalize_function(dept) or normalize_function(title)
            loc   = j.get("location") or {}
            if loc.get("remote"):
                location = "Remote"
            elif loc.get("hybrid"):
                location = "Hybrid"
            else:
                location = "In Person"
            job_id = j.get("id", "")
            jobs.append({
                "job_title":       title,
                "application_url": f"https://jobs.smartrecruiters.com/{slug}/{job_id}" if job_id else url,
                "job_function":    fn,
                "is_evergreen":    False,
                "job_location":    location,
            })

        if len(content) < limit:
            break
        offset += limit

    return jobs
