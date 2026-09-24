# Sunset Custom Framing — pickup sync and production digest

Puts Virtual Framer pickup dates on the **work production** Google Calendar as
`PICKUP — {client} — {artwork code}` events at 9:00am Eastern, then emails a
daily production digest.

Runs automatically every morning: **Google Apps Script** starts it, **GitHub
Actions** does the work. Everything below is for running it **by hand**, which
is safe any time — the sync is idempotent, so an extra run adds nothing.

---

## Running it by hand

All commands assume you are in the project folder:

    cd ~/Documents/respositories/sunset-custom-framing

Local runs use `./.venv/bin/python`. If the venv is missing, rebuild it:

    python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

### See what the sync would do — changes nothing

    ./.venv/bin/python vf_due_sync.py

**This is the default and it is a dry run.** It prints one line per order:
`exists` means the order already has an event and will be left alone,
`would_insert` means it would create one. Nothing is written.

### Actually add the missing events

    ./.venv/bin/python vf_due_sync.py --live

Only `--live` writes. It **only ever inserts**. It never edits, moves or
deletes an existing event, so anything your team has changed by hand — a
`DONE - ` prefix, a note, a moved time — is safe.

### Look further back or further ahead

    ./.venv/bin/python vf_due_sync.py --days-back 180 --days-ahead 365

Defaults are 90 days back, 180 forward. Use this to check on older orders
without changing what the daily run does.

### Preview the digest email — sends nothing

    ./.venv/bin/python send_digest.py

Writes `digest_preview.html`. Open it in a browser to see exactly what would
be sent.

### Send the digest now

    ./.venv/bin/python send_digest.py --send

Goes to the shop address in DIGEST_TO.

### Do both, the way the cron does

    ./run_daily.sh

Sync first, then digest. **This one writes** — it is the scheduled job, not a
preview.

---

## Quick reference

| Command | Writes to calendar? | Sends email? |
|---|---|---|
| `vf_due_sync.py` | no | no |
| `vf_due_sync.py --live` | yes, inserts only | no |
| `send_digest.py` | no | no |
| `send_digest.py --send` | no | yes |
| `run_daily.sh` | yes | yes |

---

## Files

| File | What it is |
|---|---|
| `vf_due_sync.py` | Puts missing pickups on the calendar. Insert-only. |
| `send_digest.py` | Emails the production digest. |
| `run_daily.sh` | Runs both, in order. What the GitHub job calls. |
| `.github/workflows/daily.yml` | The job GitHub runs when dispatched. |
| `trigger/AppsScriptTrigger.gs` | The schedule. Lives in Apps Script. |

The original OCR script and the one-off code-repair script were deleted once
their work was done. Both are in git history if you ever need them.

---

## Setup on a new machine

    python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
    cp .env.example .env    # fill in VF_USERNAME, VF_PASSWORD, VF_CALENDAR_ID, DIGEST_TO

Put `credentials.json` (the Google OAuth client) in this folder, then:

    ./.venv/bin/python vf_due_sync.py --auth

That prints a URL. Open it in a browser signed into the
`your Google Workspace` Google account, approve, and it writes `token.json`.
You only do this once.

None of `.env`, `credentials.json`, `token.json` or `.vf_token.json` are in git.

---

## How it decides an order already has an event

Every event it creates carries a hidden `vfJobCode` property holding the
artwork code. It is invisible in Google Calendar and survives renaming, so you
can retitle an event to anything and the sync still recognises it.

If that lookup fails it falls back to same-day matching that tolerates the
character confusions the old OCR script used to make (`O`/`0`, `I`/`1`,
`S`/`5`), and then to same-day-plus-client when a code is off by one
character. A matched event is claimed and cannot be matched again, so a client
with several artworks on one day gets one event per artwork.

## When something is wrong

The digest is sent first and on its own. A problem with one job must not stop
the other thirty being reported, so anything that went wrong is emailed
**separately**, as *"Production Digest — N things to look at"*.

Each item says three things, in plain words:

