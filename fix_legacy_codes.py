"""Repair the hidden vfJobCode on events the old OCR script mislabelled.

The old sync stored whatever Tesseract read, so the calendar holds events
tagged MNO90 when the real artwork code is MNO9I. vf_due_sync.py recognises
those through fuzzy matching and leaves them alone, which is correct but means
every run depends on the fuzzy tiers. This repairs them once, at the source.

It changes ONE invisible field: extendedProperties.private.vfJobCode. Titles,
descriptions, times, colours and reminders are untouched, so your "DONE - "
prefixes and any notes survive exactly as they are.

It NEVER deletes an event. Deleting and recreating would lose your annotations
and the event's history; a patch achieves the same thing without that cost.

    python fix_legacy_codes.py              # dry run, shows what it would fix
    python fix_legacy_codes.py --apply      # patch the hidden codes
    python fix_legacy_codes.py --apply --titles   # also correct the visible title
    python fix_legacy_codes.py --apply --interactive   # confirm each one
"""

import argparse
import sys

import vf_due_sync as sync


def find_damaged(service, jobs):
    """Orders whose event was found by something other than an exact code."""
    index = sync.load_existing_events(service, jobs[0]["pickup_date"],
                                      jobs[-1]["pickup_date"])
    per_client_day = {}
    for job in jobs:
        key = (job["client"], job["pickup_date"])
        per_client_day[key] = per_client_day.get(key, 0) + 1

    claimed, damaged = set(), []
    for job in jobs:
        event, how = sync.find_match(job, index, claimed, per_client_day)
        if not event:
            continue
        claimed.add(event["id"])
        if how == "exact code":
            continue
        private = (event.get("extendedProperties", {}) or {}).get("private", {}) or {}
        damaged.append({
            "job": job,
            "event": event,
            "how": how,
            "stored": private.get("vfJobCode"),
            "correct": job["job_code"],
        })
    return damaged


def repair(service, item, fix_title, dry_run):
    event, job = item["event"], item["job"]

    # Merge rather than replace, so any other private properties survive.
    private = dict((event.get("extendedProperties", {}) or {}).get("private", {}) or {})
    private["vfJobCode"] = item["correct"]
    body = {"extendedProperties": {"private": private}}

    if fix_title and item["stored"]:
        summary = event.get("summary") or ""
        if item["stored"] in summary:
            body["summary"] = summary.replace(item["stored"], item["correct"])

    if dry_run:
        return body

    sync.service_patch(service, event["id"], body)
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually patch (default is a dry run)")
    ap.add_argument("--titles", action="store_true",
                    help="also correct the code inside the visible event title")
    ap.add_argument("--interactive", action="store_true",
                    help="ask before each event")
    ap.add_argument("--days-ahead", type=int, default=sync.DAYS_AHEAD)
    ap.add_argument("--days-back", type=int, default=sync.DAYS_BACK)
    args = ap.parse_args()
    dry_run = not args.apply

    try:
        token, company_id = sync.get_vf_credentials()
        jobs, _ = sync.to_jobs(sync.fetch_rows(token, company_id,
                                               args.days_back, args.days_ahead))
        if not jobs:
            sys.exit("No open orders returned; nothing to compare against.")
        service = sync.gcal_service()
        damaged = find_damaged(service, jobs)
    except sync.SyncError as exc:
        sys.exit(f"ERROR: {exc}")

    if not damaged:
        print("No damaged codes found. Nothing to repair.")
        return

    print(f"{len(damaged)} event(s) with a mislabelled code:\n")
    for item in damaged:
        print(f"  {item['job']['pickup_date']:%m/%d/%Y}  {item['event'].get('summary')!r}")
        print(f"      hidden code {item['stored']!r} -> {item['correct']!r}   "
              f"(found via {item['how']})")
    print()

    if dry_run:
        print("DRY RUN — nothing changed. Re-run with --apply to patch.")
        if not args.titles:
            print("Add --titles to also correct the code shown in the event title.")
        return

    fixed = skipped = failed = 0
    for item in damaged:
        if args.interactive:
            answer = input(f"Patch {item['event'].get('summary')!r}? [y/N] ").strip().lower()
            if answer != "y":
                skipped += 1
                continue
        try:
            repair(service, item, args.titles, dry_run=False)
            fixed += 1
            print(f"  fixed {item['stored']} -> {item['correct']}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED {item['stored']}: {exc}")

    print(f"\nfixed={fixed}  skipped={skipped}  failed={failed}")
    print("No events were deleted. This script cannot delete.")


if __name__ == "__main__":
    main()
