"""Sync Virtual Framer pickup dates into Google Calendar.

Replaces the OCR-based vf_due_sync_pdf_backup.py. That script screenshotted a
jsPDF-rendered report, OCR'd the pixels, and regex'd the result; when Virtual
Framer relabelled the report fields it silently parsed zero records and still
printed "Sync complete."

This one calls the JSON endpoint the web app calls for itself, so the data
arrives typed and exact. Auth rides on the app's own 30-day JWT, captured from
a real browser session so no password is ever handled here.

Default is a dry run. Pass --live to actually write to the calendar.
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = Path(__file__).resolve().parent

# --- Virtual Framer ----------------------------------------------------------
VF_ORIGIN = "https://backend.virtualframer.com"
VF_APP_URL = f"{VF_ORIGIN}/#/workshop/workflow/summary"
VF_ENDPOINT = f"{VF_ORIGIN}/prod-api/companyProjects/web/pinned/withoutPrice"

# isDelivered states the app's own "ongoing" view hides. Keeping this identical
# to the web UI is what makes our row set match what you see on screen.
EXCLUDE_DELIVERED = "1,4,6"

PROFILE_DIR = HERE / ".vf_browser_profile"   # persists the logged-in session
TOKEN_CACHE = HERE / ".vf_token.json"

# How wide a pickup window to sync, relative to today.
DAYS_BACK = 30
DAYS_AHEAD = 180

# --- Google Calendar ---------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TZ = ZoneInfo("America/New_York")
CALENDAR_ID = os.environ.get(
    "VF_CALENDAR_ID",
    "YOUR_CALENDAR_ID@group.calendar.google.com",
)
EVENT_TIME_HOUR = 9
EVENT_DURATION_MIN = 30

# Marking an event done by hand in Google Calendar tells this script to leave
# it alone. Carried over from the original script — it is a manual override, so
# a sync must never clobber it.
DONE_WORDS = ["done", "paid", "picked up", "complete", "completed", "collected"]

# Refuse to run live if the feed returns fewer than this. The old script's
# fatal flaw was treating "parsed nothing" as success.
MIN_EXPECTED_JOBS = 1


# ============================== Virtual Framer ===============================

def _jwt_expiry(token):
    """Seconds-since-epoch expiry from a JWT payload, or None if unreadable."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except Exception:
        return None


def _token_is_fresh(token, margin_hours=12):
    exp = _jwt_expiry(token)
    if exp is None:
        return False
    return exp - time.time() > margin_hours * 3600


def _read_cached_token():
    if not TOKEN_CACHE.exists():
        return None, None
    try:
        blob = json.loads(TOKEN_CACHE.read_text())
    except Exception:
        return None, None
    token, company_id = blob.get("token"), blob.get("companyId")
    if token and company_id and _token_is_fresh(token):
        return token, company_id
    return None, None