- **What happened** — which job, which client, which code
- **What it means** — the effect on today's list, so it is clear whether the
  digest can be trusted
- **What to do** — the actual next step, not a suggestion to read the logs

The raw error text comes last, and only when it helps.

The point is that a problem is visible rather than silent, and actionable by
someone who did not write the script and does not have time to read it.


Every run writes `last_run_report.json`, and the digest email surfaces it:

- **ERRORS** — the software failed. A rejected login, a dead network, a
  truncated response. The calendar may be out of date.
- **NEEDS ATTENTION** — a record needs fixing in Virtual Framer, usually an
  order with no pickup date. The software is fine; the data is not.

The sync exits non-zero and refuses to write rather than reporting success on
an empty result. If a scheduled run fails, GitHub emails you and the report is
attached to the run as an artifact.

## When sign-in breaks

The Virtual Framer token renews itself from `.env` and never needs you. The
Google token is long-lived but not permanent — it stops working if someone
revokes the app's access, if the Workspace admin changes its configuration, or
if it goes unused for six months. Running daily means the six-month rule never
fires.

You will know because the run fails, GitHub emails you, and the digest reports
it under **ERRORS** with these steps included in the email.

**Google sign-in failed** — on the shop computer:

    cd ~/Documents/respositories/sunset-custom-framing
    ./.venv/bin/python vf_due_sync.py --auth

Open the URL it prints in a browser signed in to the `your Google Workspace`
Google account and approve. That writes a new `token.json`. Then update the
copy GitHub uses:

    gh secret set GOOGLE_TOKEN_JSON < token.json

**Virtual Framer sign-in failed** — the password in `.env` is wrong or has
changed. Fix it there, then:

    gh secret set VF_PASSWORD

and paste the new password when prompted.

## What schedules it

**Google Apps Script**, not GitHub cron.

GitHub's scheduler never fired once for this repository — six scheduled slots
across two days, both while private and after going public, zero runs, while
every manual dispatch succeeded. Actions reported operational throughout and
every setting checked out. It is a known unfixed GitHub bug, reported in
community discussions 202602, 203822, 205984 and others, all unanswered.

So the workflow is started from outside. `trigger/AppsScriptTrigger.gs` holds
two time triggers in an Apps Script project called **Sunset daily sync**:

| When | Function | What it does |
|---|---|---|
| 10–11am | `fireDailySync` | dispatches the GitHub workflow; emails if it cannot |
| 11am–noon | `checkDailySyncRan` | confirms a run succeeded; emails if none did |

Apps Script day timers fire somewhere inside the hour you pick, in the script
account's timezone, and handle daylight saving themselves. GitHub still does
all the work — it is simply not asked to know what time it is.

`daily.yml` has no `schedule:` at all — it only runs when dispatched. GitHub's
cron did eventually start firing once the repo was public, but around four
hours late: a 14:23 slot ran at 18:08. Useless for a morning digest, and not
worth a second scheduler to explain.

There is no once-per-day guard. The job runs when it is dispatched and sends
when it runs. How often that happens is the schedule's business, not the
script's — a "have we already done today" check inside the code meant an
arbitrary midnight boundary, and made testing impossible until it passed.

Dispatch it twice and you get two emails. That is the correct answer to
someone asking for it twice.

### Rebuilding the Apps Script side

1. A GitHub **fine-grained** token: Settings → Developer settings → Personal
   access tokens → Fine-grained. Set *Repository access* to **Only select
   repositories** → this repo **first** — the permissions section stays empty
   until you do. Then **Add permissions** → search **Actions** → **Read and
   write**. Nothing else. Read alone gives 403 on dispatch; read and write
   gives 204.
2. script.google.com → New project → paste `trigger/AppsScriptTrigger.gs`.
3. Project Settings → Script Properties:
   `GITHUB_TOKEN` = the token, `DIGEST_TO` = the recipient address.
4. Triggers → Add Trigger, twice, as in the table above. Deployment: **Head**.
   Failure notifications: **Notify immediately**.
5. Run `fireDailySync` by hand. A run should appear in the Actions tab within
   seconds.

