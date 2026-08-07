# Job Board — Handoff

This document exists to make sure the job board pipeline keeps running
smoothly after Aiden Fisher (the original builder of this system) leaves
Atlanta Ventures. Jacey Cadet now owns and administers this system. For each
service this system depends on, this covers what it's for, how ownership
transfer actually works, and what's been done vs. what's still open.

## GitHub

**What it's for:** hosts this repository — all the pipeline code, `CLAUDE.md`,
this file, everything version-controlled.

**How to pass it off:** the repo needs to live under an organization (not a
personal account), and more than one person needs Owner/Admin rights on that
org — otherwise the whole repo's access depends on one person's GitHub
account staying active.

**Status:** done. The repo lives under the Atlanta Ventures GitHub org, not
Aiden's personal account.

## Railway

**What it's for:** hosts the always-on webhook service (`sync/webhook.py`)
that the Google Sheet's buttons and the scheduled cron job both call to
trigger a scrape, approve/remove a job, or nuke the board.

**How to pass it off:** Railway projects live inside a "workspace." A Hobby
workspace only ever has one member — there's no way to add a second person
with their own login while on Hobby, at any price. To have more than one
real login on the project, the workspace needs to be on the Pro plan
($20/month; additional members are free once you're on it), which then lets
you invite people by email under **Workspace Settings → Members**.

**Status:** the Railway project itself is transferred to Jacey's account. It
is currently on the Hobby plan, meaning nobody else has their own login to
it. Two ways to get outside help if needed going forward:
1. Stay on Hobby, and handle anything Railway-specific (an environment
   variable change, checking logs, a manual redeploy) via a live screen-share
   call — Jacey drives, since she owns the login; whoever's helping guides
   her. This avoids ever sharing her password.
2. Upgrade to Pro and invite a real collaborator with their own login.

This wasn't decided during the transition — worth deciding deliberately
rather than defaulting to sharing Jacey's password, which is both a security
risk and makes it impossible to tell later who actually changed something.

## Google Cloud project (Sheets access)

**What it's for:** the pipeline reads and writes the Google Sheet using a
"service account" — a non-human Google identity whose credentials live in
`config/google_credentials.json`. That service account belongs to a Google
Cloud project, and that project has its own owners, separate from who owns
the Sheet itself.

**How to pass it off:** in Google Cloud Console, open the project, go to
**IAM & Admin → IAM**, and check who holds the **Owner** role. If only one
person's personal account is listed, add another (Jacey's, or an org
account) via **Grant Access** — this only requires knowing their email
address, not logging in as them, and does *not* require touching the
existing service account or its credentials file at all. Also worth checking
**Billing** in the same sidebar separately — IAM ownership and the linked
billing account/card are two different things, and a project can be
correctly owned but still tied to someone's personal card.

**Status:** confirmed — Jacey has Owner access, inherited automatically from
the Google Workspace organization level (meaning she has Owner rights on
every project in that org, not just this one specifically).

## The Google Sheet itself

**What it's for:** the `Companies`/`Jobs`/`Skipped` tabs the pipeline reads
and writes, plus the `Job Board` menu (Approve/Remove/Run/Nuke) that calls
into the Railway webhook.

**How to pass it off:** transfer file ownership in Google Drive to the
person who should administer it long-term.

**Status:** done — ownership transferred to Jacey.

**One thing worth knowing regardless of who owns it:** anyone with *Editor*
access to the Sheet can use every option in the `Job Board` menu, including
"Nuke all jobs" — which permanently deletes every job from WordPress and
clears the Jobs/Skipped tabs. The "type NUKE to confirm" prompt is a
safeguard built into the Sheet's script, not something the server enforces
(see `sync/AppsScript.js` and the `/nuke` route in `sync/webhook.py`) — so it
stops an accidental click, not a deliberate one. Keep the Sheet's Editor list
limited to people who should reasonably be trusted with that.

## WordPress (staging and production)

**What it's for:** `sync/wp_sync.py` and `sync/webhook.py` post new jobs to
and delete expired jobs from the live atlantaventures.com job board (and a
staging copy) using a WordPress user's credentials.

**How to pass it off:** the WordPress user account used for `WP_USERNAME`/
`WP_APP_PASSWORD` (and the `WP_PROD_*` equivalents) should be a dedicated
account belonging to whoever administers this long-term — not literally
someone's personal WordPress login, since deactivating that person's account
would break every post/delete call immediately.

**Status:** done — both staging and production credentials are under
Jacey's name.

## Anthropic / Claude organization (Claude Tag + the API key the pipeline uses)

**What it's for:** two distinct things live under this one organization:
1. **Claude Tag** — the Slack bot that diagnoses and fixes low-risk issues,
   and explains/escalates high-risk ones.
2. **`ANTHROPIC_API_KEY`** — a separate credential the pipeline code itself
   calls directly (in `job_loader.py` and `sync/webhook.py`) to filter job
   relevance and classify manually-approved jobs. This is *not* the same
   thing as Claude Tag, even though both live under the same Anthropic
   organization.

