import base64
import json
import re

import anthropic
import requests

from core.normalize import normalize_function, normalize_location


def _normalize_pdf_url(url: str) -> str:
    """Convert a Google Drive share link to a direct download URL."""
    m = re.search(r"drive\.google\.com/file/d/([^/?#]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def scrape_pdf(client: anthropic.Anthropic, company: str, url: str) -> list:
    """Download a PDF and use Claude's native document support to extract job listings."""
    url = _normalize_pdf_url(url)
    resp = requests.get(
        url, timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
    )
    resp.raise_for_status()

    pdf_b64 = base64.standard_b64encode(resp.content).decode("utf-8")

    prompt = f"""You are a data extraction assistant. The attached PDF is from {company}'s careers page.

Extract all job listings and return a JSON array where each item has exactly these fields:
- "job_title": title of the role (string)
- "application_url": direct URL to that specific job posting if one is present in the document. Use "" if not found. (string)
- "job_location": MUST be exactly one of: "Remote", "Hybrid", "In Person". Default to "In Person" if unclear. "Work From Home", "WFH", "Anywhere", "Distributed" = Remote. A location that says "[City] (preferred) or Remote" = "In Person". (string)
- "job_function": MUST be exactly one of: "Engineering", "Sales", "Marketing", "Operations", "Finance". Use "" if none fits. Engineering = software/data/AI/infra/product/design/QA/robotics/hardware/architecture/analytics/science/manufacturing. Sales = AEs/SDRs/BDRs/revenue/account exec. Marketing = demand gen/brand/content/growth/SEO/communications/PR/social media/advertising. Finance = accounting/FP&A/audit/tax/payroll/treasury. Operations = everything else (HR/recruiting/talent/legal/customer success/project management/program management/procurement/supply chain/logistics/facilities/compliance/implementation/events). (string)
- "is_evergreen": true ONLY for generic "always hiring" / "send us your resume" listings with no specific headcount. Default to false. (boolean)

Rules:
- Return ONLY a valid JSON array, no explanation, no markdown, no code fences
- job_location and job_function must use the exact strings listed above or ""
- If no jobs found, return []
- No duplicates

PDF content is attached above."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5000,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
        }],
    )

    text = response.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"    Claude returned invalid JSON for PDF extraction — skipping")
        return []
    if not isinstance(result, list):
        return []
    for job in result:
        job["job_location"] = normalize_location(job.get("job_location", ""))
        job["job_function"]  = normalize_function(job.get("job_function", ""))
        job.pop("job_description", None)
        if job.get("application_url"):
            job["is_evergreen"] = False
    return result