## What happens when it does not run at all

The digest can only report a problem if the script ran. If nothing starts it,
nothing is caught and nothing is sent — silence looks exactly like success.

**`checkDailySyncRan`** covers that: it asks GitHub whether the sync succeeded
today and emails **Production Digest DID NOT RUN** if not. It lives in Apps
Script rather than GitHub deliberately — a check that runs on the scheduler it
is checking is not a check. Google's own "notify immediately" setting on the
trigger covers the narrower case of the script itself crashing.

**A dead-man's switch** is wired but optional. Set a `HEALTHCHECK_URL` secret
(healthchecks.io has a free tier) and the job pings it on success; if the ping
stops arriving, that service emails you. It is the only thing that would catch
both GitHub and Apps Script being down at once. Without the secret the step
skips silently.

## Why this repository is public

It was made public while trying to fix the scheduler, on the theory that the
bug only affected private repos on Free plans. It did not help. It can go back
to private at any time — Apps Script triggers work either way.

Because it is public, nothing identifying is committed. The calendar id and
the recipient address come from configuration, not code; the artwork codes and
client names that appeared in early comments and commit messages were removed
from the whole history. Before committing anything, check it does not contain
a customer name, an artwork code, a calendar id, or that address — it doubles
as the Virtual Framer username.

## GitHub secrets

The GitHub job reads six secrets. They are already set; this is only for
reference or a rebuild.

| Secret | Where it comes from |
|---|---|
| `VF_USERNAME` | `.env` |
| `VF_PASSWORD` | `.env` |
| `VF_CALENDAR_ID` | the `work production` calendar |
| `DIGEST_TO` | where the digest and alerts are sent |
| `GOOGLE_CREDENTIALS_JSON` | the whole `credentials.json` file |
| `GOOGLE_TOKEN_JSON` | the whole `token.json` file |

To set them all again from the project folder:

    set -a && . ./.env && set +a
    printf '%s' "$VF_USERNAME" | gh secret set VF_USERNAME
    printf '%s' "$VF_PASSWORD" | gh secret set VF_PASSWORD
    printf '%s' "$DIGEST_TO" | gh secret set DIGEST_TO
    gh secret set GOOGLE_CREDENTIALS_JSON < credentials.json
    gh secret set GOOGLE_TOKEN_JSON      < token.json

Run the workflow by hand any time:

    gh workflow run daily.yml
    gh run watch

## Marking something done

A job leaves the digest only when **both** have happened:

1. It is marked complete in **Virtual Framer**, and
2. Its calendar event title contains *done*, *paid*, *picked up* or *complete*

Either alone leaves it listed. The calendar mark is a second pair of eyes, so
a job marked complete by mistake still gets noticed. The cost is that
completed work stays on the list until someone retitles the event — that is
the double check working, not a fault. A client with six pieces needs six
events retitled.

The one exception is an order with **no calendar event at all**: there is
nothing to mark, so Virtual Framer decides alone. Inside the 90-day window
this currently applies to nothing — every order in the window has an event.

Finding that event is not always straightforward. A pickup date can move in
Virtual Framer while the event stays where it was, because the sync never
moves an event. So an order inside the 90-day window can have its event
outside it, where the windowed load will not see it — and it would then look
like an order with no event and skip the calendar half of the check.

When an order in the window has no event in the window, the digest asks the
calendar for that one event by its `vfJobCode`, with no date bounds. Orders
outside the window are not chased: their events are legitimately elsewhere
and they cannot affect the digest. Today exactly one order needs this.

Follow-ups are different. They exist only on the calendar and have no Virtual
Framer record to check against, so a done word in the title is the only
signal and it does drop them.

None of this affects the **sync**, which never modifies an existing event
whatever its title says. This is only about what the digest lists.

## A note on order status

`isDelivered` is 2 while the job is still the shop's work, and 1, 4 or 6 once
it is complete — delivered, picked up or shipped.

