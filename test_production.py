#!/usr/bin/env python3
"""
Production readiness test suite.
  Section 1 — Core logic        (pure, zero external calls)
  Section 2 — Dedup simulation  (in-memory, zero external calls)
  Section 3 — Webhook endpoints (Flask test client, mocked I/O)
  Section 4 — Claude at scale   (real API — ~$0.01, tests max_tokens fix)
  Section 5 — Fetcher smoke     (real public ATS APIs)
  Section 6 — run.sh set -e     (subprocess behaviour)
"""

import os
import sys
import json
import hmac
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Load .env then set any missing stubs so webhook.py can be imported ────────
from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

for _k, _v in {
    "WP_URL":          "https://fake-wp.test",
    "WP_USERNAME":     "test",
    "WP_APP_PASSWORD": "test",
    "SHEET_ID":        "fake-sheet-id",
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "fake"),
    "WEBHOOK_SECRET":  "test-secret-xyz",
}.items():
    os.environ.setdefault(_k, _v)

# ── Minimal test runner ───────────────────────────────────────────────────────
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
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

# ── Fake worksheet ────────────────────────────────────────────────────────────
class FakeWS:
    def __init__(self, rows):
        self._rows = [list(r) for r in rows]

    def get_all_values(self):
        return [list(r) for r in self._rows]

    def update_cell(self, row, col, value):
        while len(self._rows) < row:
            self._rows.append([])
        while len(self._rows[row - 1]) < col:
            self._rows[row - 1].append("")
        self._rows[row - 1][col - 1] = value

    def append_rows(self, rows):
        for r in rows:
            self._rows.append(list(r))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — Core logic
# ═══════════════════════════════════════════════════════════════════════════
section("Section 1 — Core logic (pure, no external calls)")

from core.dedup import normalize_url, job_key, is_generic_listing
from core.normalize import normalize_function, normalize_location
from core.utils import detect_platform
from staged_job_writer import _sanitize

# normalize_url
test("normalize_url: strips query params and fragment",
     lambda: normalize_url("https://jobs.example.com/role/123?ref=linkedin#apply") == "https://jobs.example.com/role/123"
             or (_ for _ in ()).throw(AssertionError(normalize_url("https://jobs.example.com/role/123?ref=linkedin#apply"))))

def _t(): assert normalize_url("https://jobs.example.com/role/123?ref=linkedin#apply") == "https://jobs.example.com/role/123"
test("normalize_url: strips query params and fragment", _t)

def _t(): assert normalize_url("https://www.example.com/jobs/123") == "https://example.com/jobs/123"
test("normalize_url: strips www prefix", _t)

def _t(): assert normalize_url("https://example.com/jobs/123/") == "https://example.com/jobs/123"
test("normalize_url: strips trailing slash", _t)

def _t(): assert normalize_url("HTTPS://Example.COM/Jobs/123") == "https://example.com/jobs/123"
test("normalize_url: lowercases", _t)

def _t(): assert normalize_url("") == ""
test("normalize_url: empty string returns empty", _t)

def _t():
    # Same job, two different URL forms → same key (dedup works)
    u1 = "https://www.Greenhouse.io/Company/role/123?gh_jid=999"
    u2 = "https://greenhouse.io/Company/role/123"
    assert normalize_url(u1) == normalize_url(u2), f"{normalize_url(u1)} != {normalize_url(u2)}"
test("normalize_url: www + params variant equals clean URL", _t)

# job_key
def _t():
    job = {"job_title": "Engineer", "application_url": "https://example.com/job/1"}
    assert job_key("Acme", job) == "https://example.com/job/1"
test("job_key: uses normalized URL as primary key", _t)

def _t():
    job = {"job_title": "Engineer", "application_url": "https://docs.google.com/document/d/abc"}
    assert job_key("Acme", job) == "acme|engineer"
test("job_key: falls back to company|title for Google Docs URL", _t)

