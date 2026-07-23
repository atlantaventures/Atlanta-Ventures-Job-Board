#!/usr/bin/env python3
"""
Failure-handling regression suite. Fast, fully mocked, zero external calls —
run this before proposing or shipping any change to a fetcher, job_loader.py,
notify.py, or sync/wp_sync.py.

Covers the specific bug class this repo is built to avoid: a fetcher silently
returning [] on an unexpected API shape (instead of raising) can cause a
company's real, still-live jobs to be wrongly expired from WordPress. See
CLAUDE.md — "Why the system is built to fail safe" — before touching any of
the files this suite covers.

  Section 1 — Fetcher shape guards   (mocked HTTP, zero external calls)
  Section 2 — skip_expiry protection (in-memory, zero external calls)
  Section 3 — platform_wide_breaks   (pure logic)
  Section 4 — notify.py Slack blocks (pure logic)
  Section 5 — wp_sync pagination     (mocked HTTP)
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

for _k, _v in {
    "WP_URL": "https://fake-wp.test", "WP_USERNAME": "test", "WP_APP_PASSWORD": "test",
    "SHEET_ID": "fake-sheet-id", "ANTHROPIC_API_KEY": "fake", "WEBHOOK_SECRET": "test-secret",
    "ANTHROPIC_MODEL": "fake-model",
}.items():
    os.environ.setdefault(_k, _v)

_results = []

def test(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        _results.append((name, True, ""))
    except AssertionError as e:
        msg = str(e) or "assertion failed"
        print(f"  FAIL  {name}: {msg}")
        _results.append((name, False, msg))
    except Exception as e:
        print(f"  ERR   {name}: {type(e).__name__}: {e}")
        _results.append((name, False, f"{type(e).__name__}: {e}"))

def section(title):
    print(f"\n{'─'*60}\n  {title}\n{'─'*60}")

def fake_response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body
    if status_code >= 400 and status_code != 404:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


class FakeWS:
    """Matches test_production.py's FakeWS — reused so both suites stay consistent."""
    def __init__(self, rows):
        self._rows = [list(r) for r in rows]
        self.calls = []

    def get_all_values(self):
        return [list(r) for r in self._rows]

    def update_cell(self, row, col, value):
        self.calls.append(("update_cell", row, col, value))
        while len(self._rows) < row:
            self._rows.append([])
        while len(self._rows[row - 1]) < col:
            self._rows[row - 1].append("")
        self._rows[row - 1][col - 1] = value

    def append_rows(self, rows):
        self.calls.append(("append_rows", [list(r) for r in rows]))
        for r in rows:
            self._rows.append(list(r))


# ═══════════════════════════════════════════════════════════════════════════
section("Section 1 — Fetcher shape guards (mocked HTTP)")
# ═══════════════════════════════════════════════════════════════════════════

from core.utils import ScrapeShapeError

def _t():
    from fetchers.lever import scrape_lever
    with patch("fetchers.lever.requests.get", return_value=fake_response(200, [])):
        assert scrape_lever("https://jobs.lever.co/acme") == [], "genuine empty list must not raise"
test("Lever: genuine empty result returns []", _t)

def _t():
    from fetchers.lever import scrape_lever
    with patch("fetchers.lever.requests.get", return_value=fake_response(200, {"not": "a list"})):
        try:
            scrape_lever("https://jobs.lever.co/acme")
            assert False, "shape-broken response must raise ScrapeShapeError"
        except ScrapeShapeError:
            pass
test("Lever: dict instead of list raises ScrapeShapeError", _t)

def _t():
    from fetchers.lever import scrape_lever
    with patch("fetchers.lever.requests.get", return_value=fake_response(404)):
        assert scrape_lever("https://jobs.lever.co/gone") == [], "404 must still return [] unaffected"
test("Lever: 404 still returns [] (unaffected by shape check)", _t)

def _t():
    from fetchers.breezy import scrape_breezy
    with patch("fetchers.breezy.requests.get", return_value=fake_response(200, {"jobs": []})):
        try:
            scrape_breezy("https://acme.breezy.hr/jobs")
            assert False, "top-level dict instead of list must raise"
        except ScrapeShapeError:
            pass
test("Breezy: dict instead of list raises ScrapeShapeError", _t)

