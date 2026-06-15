"""
Webhook server — runs on Digital Ocean alongside the scraper.
Receives calls from the Google Sheets Apps Script menu.

Endpoints:
  POST /approve-job  — post a skipped job to WordPress, update both sheets
  POST /remove-job   — expire a job in the Jobs tab and delete it from WordPress
  POST /run          — trigger a full scrape + sync in the background

Start:
    python3 sync/webhook.py

All requests must include the header:  X-Secret: <WEBHOOK_SECRET from .env>
"""

import os
import subprocess
import threading
from pathlib import Path

import anthropic
import gspread
import requests
from dotenv import find_dotenv, load_dotenv
from flask import Flask, jsonify, request

load_dotenv(find_dotenv())

_REQUIRED = ["WP_URL", "WP_USERNAME", "WP_APP_PASSWORD", "SHEET_ID",
             "ANTHROPIC_API_KEY", "WEBHOOK_SECRET"]
_missing = [v for v in _REQUIRED if not os.environ.get(v)]
if _missing:
    raise RuntimeError(f"Missing env vars: {', '.join(_missing)}")

WP_URL          = os.environ["WP_URL"].rstrip("/")
WP_USERNAME     = os.environ["WP_USERNAME"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
SHEET_ID        = os.environ["SHEET_ID"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
WEBHOOK_SECRET  = os.environ["WEBHOOK_SECRET"]

CREDENTIALS_FILE = Path(__file__).parent.parent / "config" / "google_credentials.json"
WP_AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
WP_HEADERS = {"Content-Type": "application/json"}

# Column indices for the Jobs tab (0-indexed)
COL_WP_STATUS = 7

app = Flask(__name__)


def _check_secret():
    if request.headers.get("X-Secret") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _sheets():
    gc = gspread.service_account(filename=str(CREDENTIALS_FILE))
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet("Jobs"), sh.worksheet("Skipped")


def _classify_job(title: str, company: str) -> dict:
    """Ask Claude to assign function and location for a manually approved job."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""Given this job title "{title}" at "{company}", return a JSON object with:
- "job_function": exactly one of "Engineering", "Sales", "Marketing", "Operations", "Finance", or ""
- "job_location": exactly one of "Remote", "Hybrid", "In Person"

Return ONLY the JSON object, no explanation."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    import json, re
    text = resp.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        return {"job_function": "", "job_location": "In Person"}


def _post_to_wp(company: str, title: str, url: str, function: str, location: str) -> int | None:
    """POST a job to WordPress. Returns the new post ID on success, None on failure."""
    resp = requests.post(
        f"{WP_URL}/wp-json/wp/v2/av_job",
        json={
            "title":  title,
            "status": "publish",
            "acf": {
                "job_link":     url,
                "job_function": function,
                "job_location": location,
                "is_evergreen": False,
                "job_company":  company,
            },
        },
        auth=WP_AUTH,
        headers=WP_HEADERS,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        return resp.json().get("id")
    print(f"WordPress post failed: {resp.status_code} {resp.text[:500]}", flush=True)
    return None


def _delete_from_wp(app_url: str) -> bool:
    """Find a WP job by its URL and permanently delete it."""
    search = requests.get(
        f"{WP_URL}/wp-json/wp/v2/av_job",
        params={"per_page": 100, "_fields": "id,acf"},
        auth=WP_AUTH,
        headers=WP_HEADERS,
        timeout=30,
    )
    if not search.ok:
        return False
    norm = app_url.rstrip("/").lower()
    for job in search.json():
        if job.get("acf", {}).get("job_link", "").rstrip("/").lower() == norm:
            resp = requests.delete(
                f"{WP_URL}/wp-json/wp/v2/av_job/{job['id']}",
                params={"force": True},
                auth=WP_AUTH,
                headers=WP_HEADERS,
                timeout=30,
            )
            return resp.status_code == 200
    return False


@app.route("/approve-job", methods=["POST"])
def approve_job():
    err = _check_secret()
    if err:
        return err

    data       = request.json or {}
    company    = data.get("company", "").strip()
    title      = data.get("job_title", "").strip()
    app_url    = data.get("application_url", "").strip()
    skipped_row = data.get("row_number")

    if not company or not title:
        return jsonify({"error": "Missing company or job_title"}), 400

    classified = _classify_job(title, company)
    function   = classified.get("job_function", "")
    location   = classified.get("job_location", "In Person")

    post_id = _post_to_wp(company, title, app_url, function, location)
    if not post_id:
        return jsonify({"error": "WordPress post failed"}), 500

    # Write a "posted" row to the Jobs tab and mark the Skipped row
    try:
        from datetime import date
        jobs_ws, skipped_ws = _sheets()
        today = date.today().isoformat()
        jobs_ws.append_rows([[company, title, app_url, function, False, location, today, "posted"]])
        if skipped_row:
            # Append a note in the Reason column so the Skipped row is clearly resolved
            reason_col = 4  # column D (1-indexed)
            skipped_ws.update_cell(skipped_row, reason_col, "manually approved")
    except Exception as e:
        # WP post succeeded — don't fail the whole request over a sheet write error
        print(f"Sheet update error after approve: {e}")

    return jsonify({"status": "posted", "wp_id": post_id, "function": function, "location": location})


@app.route("/remove-job", methods=["POST"])
def remove_job():
    err = _check_secret()
    if err:
        return err

    data    = request.json or {}
    app_url = data.get("application_url", "").strip()
    job_row = data.get("row_number")

    if not app_url:
        return jsonify({"error": "Missing application_url"}), 400

    deleted = _delete_from_wp(app_url)

    try:
        jobs_ws, _ = _sheets()
        if job_row:
            jobs_ws.update_cell(job_row, COL_WP_STATUS + 1, "removed")
    except Exception as e:
        print(f"Sheet update error after remove: {e}")

    status = "removed" if deleted else "not_found_on_wp"
    return jsonify({"status": status})


@app.route("/run", methods=["POST"])
def run_scraper():
    err = _check_secret()
    if err:
        return err

    repo_root = Path(__file__).parent.parent

    def _run():
        subprocess.run(["bash", "run.sh"], cwd=str(repo_root))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("WEBHOOK_PORT", 5001))
    app.run(host="0.0.0.0", port=port)
