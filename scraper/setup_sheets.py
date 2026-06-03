"""
One-time setup: creates Companies and Jobs tabs in Google Sheets
and populates the Companies tab from companies.csv.

Run once before using playwright_extract.py:
    python scraper/setup_sheets.py
"""

import csv
import os
from pathlib import Path

import gspread
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

SHEET_ID         = os.environ["SHEET_ID"]
CREDENTIALS_FILE = Path(__file__).parent.parent / "config" / "google_credentials.json"
BASE_DIR         = Path(__file__).parent

COMPANIES_HEADERS = ["Company", "Status", "Careers URL", "Last Scraped"]
JOBS_HEADERS      = [
    "Company", "Job Title", "Application URL", "Function",
    "Description", "Evergreen", "Location", "Scraped At", "WP Status",
]
SKIPPED_HEADERS   = ["Company", "Job Title", "Application URL", "Reason", "Scraped At"]


def get_or_create_worksheet(sh, title, headers, clear_data=False):
    try:
        ws = sh.worksheet(title)
        if clear_data:
            ws.clear()
            print(f"  Reset tab: '{title}'")
        else:
            print(f"  Found existing tab: '{title}'")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        print(f"  Created tab: '{title}'")
    ws.update([headers], "A1")
    return ws


def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)

    print("Setting up Google Sheets...")
    companies_ws = get_or_create_worksheet(sh, "Companies", COMPANIES_HEADERS, clear_data=False)
    get_or_create_worksheet(sh, "Jobs",     JOBS_HEADERS,     clear_data=True)
    get_or_create_worksheet(sh, "Skipped",  SKIPPED_HEADERS,  clear_data=True)

    csv_path = BASE_DIR / "companies.csv"
    if not csv_path.exists():
        print("\nNo companies.csv found — skipping company import.")
        print("Add companies to the Companies tab manually.")
        return

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            company = row.get("Company", "").strip()
            if not company:
                continue
            rows.append([
                company,
                row.get("Status", "").strip(),
                row.get("Link", "").strip(),
                "",
            ])

    existing = companies_ws.get_all_values()
    header   = existing[0] if existing else COMPANIES_HEADERS
    data     = existing[1:] if len(existing) > 1 else []

    # Deduplicate by company name (column 0), keeping first occurrence
    seen    = set()
    deduped = []
    for row in data:
        name = row[0].strip().lower() if row else ""
        if name and name not in seen:
            seen.add(name)
            deduped.append(row)

    if len(deduped) < len(data):
        removed = len(data) - len(deduped)
        companies_ws.clear()
        companies_ws.update([header] + deduped, "A1")
        print(f"\nRemoved {removed} duplicate company row(s).")
    elif deduped:
        print(f"\nCompanies tab has {len(deduped)} companies — no duplicates found.")
    elif rows:
        companies_ws.append_rows(rows)
        print(f"\nImported {len(rows)} companies from companies.csv.")

    print(f"\nSetup complete.")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


if __name__ == "__main__":
    main()
