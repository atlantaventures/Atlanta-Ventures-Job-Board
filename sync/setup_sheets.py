"""
Resets the Jobs and Skipped tabs in Google Sheets (clears all data, keeps headers).
Companies tab is left untouched.

Run before a full re-scrape:
    python3 sync/setup_sheets.py
"""

import os
from pathlib import Path

import gspread
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

SHEET_ID         = os.environ["SHEET_ID"]
CREDENTIALS_FILE = Path(__file__).parent.parent / "config" / "google_credentials.json"

COMPANIES_HEADERS = ["Company", "Evergreen", "Careers URL", "Last Scraped", "Custom Title", "Description"]
JOBS_HEADERS      = [
    "Company", "Job Title", "Application URL", "Function",
    "Evergreen", "Location", "Scraped At", "WP Status", "Description",
]
SKIPPED_HEADERS   = ["Company", "Job Title", "Application URL", "Reason", "Scraped At"]


def get_or_create_worksheet(sh, title, headers, clear_data=False):
    try:
        ws = sh.worksheet(title)
        if clear_data:
            ws.clear()
            print(f"  Reset tab: '{title}'")
        else:
            print(f"  Found existing tab: '{title}' (not modified)")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        print(f"  Created tab: '{title}'")
    ws.update([headers], "A1")
    return ws


def main():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)

    confirm = input("This will clear all rows from Jobs and Skipped tabs. Type RESET to continue: ").strip()
    if confirm != "RESET":
        print("Aborted.")
        return

    print("\nResetting Google Sheets...")
    get_or_create_worksheet(sh, "Companies", COMPANIES_HEADERS, clear_data=False)
    get_or_create_worksheet(sh, "Jobs",      JOBS_HEADERS,      clear_data=True)
    get_or_create_worksheet(sh, "Skipped",   SKIPPED_HEADERS,   clear_data=True)

    print(f"\nDone. Run job_loader.py to re-scrape all companies fresh.")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


if __name__ == "__main__":
    main()
