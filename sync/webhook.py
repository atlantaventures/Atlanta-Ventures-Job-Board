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

import hmac
import logging
import os
import subprocess
import sys
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
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

WP_AUTH    = (WP_USERNAME, WP_APP_PASSWORD)
WP_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Column indices for the Jobs tab (0-indexed)
COL_WP_STATUS = 7

app = Flask(__name__)


def _post_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]},
            timeout=10,
        )
    except Exception:
        pass

_wz = logging.getLogger("werkzeug")
_wz.handlers = [logging.StreamHandler(sys.stdout)]


def _check_secret():
    provided = request.headers.get("X-Secret", "")
    if not hmac.compare_digest(provided, WEBHOOK_SECRET):
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


def _find_wp_job_id(app_url: str) -> int | None:
    """Paginate through all WP jobs and return the post ID matching app_url, or None."""
    norm = app_url.rstrip("/").lower()
    page = 1
    while True:
        resp = requests.get(
            f"{WP_URL}/wp-json/wp/v2/av_job",
            params={"per_page": 100, "page": page, "_fields": "id,acf"},
            auth=WP_AUTH,
            headers=WP_HEADERS,
            timeout=30,
        )
        if not resp.ok:
            return None
        batch = resp.json()
        if not batch:
            return None
        for job in batch:
            if job.get("acf", {}).get("job_link", "").rstrip("/").lower() == norm:
                return job["id"]
        if len(batch) < 100:
            return None
        page += 1


def _delete_from_wp(app_url: str) -> bool:
    """Find a WP job by its URL and permanently delete it."""
    post_id = _find_wp_job_id(app_url)
    if post_id is None:
        return False
    resp = requests.delete(
        f"{WP_URL}/wp-json/wp/v2/av_job/{post_id}",
        params={"force": True},
        auth=WP_AUTH,
        headers=WP_HEADERS,
        timeout=30,
    )
    return resp.status_code == 200


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

    if app_url and _find_wp_job_id(app_url) is not None:
        return jsonify({"status": "already_posted", "message": "Job already exists on WordPress"}), 409

    classified = _classify_job(title, company)
    function   = classified.get("job_function", "")
    location   = classified.get("job_location", "In Person")

    post_id = _post_to_wp(company, title, app_url, function, location)
    if not post_id:
        _post_slack(
            f":x: *Failed to post job to website*\n"
            f"• *{company}* — {title}\n"
            f"_The website may be temporarily unavailable. Try approving it again in a few minutes._"
        )
        return jsonify({"error": "WordPress post failed"}), 500

    # Write a "posted" row to the Jobs tab and mark the Skipped row
    try:
        from datetime import date
        jobs_ws, skipped_ws = _sheets()
        today = date.today().isoformat()
        jobs_ws.append_rows([[company, title, app_url, function, False, location, today, "posted"]])
        if skipped_row:
            skipped_ws.delete_rows(skipped_row)
    except Exception as e:
        # WP post succeeded — don't fail the whole request over a sheet write error
        print(f"Sheet update error after approve: {e}")

    _post_slack(f":white_check_mark: *Job manually approved*\n• *{company}* — {title}")
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
            jobs_ws.update_cell(job_row, COL_WP_STATUS + 1, "blocked")
    except Exception as e:
        print(f"Sheet update error after remove: {e}")

    status    = "removed" if deleted else "not_found_on_wp"
    job_title = data.get("job_title", "").strip()
    company   = data.get("company", "").strip()
    if deleted:
        _post_slack(f":wastebasket: *Job manually removed*\n• *{company}* — {job_title}")
    else:
        _post_slack(
            f":wastebasket: *Job removed from sheet* (was not on the website)\n"
            f"• *{company}* — {job_title}\n"
            f"_The job has been blocked in the sheet and won't come back. It may not have been published to the website yet._"
        )
    return jsonify({"status": status})


_LOCK_FILE = Path("/tmp/scraper.lock")