def _t():
    from fetchers.ashby import scrape_ashby
    with patch("fetchers.ashby.requests.get", return_value=fake_response(200, {"postings": []})):
        try:
            scrape_ashby("https://jobs.ashbyhq.com/acme")
            assert False, "renamed key ('postings' not 'jobs') must raise"
        except ScrapeShapeError:
            pass
test("Ashby: renamed 'jobs' key raises ScrapeShapeError", _t)

def _t():
    from fetchers.ashby import scrape_ashby
    with patch("fetchers.ashby.requests.get", return_value=fake_response(200, {"jobs": []})):
        assert scrape_ashby("https://jobs.ashbyhq.com/acme") == []
test("Ashby: genuine empty {'jobs': []} returns []", _t)

def _t():
    from fetchers.greenhouse import scrape_greenhouse
    with patch("fetchers.greenhouse.requests.get", return_value=fake_response(200, [])):
        try:
            scrape_greenhouse("https://boards.greenhouse.io/acme")
            assert False, "top-level list instead of dict must raise"
        except ScrapeShapeError:
            pass
test("Greenhouse: top-level list instead of dict raises ScrapeShapeError", _t)

def _t():
    from fetchers.smartrecruiters import scrape_smartrecruiters
    with patch("fetchers.smartrecruiters.requests.get", return_value=fake_response(200, {"totalFound": 0})):
        try:
            scrape_smartrecruiters("https://smartrecruiters.com/acme")
            assert False, "missing 'content' key must raise"
        except ScrapeShapeError:
            pass
test("SmartRecruiters: missing 'content' key raises ScrapeShapeError", _t)

def _t():
    from fetchers.workable import scrape_workable
    with patch("fetchers.workable.requests.post", return_value=fake_response(200, {"jobs": [], "cursor": None})):
        try:
            scrape_workable("https://apply.workable.com/acme")
            assert False, "renamed 'results' key must raise"
        except ScrapeShapeError:
            pass
test("Workable: renamed 'results' key raises ScrapeShapeError", _t)

def _t():
    from fetchers.recruitee import scrape_recruitee
    with patch("fetchers.recruitee.requests.get", return_value=fake_response(200, {"offers": "not-a-list"})):
        try:
            scrape_recruitee("https://acme.recruitee.com")
            assert False, "wrong-typed 'offers' value must raise"
        except ScrapeShapeError:
            pass
test("Recruitee: wrong-typed 'offers' value raises ScrapeShapeError", _t)

def _t():
    from fetchers.web_scraper import extract_jobs_with_claude
    fake_client = MagicMock()
    fake_client.messages.create.return_value.content = [MagicMock(text="not valid json")]
    try:
        extract_jobs_with_claude(fake_client, "Acme", "some page content")
        assert False, "invalid JSON from Claude must raise"
    except ScrapeShapeError:
        pass
test("web_scraper: invalid Claude JSON raises ScrapeShapeError", _t)

def _t():
    from fetchers.doc_loader import scrape_google_doc
    with patch("fetchers.doc_loader.requests.get", return_value=fake_response(200, text="Sign in to continue")):
        try:
            scrape_google_doc(MagicMock(), "Acme", "https://docs.google.com/document/d/abc123/edit")
            assert False, "short permission-denied-shaped text must raise"
        except ScrapeShapeError:
            pass