The web app has an `orderComletedStatusDeafult()` enum whose ids overlap
those numbers. **It is a different field.** Reading one as the other makes 2
mean "Completed - To be delivered", which would say every order in the system
is complete and nothing is in production. It is not.


Every run writes `last_run_report.json`, and the digest email surfaces it:

- **ERRORS** — the software failed. A rejected login, a dead network, a
  truncated response. The calendar may be out of date.
- **NEEDS ATTENTION** — a record needs fixing in Virtual Framer, usually an
  order with no pickup date. The software is fine; the data is not.

The sync exits non-zero and refuses to write rather than reporting success on
an empty result. If a scheduled run fails, GitHub emails you and the report is
attached to the run as an artifact.

## When sign-in breaks

The Virtual Framer token renews itself from `.env` and never needs you. The
Google token is long-lived but not permanent — it stops working if someone
revokes the app's access, if the Workspace admin changes its configuration, or
if it goes unused for six months. Running daily means the six-month rule never
fires.

You will know because the run fails, GitHub emails you, and the digest reports
it under **ERRORS** with these steps included in the email.

**Google sign-in failed** — on the shop computer:

    cd ~/Documents/respositories/sunset-custom-framing
    ./.venv/bin/python vf_due_sync.py --auth

Open the URL it prints in a browser signed in to the `your Google Workspace`
Google account and approve. That writes a new `token.json`. Then update the
copy GitHub uses:

    gh secret set GOOGLE_TOKEN_JSON < token.json

**Virtual Framer sign-in failed** — the password in `.env` is wrong or has
changed. Fix it there, then:

    gh secret set VF_PASSWORD

and paste the new password when prompted.

## What schedules it

**Google Apps Script**, not GitHub cron.

GitHub's scheduler never fired once for this repository — six scheduled slots
across two days, both while private and after going public, zero runs, while
every manual dispatch succeeded. Actions reported operational throughout and
every setting checked out. It is a known unfixed GitHub bug, reported in
community discussions 202602, 203822, 205984 and others, all unanswered.

So the workflow is started from outside. `trigger/AppsScriptTrigger.gs` holds
two time triggers in an Apps Script project called **Sunset daily sync**:

| When | Function | What it does |
|---|---|---|
| 10–11am | `fireDailySync` | dispatches the GitHub workflow; emails if it cannot |
| 11am–noon | `checkDailySyncRan` | confirms a run succeeded; emails if none did |

Apps Script day timers fire somewhere inside the hour you pick, in the script
account's timezone, and handle daylight saving themselves. GitHub still does
all the work — it is simply not asked to know what time it is.

`daily.yml` has no `schedule:` at all — it only runs when dispatched. GitHub's
cron did eventually start firing once the repo was public, but around four
hours late: a 14:23 slot ran at 18:08. Useless for a morning digest, and not
worth a second scheduler to explain.

There is no once-per-day guard. The job runs when it is dispatched and sends
when it runs. How often that happens is the schedule's business, not the
script's — a "have we already done today" check inside the code meant an
arbitrary midnight boundary, and made testing impossible until it passed.

Dispatch it twice and you get two emails. That is the correct answer to
someone asking for it twice.

### Rebuilding the Apps Script side

1. A GitHub **fine-grained** token: Settings → Developer settings → Personal
   access tokens → Fine-grained. Set *Repository access* to **Only select
   repositories** → this repo **first** — the permissions section stays empty
   until you do. Then **Add permissions** → search **Actions** → **Read and
   write**. Nothing else. Read alone gives 403 on dispatch; read and write
   gives 204.
2. script.google.com → New project → paste `trigger/AppsScriptTrigger.gs`.
3. Project Settings → Script Properties:
   `GITHUB_TOKEN` = the token, `DIGEST_TO` = the recipient address.
4. Triggers → Add Trigger, twice, as in the table above. Deployment: **Head**.
   Failure notifications: **Notify immediately**.
5. Run `fireDailySync` by hand. A run should appear in the Actions tab within
   seconds.

## What happens when it does not run at all

The digest can only report a problem if the script ran. If nothing starts it,
nothing is caught and nothing is sent — silence looks exactly like success.

