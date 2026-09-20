# Sunset Custom Framing — pickup sync and production digest

Puts Virtual Framer pickup dates on the **work production** Google Calendar as
`PICKUP — {client} — {artwork code}` events at 9:00am Eastern, then emails a
daily production digest.

Runs automatically every morning via GitHub Actions. Everything below is for
running it **by hand**, which is safe to do any time — the sync is idempotent,
so an extra run adds nothing.

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

Goes to DIGEST_TO_ADDRESS.

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
| `run_daily.sh` | Runs both, in order. The cron entry point. |
| `.github/workflows/daily.yml` | The daily schedule. |

The original OCR script and the one-off code-repair script were deleted once
their work was done. Both are in git history if you ever need them.

---

## Setup on a new machine

    python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
    cp .env.example .env        # then fill in VF_USERNAME and VF_PASSWORD

Put `credentials.json` (the Google OAuth client) in this folder, then:

    ./.venv/bin/python vf_due_sync.py --auth

That prints a URL. Open it in a browser signed into the
`your-workspace.example` Google account, approve, and it writes `token.json`.
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

Every run writes `last_run_report.json`, and the digest email surfaces it:

- **ERRORS** — the software failed. A rejected login, a dead network, a
  truncated response. The calendar may be out of date.
- **NEEDS ATTENTION** — a record needs fixing in Virtual Framer, usually an
  order with no pickup date. The software is fine; the data is not.

The sync exits non-zero and refuses to write rather than reporting success on
an empty result. If a scheduled run fails, GitHub emails you and the report is
attached to the run as an artifact.

## Marking something done

Renaming a calendar event to include *done*, *paid*, *picked up* or *complete*
stops it being modified — though the sync never modifies events anyway. It does
**not** remove the pickup from the digest. That takes marking the order
delivered in Virtual Framer. Two confirmations.

Follow-ups are the exception: they exist only on the calendar, so a done mark
in the title is the only signal there is and it does drop them from the digest.
