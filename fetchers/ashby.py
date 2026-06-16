import requests

from core.normalize import normalize_function, normalize_location


def scrape_ashby(url: str) -> list:
    slug = url.rstrip("/").split("/")[-1]
    if not slug:
        return []
    resp = requests.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        timeout=10,
    )
    resp.raise_for_status()
    jobs = []
    for j in resp.json().get("jobPostings", []):
        title    = j.get("title", "")
        dept     = j.get("departmentName", "")
        fn       = normalize_function(dept) or normalize_function(title)
        location = j.get("locationName", "")
        wt       = j.get("employmentType", "").lower()
        jobs.append({
            "job_title":       title,
            "application_url": j.get("jobUrl", url),
            "job_function":    fn,
            "is_evergreen":    False,
            "job_location":    normalize_location(location) if location else "In Person",
        })
    return jobs
