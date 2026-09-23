"""One-off: make the calendar's codes match Virtual Framer, then delete this.

Two jobs, both scoped to the last 90 days — the window the sync and digest
actually look at. Older records are deliberately left alone.

1. Repair events whose stored vfJobCode is a misreading from the old OCR
   script, so every event matches Virtual Framer exactly. Once that is true
   the fuzzy matching in vf_due_sync.py and send_digest.py can be removed.

2. Create events for delivered orders that never got one. Titled
   "DONE - {client} - {code}" so they are a correct historical record and
   stay out of the digest.

A code is only repaired when the match is confident: the two differ solely by
characters the OCR confused, or by a single character. Anything matched on
weaker evidence is listed for a human and left untouched — overwriting a
correct code with a wrong one is worse than leaving it mismatched.

    python repair_once.py            # show what it would do
    python repair_once.py --apply    # do it
"""

import argparse
import datetime
import sys

import send_digest as digest
import vf_due_sync as sync


def confident(vf_code, stored):
    """Only fold-equal or one-character-different counts as the same code."""
    if not stored:
        return False
    return (sync.canon(vf_code) == sync.canon(stored)
            or sync._edit_distance_1(vf_code, stored))


def pickup_date(row):
    raw = row.get("pickDate")
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).astimezone(sync.TZ).date()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually write (default is a preview)")
    args = ap.parse_args()
    dry = not args.apply

    token, company_id = sync.get_vf_credentials()
    today = datetime.datetime.now(sync.TZ).date()
    cutoff = today - datetime.timedelta(days=sync.DAYS_BACK)

    rows = digest.fetch_every_order(token, company_id)
    cal = sync.gcal_service()
    # Load far more calendar history than we act on. Matching has to see old
    # events -- an order whose pickup date moved can have an event well outside
    # the 90-day window -- or the lookup falls through to the weakest tier and
    # returns the wrong event entirely.
    events = sync.load_existing_events(
        cal, datetime.date(2023, 1, 1), today + datetime.timedelta(days=sync.DAYS_AHEAD))

    by_code, by_day_client = {}, {}
    for event in events["all"]:
        code = ((event.get("extendedProperties", {}) or {}).get("private", {}) or {}).get("vfJobCode")
        if code:
            by_code[code.strip().upper()] = event
        day = sync.event_day(event)
        if day:
            for part in (event.get("summary") or "").lower().replace("—", "-").split("-"):
                part = part.strip()
                if len(part) > 3:
                    by_day_client.setdefault((day, part), []).append(event)

    repairs, creations, uncertain = [], [], []

    # Orders whose code matches an event exactly claim it before anything else
    # gets to guess. Without this an order can steal the event belonging to a
    # different artwork on the same day for the same client.
    claimed = {by_code[(r.get("vfReference") or "").strip().upper()]["id"]
               for r in rows
               if (r.get("vfReference") or "").strip().upper() in by_code}

    for row in rows:
        code = (row.get("vfReference") or "").strip().upper()
        day = pickup_date(row)
        if not code or not day or day < cutoff:
            continue
        if code in by_code:
            continue

        event = digest.find_event(row, by_code, by_day_client)
        if event is not None and event["id"] in claimed:
            event = None          # another order already claimed it
        client = (row.get("clientName") or "").strip()

        if event is not None:
            claimed.add(event["id"])

        if event is None:
            if row.get("isDelivered") in digest.VF_DONE_STATES:
                creations.append((day, client, code, row))
            continue

        stored = ((event.get("extendedProperties", {}) or {}).get("private", {}) or {}).get("vfJobCode")
        (repairs if confident(code, stored) else uncertain).append((day, client, stored, code, event))

    print(f"Window: {cutoff} to {today + datetime.timedelta(days=sync.DAYS_AHEAD)}\n")

    print(f"=== {len(repairs)} code(s) to repair ===")
    for day, client, stored, code, _ in sorted(repairs, key=lambda r: str(r[0])):
        print(f"  {day}  {client:<24} {stored!r} -> {code!r}")

    print(f"\n=== {len(creations)} event(s) to create ===")
    for day, client, code, _ in sorted(creations, key=lambda r: str(r[0])):
        print(f"  {day}  DONE - {client} - {code}")

    print(f"\n=== {len(uncertain)} match(es) too weak to touch — check by hand ===")
    for day, client, stored, code, event in sorted(uncertain, key=lambda r: str(r[0])):
        print(f"  {day}  {client:<24} calendar {stored!r} vs Virtual Framer {code!r}")
        print(f"       {event.get('summary')!r}")
        print(f"       matched only on same day and client, so it may be a different artwork")

    if dry:
        print("\nPREVIEW — nothing changed. Re-run with --apply.")
        return

    fixed = made = failed = 0
    for _, _, stored, code, event in repairs:
        try:
            private = dict(((event.get("extendedProperties", {}) or {}).get("private", {}) or {}))
            private["vfJobCode"] = code
            body = {"extendedProperties": {"private": private}}
            summary = event.get("summary") or ""
            if stored and stored in summary:
                body["summary"] = summary.replace(stored, code)
            cal.events().patch(calendarId=sync.CALENDAR_ID,
                               eventId=event["id"], body=body).execute()
            fixed += 1
            print(f"  repaired {stored} -> {code}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED {stored}: {exc}")

    for day, client, code, _ in creations:
        try:
            start = datetime.datetime.combine(day, datetime.time(sync.EVENT_TIME_HOUR)).replace(tzinfo=sync.TZ)
            cal.events().insert(calendarId=sync.CALENDAR_ID, body={
                "summary": f"DONE - {client} - {code}",
                "description": (f"Client: {client}\nJob Code: {code}\n"
                                f"Pick-up Date: {day:%m/%d/%Y}\n"
                                "Source: Virtual Framer (backfilled)"),
                "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
                "end": {"dateTime": (start + datetime.timedelta(minutes=sync.EVENT_DURATION_MIN)).isoformat(),
                        "timeZone": "America/New_York"},
                "extendedProperties": {"private": {"vfJobCode": code}},
            }).execute()
            made += 1
            print(f"  created DONE - {client} - {code}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED creating {code}: {exc}")

    print(f"\nrepaired={fixed}  created={made}  failed={failed}")
    print("Nothing was deleted. This script cannot delete.")


if __name__ == "__main__":
    main()
