# Atlanta Ventures Job Board — Project Context

This file exists so that a fresh Claude session (Claude Tag in Slack, or Claude
Code) can understand this system quickly without re-deriving it from scratch.
Read this before touching anything.

## Before running any Python in this repo

Install dependencies first: `pip install -r requirements.txt`. The sandbox a
session runs in does not have these preinstalled, so `import anthropic`,
`import gspread`, `import flask`, etc. will fail with `ModuleNotFoundError`
otherwise — that failure means "dependencies aren't installed," not "the repo
is broken." Do this before running `test_failure_handling.py` or any fetcher
directly. (`playwright install --with-deps chromium` is only needed if you're
actually driving a real browser via `fetchers/web_scraper.py`'s `WebScraper`
— not needed to run the test suite.)

## What this is

A pipeline that scrapes job postings from portfolio companies' careers pages
(Ashby, Lever, Greenhouse, SmartRecruiters, Workable, Breezy, Google Docs,
PDFs, and generic pages via Claude extraction), filters them for relevance
with Claude, stores them in a Google Sheet, and syncs them to the
atlantaventures.com WordPress job board. It runs on a schedule via a cron
trigger hitting a webhook.

## Additional documentation

There's a documentation tab in the job board's Google Sheet with more
written background on the system, kept separate from the `Companies`/`Jobs`/
`Skipped` tabs the pipeline reads and writes:
https://docs.google.com/spreadsheets/d/18kA-fyuvR0nQUm_82e6SmYqsqbM5bNbFfrEVoI6U1Ig/edit?gid=2038823298#gid=2038823298

Treat this as a lookup, not something to read by default — open it only when
a question isn't already answered by this file.

## Pipeline order (run.sh)

1. `job_loader.py` — reads the `Companies` tab, routes each company's Careers
   URL(s) to the right fetcher in `fetchers/`, filters results through
   `staged_job_writer.py` (Claude relevance pass), writes to the `Jobs` /
   `Skipped` tabs. Writes `/tmp/run_stats.json`.
2. `remind.py` — auto-expires old manually-added listings, reminds about
   others due for review.
3. `sync/wp_sync.py` — pushes new `Jobs` tab rows to WordPress, deletes
   expired ones. Merges more stats into `/tmp/run_stats.json`.
4. `notify.py` — reads `/tmp/run_stats.json`, posts one Slack summary for
   the whole run.

`sync/webhook.py` is the always-on Flask service (triggered by `cron.sh`,
and by manual buttons in the Google Sheet) that kicks off `run.sh` in the
background, and also handles manual job approve/remove/nuke actions directly.

## Branches and deploy — read before merging anything

Two branches: `dev` and `main`. **Open PRs against `dev`.** The service is
hosted on Railway. Merging `dev → main` is believed to trigger a redeploy of
the live service — confirm this against the Railway dashboard before
assuming it's automatic. Treat a merge to `main` as equivalent to shipping
to production immediately — there is no separate deploy step or CI gate
today.

## Secrets

`.env` holds live WordPress admin credentials (staging and production),
`SLACK_WEBHOOK_URL`, `WEBHOOK_SECRET`, and `ANTHROPIC_API_KEY`. **Never
print, log, or include these values in a Slack reply, PR description, or
commit.** `.env` is gitignored — keep it that way.

## Why the system is built to fail safe (read this before "fixing" anything)

The pipeline is deliberately structured so that a broken fetcher stops new
jobs from being found, but never deletes real ones. Do not casually change
this. Specifics:

- Every fetcher in `fetchers/` raises `core.utils.ScrapeShapeError` if the
  vendor's API returns something shaped differently than expected (a
  renamed/missing key, wrong type, invalid JSON). It does **not** silently
  return `[]` for that case — `[]` is reserved for a genuine "this company
  has zero open jobs right now" result. This distinction matters because
  `job_loader.py` uses whether a fetch *raised* to decide whether it's safe
  to run expiry logic for that company (`skip_expiry`). If a fetcher
  silently returned `[]` on a shape change instead of raising, a company
  with multiple Careers URLs across platforms could have its real,
  still-live jobs from an unaffected platform wrongly marked "expired" and
  deleted from WordPress, just because one of its other URLs broke that run.
  **If you ever add a new fetcher or modify an existing one, preserve this:
  raise on unexpected shape, return `[]` only for a confirmed-empty result.**
- `job_loader.py` tracks failures per platform. If every company attempted
  on one platform fails in the same run, that's flagged as a
  `platform_wide_break` — a much stronger signal than an isolated failure,
  because it means the vendor's API almost certainly changed, not that N
  unrelated companies all went down at once.
- `notify.py` renders a distinct, loud Slack message for platform-wide
  breaks, for a `wp_sync.py` crash, and for the scraper crashing before
  writing stats at all — each is a different failure mode with a different
  fix, and they're kept visually distinct on purpose. Don't collapse them
  into one generic "something failed" message.