test("doc_loader: short (<80 char) export raises ScrapeShapeError", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 2 — skip_expiry protection (the actual bug this repo prevents)")
# ═══════════════════════════════════════════════════════════════════════════

from staged_job_writer import process_company_jobs

def _t():
    """
    Acme has an existing Lever-sourced job in the sheet. This run, Lever's fetch
    raised (has_fetch_errors=True -> skip_expiry=True), and only a Greenhouse job
    came back. The Lever row must survive untouched.
    """
    jobs_ws = FakeWS([
        ["Company", "Job Title", "Application URL", "Function", "Evergreen", "Location", "Scraped At", "WP Status"],
        ["Acme", "Lever Job A", "https://jobs.lever.co/acme/abc123", "Engineering", "False", "Remote", "2026-07-01", ""],
    ])
    skipped_ws = FakeWS([["Company", "Job Title", "Application URL", "Reason", "Scraped At"]])
    companies_ws = FakeWS([["Company"]])
    fake_claude = MagicMock()
    fake_claude.messages.create.return_value.content = [
        MagicMock(text=json.dumps([{"keep": True, "reason": "professional role", "function": "Engineering"}]))
    ]
    all_job_rows = jobs_ws.get_all_values()
    stats = process_company_jobs(
        fake_claude, jobs_ws, skipped_ws, companies_ws, "Acme",
        "https://boards.greenhouse.io/acme", 2,
        [{"job_title": "Greenhouse Job B", "application_url": "https://boards.greenhouse.io/acme/xyz",
          "job_function": "", "job_location": "Remote", "is_evergreen": False}],
        existing_keys=set(), all_job_rows=all_job_rows, today="2026-07-23",
        skip_expiry=True,
    )
    expired_calls = [c for c in jobs_ws.calls if c[0] == "update_cell" and c[3] == "expired"]
    assert not expired_calls, f"Lever row must NOT be expired when skip_expiry=True, got: {expired_calls}"
    assert stats["removed_jobs"] == []
test("skip_expiry=True protects other-platform jobs from wrongful expiry", _t)

def _t():
    """Control case: same fixture, skip_expiry=False — proves the test is actually
    sensitive to the bug (i.e. this isn't a no-op check)."""
    jobs_ws = FakeWS([
        ["Company", "Job Title", "Application URL", "Function", "Evergreen", "Location", "Scraped At", "WP Status"],
        ["Acme", "Lever Job A", "https://jobs.lever.co/acme/abc123", "Engineering", "False", "Remote", "2026-07-01", ""],
    ])
    skipped_ws = FakeWS([["Company", "Job Title", "Application URL", "Reason", "Scraped At"]])
    companies_ws = FakeWS([["Company"]])
    fake_claude = MagicMock()
    fake_claude.messages.create.return_value.content = [
        MagicMock(text=json.dumps([{"keep": True, "reason": "professional role", "function": "Engineering"}]))
    ]
    all_job_rows = jobs_ws.get_all_values()
    stats = process_company_jobs(
        fake_claude, jobs_ws, skipped_ws, companies_ws, "Acme",
        "https://boards.greenhouse.io/acme", 2,
        [{"job_title": "Greenhouse Job B", "application_url": "https://boards.greenhouse.io/acme/xyz",
          "job_function": "", "job_location": "Remote", "is_evergreen": False}],
        existing_keys=set(), all_job_rows=all_job_rows, today="2026-07-23",
        skip_expiry=False,
    )
    expired_calls = [c for c in jobs_ws.calls if c[0] == "update_cell" and c[3] == "expired"]
    assert expired_calls, "control case: skip_expiry=False should wrongly expire the Lever row (proves the test is real)"
test("skip_expiry=False control case reproduces the bug (sanity check on the test itself)", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 3 — platform_wide_breaks (pure logic)")
# ═══════════════════════════════════════════════════════════════════════════

from core.utils import compute_platform_wide_breaks

def _t():
    result = compute_platform_wide_breaks({"lever": 1}, {"lever": {"Acme"}})
    assert result == [], f"1 attempt / 1 failure must NOT flag, got {result}"
test("1 attempt, 1 failure -> not flagged", _t)

def _t():
    result = compute_platform_wide_breaks({"lever": 2}, {"lever": {"Acme"}})
    assert result == [], f"2 attempts / 1 failure must NOT flag, got {result}"
test("2 attempts, 1 failure -> not flagged", _t)

def _t():
    result = compute_platform_wide_breaks({"lever": 2}, {"lever": {"Acme", "Widgetco"}})
    assert result == ["lever"], f"2/2 failures must flag, got {result}"
test("2 attempts, 2 failures -> flagged", _t)

def _t():
    result = compute_platform_wide_breaks({"lever": 5}, {"lever": {"A", "B", "C", "D"}})
    assert result == [], f"4/5 must NOT flag (not 100%), got {result}"
test("5 attempts, 4 failures -> not flagged", _t)

def _t():
    result = compute_platform_wide_breaks({}, {})
    assert result == []
test("no attempts at all -> no breaks, no KeyError", _t)

def _t():
    # same company fails on 2 of 3 attempts on the same platform -> failure SET
    # size is 1, not 2, so this must not false-positive
    result = compute_platform_wide_breaks({"lever": 3}, {"lever": {"Acme"}})
    assert result == [], f"same-company-fails-twice must not inflate the failure count, got {result}"
test("same company failing twice on one platform doesn't double-count", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 4 — notify.py Slack block rendering (pure logic)")
# ═══════════════════════════════════════════════════════════════════════════

import notify

def _t():
    blocks = notify._summary_blocks("2026-07-23", 0, 0, 0, True)
    text = blocks[1]["text"]["text"]
    assert "Jobs Synced Successfully" in text
    full_text = "\n".join(b["text"]["text"] for b in blocks if "text" in b)
    assert "couldn't be" not in full_text and "failed" not in full_text.lower(), \
        "an all-ok run should have no error content anywhere in the message"
test("all_ok=True renders success tier, no error block", _t)

def _t():
    blocks = notify._summary_blocks(
        "2026-07-23", 5, 0, 0, False, companies=5,
        failed_companies=["Acme", "Widgetco"],
        platform_wide_breaks=["lever"],
        platform_failures={"lever": ["Acme", "Widgetco"]},
    )
    full_text = "\n".join(b["text"]["text"] for b in blocks if "text" in b)
    assert "Lever" in full_text
    assert full_text.count("Acme") == 1, "company already explained by the platform-wide line must not repeat"
    assert full_text.count("Widgetco") == 1
    assert "fetchers/lever.py" in full_text
test("platform_wide_breaks dedupes against generic failed_companies list", _t)

def _t():
    for platform, expected_file in [
        ("custom", "fetchers/web_scraper.py"),
        ("googledoc", "fetchers/doc_loader.py"),
        ("pdf", "fetchers/pdf_loader.py"),
    ]:
        blocks = notify._summary_blocks(
            "2026-07-23", 2, 0, 0, False,
            failed_companies=["Acme"], platform_wide_breaks=[platform],
            platform_failures={platform: ["Acme"]},
        )
        full_text = "\n".join(b["text"]["text"] for b in blocks if "text" in b)
        assert expected_file in full_text, f"{platform} should point at {expected_file}, got: {full_text}"
test("custom/googledoc/pdf platforms point at their real fetcher file", _t)

def _t():
    blocks = notify._summary_blocks(
        "2026-07-23", 0, 0, 0, False,
        wp_crashed=True, wp_crash_error="Connection refused",
    )
    full_text = "\n".join(b["text"]["text"] for b in blocks if "text" in b)
    assert "Connection refused" in full_text
    assert "Website Sync Crashed" in full_text
    assert "Website Sync Failed" not in full_text, "crash must render distinctly from a plain sync failure"
test("wp_crashed renders distinctly from a plain sync failure", _t)

def _t():
    # must not raise with only the required positional args
    blocks = notify._summary_blocks("2026-07-23", 0, 0, 0, True)
    assert isinstance(blocks, list) and len(blocks) > 0
test("_summary_blocks works with only required args (back-compat)", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 5 — wp_sync.py pagination (mocked HTTP)")
# ═══════════════════════════════════════════════════════════════════════════

import sync.wp_sync as wp_sync

def _t():
    page1 = fake_response(200, [{"id": i, "acf": {"job_link": f"https://x.com/{i}"}} for i in range(100)])
    page2 = fake_response(400, {"code": "rest_post_invalid_page_number"})
    with patch("sync.wp_sync.requests.get", side_effect=[page1, page2]):
        jobs = wp_sync.get_existing_wp_jobs()
    assert len(jobs) == 100, "real pagination-end signal must terminate cleanly, not raise"
test("wp_sync: real 'rest_post_invalid_page_number' 400 ends pagination cleanly", _t)

def _t():
    bad_400 = fake_response(400, {"code": "rest_forbidden"}, text="Forbidden")
    with patch("sync.wp_sync.requests.get", return_value=bad_400):
        try:
            wp_sync.get_existing_wp_jobs()
            assert False, "a non-pagination 400 must raise, not be swallowed as 'no more pages'"
        except RuntimeError:
            pass
test("wp_sync: a different 400 code raises instead of silently stopping", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
passed = sum(1 for _, ok, _ in _results if ok)
failed = sum(1 for _, ok, _ in _results if not ok)
total  = len(_results)
print(f"  {passed}/{total} passed   {failed} failed")

if failed:
    print(f"\n  Failures:")
    for name, ok, msg in _results:
        if not ok:
            print(f"    ✗ {name}")
            if msg:
                print(f"      {msg}")

print(f"{'═'*60}\n")
sys.exit(0 if failed == 0 else 1)
