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
  Section 6 — detect_platform routing (pure logic)
  Section 7 — no_job_companies reporting (pure logic)
  Section 8 — parse_flexible_date         (pure logic)
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
section("Section 6 — detect_platform routing + slug extraction (pure logic)")
# ═══════════════════════════════════════════════════════════════════════════
# Added after a real incident: commit 1f0817b (an unrelated change that added
# sheets_write) also flipped `if "workable.com" in url` to `if "aft" in url` in
# core/utils.detect_platform. Nothing caught it. The effects were both silent:
#   - every Workable URL fell through to "custom" (Playwright + a paid Claude
#     extraction call) and fetchers/workable.py became unreachable dead code;
#   - any URL merely CONTAINING "aft" (careers.draftkings.com,
#     aftership.recruitee.com) was misrouted to Workable, failed slug
#     extraction, and returned [] with no error counted anywhere.
# A misrouted company reports zero jobs, which job_loader.py counts as
# fail_count — NOT error_count — so Slack still renders a green success. Keep
# this section exhaustive: routing bugs do not announce themselves.

from core.utils import detect_platform
from fetchers.workable import scrape_workable

_ROUTING_CASES = [
    # (url, expected platform)
    ("https://boards.greenhouse.io/acme",            "greenhouse"),
    ("https://job-boards.greenhouse.io/acme",        "greenhouse"),
    ("https://jobs.lever.co/acme",                   "lever"),
    ("https://jobs.ashbyhq.com/acme",                "ashby"),
    ("https://apply.workable.com/acme/",             "workable"),
    ("https://acme.workable.com/",                   "workable"),
    ("https://apply.workable.com/acme/j/ABC123/",    "workable"),
    ("https://careers.smartrecruiters.com/acme",     "smartrecruiters"),
    ("https://acme.breezy.hr/",                      "breezy"),
    ("https://acme.recruitee.com/",                  "recruitee"),
    ("https://docs.google.com/document/d/xyz/edit",  "googledoc"),
    ("https://www.linkedin.com/jobs/view/123",       "linkedin"),
    ("https://drive.google.com/file/d/xyz/view",     "pdf"),
    ("https://acme.com/careers/openings.pdf",        "pdf"),
    ("https://acme.com/careers",                     "custom"),
]

for _url, _want in _ROUTING_CASES:
    def _t(url=_url, want=_want):
        got = detect_platform(url)
        assert got == want, f"{url} routed to {got!r}, expected {want!r}"
    test(f"detect_platform: {_url} -> {_want}", _t)

# The specific regression: substrings must never decide routing. Each of these
# contains "aft" and must route on its real domain, not on Workable.
_AFT_TRAPS = [
    ("https://careers.draftkings.com/",     "custom"),
    ("https://aftership.recruitee.com/",    "recruitee"),
    ("https://acme.com/aftermarket-jobs",   "custom"),
    ("https://boards.greenhouse.io/draft",  "greenhouse"),
]
for _url, _want in _AFT_TRAPS:
    def _t(url=_url, want=_want):
        got = detect_platform(url)
        assert got == want, (
            f'{url} routed to {got!r} instead of {want!r} — a substring is '
            f"deciding routing again (see commit 1f0817b)"
        )
    test(f"detect_platform: 'aft' substring does not hijack {_url}", _t)

def _t():
    assert detect_platform("https://apply.workable.com/acme/") == "workable", (
        "Workable routing is broken, so fetchers/workable.py is unreachable and "
        "every Workable company is silently paying for Claude extraction instead"
    )
test("detect_platform: fetchers/workable.py is actually reachable", _t)

# Slug extraction for Workable. The subdomain pattern was `([^.]+)\.workable\.com`,
# which greedily captured the scheme too ('https://acme'), producing a 404 and a
# silent []. Latent while routing was broken; live again once it was fixed.
_SLUG_CASES = [
    ("https://apply.workable.com/acme/",           "acme"),
    ("https://apply.workable.com/acme/j/XYZ/",     "acme"),
    ("https://apply.workable.com/acme?utm=x",      "acme"),
    ("https://acme.workable.com/",                 "acme"),
    ("http://acme.workable.com",                   "acme"),
    ("acme.workable.com",                          "acme"),
    ("https://acme.workable.com/j/ABC?src=x",      "acme"),
]
for _url, _want in _SLUG_CASES:
    def _t(url=_url, want=_want):
        captured = {}
        def _fake_post(u, **kw):
            captured["url"] = u
            return fake_response(200, {"results": [], "cursor": None})
        with patch("fetchers.workable.requests.post", side_effect=_fake_post):
            scrape_workable(url)
        assert "url" in captured, f"{url} produced no slug, so no request was made"
        expected = f"https://apply.workable.com/api/v3/accounts/{want}/jobs"
        assert captured["url"] == expected, (
            f"{url} -> requested {captured['url']!r}, expected slug {want!r}"
        )
    test(f"scrape_workable slug: {_url} -> {_want}", _t)

