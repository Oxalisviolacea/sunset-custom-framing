"""Email the production digest. Run after vf_due_sync.py.

Pickups come from the Virtual Framer API, not the calendar, so the digest is
built from the same typed data the sync uses. Follow-ups come from the calendar,
because they are made by hand and have no Virtual Framer record.

A "DONE - " prefix on a calendar event does NOT remove a pickup from the digest.
Closing an order takes two confirmations: the calendar mark, and marking it
delivered in Virtual Framer. Until both happen it keeps showing up.

If anything fails, a plain alert goes out instead. If the digest send itself
fails, the alert is a second, much simpler send -- fewer moving parts, so it
tends to survive whatever broke the first one.
"""

import base64
import datetime
import json
import sys
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import vf_due_sync as sync

ALERT_TO = "DIGEST_TO_ADDRESS"
FOLLOW_UP_PREFIX = "follow up"
LOOKAHEAD_DAYS = 365
LOOKBACK_DAYS = 365


def fmt(d):
    return f"{d:%m-%d-%y}"


def gmail():
    return build("gmail", "v1", credentials=sync.google_credentials())


def send_mail(service, to, subject, html=None, text=None):
    body = MIMEText(html, "html") if html else MIMEText(text or "", "plain")
    body["to"] = to
    body["subject"] = subject
    raw = base64.urlsafe_b64encode(body.as_bytes()).decode()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def collect_follow_ups(cal, today):
    """Hand-made FOLLOW UP events. These exist only on the calendar."""
    lo = today - datetime.timedelta(days=LOOKBACK_DAYS)
    hi = today + datetime.timedelta(days=LOOKAHEAD_DAYS)
    out = []
    for event in sync.load_existing_events(cal, lo, hi)["all"]:
        title = (event.get("summary") or "").strip()
        low = title.lower()
        if not low.startswith(FOLLOW_UP_PREFIX):
            continue
        # Follow-ups have no Virtual Framer record to confirm against, so a
        # done mark on the calendar is the only signal there is.
        if sync.title_looks_done(title):
            continue
        day = sync.event_day(event)
        if day:
            out.append({"date": day, "title": title})
    return sorted(out, key=lambda f: f["date"])


def table(rows, colour=None):
    style = f' style="color:{colour}; font-weight:600;"' if colour else ""
    html = ['<table style="border-collapse:collapse; width:100%;">',
            '<tr><th style="text-align:left;padding-right:12px;">Due</th>'
            '<th style="text-align:left;">Job</th></tr>']
    for r in rows:
        html.append(f'<tr{style}>'
                    f'<td style="padding:6px 12px 6px 0; white-space:nowrap;">{fmt(r["date"])}</td>'
                    f'<td style="padding:6px 0;">{r["title"]}</td></tr>')
    html.append("</table>")
    return "".join(html)


def build_html(past_due, upcoming, follow_ups, problems, now):
    all_clear = not past_due and not problems
    h = ["<h2>Production Digest</h2>", f"<p>As of {now:%b %d, %Y %-I:%M %p}</p>"]

    if all_clear:
        h.append(
            '<div style="background:#e6f4ea; border-left:4px solid #1e8e3e;'
            ' padding:14px 16px; margin:16px 0; border-radius:4px;">'
            '<div style="font-size:30px; line-height:1;">&#127880;&#127881;&#127880;</div>'
            '<p style="margin:8px 0 0; color:#1e8e3e; font-weight:600; font-size:16px;">'
            'All clear &mdash; nothing past due and nothing needing attention.</p>'
            '</div>')

    if problems:
        h.append('<h3 style="color:#d93025;">NEEDS ATTENTION:</h3>')
        h.append('<ul style="color:#d93025;">')
        for p in problems:
            h.append(f"<li>{p}</li>")
        h.append("</ul>")

    h.append("<h3>PAST DUE:</h3>")
    h.append(table(past_due, "#d93025") if past_due else "<p>None.</p>")

    h.append('<h3 style="margin-top:20px;">UPCOMING:</h3>')
    h.append(table(upcoming) if upcoming else "<p>None.</p>")

    if follow_ups:
        h.append('<h3 style="margin-top:20px;">FOLLOW UPS:</h3>')
        h.append(table(follow_ups, "#6f42c1"))
    return "".join(h)