## Risk tiers — what's safe to fix and ship alone vs. what needs a human

**Low risk — safe to fix, open a PR, and merge without a second opinion:**
- `fetchers/*.py` — each is a single, narrow scraper for one vendor's API.
  A bad fix here mostly fails back to "still not finding jobs from that
  company," which is roughly the starting state. Low blast radius.

**High risk — do NOT propose a fix or open a PR. Diagnose the problem, explain
it in plain language, and say this one needs Aiden — then stop. Getting these
wrong can cause real data loss, not just a missed job:**
- `sync/wp_sync.py`, `sync/webhook.py` — anything that posts to or deletes
  from the live WordPress site (including the `/nuke` endpoint).
- `core/dedup.py`, `staged_job_writer.py` — the expiry/dedup logic that
  decides what counts as "no longer on the careers page."
- `.env`, `config/google_credentials.json`, or anything involving
  credentials or auth.
- Anything that would change `job_loader.py`'s `skip_expiry` behavior.

**Why no PR at all, not even "propose but flag":** whoever is maintaining
this after Aiden leaves (Jacey — non-technical, no git/code background) will
in practice approve a proposed PR regardless of whether she can evaluate the
diff, because she has no way to evaluate it. "Propose a fix, flag for
review" only works as a safety check if the reviewer can meaningfully say no.
She can't, so the only real safety is not producing a PR for this category
in the first place — surface the problem and stop.

## Common failure signatures → what they actually mean

| Slack message says | Likely cause | Where to look |
|---|---|---|
| "Every X company failed this run" (`:rotating_light:`) | X's API changed shape | `fetchers/<x>.py` — compare against a live response from that vendor |
| "Scrape failed — [company names]" (no platform-wide flag) | Isolated: bad/stale URL, page temporarily down, or a one-off shape issue | Check that company's Careers URL in the `Companies` tab first |
| "Website Sync Crashed" | `sync/wp_sync.py` itself crashed (not just some posts failing) | Check WP credentials/connectivity; nothing posted or removed this run |
| "Website Sync Failed" (no crash) | Some individual posts/deletes failed, sync otherwise ran | Usually transient — will retry next run |
| "AI model is invalid or deprecated" | `ANTHROPIC_MODEL` env var is wrong/retired | Railway env vars, not application code |
| Run stopped unexpectedly (no stats file at all) | `job_loader.py` crashed before writing any stats | Check the crash traceback in host logs |

## Fixing a deprecated/invalid model (Railway, not code)

When Slack reports "AI model is invalid or deprecated," the fix is a
one-line environment variable change in Railway (`ANTHROPIC_MODEL`) — do
**not** open a PR or touch any file for this. Two things make it non-trivial
for someone without repo access:

- Railway has separate Staging and Production environments, each with its
  own copy of `ANTHROPIC_MODEL`. Check which environment(s) actually run the
  failing model — both may need updating, not just one.
- Changing a variable in Railway does **not** auto-redeploy the service.
  Whoever updates it must also manually trigger a redeploy for each
  environment they changed, or the old value keeps running.

This is why this particular fix is "guide a human through Railway's
dashboard" rather than "open a PR" — see the Slack channel's custom
instructions for the plain-language walkthrough to give a non-technical
person.

## Workflow for a fix

This workflow (through opening a PR) applies to **low-risk** fixes only. If
step 1 shows the affected file(s) fall in the **high-risk** tier above, stop
right there: explain the problem in plain language and say this one needs
Aiden. Do not write a fix, and do not open a PR.

1. Read the specific error and the file(s) it points to. Don't guess —
   fetch a live response from the vendor's API/page if you need to confirm
   what actually changed.
2. Make the minimal change that fixes the reported failure. Don't refactor
   surrounding code, add abstractions, or "improve" things not implicated
   in the failure.
3. **Run `python3 test_failure_handling.py` before proposing anything.** It's
   fast (well under a second, fully mocked, no network calls) and covers the
   exact regression classes this repo is built to avoid — a fetcher silently
   swallowing a shape change, the `skip_expiry` protection breaking, the
   `platform_wide_breaks` math, and notify.py's Slack rendering. If your
   change touches any of `fetchers/*.py`, `job_loader.py`, `notify.py`, or
   `sync/wp_sync.py`, this test file already has a check for whether you just
   broke the safety behavior. `test_production.py` is a broader smoke-test
   suite that hits real vendor APIs — don't run that one automatically; it
   costs real API calls and isn't meant for a quick per-fix check.
4. Open the PR against `dev`, not `main`.
5. In the PR description, state plainly what broke and what the fix does —
   the person merging may not read the diff, so the description is what
   they're actually relying on.