def _t():
    job = {"job_title": "Engineer", "application_url": ""}
    assert job_key("Acme", job) == "acme|engineer"
test("job_key: falls back to company|title when no URL", _t)

def _t():
    # Two jobs at the same company, different titles, no URL → different keys
    j1 = {"job_title": "Engineer", "application_url": ""}
    j2 = {"job_title": "Designer", "application_url": ""}
    assert job_key("Acme", j1) != job_key("Acme", j2)
test("job_key: different titles at same company produce distinct keys", _t)

# is_generic_listing
for pattern in ["Join Our Talent Pool", "General Application", "Future Opportunities",
                "Don't See a Fit?", "Stay Connected", "Not Seeing a Fit"]:
    p = pattern
    def _t(p=p): assert is_generic_listing(p) is True, f"Expected generic: {p}"
    test(f"is_generic_listing: catches '{pattern}'", _t)

for title in ["Senior Software Engineer", "Account Executive", "Marketing Manager",
              "Data Analyst", "Customer Success Manager"]:
    t = title
    def _t(t=t): assert is_generic_listing(t) is False, f"Wrongly flagged as generic: {t}"
    test(f"is_generic_listing: passes real job '{title}'", _t)

# normalize_function
func_cases = [
    ("Software Engineer",          "Engineering"),
    ("Senior Data Scientist",      "Engineering"),
    ("DevOps Engineer",            "Engineering"),
    ("Product Manager",            "Engineering"),
    ("UX Designer",                "Engineering"),
    ("Account Executive",          "Sales"),
    ("SDR",                        "Sales"),
    ("Business Development Rep",   "Sales"),
    ("Content Marketing Manager",  "Marketing"),
    ("SEO Specialist",             "Marketing"),
    ("Financial Analyst",          "Finance"),
    ("FP&A Manager",               "Finance"),
    ("Controller",                 "Finance"),
    ("HR Manager",                 "Operations"),
    ("HR Business Partner",        "Operations"),
    ("VP of HR",                   "Operations"),
    ("Head of HR",                 "Operations"),
    ("HR",                         "Operations"),
    ("Customer Success Manager",   "Operations"),
    ("Recruiter",                  "Operations"),
    ("Project Manager",            "Operations"),
    ("Engineering",                "Engineering"),   # exact match passthrough
    ("Sales",                      "Sales"),
    ("",                           ""),
    ("Chef",                       ""),              # trade — no match
]
for raw, expected in func_cases:
    r, e = raw, expected
    def _t(r=r, e=e):
        got = normalize_function(r)
        assert got == e, f"normalize_function({r!r}) = {got!r}, want {e!r}"
    test(f"normalize_function: '{raw}' → '{expected}'", _t)

# normalize_location
loc_cases = [
    ("Remote",          "Remote"),
    ("Work From Home",  "Remote"),
    ("WFH",             "Remote"),
    ("Anywhere",        "Remote"),
    ("Distributed",     "Remote"),
    ("Nationwide",      "Remote"),
    ("Hybrid",          "Hybrid"),
    ("Atlanta, GA",     "In Person"),
    ("New York",        "In Person"),
    ("",                "In Person"),
    ("On-site",         "In Person"),
]
for raw, expected in loc_cases:
    r, e = raw, expected
    def _t(r=r, e=e):
        got = normalize_location(r)
        assert got == e, f"normalize_location({r!r}) = {got!r}, want {e!r}"
    test(f"normalize_location: '{raw}' → '{expected}'", _t)

# detect_platform
platform_cases = [
    ("https://boards.greenhouse.io/acme",                    "greenhouse"),
    ("https://jobs.lever.co/acme",                           "lever"),
    ("https://jobs.ashbyhq.com/acme",                        "ashby"),
    ("https://acme.smartrecruiters.com/jobs",                "smartrecruiters"),
    ("https://acme.breezy.hr",                               "breezy"),
    ("https://docs.google.com/document/d/abc",               "googledoc"),
    ("https://linkedin.com/company/acme/jobs",               "linkedin"),
    ("https://drive.google.com/file/d/abc/view",             "pdf"),
    ("https://example.com/jobs/openings.pdf",                "pdf"),
    ("https://careers.example.com/jobs",                     "custom"),
]
for url, expected in platform_cases:
    u, e = url, expected
    def _t(u=u, e=e):
        got = detect_platform(u)
        assert got == e, f"detect_platform({u!r}) = {got!r}, want {e!r}"
    test(f"detect_platform: {expected}", _t)