**`checkDailySyncRan`** covers that: it asks GitHub whether the sync succeeded
today and emails **Production Digest DID NOT RUN** if not. It lives in Apps
Script rather than GitHub deliberately — a check that runs on the scheduler it
is checking is not a check. Google's own "notify immediately" setting on the
trigger covers the narrower case of the script itself crashing.

**A dead-man's switch** is wired but optional. Set a `HEALTHCHECK_URL` secret
(healthchecks.io has a free tier) and the job pings it on success; if the ping
stops arriving, that service emails you. It is the only thing that would catch
both GitHub and Apps Script being down at once. Without the secret the step
skips silently.

## Why this repository is public

It was made public while trying to fix the scheduler, on the theory that the
bug only affected private repos on Free plans. It did not help. It can go back
to private at any time — Apps Script triggers work either way.

Because it is public, nothing identifying is committed. The calendar id and
the recipient address come from configuration, not code; the artwork codes and
client names that appeared in early comments and commit messages were removed
from the whole history. Before committing anything, check it does not contain
a customer name, an artwork code, a calendar id, or that address — it doubles
as the Virtual Framer username.

## GitHub secrets

The GitHub job reads six secrets. They are already set; this is only for
reference or a rebuild.

| Secret | Where it comes from |
|---|---|
| `VF_USERNAME` | `.env` |
| `VF_PASSWORD` | `.env` |
| `VF_CALENDAR_ID` | the `work production` calendar |
| `DIGEST_TO` | where the digest and alerts are sent |
| `GOOGLE_CREDENTIALS_JSON` | the whole `credentials.json` file |
| `GOOGLE_TOKEN_JSON` | the whole `token.json` file |

To set them all again from the project folder:

    set -a && . ./.env && set +a
    printf '%s' "$VF_USERNAME" | gh secret set VF_USERNAME
    printf '%s' "$VF_PASSWORD" | gh secret set VF_PASSWORD
    printf '%s' "$DIGEST_TO" | gh secret set DIGEST_TO
    gh secret set GOOGLE_CREDENTIALS_JSON < credentials.json
    gh secret set GOOGLE_TOKEN_JSON      < token.json

Run the workflow by hand any time:

    gh workflow run daily.yml
    gh run watch

## Marking something done

A job leaves the digest only when **both** have happened:

1. It is marked complete in **Virtual Framer**, and
2. Its calendar event title contains *done*, *paid*, *picked up* or *complete*

Either alone leaves it listed. The calendar mark is a second pair of eyes, so
a job marked complete by mistake still gets noticed. The cost is that
completed work stays on the list until someone retitles the event — that is
the double check working, not a fault. A client with six pieces needs six
events retitled.

The one exception is an order with **no calendar event at all**: there is
nothing to mark, so Virtual Framer decides alone. Inside the 90-day window
this currently applies to nothing — every order in the window has an event.

Finding that event is not always straightforward. A pickup date can move in
Virtual Framer while the event stays where it was, because the sync never
moves an event. So an order inside the 90-day window can have its event
outside it, where the windowed load will not see it — and it would then look
like an order with no event and skip the calendar half of the check.

When an order in the window has no event in the window, the digest asks the
calendar for that one event by its `vfJobCode`, with no date bounds. Orders
outside the window are not chased: their events are legitimately elsewhere
and they cannot affect the digest. Today exactly one order needs this.

Follow-ups are different. They exist only on the calendar and have no Virtual
Framer record to check against, so a done word in the title is the only
signal and it does drop them.

None of this affects the **sync**, which never modifies an existing event
whatever its title says. This is only about what the digest lists.

## A note on order status

`isDelivered` is 2 while the job is still the shop's work, and 1, 4 or 6 once
it is complete — delivered, picked up or shipped.

The web app has an `orderComletedStatusDeafult()` enum whose ids overlap
those numbers. **It is a different field.** Reading one as the other makes 2
mean "Completed - To be delivered", which would say every order in the system
is complete and nothing is in production. It is not.