**How to pass it off:** for Claude Tag, check the organization's settings
(under "Where Claude Tag works") to confirm the Slack connection is
registered at the organization level, not under one person's individual
profile — and separately, confirm more than one person is an admin on the
Claude organization itself, the same "is there a second owner" check as
everywhere else on this list. For the API key: unlike most of the other
credentials on this list, Anthropic's own console states that API keys are
owned by the workspace/organization, not by whoever created them, and
explicitly remain active after the creator is removed — so, unlike
WordPress or Google Cloud, there's nothing to transfer here at all.

**Status:** done, and simpler than expected. Claude Tag's Slack connection is
registered at the organization level, and Jacey is an admin on the Claude
organization, so both survive Aiden's departure. The `ANTHROPIC_API_KEY`
key the pipeline uses (created under Aiden's account) does not need to be
regenerated or swapped — confirmed via the Anthropic console's own API keys
page, which states keys remain active after the creator is removed. There's
also an "included usage" credit shown in the org settings that expires
soon — confirmed there is ongoing billing behind it, so that's not a
concern either.

## Known limitation — "removed" doesn't always mean actually removed

When a job is deleted from the Jobs tab (a company gets removed, a job expires,
someone clicks "Remove"), the system finds the matching post on WordPress by
looking for an exact match on its stored URL. If that match fails for any
reason — a placeholder URL, a URL that drifted slightly since it was posted,
a tracking parameter, a `www.` prefix — most of these paths quietly mark the
row "removed" anyway, on the assumption that "couldn't find it" means "must
already be gone." It doesn't always mean that. This is what happened with the
Permitable job in early August 2026: the sheet said "removed," but the post
was still live on the website.

Practical effect: **the Jobs tab saying "removed" is not a 100% guarantee the
job is actually off the website.** It usually is — this only bites when a
URL doesn't cleanly match — but it's worth occasionally spot-checking
WordPress directly against a few rows the sheet claims are gone, especially
after removing a company or noticing something looks off. This is a known,
understood gap, not something to try to fix under time pressure — it touches
`sync/wp_sync.py` and `sync/webhook.py` (the high-risk tier in `CLAUDE.md`),
and a rushed, unverified change to the live delete path is a worse risk than
the current gap itself.

## Slack (the incoming webhook + Claude Tag)

**What it's for:** two separate Slack integrations: a plain incoming webhook
app (`SLACK_WEBHOOK_URL`) that `notify.py` and other scripts post run
summaries and alerts to, and the Claude Tag bot that responds in that same
channel.

**How to pass it off — two different systems to check:**
1. **Workspace ownership** — Slack distinguishes **Owner** from plain
   **Admin**; check `[workspace].slack.com/admin` → Manage members, and
   confirm someone other than any one departing person holds Owner.
2. **App-level control**, separate from workspace roles — apps created via
   `api.slack.com/apps` have their own **Collaborators** list, independent
   of who's a workspace admin. Whoever created an app is often the only
   collaborator on it by default. Add other people as collaborators there so
   the app's configuration (tokens, scopes, reinstalling it) isn't reachable
   through only one person's account. Also worth checking, per app, under
   **OAuth & Permissions**: if it only uses **Bot Token Scopes**, the running
   integration is tied to the workspace and survives anyone leaving; if it
   also has **User Token Scopes**, that part is tied to the specific person
   who authorized it.

**Status:** the incoming webhook app — Jacey added as a collaborator. Claude
Tag doesn't show up in `api.slack.com/apps` at all (expected — it's
Anthropic's app, not one created here), but it's confirmed connected at the
organization level from the Anthropic side (see above), which is the
relevant check for that one.

## Escalation — what happens when something breaks

Claude Tag handles low-risk problems on its own — see the risk tiers in
`CLAUDE.md` for exactly what that covers. For anything higher-risk, it's
designed to stop and explain the problem in plain language rather than guess
or act on its own.

Past that point, there needs to be a real person to escalate to. As of this
handoff, the plan is that Jacey can call Aiden if she gets stuck. Worth being
explicit about the limits of that: once Aiden's own accounts are gone, he can
talk through and help understand a problem, but he can no longer log in and
fix anything himself. If a real technical contact (a developer, an agency,
anyone with actual account access) gets arranged for the longer term, that
should replace this section — a phone call to someone who no longer has
access to anything is not a durable safety net on its own.

## Where to look for more

- `CLAUDE.md`, in this same repository, is the technical reference — what
  this system is, how it's built to fail safely, which files are safe to fix
  vs. which need a human, and what common Slack alerts actually mean.
- The Google Sheet has its own documentation tab (linked from `CLAUDE.md`)
  with more written background, kept separate from the day-to-day
  `Companies`/`Jobs`/`Skipped` tabs.