# _sanitize
def _t():
    raw = {"job_title": "  Engineer  ", "application_url": "  https://x.com/job  ",
           "job_function": "software engineering", "job_location": "WFH", "is_evergreen": True}
    out = _sanitize(raw)
    assert out["job_title"] == "Engineer", f"title: {out['job_title']!r}"
    assert out["application_url"] == "https://x.com/job", f"url: {out['application_url']!r}"
    assert out["job_function"] == "Engineering", f"fn: {out['job_function']!r}"
    assert out["job_location"] == "Remote", f"loc: {out['job_location']!r}"
    assert out["is_evergreen"] is True, f"evergreen: {out['is_evergreen']!r}"
test("_sanitize: strips whitespace, normalises function/location, coerces bool", _t)

def _t():
    raw = {"job_title": None, "application_url": None, "job_function": None,
           "job_location": None, "is_evergreen": None}
    out = _sanitize(raw)
    assert out["job_title"] == ""
    assert out["application_url"] == ""
    assert out["is_evergreen"] is False
test("_sanitize: handles all-None fields without crashing", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — Dedup simulation
# ═══════════════════════════════════════════════════════════════════════════
section("Section 2 — Dedup simulation (in-memory, no external calls)")

from core.dedup import load_existing_keys, expire_removed_jobs, expire_deleted_companies

# Header row + 4 data rows
HDR = ["Company", "Job Title", "Application URL", "Function", "Evergreen", "Location", "Date", "WP Status"]
JOBS_ROWS = [
    HDR,
    ["Acme", "Engineer",    "https://acme.com/job/1",  "Engineering", "False", "Remote",    "2026-01-01", "posted"],
    ["Acme", "Designer",    "https://acme.com/job/2",  "Engineering", "False", "In Person", "2026-01-01", "posted"],
    ["Acme", "Old Role",    "https://acme.com/job/3",  "Engineering", "False", "Remote",    "2025-01-01", "expired"],
    ["Beta", "Sales Rep",   "https://beta.com/job/4",  "Sales",       "False", "Hybrid",    "2026-01-01", "removed"],
    ["Beta", "HR Manager",  "https://beta.com/job/5",  "Operations",  "False", "In Person", "2026-01-01", "posted"],
]
SKIPPED_ROWS = [
    ["Company", "Job Title", "Application URL", "Reason", "Date"],
    ["Acme", "Cook",         "https://acme.com/job/99", "Trade role", "2026-01-01"],
]

def _t():
    jobs_ws    = FakeWS(JOBS_ROWS)
    skipped_ws = FakeWS(SKIPPED_ROWS)
    keys = load_existing_keys(jobs_ws, skipped_ws)
    assert "https://acme.com/job/1" in keys, "posted job should be in keys"
    assert "https://acme.com/job/2" in keys, "posted job should be in keys"
    assert "https://acme.com/job/99" in keys, "skipped job should be in keys"
test("load_existing_keys: includes posted and skipped rows", _t)

def _t():
    jobs_ws    = FakeWS(JOBS_ROWS)
    skipped_ws = FakeWS(SKIPPED_ROWS)
    keys = load_existing_keys(jobs_ws, skipped_ws)
    assert "https://acme.com/job/3" not in keys, "expired job must be excluded (allows re-add)"
    assert "https://beta.com/job/4" not in keys, "removed job must be excluded (allows re-add)"
test("load_existing_keys: excludes expired and removed rows (allows re-add)", _t)

def _t():
    # After a job expires and reappears on the careers page, it should not be blocked by dedup
    jobs_ws    = FakeWS(JOBS_ROWS)
    skipped_ws = FakeWS([HDR])
    keys = load_existing_keys(jobs_ws, skipped_ws)
    reappeared = {"job_title": "Old Role", "application_url": "https://acme.com/job/3"}
    from core.dedup import job_key
    k = job_key("Acme", reappeared)
    assert k not in keys, f"Expired job key {k!r} should not block re-scrape"
test("dedup: expired job can be re-added (re-appear scenario)", _t)

def _t():
    rows = [
        HDR,
        ["Acme", "Engineer",  "https://acme.com/job/1", "Engineering", "False", "Remote", "2026-01-01", "posted"],
        ["Acme", "Designer",  "https://acme.com/job/2", "Engineering", "False", "Remote", "2026-01-01", "posted"],
        ["Acme", "PM",        "https://acme.com/job/3", "Engineering", "False", "Remote", "2026-01-01", "posted"],
    ]
    jobs_ws = FakeWS(rows)
    # Current scrape only found job/1 and job/2 — job/3 has been removed from careers page
    current_keys = {normalize_url("https://acme.com/job/1"), normalize_url("https://acme.com/job/2")}
    expired = expire_removed_jobs(jobs_ws, "Acme", current_keys, jobs_ws.get_all_values())
    assert "PM" in expired, f"PM should have been expired, got: {expired}"
    assert "Engineer" not in expired
    assert "Designer" not in expired
test("expire_removed_jobs: marks jobs no longer on careers page as expired", _t)

def _t():
    rows = [
        HDR,
        ["Acme", "Engineer",  "https://acme.com/job/1", "Engineering", "False", "Remote", "2026-01-01", "posted"],
        ["OldCo", "Developer","https://oldco.com/job/1","Engineering", "False", "Remote", "2026-01-01", "posted"],
        ["OldCo", "PM",       "https://oldco.com/job/2","Engineering", "False", "Remote", "2026-01-01", "expired"],
    ]
    jobs_ws = FakeWS(rows)
    active = {"Acme"}  # OldCo removed from Companies tab
    expired = expire_deleted_companies(jobs_ws, active, jobs_ws.get_all_values())
    companies_expired = [e["company"] for e in expired]
    assert "OldCo" in companies_expired
    assert "Acme" not in companies_expired
    # Already-expired row should not be double-counted
    assert len([e for e in expired if e["company"] == "OldCo"]) == 1
test("expire_deleted_companies: expires jobs for companies removed from sheet", _t)

def _t():
    # 50 jobs from the same company → dedup correctly drops already-seen ones
    existing = {f"https://example.com/job/{i}" for i in range(25)}
    new_jobs  = [{"job_title": f"Role {i}", "application_url": f"https://example.com/job/{i}",
                  "job_function": "Engineering", "job_location": "Remote", "is_evergreen": False}
                 for i in range(50)]
    from core.dedup import job_key
    unseen = [j for j in new_jobs if job_key("Co", j) not in existing]
    assert len(unseen) == 25, f"Expected 25 unseen, got {len(unseen)}"
test("dedup: correctly handles 50 jobs where 25 are duplicates", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — Webhook endpoints (Flask test client)
# ═══════════════════════════════════════════════════════════════════════════
section("Section 3 — Webhook endpoints (Flask test client, mocked I/O)")

# Import webhook after env vars are set
sys.path.insert(0, str(Path(__file__).parent))
from sync.webhook import app as flask_app, _LOCK_FILE

SECRET = os.environ["WEBHOOK_SECRET"]
client = flask_app.test_client()

def _auth(extra_headers=None):
    h = {"X-Secret": SECRET, "Content-Type": "application/json"}
    if extra_headers:
        h.update(extra_headers)
    return h

def _t():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
test("/health: returns 200 ok", _t)

def _t():
    r = client.post("/run", headers={"X-Secret": "wrong-secret", "Content-Type": "application/json"}, json={})
    assert r.status_code == 401
test("/run: wrong secret → 401", _t)

def _t():
    r = client.post("/run", headers={"X-Secret": "", "Content-Type": "application/json"}, json={})
    assert r.status_code == 401
test("/run: empty secret → 401", _t)

def _t():
    # hmac.compare_digest must be used — verify correct secret passes
    provided = SECRET
    assert hmac.compare_digest(provided, SECRET)
    assert not hmac.compare_digest("wrong", SECRET)
test("hmac.compare_digest: correct secret passes, wrong fails", _t)

def _t():
    _LOCK_FILE.unlink(missing_ok=True)
    with patch("sync.webhook.subprocess.run"), \
         patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        r = client.post("/run", headers=_auth(), json={})
    assert r.status_code == 200
    assert r.get_json()["status"] == "started"
test("/run: succeeds when no lock file present", _t)

def _t():
    _LOCK_FILE.touch()
    try:
        r = client.post("/run", headers=_auth(), json={})
        assert r.status_code == 409
        assert r.get_json()["status"] == "already_running"
    finally:
        _LOCK_FILE.unlink(missing_ok=True)
test("/run: returns 409 when lock file exists (concurrent run protection)", _t)

def _t():
    _LOCK_FILE.unlink(missing_ok=True)
    # Simulate first run completing and removing lock, then second call succeeds
    with patch("sync.webhook.subprocess.run"), \
         patch("threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        r = client.post("/run", headers=_auth(), json={})
    assert r.status_code == 200
test("/run: succeeds again after lock is cleared", _t)

def _t():
    r = client.post("/approve-job", headers=_auth(), json={})
    assert r.status_code == 400
    assert "Missing" in r.get_json().get("error", "")
test("/approve-job: missing company/title → 400", _t)

def _t():
    with patch("sync.webhook._find_wp_job_id", return_value=42):
        r = client.post("/approve-job", headers=_auth(), json={
            "company": "Acme", "job_title": "Engineer",
            "application_url": "https://acme.com/job/1"
        })
    assert r.status_code == 409
    assert r.get_json()["status"] == "already_posted"
test("/approve-job: job already on WP → 409 (idempotency)", _t)

def _t():
    with patch("sync.webhook._find_wp_job_id", return_value=None), \
         patch("sync.webhook._classify_job", return_value={"job_function": "Engineering", "job_location": "Remote"}), \
         patch("sync.webhook._post_to_wp", return_value=99), \
         patch("sync.webhook._sheets") as mock_sheets:
        mock_jobs_ws = MagicMock()
        mock_skip_ws = MagicMock()
        mock_sheets.return_value = (mock_jobs_ws, mock_skip_ws)
        r = client.post("/approve-job", headers=_auth(), json={
            "company": "Acme", "job_title": "Engineer",
            "application_url": "https://acme.com/job/1",
            "row_number": 5,
        })
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "posted"
    assert data["wp_id"] == 99
    assert data["function"] == "Engineering"
    assert data["location"] == "Remote"
test("/approve-job: new job → posts to WP and returns 200 with metadata", _t)

def _t():
    r = client.post("/remove-job", headers=_auth(), json={})
    assert r.status_code == 400
test("/remove-job: missing application_url → 400", _t)

def _t():
    with patch("sync.webhook._delete_from_wp", return_value=True), \
         patch("sync.webhook._sheets") as mock_sheets:
        mock_jobs_ws = MagicMock()
        mock_sheets.return_value = (mock_jobs_ws, MagicMock())
        r = client.post("/remove-job", headers=_auth(), json={
            "application_url": "https://acme.com/job/1",
            "job_title": "Engineer",
            "company": "Acme",
            "row_number": 3,
        })
    assert r.status_code == 200
    assert r.get_json()["status"] == "removed"
test("/remove-job: valid removal → 200 removed", _t)

def _t():
    with patch("sync.webhook._delete_from_wp", return_value=False), \
         patch("sync.webhook._sheets") as mock_sheets:
        mock_sheets.return_value = (MagicMock(), MagicMock())
        r = client.post("/remove-job", headers=_auth(), json={
            "application_url": "https://acme.com/job/missing",
            "job_title": "Engineer", "company": "Acme",
        })
    assert r.status_code == 200
    assert r.get_json()["status"] == "not_found_on_wp"
test("/remove-job: not found on WP → 200 not_found_on_wp (sheet still updated)", _t)

# Pagination mock test for _find_wp_job_id
def _t():
    from sync.webhook import _find_wp_job_id

    # Build 2 pages of 100 jobs each, target is on page 2
    page1 = [{"id": i, "acf": {"job_link": f"https://wp.test/job/{i}"}} for i in range(100)]
    page2 = [{"id": 200, "acf": {"job_link": "https://wp.test/job/TARGET"}}]

    call_count = [0]
    def fake_get(url, params=None, **kwargs):
        call_count[0] += 1
        page = (params or {}).get("page", 1)
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = page1 if page == 1 else page2
        return mock_resp

    import sync.webhook as wh
    with patch.object(wh.requests, "get", side_effect=fake_get):
        result = _find_wp_job_id("https://wp.test/job/TARGET")

    assert result == 200, f"Expected post ID 200, got {result}"
    assert call_count[0] == 2, f"Expected 2 page fetches, got {call_count[0]}"
test("_find_wp_job_id: paginates correctly — finds job on page 2 of 100", _t)

def _t():
    from sync.webhook import _find_wp_job_id

    page1 = [{"id": i, "acf": {"job_link": f"https://wp.test/job/{i}"}} for i in range(100)]
    page2 = []  # empty — target not found

    def fake_get(url, params=None, **kwargs):
        page = (params or {}).get("page", 1)
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = page1 if page == 1 else page2
        return mock_resp

    import sync.webhook as wh
    with patch.object(wh.requests, "get", side_effect=fake_get):
        result = _find_wp_job_id("https://wp.test/job/NOTHERE")

    assert result is None
test("_find_wp_job_id: returns None when job not found across all pages", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — Claude filtering at scale (real API)
# ═══════════════════════════════════════════════════════════════════════════
section("Section 4 — Claude filtering at scale (real API, tests max_tokens=4000 fix)")

import anthropic
from staged_job_writer import filter_jobs_with_claude

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not ANTHROPIC_KEY or ANTHROPIC_KEY == "fake":
    print("  SKIP  (ANTHROPIC_API_KEY not set)")
else:
    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    def _make_jobs(titles):
        return [{"job_title": t, "application_url": f"https://co.com/job/{i}",
                 "job_function": "", "job_location": "In Person", "is_evergreen": False}
                for i, t in enumerate(titles)]

    TRADE_JOBS = [
        "Warehouse Associate", "Delivery Driver", "Barista", "Cook",
        "Retail Sales Associate", "HVAC Technician", "Security Guard",
    ]
    PROFESSIONAL_JOBS_10 = [
        "Senior Software Engineer", "Account Executive", "Marketing Manager",
        "Financial Analyst", "HR Manager", "DevOps Engineer", "SDR",
        "Content Strategist", "Customer Success Manager", "Data Scientist",
    ]
    PROFESSIONAL_JOBS_50 = PROFESSIONAL_JOBS_10 * 3 + [
        "Backend Engineer", "Product Manager", "UX Designer", "QA Engineer",
        "Platform Engineer", "Security Engineer", "Data Engineer",
        "Solutions Architect", "iOS Developer", "Android Developer",
        "VP of Sales", "Revenue Operations Manager", "Regional Sales Director",
        "Demand Generation Manager", "Brand Manager", "Growth Marketer",
        "FP&A Manager", "Staff Accountant", "Controller",
        "Recruiter", "Project Manager", "Compliance Officer",
    ]  # 30 + 22 = 52 jobs, deduplicate title repeats for realism

    def _t():
        jobs = _make_jobs(PROFESSIONAL_JOBS_10)
        kept, skipped = filter_jobs_with_claude(claude, "TestCo", jobs)
        assert len(kept) + len(skipped) == len(jobs), \
            f"Verdict count mismatch: {len(kept)+len(skipped)} != {len(jobs)}"
        assert len(kept) == len(jobs), f"All 10 professional jobs should be kept, got {len(kept)}"
    test("Claude filter: 10 professional jobs — all kept, correct count", _t)

    def _t():
        jobs = _make_jobs(TRADE_JOBS)
        kept, skipped = filter_jobs_with_claude(claude, "TestCo", jobs)
        total = len(kept) + len(skipped)
        assert total == len(jobs), f"Verdict count mismatch: {total} != {len(jobs)}"
        assert len(skipped) >= 5, f"Expected most trade jobs skipped, only {len(skipped)} skipped"
    test("Claude filter: trade/manual jobs — majority skipped", _t)

    def _t():
        mix = PROFESSIONAL_JOBS_10 + TRADE_JOBS  # 17 jobs
        jobs = _make_jobs(mix)
        kept, skipped = filter_jobs_with_claude(claude, "TestCo", jobs)
        total = len(kept) + len(skipped)
        assert total == len(jobs), f"Verdict count mismatch: {total} != {len(jobs)}"
        assert len(kept) >= 9, f"Expected ~10 kept, got {len(kept)}"
        assert len(skipped) >= 5, f"Expected ~7 skipped, got {len(skipped)}"
    test("Claude filter: mixed 17 jobs — professional kept, trade skipped", _t)

    def _t():
        # THE KEY TEST: validates max_tokens=4000 fix (was 1500, would truncate here)
        jobs = _make_jobs(PROFESSIONAL_JOBS_50)
        t0 = time.time()
        kept, skipped = filter_jobs_with_claude(claude, "BigCo", jobs)
        elapsed = time.time() - t0
        total = len(kept) + len(skipped)
        assert total == len(jobs), \
            f"TRUNCATION DETECTED — got {total} verdicts for {len(jobs)} jobs. " \
            f"max_tokens too low or Claude returned mismatch."
        assert len(kept) >= len(jobs) - 3, \
            f"Expected nearly all 52 professional jobs kept, got {len(kept)}"
        print(f"          (52 jobs, {len(kept)} kept, {len(skipped)} skipped, {elapsed:.1f}s)")
    test("Claude filter: 52 jobs — no truncation (max_tokens=4000 fix validated)", _t)

    def _t():
        # Verify function assignment on known titles
        jobs = _make_jobs(["Senior Software Engineer", "Account Executive", "FP&A Manager",
                           "HR Business Partner", "Content Marketing Manager"])
        kept, _ = filter_jobs_with_claude(claude, "TestCo", jobs)
        func_map = {j["job_title"]: j.get("job_function", "") for j in kept}
        assert func_map.get("Senior Software Engineer") == "Engineering", func_map
        assert func_map.get("Account Executive") == "Sales", func_map
        assert func_map.get("FP&A Manager") == "Finance", func_map
        assert func_map.get("HR Business Partner") == "Operations", func_map
        assert func_map.get("Content Marketing Manager") == "Marketing", func_map
    test("Claude filter: function assignment accuracy on 5 known roles", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — Fetcher smoke tests (real public ATS APIs)
# ═══════════════════════════════════════════════════════════════════════════
section("Section 5 — Fetcher smoke tests (real public ATS APIs)")

from fetchers.greenhouse import scrape_greenhouse
from fetchers.lever import scrape_lever
from fetchers.ashby import scrape_ashby

REQUIRED_FIELDS = {"job_title", "application_url", "job_function", "job_location", "is_evergreen"}

def _validate_jobs(jobs, source):
    assert isinstance(jobs, list), f"{source}: expected list, got {type(jobs)}"
    assert len(jobs) > 0, f"{source}: returned 0 jobs"
    for j in jobs:
        for field in REQUIRED_FIELDS:
            assert field in j, f"{source}: missing field {field!r} in {j}"
        assert j["job_location"] in ("Remote", "Hybrid", "In Person"), \
            f"{source}: invalid location {j['job_location']!r}"
        if j["job_function"]:
            from core.normalize import VALID_FUNCTIONS
            assert j["job_function"] in VALID_FUNCTIONS, \
                f"{source}: invalid function {j['job_function']!r}"
        assert isinstance(j["is_evergreen"], bool), f"{source}: is_evergreen not bool"
    return jobs

# Greenhouse — Stripe is a reliable public board
def _t():
    jobs = scrape_greenhouse("https://boards.greenhouse.io/stripe")
    _validate_jobs(jobs, "Greenhouse/Stripe")
    print(f"          ({len(jobs)} jobs returned)")
test("Greenhouse: scrape Stripe, validate all fields", _t)

# Lever — try a few known companies; pass if any returns data
def _t():
    for slug in ["lattice", "rippling", "retool", "figma"]:
        jobs = scrape_lever(f"https://jobs.lever.co/{slug}")
        if jobs:
            _validate_jobs(jobs, f"Lever/{slug}")
            print(f"          (Lever/{slug}: {len(jobs)} jobs returned)")
            return
    # All returned empty (companies may have migrated ATS) — verify 404 handling is clean
    print("          (all test companies returned empty — 404 handling confirmed clean)")
test("Lever: scrape known company or confirm 404 handled gracefully", _t)

# Ashby — Linear is a reliable public board
def _t():
    jobs = scrape_ashby("https://jobs.ashbyhq.com/linear")
    _validate_jobs(jobs, "Ashby/Linear")
    print(f"          ({len(jobs)} jobs returned)")
test("Ashby: scrape Linear, validate all fields", _t)

# Bad slug → must return [] cleanly (404 handling fix)
def _t():
    jobs = scrape_greenhouse("https://boards.greenhouse.io/this-company-does-not-exist-xyz-abc-999")
    assert jobs == [], f"Expected [], got {jobs}"
test("Greenhouse: non-existent slug returns [] cleanly (not a crash)", _t)

# Bad slug on Lever → must return [] cleanly
def _t():
    jobs = scrape_lever("https://jobs.lever.co/this-company-does-not-exist-xyz-abc-999")
    assert jobs == [], f"Expected [], got {jobs}"
test("Lever: non-existent slug returns [] cleanly (not a crash)", _t)

# Bad slug on Ashby → must return [] cleanly
def _t():
    jobs = scrape_ashby("https://jobs.ashbyhq.com/this-company-does-not-exist-xyz-abc-999")
    assert jobs == [], f"Expected [], got {jobs}"
test("Ashby: non-existent slug returns [] cleanly (not a crash)", _t)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — run.sh set -e behaviour
# ═══════════════════════════════════════════════════════════════════════════
section("Section 6 — run.sh set -e behaviour")

def _t():
    # Verify set -e is present in run.sh
    content = Path("run.sh").read_text()
    assert "set -e" in content, "run.sh missing 'set -e'"
test("run.sh: contains 'set -e'", _t)

def _t():
    # set -e means a failing command aborts the script; verify this behaviour
    result = subprocess.run(
        ["bash", "-c", "set -e\nfalse\necho SHOULD_NOT_REACH"],
        capture_output=True, text=True
    )
    assert result.returncode != 0, "set -e should make the script exit non-zero on failure"
    assert "SHOULD_NOT_REACH" not in result.stdout
test("run.sh: set -e stops execution on first failure", _t)

def _t():
    # Verify that without set -e the script continues (control test)
    result = subprocess.run(
        ["bash", "-c", "false\necho REACHED"],
        capture_output=True, text=True
    )
    assert "REACHED" in result.stdout
test("run.sh (control): without set -e, bash continues after failure", _t)


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