# Non-account Workable URLs must yield no slug and make zero HTTP calls,
# rather than requesting a garbage account like 'www' or 'pply'.
for _url in ["https://apply.workable.com/", "https://www.workable.com/", "https://workable.com/"]:
    def _t(url=_url):
        with patch("fetchers.workable.requests.post") as mock_post:
            assert scrape_workable(url) == []
            assert not mock_post.called, (
                f"{url} has no account slug but still called the API with "
                f"{mock_post.call_args}"
            )
    test(f"scrape_workable: no bogus slug for {_url}", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 7 — no_job_companies reporting (pure logic)")
# ═══════════════════════════════════════════════════════════════════════════
# A company whose ATS slug goes stale returns [] — same as a company that simply isn't hiring,
# because every fetcher returns [] on a 404. job_loader.py counts that as fail_count, which never
# reaches error_count/failed_companies, so scraper_ok stays True and Slack renders a green
# success. And since such a company adds no jobs and removes none, added_jobs/removed_jobs don't
# reveal it either. It disappears from the board permanently, invisibly.
#
# The fix is reporting, not classification: list the names and let repetition be the signal. These
# tests pin the part that matters — that surfacing them does NOT promote the run to an error tier,
# because a company with no openings is a normal, weekly occurrence.

import notify as _notify

def _t():
    blocks = _notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=5,
        no_job_companies=["Acme", "Nimbus"],
    )
    text = json.dumps(blocks)
    assert "2 companies returned no jobs" in text, "the count line is missing"
    assert "Acme" in text and "Nimbus" in text, "company names are missing"
test("no_job_companies renders a section listing each company", _t)

def _t():
    blocks = _notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=5,
        no_job_companies=["Acme"],
    )
    text = json.dumps(blocks)
    assert "1 company returned no jobs" in text, "singular grammar is wrong"
test("no_job_companies uses singular grammar for one company", _t)

def _t():
    blocks = _notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=5,
        no_job_companies=["Acme", "Nimbus"],
    )
    text = json.dumps(blocks)
    assert ":white_check_mark:" in text, (
        "a company returning no jobs must NOT change the verdict tier — not hiring is normal, and "
        "alerting on it weekly would train everyone to ignore this message"
    )
    assert "rotating_light" not in text and "<!channel>" not in text, "must not escalate or ping"
test("no_job_companies does not downgrade an otherwise-clean run", _t)

def _t():
    blocks = _notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=5,
        no_job_companies=["Acme"],
    )
    text = json.dumps(blocks)
    assert "Last Scraped" in text, (
        "the section must point at the Companies tab's Last Scraped column — it's the only way to "
        "tell a stale URL from a company that isn't hiring"
    )
test("no_job_companies explains how to tell stale from empty", _t)

def _t():
    blocks = _notify._summary_blocks("2026-07-28", 0, 0, 0, True, companies=5, no_job_companies=[])
    assert "returned no jobs" not in json.dumps(blocks), "must render nothing when the list is empty"
test("no_job_companies section absent when every company returned jobs", _t)

def _t():
    many = [f"Company{i}" for i in range(45)]
    text = json.dumps(_notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=50, no_job_companies=many,
    ))
    assert "45 companies returned no jobs" in text, "the total must still be the real total"
    assert "and 15 more" in text, "the list must be capped with an overflow note"
    assert "Company44" not in text, "entries past the cap must not be rendered"
test("no_job_companies caps a long list but reports the true total", _t)

def _t():
    # back-compat: notify.py is called positionally from _post_slack_blocks in main()
    blocks = _notify._summary_blocks("2026-07-28", 0, 0, 0, True)
    assert isinstance(blocks, list) and blocks, "must still work with only the required args"
    assert "returned no jobs" not in json.dumps(blocks)
test("no_job_companies is optional (back-compat with existing callers)", _t)