def _harvest_token(headless):
    """Open the app in a persistent browser profile and read its localStorage.

    Headless succeeds whenever the saved profile still holds a live session.
    When it doesn't, we reopen headed so you can log in by hand, once, and the
    profile carries that session for the next ~30 days.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(VF_APP_URL, wait_until="domcontentloaded")

            deadline = time.time() + (20 if headless else 300)
            while time.time() < deadline:
                token = page.evaluate("() => localStorage.getItem('VEA-TOKEN')")
                company_id = page.evaluate(
                    "() => (localStorage.getItem('VEA-COMPANYID')"
                    " || localStorage.getItem('companyId') || '').replace(/\"/g,'')"
                )
                if token and company_id and _token_is_fresh(token):
                    TOKEN_CACHE.write_text(json.dumps({"token": token, "companyId": company_id}))
                    TOKEN_CACHE.chmod(0o600)
                    return token, company_id
                page.wait_for_timeout(1000)
            return None, None
        finally:
            ctx.close()


def get_vf_credentials():
    token, company_id = _read_cached_token()
    if token:
        return token, company_id

    token, company_id = _harvest_token(headless=True)
    if token:
        return token, company_id

    print("No live Virtual Framer session. Opening a browser — please log in.")
    token, company_id = _harvest_token(headless=False)
    if not token:
        sys.exit("ERROR: never saw a Virtual Framer token. Aborting without touching the calendar.")
    return token, company_id


def fetch_rows(token, company_id):
    now = datetime.now(TZ)
    params = {
        "isPin": "1",
        "excludeIsDelivered": EXCLUDE_DELIVERED,
        "searchValue": "",
        "sortsType": "3",
        "projectCompanyId": company_id,
        "limit": "500",
        "page": "1",
        "startPickupDate": (now - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d 00:00:00"),
        "endPickupDate": (now + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d 23:59:59"),
        "token": token,
        "companyId": company_id,
        "deviceOs": "backend",
        "language": "3",
    }
    resp = requests.get(VF_ENDPOINT, params=params, timeout=60)
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 200:
        sys.exit(f"ERROR: Virtual Framer returned code={body.get('code')} msg={body.get('msg')!r}")

    rows = body.get("data") or []
    count = body.get("count")
    if count is not None and count != len(rows):
        print(f"WARNING: API reports count={count} but returned {len(rows)} rows — possible pagination.")
    return rows


def to_jobs(rows):
    """Map API rows to calendar jobs, reporting anything unusable rather than
    dropping it on the floor."""
    jobs, skipped = [], []

    for row in rows:
        code = (row.get("randomReference") or "").strip().upper()
        client = (row.get("clientName") or "").strip()
        raw_pick = row.get("pickDate")

        if not code:
            skipped.append(f"no artwork code (projectId={row.get('parentId')}, client={client!r})")
            continue
        if not raw_pick:
            skipped.append(f"{code}: no pickDate")
            continue
        if not client:
            client = "(no client)"

        # pickDate is UTC at local midnight: "2026-10-10 04:00:00" is Oct 10 EDT.
        try:
            pick_utc = datetime.strptime(raw_pick, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            skipped.append(f"{code}: unparseable pickDate {raw_pick!r}")
            continue
        pickup_date = pick_utc.astimezone(TZ).date()

        jobs.append({
            "job_code": code,
            "client": client,
            "artwork": (row.get("artworkName") or "").strip(),
            "pickup_date": pickup_date,
            "title": f"PICKUP — {client} — {code}",
        })

    # Artwork codes are unique per row; a collision means the feed changed shape.
    by_code = {}
    for job in jobs:
        if job["job_code"] in by_code:
            skipped.append(f"{job['job_code']}: duplicate artwork code, keeping first")
            continue
        by_code[job["job_code"]] = job

    jobs = sorted(by_code.values(), key=lambda j: j["pickup_date"])
    return jobs, skipped


# ============================== Google Calendar ==============================

# Characters Tesseract routinely swapped in the old OCR pipeline. Folding these
# lets us recognise an event the old script wrote as "Q30B8" when the real code
# is "YZA78", so we update it instead of creating a second event.
CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5",
                             "B": "8", "Z": "2", "G": "6"})


def canon(code):
    return (code or "").strip().upper().translate(CONFUSABLES)


DONE_RE = re.compile(
    r"\b(" + "|".join(w.replace(" ", r"\s+") for w in DONE_WORDS) + r")\b",
    re.IGNORECASE,
)


def title_looks_done(title, client=""):
    """True when someone has hand-marked this event as finished.

    The client name is removed before testing. The original used a plain
    substring check, which fired on a client called "Donello" (contains "done")
    and would have silently skipped that job forever.
    """
    probe = title or ""
    if client:
        probe = re.sub(re.escape(client), " ", probe, flags=re.IGNORECASE)
    return bool(DONE_RE.search(probe))


def gcal_service():
    creds = None
    token_path = HERE / "token.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(HERE / "credentials.json"), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def event_day(event):
    start = event.get("start", {})
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).astimezone(TZ).date()
    if "date" in start:
        return date.fromisoformat(start["date"])
    return None


def event_code(event):
    """The job code an event claims, from its property or its title."""
    private = (event.get("extendedProperties", {}) or {}).get("private", {}) or {}
    if private.get("vfJobCode"):
        return private["vfJobCode"].strip().upper()
    match = re.search(r"([A-Z0-9]{4,6})\s*$", (event.get("summary") or "").strip())
    return match.group(1).upper() if match else ""


def load_existing_events(service, start_date, end_date):
    """Every event in the window, fetched once and indexed locally."""
    time_min = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=TZ)
    time_max = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=TZ)

    events, page_token = [], None
    while True:
        resp = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            showDeleted=False,
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    by_code, by_day = {}, {}
    for event in events:
        code = event_code(event)
        if code:
            by_code.setdefault(code, []).append(event)
        day = event_day(event)
        if day:
            by_day.setdefault(day, []).append(event)

    return {"all": events, "by_code": by_code, "by_day": by_day}


def find_match(job, index, claimed, jobs_per_client_day):
    """Locate an existing event for this order, widest-confidence first.

    Anything already claimed by another job this run is off the table, so two
    artworks picked up the same day can never collapse onto one event.
    """
    for event in index["by_code"].get(job["job_code"], []):
        if event["id"] not in claimed:
            return event, "exact code"

    same_day = [e for e in index["by_day"].get(job["pickup_date"], [])
                if e["id"] not in claimed]

    wanted = canon(job["job_code"])
    for event in same_day:
        if wanted and canon(event_code(event)) == wanted:
            return event, "legacy code"

    # Client name alone is only safe when this client has exactly one pickup
    # that day and exactly one candidate event matches.
    if jobs_per_client_day.get((job["client"], job["pickup_date"]), 0) == 1:
        hits = [e for e in same_day
                if job["client"].lower() in (e.get("summary") or "").lower()]
        if len(hits) == 1:
            return hits[0], "client + date"

    return None, None


def build_body(job):
    start = datetime.combine(job["pickup_date"], datetime.min.time()).replace(
        tzinfo=TZ, hour=EVENT_TIME_HOUR
    )
    end = start + timedelta(minutes=EVENT_DURATION_MIN)
    return {
        "summary": job["title"],
        "description": (
            f"Client: {job['client']}\n"
            f"Artwork: {job['artwork']}\n"
            f"Job Code: {job['job_code']}\n"
            f"Pick-up Date: {job['pickup_date']:%m/%d/%Y}\n"
            f"Source: Virtual Framer API"
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"},
        # Stamping the correct code heals events the old OCR script mislabelled.
        "extendedProperties": {"private": {"vfJobCode": job["job_code"]}},
    }


def upsert_event(service, job, index, claimed, jobs_per_client_day, dry_run):
    existing, how = find_match(job, index, claimed, jobs_per_client_day)
    body = build_body(job)

    if existing:
        claimed.add(existing["id"])

        old_title = (existing.get("summary") or "").strip()
        if title_looks_done(old_title, job["client"]):
            return "skipped_done"

        if dry_run:
            return f"would_update ({how})"
        service.events().patch(
            calendarId=CALENDAR_ID, eventId=existing["id"], body=body
        ).execute()
        return f"updated ({how})"

    if dry_run:
        return "would_insert"

    body["reminders"] = {"useDefault": True}
    created = service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
    claimed.add(created["id"])
    return "inserted"


# ==================================== main ===================================

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="actually write to Google Calendar (default is a dry run)")
    ap.add_argument("--days-ahead", type=int, default=DAYS_AHEAD)
    ap.add_argument("--days-back", type=int, default=DAYS_BACK)
    args = ap.parse_args()

    global DAYS_AHEAD, DAYS_BACK
    DAYS_AHEAD, DAYS_BACK = args.days_ahead, args.days_back
    dry_run = not args.live

    token, company_id = get_vf_credentials()
    exp = _jwt_expiry(token)
    if exp:
        days_left = (exp - time.time()) / 86400
        print(f"Virtual Framer session valid for {days_left:.1f} more days (company {company_id}).")

    rows = fetch_rows(token, company_id)
    jobs, skipped = to_jobs(rows)

    print(f"Rows from API: {len(rows)}   ->   usable jobs: {len(jobs)}")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for reason in skipped:
            print(f"  - {reason}")

    if len(jobs) < MIN_EXPECTED_JOBS:
        sys.exit(
            f"ERROR: only {len(jobs)} job(s) parsed, expected at least {MIN_EXPECTED_JOBS}. "
            "The feed shape probably changed. Refusing to sync."
        )

    if dry_run:
        print("\n--- DRY RUN (pass --live to write) ---")

    service = gcal_service()

    jobs_per_client_day = {}
    for job in jobs:
        key = (job["client"], job["pickup_date"])
        jobs_per_client_day[key] = jobs_per_client_day.get(key, 0) + 1

    collisions = {}
    for job in jobs:
        collisions.setdefault(canon(job["job_code"]), []).append(job["job_code"])
    for folded, codes in collisions.items():
        if len(codes) > 1:
            print(f"WARNING: codes {codes} are indistinguishable after OCR folding.")

    index = load_existing_events(service, jobs[0]["pickup_date"], jobs[-1]["pickup_date"])
    print(f"Existing events in window: {len(index['all'])}")

    claimed = set()
    tally = {}
    errors = 0

    for job in jobs:
        try:
            result = upsert_event(service, job, index, claimed,
                                  jobs_per_client_day, dry_run)
            tally[result] = tally.get(result, 0) + 1
            print(f"  {job['pickup_date']:%m/%d/%Y}  {result:<14} {job['title']}")
        except Exception as exc:
            errors += 1
            print(f"  ERROR: {job['title']} -> {exc}")

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())) + f"  errors={errors}")
    if dry_run:
        print("Dry run complete — nothing was written.")
    else:
        print("Sync complete.")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
