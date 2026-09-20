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

VF_LOGIN_ENDPOINT = f"{VF_ORIGIN}/prod-api/api/login"
VF_USERINFO_ENDPOINT = f"{VF_ORIGIN}/prod-api/api/users/checkUserInfo"
TOKEN_CACHE = HERE / ".vf_token.json"

# Confirmed against the live endpoint and the app's own bundle, which ships
# unminified comments. Its login helper reads:
#
#     const Login = (params, isDefult = false) => {
#       params.pwd = params.password;
#       return service({ url: isDefult ? '/api/loginWebApp' : '/api/login',
#                        method: 'POST', params });
#     };
#
# So: query parameters (not a JSON body), "userName" with a capital N, and the
# password sent twice -- as both `password` and `pwd`. Sending only `password`
# returns 400 "Wrong password" even when the password is correct.

# How wide a pickup window to sync, relative to today.
DAYS_BACK = 30
DAYS_AHEAD = 180

# --- Google Calendar ---------------------------------------------------------
# calendar: create pickup events. gmail.send: send the daily digest.
# gmail.send can only SEND mail -- it grants no ability to read the inbox.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
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
# Verbatim from the original script, plus "completed": the original used a
# substring test where "complete" already matched "completed", and the
# word-boundary test below would otherwise silently narrow the behaviour.
DONE_WORDS = ["done", "paid", "picked up", "complete", "completed"]

# Refuse to run live if the feed returns fewer than this. The old script's
# fatal flaw was treating "parsed nothing" as success.
MIN_EXPECTED_JOBS = 1

# Written on every run, success or failure, for the daily digest email to read.
REPORT_PATH = HERE / "last_run_report.json"


class SyncError(Exception):
    """Anything that should end the run and appear in the digest."""


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


def load_env():
    """Minimal .env reader -- avoids a dependency for four keys."""
    env = {}
    path = HERE / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("\'")
    return {k: os.environ.get(k) or env.get(k, "") for k in
            ("VF_USERNAME", "VF_PASSWORD", "VF_CALENDAR_ID", "GOOGLE_SERVICE_ACCOUNT_FILE")}


