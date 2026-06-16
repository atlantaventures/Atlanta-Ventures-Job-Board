"""
Job Loader
Reads the company list from Google Sheets, routes each company to the appropriate
fetcher, then passes results to staged_job_writer to filter and write to the sheet.

Run:
    python job_loader.py
"""

import os
import sys
import time
from datetime import date
from pathlib import Path

import anthropic
import gspread
from dotenv import find_dotenv, load_dotenv

from core.utils import detect_platform
from core.dedup import load_existing_keys
from fetchers.ashby import scrape_ashby
from fetchers.breezy import scrape_breezy
from fetchers.greenhouse import scrape_greenhouse
from fetchers.lever import scrape_lever
from fetchers.smartrecruiters import scrape_smartrecruiters
from fetchers.workable import scrape_workable
from fetchers.doc_loader import scrape_google_doc
from fetchers.pdf_loader import scrape_pdf
from fetchers.web_scraper import WebScraper, extract_jobs_with_claude
from staged_job_writer import process_company_jobs, process_evergreen_company

load_dotenv(find_dotenv())

_REQUIRED_VARS = ["ANTHROPIC_API_KEY", "SHEET_ID"]
_missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    sys.exit(1)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SHEET_ID          = os.environ["SHEET_ID"]
CREDENTIALS_FILE  = Path(__file__).parent / "config" / "google_credentials.json"
COMPANIES_TAB     = "Companies"
JOBS_TAB          = "Jobs"
DELAY_SECONDS     = 2