def _t():
    text = json.dumps(_notify._summary_blocks(
        "2026-07-28", 0, 0, 0, True, companies=5,
        removed_jobs=[{"company": "Acme", "title": "Engineer"}],
    ))
    assert "Companies tab" in text, (
        "the Jobs Removed blurb must name the Companies tab — removals are just as often caused by "
        "editing that sheet as by a company taking a posting down, and the old wording only "
        "mentioned the careers page"
    )
test("Jobs Removed blurb names sheet edits as a possible cause", _t)


# ═══════════════════════════════════════════════════════════════════════════
section("Section 8 — parse_flexible_date (pure logic)")
# ═══════════════════════════════════════════════════════════════════════════
# remind.py used a strict strptime on "%Y-%m-%d" and swallowed ValueError with a bare `continue`.
# Everything the code writes is ISO, but the Jobs tab is hand-edited and Google Sheets happily
# reformats a date-typed cell — so a row reading "7/28/2026" was skipped silently, forever: never
# reminded on, never auto-expired, just sitting on the board with nobody prompted. A formatting
# difference should never decide whether a row gets processed.

from datetime import date as _date, datetime as _datetime
from core.utils import parse_flexible_date

_EXPECTED = _date(2026, 7, 28)
_PARSE_CASES = [
    ("2026-07-28",            "ISO — what the code itself writes"),
    ("2026/07/28",            "ISO with slashes"),
    ("07/28/2026",            "US padded"),
    ("7/28/2026",             "US unpadded — the common Sheets reformat"),
    ("07-28-2026",            "US with dashes"),
    ("7/28/26",               "US two-digit year"),
    ("Jul 28, 2026",          "abbreviated month name"),
    ("July 28, 2026",         "full month name"),
    ("Jul 28 2026",           "month name, no comma"),
    ("28 Jul 2026",           "day-first with month name"),
    ("28 July 2026",          "day-first, full month name"),
    ("28/07/2026",            "day-first numeric (month 28 is invalid, so falls through)"),
    ("2026-07-28 10:30:00",   "ISO with a time component"),
    ("2026-07-28T10:30:00",   "ISO 8601 with T separator"),
    ("7/28/2026 3:04 PM",     "US with a time component"),
    ("  2026-07-28  ",        "surrounding whitespace"),
]
for _raw, _why in _PARSE_CASES:
    def _t(raw=_raw, why=_why):
        got = parse_flexible_date(raw)
        assert got == _EXPECTED, f"{raw!r} ({why}) parsed as {got!r}, expected {_EXPECTED!r}"
    test(f"parses {_raw!r} — {_why}", _t)

def _t():
    # Ambiguous by nature; US wins because that's how this sheet is typed.
    assert parse_flexible_date("7/8/2026") == _date(2026, 7, 8), "ambiguous D/M vs M/D must resolve US-first"
test("parses ambiguous '7/8/2026' as US (July 8), matching sheet convention", _t)

def _t():
    assert parse_flexible_date("") is None
    assert parse_flexible_date("   ") is None
    assert parse_flexible_date(None) is None
test("blank/None return None (a missing date is not an error)", _t)

for _junk in ["not a date", "TBD", "n/a", "-", "2026", "13/13/2026", "0000-00-00"]:
    def _t(junk=_junk):
        assert parse_flexible_date(junk) is None, f"{junk!r} should be unparseable, not silently wrong"
    test(f"returns None for genuinely unreadable {_junk!r}", _t)

def _t():
    # Never raises — the whole point is that callers get None instead of an exception to swallow.
    for value in ["", None, "garbage", 12345, [], {}, "2026-07-28"]:
        try:
            parse_flexible_date(value)
        except Exception as e:
            assert False, f"parse_flexible_date({value!r}) raised {type(e).__name__}: {e}"
test("never raises, for any input type", _t)

def _t():
    assert parse_flexible_date(_date(2026, 7, 28)) == _EXPECTED, "a real date object should pass through"
    assert parse_flexible_date(_datetime(2026, 7, 28, 10, 30)) == _EXPECTED, "a datetime should narrow to its date"
test("accepts date/datetime objects directly (gspread can return them)", _t)

def _t():
    # The bug this section exists for: US-formatted dates used to be dropped entirely.
    from datetime import timedelta as _td
    old_us = (_date.today() - _td(days=45)).strftime("%m/%d/%Y")
    parsed = parse_flexible_date(old_us)
    assert parsed is not None, f"{old_us!r} must parse, or the row silently never expires"
    assert (_date.today() - parsed).days == 45, f"parsed to {parsed!r}, age is wrong"
test("a 45-day-old US-formatted date now yields a correct age (was: skipped forever)", _t)


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