def _extract_token(payload):
    """Find the JWT and company id anywhere in a login response."""
    token = company = None

    def walk(node):
        nonlocal token, company
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and value.startswith("eyJ") and value.count(".") == 2:
                    token = token or value
                if key.lower() in ("companyid", "company_id") and value:
                    company = company or str(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return token, company


def vf_login(username, password):
    """Log in, then look up the company id. Returns (token, companyId)."""
    if not username or not password:
        raise SyncError("VF_USERNAME / VF_PASSWORD are not set in .env")

    common = {"deviceOs": "backend", "language": "3"}
    params = {"userName": username, "password": password, "pwd": password, **common}

    try:
        resp = requests.post(VF_LOGIN_ENDPOINT, params=params, timeout=30)
        payload = resp.json()
    except requests.RequestException as exc:
        raise SyncError(f"could not reach Virtual Framer: {exc}") from exc
    except ValueError:
        raise SyncError(f"login returned HTTP {resp.status_code}, not JSON")

    token = (payload.get("data") or {}).get("token")
    if not token:
        # One attempt only. Retrying would just be repeated failed logins
        # against a real account, which is how you get locked out.
        raise SyncError(f"login rejected -- code={payload.get('code')} "
                        f"msg={payload.get('msg')!r}")

    try:
        info = requests.get(VF_USERINFO_ENDPOINT,
                            params={"token": token, **common}, timeout=30).json()
        company_id = (info.get("data") or {}).get("companyId")
    except (requests.RequestException, ValueError):
        company_id = None

    if not company_id:
        raise SyncError("logged in but could not read companyId from "
                        "/api/users/checkUserInfo")

    print(f"Logged in as {username} (company {company_id}).")
    return token, str(company_id)


def get_vf_credentials():
    token, company_id = _read_cached_token()
    if token:
        return token, company_id

    env = load_env()
    token, company_id = vf_login(env["VF_USERNAME"], env["VF_PASSWORD"])
    TOKEN_CACHE.write_text(json.dumps({"token": token, "companyId": company_id}))
    TOKEN_CACHE.chmod(0o600)
    return token, company_id


def fetch_rows(token, company_id, days_back=DAYS_BACK, days_ahead=DAYS_AHEAD):
    now = datetime.now(TZ)
    params = {
        "isPin": "1",
        "excludeIsDelivered": EXCLUDE_DELIVERED,
        "searchValue": "",
        "sortsType": "3",
        "projectCompanyId": company_id,
        "limit": "500",
        "page": "1",
        "startPickupDate": (now - timedelta(days=days_back)).strftime("%Y-%m-%d 00:00:00"),
        "endPickupDate": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d 23:59:59"),
        "token": token,
        "companyId": company_id,
        "deviceOs": "backend",
        "language": "3",
    }
    try:
        resp = requests.get(VF_ENDPOINT, params=params, timeout=60)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise SyncError(f"orders request failed: {exc}") from exc
    except ValueError as exc:
        raise SyncError(f"orders request returned non-JSON (HTTP {resp.status_code})") from exc

    if body.get("code") != 200:
        raise SyncError(f"API returned code={body.get('code')} msg={body.get('msg')!r}")

    rows = body.get("data") or []
    count = body.get("count")
    if count is not None and count != len(rows):
        raise SyncError(f"API reports count={count} but returned {len(rows)} rows "
                        "-- the result is truncated or paginated")
    return rows


def to_jobs(rows):
    """Map API rows to calendar jobs.

    Returns (jobs, flags). A flag is a dict, not a sentence, because the daily
    digest email has to render it. A row missing its artwork code or pickup
    date is a data problem worth a human looking at -- never a quiet skip.
    """
    jobs, flags = [], []

    def flag(reason, row, code=""):
        flags.append({
            "reason": reason,
            "artworkCode": code or None,
            "project": row.get("projectName"),
            "client": row.get("clientName"),
            "artwork": row.get("artworkName"),
            "pickDate": row.get("pickDate"),
            "projectId": row.get("parentId") or row.get("id"),
        })

    for row in rows:
        # vfReference is the artwork code (ABC12). randomReference is present in
        # the payload but always null -- do not trust it.
        code = (row.get("vfReference") or row.get("randomReference") or "").strip().upper()
        client = (row.get("clientName") or "").strip()
        raw_pick = row.get("pickDate")

        if not code:
            flag("missing artwork code", row)
            continue
        if not raw_pick:
            flag("missing pickup date", row, code)
            continue
        if not client:
            flag("missing client name", row, code)
            client = "(no client)"

        # pickDate is UTC at local midnight: "2026-10-10 04:00:00" is Oct 10 EDT.
        try:
            pick_utc = datetime.strptime(raw_pick, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            flag(f"unparseable pickup date {raw_pick!r}", row, code)
            continue
        pickup_date = pick_utc.astimezone(TZ).date()

        jobs.append({
            "job_code": code,
            "client": client,
            "artwork": (row.get("artworkName") or "").strip(),
            "pickup_date": pickup_date,
            "title": f"PICKUP — {client} — {code}",
        })

    seen = {}
    for job in jobs:
        if job["job_code"] in seen:
            flags.append({"reason": "duplicate artwork code in feed",
                          "artworkCode": job["job_code"], "client": job["client"],
                          "project": None, "artwork": job["artwork"],
                          "pickDate": str(job["pickup_date"]), "projectId": None})
            continue
        seen[job["job_code"]] = job

    return sorted(seen.values(), key=lambda j: j["pickup_date"]), flags


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


def gcal_service(allow_interactive=False):
    creds = None
    token_path = HERE / "token.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not allow_interactive:
            raise SyncError(
                "no valid Google token. Run once with --auth to grant access "
                "(that opens a browser); after that it runs unattended."
            )
        if not (HERE / "credentials.json").exists():
            raise SyncError("credentials.json is missing from the project folder.")
        flow = InstalledAppFlow.from_client_secrets_file(str(HERE / "credentials.json"), SCOPES)
        # Two deliberate choices here:
        #   host="127.0.0.1" -- the default is "localhost", which on macOS often
        #     resolves to IPv6 ::1 while the server binds IPv4 only, so the
        #     browser lands on "This site can't be reached" after consent.
        #   open_browser=False -- run_local_server opens the OS default browser,
        #     which may not be the one signed into the right Google account.
        creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=False,
                                      timeout_seconds=600,
                                      authorization_prompt_message=
                                      "\n>>> Open this URL in the browser signed "
                                      "into the Workspace account:\n\n{url}\n")
        token_path.write_text(creds.to_json())
        token_path.chmod(0o600)

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


def _edit_distance_1(a, b):
    """True when a and b differ by at most one substitution/insert/delete."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long)):
        if short == long[:i] + long[i + 1:]:
            return True
    return False


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

    # Same client, same day, code off by a single character. Tesseract made
    # errors outside the fold map too -- it read ABC12 as ABC1Z -- and without
    # this the sync would insert a second event beside the damaged one.
    # Deliberately narrow: same day AND same client AND one character.
    for event in same_day:
        existing_code = event_code(event)
        if (existing_code
                and job["client"].lower() in (event.get("summary") or "").lower()
                and _edit_distance_1(job["job_code"], existing_code)):
            return event, "near-miss code"

    # Client name alone is only safe when this client has exactly one pickup
    # that day and exactly one candidate event matches.
    if jobs_per_client_day.get((job["client"], job["pickup_date"]), 0) == 1:
        hits = [e for e in same_day
                if job["client"].lower() in (e.get("summary") or "").lower()]
        if len(hits) == 1:
            return hits[0], "client + date"

    return None, None


def service_patch(service, event_id, body):
    """Patch an existing event. Used only by fix_legacy_codes.py.

    The sync itself never calls this -- it is insert-only by design. Repairing
    a mislabelled code is a deliberate, separate, opt-in operation.
    """
    return service.events().patch(
        calendarId=CALENDAR_ID, eventId=event_id, body=body
    ).execute()


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
    """Insert an event when the order has none. Never modify one that exists.

    The shop marks events up by hand -- renaming them, annotating the body,
    flagging them done or finished in whatever words they like. That record is
    theirs. A sync that rewrote it would destroy their process, so the only
    write this function ever performs is an insert for an order that has no
    event at all.
    """
    existing, how = find_match(job, index, claimed, jobs_per_client_day)

    if existing:
        claimed.add(existing["id"])
        old_title = (existing.get("summary") or "").strip()
        if title_looks_done(old_title, job["client"]):
            return f"exists, marked done ({how})"
        return f"exists ({how})"

    if dry_run:
        return "would_insert"

    body = build_body(job)
    body["reminders"] = {"useDefault": True}
    created = service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
    claimed.add(created["id"])
    return "inserted"


# ==================================== main ===================================

def write_report(report):
    """Always leave a machine-readable record behind, success or failure."""
    report["finished_at"] = datetime.now(TZ).isoformat()
    try:
        REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    except OSError as exc:
        print(f"WARNING: could not write {REPORT_PATH.name}: {exc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="actually write to Google Calendar (default is a dry run)")
    ap.add_argument("--days-ahead", type=int, default=DAYS_AHEAD)
    ap.add_argument("--days-back", type=int, default=DAYS_BACK)
    ap.add_argument("--auth", action="store_true",
                    help="allow the Google consent browser window to open "
                         "(one-time setup; never use this from cron)")
    args = ap.parse_args()
    dry_run = not args.live

    report = {
        "started_at": datetime.now(TZ).isoformat(),
        "dry_run": dry_run,
        "status": "failed",
        "rows_from_api": 0,
        "jobs_usable": 0,
        "flags": [],
        "errors": [],
        "actions": {},
    }

    try:
        token, company_id = get_vf_credentials()
        report["companyId"] = company_id

        rows = fetch_rows(token, company_id, args.days_back, args.days_ahead)
        jobs, flags = to_jobs(rows)
        report["rows_from_api"] = len(rows)
        report["jobs_usable"] = len(jobs)
        report["flags"] = flags

        print(f"Rows from API: {len(rows)}   ->   usable jobs: {len(jobs)}")
        for f in flags:
            print(f"  FLAG {f['reason']}: project={f['project']!r} "
                  f"client={f['client']!r} code={f['artworkCode']!r}")

        if len(jobs) < MIN_EXPECTED_JOBS:
            raise SyncError(f"only {len(jobs)} usable job(s) from {len(rows)} rows; "
                            "refusing to sync")

        service = gcal_service(args.auth)

        jobs_per_client_day = {}
        for job in jobs:
            key = (job["client"], job["pickup_date"])
            jobs_per_client_day[key] = jobs_per_client_day.get(key, 0) + 1

        folded = {}
        for job in jobs:
            folded.setdefault(canon(job["job_code"]), []).append(job["job_code"])
        for codes in folded.values():
            if len(codes) > 1:
                report["flags"].append({"reason": "codes indistinguishable after folding",
                                        "artworkCode": ", ".join(codes)})
                print(f"WARNING: codes {codes} are indistinguishable after folding.")

        index = load_existing_events(service, jobs[0]["pickup_date"], jobs[-1]["pickup_date"])
        report["existing_events_in_window"] = len(index["all"])
        print(f"Existing events in window: {len(index['all'])}")

        if dry_run:
            print("\n--- DRY RUN (pass --live to write) ---")

        claimed, tally = set(), {}
        for job in jobs:
            try:
                result = upsert_event(service, job, index, claimed,
                                      jobs_per_client_day, dry_run)
            except Exception as exc:
                result = "error"
                report["errors"].append({"stage": "calendar", "job": job["job_code"],
                                         "message": str(exc)})
                print(f"  ERROR {job['title']}: {exc}")
            tally[result] = tally.get(result, 0) + 1
            if result != "error":
                print(f"  {job['pickup_date']:%m/%d/%Y}  {result:<26} {job['title']}")

        report["actions"] = tally
        report["status"] = "ok_with_flags" if (flags or report["errors"]) else "ok"
        print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        print("Dry run complete — nothing was written." if dry_run else "Sync complete.")

    except SyncError as exc:
        report["errors"].append({"stage": "sync", "message": str(exc)})
        print(f"ERROR: {exc}")
    except Exception as exc:  # never let the cron job die without a report
        report["errors"].append({"stage": "unexpected",
                                 "message": f"{type(exc).__name__}: {exc}"})
        print(f"UNEXPECTED ERROR: {type(exc).__name__}: {exc}")

    write_report(report)
    print(f"Report written to {REPORT_PATH.name} (status: {report['status']})")
    sys.exit(0 if report["status"].startswith("ok") and not report["errors"] else 1)


if __name__ == "__main__":
    main()