@app.route("/run", methods=["POST"])
def run_scraper():
    err = _check_secret()
    if err:
        return err

    if _LOCK_FILE.exists():
        _post_slack(":hourglass_flowing_sand: *Scraper already running* — a scrape is already in progress. Check back in a few minutes.")
        return jsonify({"status": "already_running", "message": "A scraper run is already in progress"}), 409

    _LOCK_FILE.touch()

    repo_root = Path(__file__).parent.parent

    def _run():
        try:
            subprocess.run(["bash", "run.sh"], cwd=str(repo_root))
        finally:
            _LOCK_FILE.unlink(missing_ok=True)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/nuke", methods=["POST"])
def nuke_jobs():
    err = _check_secret()
    if err:
        return err

    if _LOCK_FILE.exists():
        return jsonify({"error": "A scraper run is in progress — wait for it to finish first"}), 409

    _JOBS_HEADERS    = ["Company", "Job Title", "Application URL", "Function",
                        "Evergreen", "Location", "Scraped At", "WP Status", "Description"]
    _SKIPPED_HEADERS = ["Company", "Job Title", "Application URL", "Reason", "Scraped At"]

    def _run_nuke():
        # Step 1: delete all WP jobs
        page         = 1
        deleted      = 0
        delete_fails = 0
        wp_fetch_ok  = True
        print("Nuke started — deleting all WordPress jobs...", flush=True)
        while True:
            resp = requests.get(
                f"{WP_URL}/wp-json/wp/v2/av_job",
                params={"per_page": 100, "page": page, "_fields": "id"},
                auth=WP_AUTH,
                headers=WP_HEADERS,
                timeout=30,
            )
            if resp.status_code == 400:
                break
            if not resp.ok:
                print(f"  Nuke: WP fetch failed {resp.status_code}", flush=True)
                wp_fetch_ok = False
                break
            batch = resp.json()
            if not batch:
                break
            for job in batch:
                del_resp = requests.delete(
                    f"{WP_URL}/wp-json/wp/v2/av_job/{job['id']}",
                    params={"force": True},
                    auth=WP_AUTH,
                    headers=WP_HEADERS,
                    timeout=30,
                )
                if del_resp.status_code == 200:
                    deleted += 1
                else:
                    delete_fails += 1
                    print(f"  Nuke: failed to delete post {job['id']}: {del_resp.status_code}", flush=True)
            if len(batch) < 100:
                break
            page += 1
        print(f"  WP: {deleted} job(s) deleted", flush=True)

        # Step 2: reset Jobs and Skipped tabs (clear all rows, restore headers)
        sheets_ok = True
        try:
            gc       = gspread.service_account(filename=str(CREDENTIALS_FILE))
            sh       = gc.open_by_key(SHEET_ID)
            jobs_ws  = sh.worksheet("Jobs")
            skip_ws  = sh.worksheet("Skipped")
            jobs_ws.clear()
            jobs_ws.update([_JOBS_HEADERS], "A1")
            skip_ws.clear()
            skip_ws.update([_SKIPPED_HEADERS], "A1")
            print("  Sheets: Jobs and Skipped tabs reset", flush=True)
        except Exception as e:
            sheets_ok = False
            print(f"  Sheets reset failed: {e}", flush=True)

        print("Nuke complete.", flush=True)

        if not wp_fetch_ok or delete_fails or not sheets_ok:
            problems = []
            if not wp_fetch_ok:
                problems.append("couldn't reach the website to delete jobs — some may still be visible on the job board")
            elif delete_fails:
                problems.append(f"{delete_fails} job{'s' if delete_fails != 1 else ''} couldn't be deleted from the website and may still be visible")
            if not sheets_ok:
                problems.append("the Jobs and Skipped tabs couldn't be cleared — try running the nuke again")
            _post_slack(
                f":warning: *Nuke finished with errors* — {deleted} WP job(s) deleted\n"
                + "\n".join(f"• {p}" for p in problems)
            )
        else:
            _post_slack(f":boom: *Nuke complete* — {deleted} WP job(s) deleted, Jobs + Skipped tabs cleared")

    threading.Thread(target=_run_nuke, daemon=True).start()
    return jsonify({"status": "started", "message": "Nuking all WordPress jobs in the background"})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("WEBHOOK_PORT", 5001))
    app.run(host="0.0.0.0", port=port)
