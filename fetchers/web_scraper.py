import json
import re

import anthropic
from playwright.sync_api import sync_playwright

from core.normalize import normalize_function, normalize_location

PAGE_TIMEOUT = 15000


def get_page_content(page, url: str) -> str:
    """Use Playwright to render a page and return its text + job-relevant links."""
    page.goto(url, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    for _ in range(10):
        clicked = False
        for selector in [
            "button:has-text('Load More')", "button:has-text('Show More')",
            "button:has-text('View More')", "button:has-text('See More')",
            "a:has-text('Load More')",      "a:has-text('Show More')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    page.wait_for_timeout(3000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break

    links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a'))
            .map(a => ({ text: a.innerText.trim(), href: a.href }))
            .filter(a => a.text && a.href && a.href.startsWith('http'))
    """)
    text = page.inner_text("body")

    # Prefer links whose path looks job-related so nav/footer links don't crowd out job URLs
    job_path_hints = ["/job", "/career", "/opening", "/position", "/apply", "/role", "/vacancy", "/hire", "/posting", "/opportunity"]
    job_links      = [l for l in links if any(p in l["href"].lower() for p in job_path_hints)]
    display_links  = job_links if job_links else links
    links_formatted = "\n".join(f"LINK: {l['text']} -> {l['href']}" for l in display_links if l["text"])

    return f"PAGE TEXT:\n{text[:30000]}\n\nALL PAGE LINKS:\n{links_formatted[:15000]}"


def extract_jobs_with_claude(client: anthropic.Anthropic, company: str, content: str) -> list:
    """Send raw page content to Claude and get back a structured list of job dicts."""
    prompt = f"""You are a data extraction assistant. Below is content from {company}'s careers page.

Extract all job listings and return a JSON array where each item has exactly these fields:
- "job_title": title of the role (string)
- "application_url": direct URL to that specific job posting — match job titles to links in ALL PAGE LINKS. Use "" if not found. (string)
- "job_location": MUST be exactly one of: "Remote", "Hybrid", "In Person". Default to "In Person" if unclear. Only use "Remote" if the specific job listing itself states it — do NOT infer remote from general company culture text, benefits sections, or other jobs. "Work From Home", "WFH", "Anywhere", "Distributed" = Remote. A location that says "[City] (preferred) or Remote" = "In Person". (string)
- "job_function": MUST be exactly one of: "Engineering", "Sales", "Marketing", "Operations", "Finance". Use "" if none fits. Engineering = software/data/AI/infra/product/design/QA/robotics/hardware/architecture/analytics/science/manufacturing. Sales = AEs/SDRs/BDRs/revenue/account exec. Marketing = demand gen/brand/content/growth/SEO/communications/PR/social media/advertising. Finance = accounting/FP&A/audit/tax/payroll/treasury. Operations = everything else (HR/recruiting/talent/legal/customer success/project management/program management/procurement/supply chain/logistics/facilities/compliance/implementation/events). (string)
- "is_evergreen": true ONLY for generic "always hiring" / "send us your resume" listings with no specific headcount — e.g. "General Application", "Join our talent pool". If there is a real job title and/or a direct URL to the posting, this is false. Default to false. (boolean)

Rules:
- Return ONLY a valid JSON array, no explanation, no markdown, no code fences
- job_location and job_function must use the exact strings listed above or ""
- If no jobs found, return []
- No duplicates

Page content:
{content}
"""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"    Claude returned invalid JSON for job extraction — skipping")
        return []
    if not isinstance(result, list):
        return []
    for job in result:
        job["job_location"] = normalize_location(job.get("job_location", ""))
        job["job_function"] = normalize_function(job.get("job_function", ""))
        job.pop("job_description", None)
        # A job with a direct URL is never evergreen — override Claude's guess
        if job.get("application_url"):
            job["is_evergreen"] = False
    return result


class WebScraper:
    """Context manager that owns the Playwright browser lifecycle."""

    _USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def __enter__(self):
        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page    = self._browser.new_page()
        self._page.set_extra_http_headers({"User-Agent": self._USER_AGENT})
        return self

    def __exit__(self, *args):
        self._browser.close()
        self._pw.stop()

    def get_content(self, url: str) -> str:
        return get_page_content(self._page, url)
