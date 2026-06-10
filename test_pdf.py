"""
Manual test for the PDF fetcher and multi-URL routing.

Usage:
    # Test detection only (no API calls, no PDF required)
    python test_pdf.py --detect-only

    # Test PDF fetcher with a real URL
    python test_pdf.py <pdf_url>

    # Test mixed multi-URL (PDF + another platform, pipe-delimited as in the sheet)
    python test_pdf.py "<pdf_url>|<other_url>"

Examples:
    python test_pdf.py "https://example.com/careers.pdf"
    python test_pdf.py "https://example.com/jobs.pdf|https://boards.greenhouse.io/acme"
"""

import json
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# ── 1. Detection routing tests (no API calls) ─────────────────────────────────

from core.utils import detect_platform

DETECT_CASES = [
    # (url, expected_platform)
    ("https://example.com/careers.pdf",              "pdf"),
    ("https://example.com/jobs.pdf?v=2",             "pdf"),
    ("https://example.com/openroles.PDF",            "pdf"),
    ("https://example.com/jobs.pdf#page=2",          "pdf"),
    ("https://boards.greenhouse.io/acme",            "greenhouse"),
    ("https://jobs.lever.co/acme",                   "lever"),
    ("https://docs.google.com/document/d/abc123",    "googledoc"),
    ("https://acme.com/careers",                     "custom"),
    ("https://acme.com/join-us.html",                "custom"),
    ("https://drive.google.com/file/d/ABC123/view", "pdf"),
    ("https://drive.google.com/file/d/ABC123/view?usp=sharing", "pdf"),
    ("https://drive.google.com/uc?export=download&id=ABC123", "pdf"),
]

def run_detect_tests():
    print("=" * 55)
    print("SECTION 1 — detect_platform routing")
    print("=" * 55)
    passed = 0
    for url, expected in DETECT_CASES:
        got = detect_platform(url)
        ok  = got == expected
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  {expected:<12}  {url}")
        if not ok:
            print(f"           ^ got '{got}'")
        else:
            passed += 1
    print(f"\n  {passed}/{len(DETECT_CASES)} passed\n")
    return passed == len(DETECT_CASES)


# ── 2. PDF fetcher test ───────────────────────────────────────────────────────

def run_pdf_fetch_test(pdf_url: str):
    import anthropic
    from fetchers.pdf_loader import scrape_pdf

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    print("=" * 55)
    print("SECTION 2 — scrape_pdf()")
    print(f"  URL : {pdf_url}")
    print("=" * 55)

    client = anthropic.Anthropic(api_key=api_key)
    try:
        jobs = scrape_pdf(client, "Test Company", pdf_url)
    except Exception as e:
        print(f"  FAILED — {e}")
        return False

    if not jobs:
        print("  No jobs returned (may be correct if PDF has no listings)")
        return True

    print(f"  {len(jobs)} job(s) extracted:\n")
    for i, job in enumerate(jobs, 1):
        print(f"  [{i}] {job.get('job_title', '(no title)')}")
        print(f"       function : {job.get('job_function', '')!r}")
        print(f"       location : {job.get('job_location', '')!r}")
        print(f"       evergreen: {job.get('is_evergreen', False)}")
        print(f"       url      : {job.get('application_url', '')!r}")
        print()

    # Validate required fields
    issues = []
    for i, job in enumerate(jobs, 1):
        if not job.get("job_title"):
            issues.append(f"  Job {i}: missing job_title")
        if job.get("job_location") not in ("Remote", "Hybrid", "In Person", ""):
            issues.append(f"  Job {i}: invalid job_location '{job['job_location']}'")
        if job.get("job_function") not in ("Engineering", "Sales", "Marketing", "Operations", "Finance", ""):
            issues.append(f"  Job {i}: invalid job_function '{job['job_function']}'")
        if not isinstance(job.get("is_evergreen"), bool):
            issues.append(f"  Job {i}: is_evergreen is not a bool")

    if issues:
        print("  VALIDATION ISSUES:")
        for iss in issues:
            print(f"  {iss}")
        return False
    else:
        print("  All field validations passed.")
        return True


# ── 3. Multi-URL parsing simulation ──────────────────────────────────────────

def run_multiurl_test(raw: str):
    print("=" * 55)
    print("SECTION 3 — multi-URL parsing + routing")
    print(f"  Input: {raw!r}")
    print("=" * 55)

    urls      = [u.strip() for u in raw.split("|") if u.strip()]
    platforms = [detect_platform(u) for u in urls]

    label = " | ".join(dict.fromkeys(platforms))
    print(f"  Parsed {len(urls)} URL(s)  →  label: {label!r}\n")

    for url, plat in zip(urls, platforms):
        print(f"  {plat:<12}  {url}")

    print()

    # If there's exactly one PDF URL, also run the fetch
    pdf_urls = [u for u, p in zip(urls, platforms) if p == "pdf"]
    if pdf_urls:
        print(f"  Found {len(pdf_urls)} PDF URL(s) — running fetch on first one...\n")
        run_pdf_fetch_test(pdf_urls[0])

    # If there's a greenhouse URL, show what slug would be parsed
    greenhouse_urls = [u for u, p in zip(urls, platforms) if p == "greenhouse"]
    if greenhouse_urls:
        from core.utils import extract_slug
        for gh_url in greenhouse_urls:
            slug = extract_slug(r"greenhouse\.io/v1/boards/([^/]+)", gh_url) or \
                   extract_slug(r"greenhouse\.io/([^/?#]+)", gh_url)
            print(f"  Greenhouse slug: {slug!r}  ({gh_url})")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "--detect-only":
        run_detect_tests()
        sys.exit(0)

    raw = args[0]

    all_ok = run_detect_tests()

    if "|" in raw:
        run_multiurl_test(raw)
    else:
        run_pdf_fetch_test(raw)

    sys.exit(0 if all_ok else 1)