def gather_problems():
    """Blocking flags and errors from the sync's last run."""
    problems = []
    try:
        report = json.loads(sync.REPORT_PATH.read_text())
    except (OSError, ValueError):
        return ["Could not read last_run_report.json — did vf_due_sync.py run?"]

    for err in report.get("errors", []):
        problems.append(f"Sync error ({err.get('stage')}): {err.get('message')}")
    for flag in report.get("flags", []):
        if flag.get("severity") != "blocking":
            continue
        problems.append(
            f"{flag.get('reason')} — {flag.get('client') or 'unknown client'}"
            f" (artwork {flag.get('artworkCode') or '?'},"
            f" project {flag.get('project') or '?'}) — no calendar event exists"
        )
    return problems


def main():
    dry_run = "--send" not in sys.argv
    cal = None
    try:
        token, company_id = sync.get_vf_credentials()
        jobs, flags = sync.to_jobs(sync.fetch_rows(token, company_id,
                                                   LOOKBACK_DAYS, LOOKAHEAD_DAYS))
        today = datetime.datetime.now(sync.TZ).date()

        past_due = [{"date": j["pickup_date"], "title": j["title"]}
                    for j in jobs if j["pickup_date"] < today]
        upcoming = [{"date": j["pickup_date"], "title": j["title"]}
                    for j in jobs if j["pickup_date"] >= today]

        cal = sync.gcal_service()
        follow_ups = collect_follow_ups(cal, today)

        problems = gather_problems()
        problems += [f"{f['reason']} — {f.get('client') or 'unknown'}"
                     for f in flags if f.get("severity") == "blocking"]
        problems = list(dict.fromkeys(problems))

        now = datetime.datetime.now(sync.TZ)
        html = build_html(past_due, upcoming, follow_ups, problems, now)
        if not past_due and not problems:
            subject = (f"\U0001F388 Production Digest — all clear "
                       f"(Upcoming: {len(upcoming)}, Follow Ups: {len(follow_ups)})")
        else:
            subject = (f"Production Digest (Past Due: {len(past_due)}, "
                       f"Upcoming: {len(upcoming)}, Follow Ups: {len(follow_ups)}"
                       + (f", NEEDS ATTENTION: {len(problems)}" if problems else "") + ")")

        print(subject)
        print(f"  past due={len(past_due)}  upcoming={len(upcoming)} "
              f" follow ups={len(follow_ups)}  problems={len(problems)}")
        for p in problems:
            print(f"    ! {p}")

        if dry_run:
            sync.HERE.joinpath("digest_preview.html").write_text(html)
            print("\nDRY RUN — not sent. Preview: digest_preview.html")
            print("Re-run with --send to email it.")
            return

        send_mail(gmail(), ALERT_TO, subject, html=html)
        print(f"Sent to {ALERT_TO}")

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"DIGEST FAILED: {message}")
        if dry_run:
            sys.exit(1)
        # Fallback: a plain-text alert with no HTML, no calendar read, no API
        # call. Deliberately the simplest possible send.
        try:
            send_mail(gmail(), ALERT_TO,
                      "Production Digest FAILED",
                      text=("The production digest could not be built or sent.\n\n"
                            f"{message}\n\nPickup events may still be correct; "
                            "this is the digest email only."))
            print(f"Fallback alert sent to {ALERT_TO}")
        except Exception as inner:
            print(f"Fallback alert ALSO failed: {type(inner).__name__}: {inner}")
        sys.exit(1)


if __name__ == "__main__":
    main()
