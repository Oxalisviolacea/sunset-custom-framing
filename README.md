# Sunset Custom Framing — pickup date sync

Pushes Virtual Framer pickup dates into Google Calendar as
`PICKUP — {client} — {artwork code}` events at 9:00am America/New_York.

## Files

| File | What it is |
|---|---|
| `vf_due_sync.py` | Puts missing pickups on the calendar. Insert-only. |
| `send_digest.py` | Emails the production digest. |
| `run_daily.sh` | Runs both, in order. The cron entry point. |

The original OCR script and the one-off code-repair script were deleted once
their work was done; both are in git history if you ever need them.

## Why the old one broke

It rendered a jsPDF report to images, OCR'd them with Tesseract, and matched
labels with regexes. Virtual Framer relabelled the report — `Project name:` to
`Project:`, `Client name:` to `Client:`, and the artwork code moved off the
`Artwork:` line — so three of its four regexes stopped matching. It parsed zero
records and still printed `Sync complete.`

`vf_due_sync.py` calls the endpoint the web app calls for itself, so the data
arrives as typed JSON. No OCR, no character-confusion between `0`/`O`.

## Setup

    pip install -r requirements.txt
    playwright install chromium

Put your Google OAuth client secrets in `credentials.json` (same file the old
script used). First run opens a browser for Google consent and writes `token.json`.

## Use

    python vf_due_sync.py              # dry run — prints what it WOULD do
    python vf_due_sync.py --live       # actually writes to the calendar

Dry run is the default on purpose. Check the counts look right before `--live`.

Window defaults to 30 days back / 180 days ahead:

    python vf_due_sync.py --days-back 7 --days-ahead 90

## Logging in

The script keeps its own browser profile in `.vf_browser_profile/`. The first
run opens a window so you can log into Virtual Framer by hand. The session
token lasts ~30 days, so after that it runs unattended until the token expires,
then prompts again.

Your password is never read, stored, or transmitted by this script — the
browser handles it and the script only reads the resulting token.

## If it breaks again

It exits non-zero and refuses to sync when the feed returns fewer rows than
expected, rather than reporting success on an empty parse. Check:

1. Is the token expired? The script prints days remaining on every run.
2. Did `randomReference` (the artwork code) stop being populated? Dropping the
   `excludeIsDelivered` filter makes the API return project-level rows with
   null codes — if the filter semantics change, that is the first thing to check.

## Duplicate prevention

Before writing anything, the script loads every existing event in the date
window and matches each order against it, in order of confidence:

1. **Exact code** — the `vfJobCode` property, anywhere in the window. Catches an
   order whose pickup date moved, and moves the event with it.
2. **Same day, OCR-folded code** — `O/0`, `I/L/1`, `S/5`, `B/8`, `Z/2`, `G/6` are
   folded together, so an event the old script wrote as `Q30B8` is recognised as
   `YZA78` and updated rather than duplicated. The correct code is stamped on
   write, so each event self-heals the first time it is touched.
3. **Same day, client name** — only when that client has exactly one pickup that
   day *and* exactly one candidate event matches.

An event matched by one order is claimed and cannot be matched by another, so
three artworks for the same client on the same day map to three distinct events
instead of collapsing onto one.

Anything not matched by those three rules is genuinely new, and gets inserted.

All 15 codes from the sample PDF remain distinct after OCR folding, so the
normalisation cannot merge two real orders.