def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    companies_ws = sh.worksheet(COMPANIES_TAB)
    jobs_ws      = sh.worksheet(JOBS_TAB)
    skipped_ws   = sh.worksheet("Skipped")

    all_rows      = companies_ws.get_all_records()
    today         = date.today().isoformat()
    claude        = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    existing_keys = load_existing_keys(jobs_ws, skipped_ws)
    all_job_rows  = jobs_ws.get_all_values()

    print(f"{len(existing_keys)} job(s) already processed — will skip duplicates\n")

    # sheet_row is 1-indexed; +2 accounts for header row + 0-indexed offset
    to_scrape = [
        (sheet_row, row)
        for sheet_row, row in enumerate(all_rows, start=2)
        if row.get("Careers URL", "").strip()
    ]

    print(f"Found {len(to_scrape)} companies with URLs\n")

    success_count  = 0
    fail_count     = 0
    error_count    = 0
    total_jobs     = 0
    duplicate_jobs = 0
    filtered_jobs  = 0
    added_jobs       = []   # [{"company": str, "title": str}, ...]
    removed_jobs     = []   # [{"company": str, "title": str}, ...]
    updated_jobs     = []   # [{"company": str, "old_title": str, "new_title": str}, ...]
    failed_companies = []   # company names that threw an exception

    with WebScraper() as scraper:
        for i, (sheet_row, row) in enumerate(to_scrape, start=1):
            company           = row["Company"]
            urls              = [u.strip() for u in row["Careers URL"].split("|") if u.strip()]
            evergreen_company = str(row.get("Evergreen", "")).strip().lower() == "true"
            platforms         = [detect_platform(u) for u in urls]

            label = "evergreen" if evergreen_company else " | ".join(dict.fromkeys(platforms))
            print(f"[{i}/{len(to_scrape)}] {company}  ({label})")

            try:
                if evergreen_company:
                    result = process_evergreen_company(
                        jobs_ws, companies_ws,
                        company, urls[0], sheet_row,
                        row, existing_keys, all_job_rows, today,
                    )
                    action = result["action"]
                    if action in ("added", "updated", "re-added"):
                        total_jobs    += 1
                        success_count += 1
                        print(f"    {action.capitalize()} evergreen listing\n")
                        if action == "updated":
                            updated_jobs.append({
                                "company":   company,
                                "old_title": result.get("old_title", ""),
                                "new_title": result.get("new_title", ""),
                            })
                    else:
                        print(f"    Already processed (no changes)\n")
                    if i < len(to_scrape):
                        time.sleep(DELAY_SECONDS)
                    continue

                # Fetch jobs from all URLs; apply source URL as fallback for jobs without a link
                all_jobs = []
                for url, platform in zip(urls, platforms):
                    try:
                        if platform == "ashby":
                            fetched = scrape_ashby(url)
                        elif platform == "breezy":
                            fetched = scrape_breezy(url)
                        elif platform == "greenhouse":
                            fetched = scrape_greenhouse(url)
                        elif platform == "lever":
                            fetched = scrape_lever(url)
                        elif platform == "smartrecruiters":
                            fetched = scrape_smartrecruiters(url)
                        elif platform == "workable":
                            fetched = scrape_workable(url)
                        elif platform == "googledoc":
                            fetched = scrape_google_doc(claude, company, url)
                        elif platform == "pdf":
                            fetched = scrape_pdf(claude, company, url)
                        elif platform == "linkedin":
                            print(f"    LinkedIn not supported — add a direct career page URL for {company}")
                            continue
                        else:
                            content = scraper.get_content(url)
                            if not content.strip():
                                print(f"    Empty page ({url}) — skipping")
                                continue
                            fetched = extract_jobs_with_claude(claude, company, content)
                        for j in fetched:
                            if not j.get("application_url"):
                                j["application_url"] = url
                        all_jobs.extend(fetched)
                    except Exception as e:
                        print(f"    FAILED fetching {url} — {e}")

                if not all_jobs:
                    print(f"    No jobs found\n")
                    fail_count += 1
                    continue

                stats = process_company_jobs(
                    claude, jobs_ws, skipped_ws, companies_ws,
                    company, urls[0], sheet_row,
                    all_jobs, existing_keys, all_job_rows, today,
                )

                duplicate_jobs += stats["dupes"]
                filtered_jobs  += stats["skipped"]
                added_jobs.extend({"company": company, "title": t} for t in stats.get("added_jobs", []))
                removed_jobs.extend({"company": company, "title": t} for t in stats.get("removed_jobs", []))

                if stats["kept"] == 0 and stats["skipped"] == 0:
                    # All jobs were duplicates from a prior run
                    msg = f"    No new jobs"
                    if stats["dupes"]:
                        msg += f" ({stats['dupes']} already processed)"
                    print(msg + "\n")
                    fail_count += 1
                else:
                    summary = f"    {stats['kept']} relevant"
                    if stats["dupes"]:
                        summary += f"  ({stats['dupes']} already processed)"
                    if stats["skipped"]:
                        summary += f"  ({stats['skipped']} filtered out)"
                    print(summary + "\n")
                    if stats["kept"] > 0:
                        total_jobs    += stats["kept"]
                        success_count += 1
                    else:
                        fail_count += 1

            except Exception as e:
                print(f"    FAILED — {e}\n")
                fail_count       += 1
                error_count      += 1
                failed_companies.append(company)

            if i < len(to_scrape):
                time.sleep(DELAY_SECONDS)

    print("=" * 50)
    print(f"DONE")
    print(f"  Companies with relevant jobs   : {success_count}")
    print(f"  Companies with errors/no jobs  : {fail_count}")
    print(f"  New jobs written to Jobs tab   : {total_jobs}")
    print(f"  Irrelevant jobs filtered out   : {filtered_jobs}")
    print(f"  Duplicates skipped             : {duplicate_jobs}")
    if error_count:
        print(f"  Script errors                  : {error_count}")
    print("=" * 50)

    import json
    Path("/tmp/run_stats.json").write_text(json.dumps({
        "date":               today,
        "companies_scraped":  len(to_scrape),
        "companies_with_jobs": success_count,
        "companies_no_jobs":  fail_count,
        "new_jobs_found":     total_jobs,
        "filtered_out":       filtered_jobs,
        "duplicates_skipped": duplicate_jobs,
        "errors":             error_count,
        "scraper_ok":         error_count == 0,
        "added_jobs":         added_jobs,
        "removed_jobs":       removed_jobs,
        "updated_jobs":       updated_jobs,
        "failed_companies":   failed_companies,
    }))

    if error_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
