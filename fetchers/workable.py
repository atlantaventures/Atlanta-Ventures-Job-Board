import requests

from core.normalize import normalize_function
from core.utils import extract_slug

_WORKPLACE_MAP = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "In Person",
}


def scrape_workable(url: str) -> list:
    slug = (
        extract_slug(r"apply\.workable\.com/([^/?#]+)", url)
        or extract_slug(r"([^.]+)\.workable\.com", url)
    )
    if not slug:
        return []

    jobs = []
    cursor = None
    while True:
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if cursor:
            body["cursor"] = cursor

        resp = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            json=body,
            timeout=15,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()

        for j in data.get("results", []):
            title     = j.get("title", "")
            depts     = j.get("department") or []
            dept_name = depts[0].get("name", "") if depts and isinstance(depts[0], dict) else ""
            fn        = normalize_function(dept_name) or normalize_function(title)
            wt        = (j.get("workplace") or "").lower()
            shortcode = j.get("shortcode", "")
            jobs.append({
                "job_title":       title,
                "application_url": f"https://apply.workable.com/{slug}/j/{shortcode}" if shortcode else url,
                "job_function":    fn,
                "is_evergreen":    False,
                "job_location":    _WORKPLACE_MAP.get(wt, "In Person"),
            })

        cursor = data.get("cursor")
        if not cursor or not data.get("results"):
            break

    return jobs
