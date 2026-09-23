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
import os
import json
import sys
from email.mime.text import MIMEText

from googleapiclient.discovery import build

import requests

import vf_due_sync as sync

# Statuses that mean the shop is finished with the job -- it has gone to the
# customer, or been parked. See FINISHED_WITH in vf_due_sync for the full list
# of what each number means.
VF_DONE_STATES = sync.COMPLETE_STATES

# Not hardcoded: this repository is public, and this address doubles as the
# Virtual Framer username. Set DIGEST_TO in .env or the environment.
ALERT_TO = os.environ.get("DIGEST_TO") or ""

# Shown whenever something looks like an expired or revoked credential, so the
# person reading the alert at 7am does not have to find the runbook.
AUTH_FIX_TEXT = """HOW TO FIX A GOOGLE SIGN-IN FAILURE

On the shop computer, in Terminal:

  cd ~/Documents/respositories/sunset-custom-framing
  ./.venv/bin/python vf_due_sync.py --auth

That prints a URL. Open it in a browser signed in to the
your Google Workspace Google account and approve. It writes a new
token.json.

Then update the copy GitHub uses:

  gh secret set GOOGLE_TOKEN_JSON < token.json

HOW TO FIX A VIRTUAL FRAMER SIGN-IN FAILURE

The password in .env is wrong or has changed. Fix it there, then:

  gh secret set VF_PASSWORD

and paste the new password when prompted."""

AUTH_FIX_HTML = (
    '<div style="background:#fff8e1; border-left:4px solid #b06000;'
    ' padding:12px 16px; margin:16px 0; border-radius:4px;">'
    '<p style="margin:0 0 8px; font-weight:600;">This looks like a sign-in '
    'problem. To fix it:</p>'
    '<p style="margin:0 0 6px;">On the shop computer, in Terminal:</p>'
    '<pre style="margin:0 0 10px; background:#fff; padding:10px; '
    'border-radius:3px; font-size:13px; overflow-x:auto;">'
    'cd ~/Documents/respositories/sunset-custom-framing\n'
    './.venv/bin/python vf_due_sync.py --auth</pre>'
    '<p style="margin:0 0 6px;">Open the URL it prints in a browser signed in '
    'to the your Google Workspace Google account and approve. Then update '
    'the copy GitHub uses:</p>'
    '<pre style="margin:0; background:#fff; padding:10px; border-radius:3px; '
    'font-size:13px; overflow-x:auto;">gh secret set GOOGLE_TOKEN_JSON &lt; token.json</pre>'
    '</div>')


def looks_like_auth_trouble(text):
    lowered = (text or "").lower()
    return any(word in lowered for word in (
        "credential", "token", "unauthorized", "invalid_grant", "expired",
        "403", "401", "wrong password", "login", "scope", "auth"))
FOLLOW_UP_PREFIX = "follow up"
# Same window the sync uses. An order the sync would never put on the calendar
# should not appear in the digest either.
LOOKAHEAD_DAYS = sync.DAYS_AHEAD
LOOKBACK_DAYS = sync.DAYS_BACK



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


def fetch_every_order(token, company_id):
    """Every order regardless of delivery state.

    The sync only wants open orders, but the digest has to see finished ones
    too: an order leaves the digest only when it is delivered in Virtual Framer
    AND marked done on the calendar. Fetching only the open ones would make the
    second half of that impossible to check.
    """
    params = {
        "isPin": "1", "searchValue": "", "sortsType": "3",
        "projectCompanyId": company_id, "limit": "500", "page": "1",
        "token": token, "companyId": company_id,
        "deviceOs": "backend", "language": "3",
    }
    try:
        body = requests.get(sync.VF_ENDPOINT, params=params, timeout=60).json()
    except (requests.RequestException, ValueError) as exc:
        raise sync.SyncError(f"orders request failed: {exc}") from exc
    if body.get("code") != 200:
        raise sync.SyncError(f"API returned code={body.get('code')} "
                             f"msg={body.get('msg')!r}")
    return body.get("data") or []


def find_event(row, by_code, _unused=None):
    """The event for this order, by code only. No fuzzy fallbacks."""
    code = (row.get("vfReference") or "").strip().upper()
    return by_code.get(code) if code else None


KNOWN_STATES = sync.COMPLETE_STATES | {sync.IN_PRODUCTION_STATE}


def production_finished(row):
    """Has the shop finished with this job?

    Finished means the framing is done, not that the piece has gone. Every
    Completed status counts, including the ones where it is still sitting in
    the shop waiting to be collected. On hold counts too.

    readDate is not used: it is set when a job is marked off, which happens at
    handover rather than at completion, so it misses exactly the cases this
    digest is supposed to drop.
    """
    return row.get("isDelivered") in VF_DONE_STATES


class Problems:
    """Things that went wrong, written for whoever opens the email at 7am.

    Each one says what happened, why it matters, and what to do. A traceback
    is not any of those, so the raw text goes last and only if it helps.
    """

    def __init__(self):
        self.items = []

    def add(self, what, why, action, detail=None):
        self.items.append({"what": what, "why": why,
                           "action": action, "detail": detail})

    def __bool__(self):
        return bool(self.items)

    def __len__(self):
        return len(self.items)

    def as_text(self):
        out = []
        for i, p in enumerate(self.items, 1):
            out.append(f"{i}. {p['what']}\n")
            out.append(f"   What it means: {p['why']}\n")
            out.append(f"   What to do:    {p['action']}\n")
            if p["detail"]:
                out.append(f"   Detail:        {p['detail']}\n")
            out.append("\n")
        return "".join(out)


def pickup_of(row):
    """The order's pickup date in local time, or date.max if it has none."""
    raw = row.get("pickDate")
    if not raw:
        return datetime.date.max
    try:
        return datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).astimezone(sync.TZ).date()
    except ValueError:
        return datetime.date.max


def find_event_anywhere(cal, code):
    """Ask the calendar for one specific event, with no date bounds.

    A pickup date can move in Virtual Framer while the event stays where it
    was, because the sync never moves an event. So an order inside the digest
    window can have its event outside it, and the windowed load will not see
    it. Rather than widening the window for everything, look up the few that
    come back empty -- usually none, at most a handful.
    """
    try:
        found = cal.events().list(
            calendarId=sync.CALENDAR_ID,
            privateExtendedProperty=f"vfJobCode={code}",
            singleEvents=True, showDeleted=False, maxResults=10,
        ).execute().get("items", [])
    except Exception as exc:
        raise LookupFailed(f"{type(exc).__name__}: {exc}") from exc
    return found[0] if found else None


class LookupFailed(Exception):
    """The calendar could not be asked about one specific event."""


def still_outstanding(row, event):
    """Should this order still appear in the digest?

    Two confirmations are needed to take a job off: complete in Virtual
    Framer, and a done word on its calendar event. Either alone leaves it
    listed. That is deliberate -- the calendar mark is a second pair of eyes,
    so a job marked complete by mistake still gets noticed.

    The cost is that completed work stays listed until someone retitles the
    event. That is the point of the double check, not a fault in it.

    One exception: an order with no calendar event has nothing to mark, so
    Virtual Framer decides alone. Without it, every order predating the
    calendar sync would sit in the digest forever.
    """
    if not production_finished(row):
        return True
    if event is None:
        return False
    return not sync.title_looks_done(event.get("summary") or "",
                                     (row.get("clientName") or "").strip())


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


def table(rows, colour=None, show_overdue=False):
    style = f' style="color:{colour}; font-weight:600;"' if colour else ""
    # A client often has several pieces due the same day, and the titles differ
    # only by a five-character code. The artwork name is what tells them apart.
    has_artwork = any(r.get("artwork") for r in rows)
    head = ('<tr><th style="text-align:left;padding-right:12px;">Due</th>'
            + ('<th style="text-align:left;padding-right:12px;">Overdue</th>'
               if show_overdue else "")
            + '<th style="text-align:left;">Job</th>'
            + ('<th style="text-align:left;padding-left:12px;">Artwork</th>'
               if has_artwork else "")
            + '</tr>')
    html = ['<table style="border-collapse:collapse; width:100%;">', head]
    for r in rows:
        overdue = ""
        if show_overdue:
            days = r.get("days_over", 0)
            label = "1 day" if days == 1 else f"{days} days"
            overdue = (f'<td style="padding:6px 12px 6px 0; white-space:nowrap;">'
                       f'{label}</td>')
        artwork = (f'<td style="padding:6px 0 6px 12px; white-space:nowrap;">'
                   f'{r.get("artwork") or ""}</td>') if has_artwork else ""
        html.append(f'<tr{style}>'
                    f'<td style="padding:6px 12px 6px 0; white-space:nowrap;">{fmt(r["date"])}</td>'
                    f'{overdue}'
                    f'<td style="padding:6px 0;">{r["title"]}</td>'
                    f'{artwork}</tr>')
    html.append("</table>")
    return "".join(html)


def build_html(past_due, upcoming, follow_ups, errors, attention, now):
    all_clear = not past_due and not errors and not attention
    h = ["<h2>Production Digest</h2>", f"<p>As of {now:%b %d, %Y %-I:%M %p}</p>"]

    if all_clear:
        h.append(
            '<div style="background:#e6f4ea; border-left:4px solid #1e8e3e;'
            ' padding:14px 16px; margin:16px 0; border-radius:4px;">'
            '<div style="font-size:30px; line-height:1;">&#127880;&#127881;&#127880;</div>'
            '<p style="margin:8px 0 0; color:#1e8e3e; font-weight:600; font-size:16px;">'
            'All clear &mdash; nothing past due and nothing needing attention.</p>'
            '</div>')

    if errors:
        h.append('<h3 style="color:#b00020;">ERRORS:</h3>')
        h.append('<p style="margin:0 0 8px; color:#b00020;">'
                 'The sync itself failed. Pickup events may be out of date.</p>')
        h.append('<ul style="color:#b00020;">')
        for e in errors:
            h.append(f"<li>{e}</li>")
        h.append("</ul>")
        if any(looks_like_auth_trouble(e) for e in errors):
            h.append(AUTH_FIX_HTML)

    if attention:
        h.append('<h3 style="color:#b06000;">NEEDS ATTENTION:</h3>')
        h.append('<p style="margin:0 0 8px; color:#b06000;">'
                 'These records need fixing in Virtual Framer.</p>')
        h.append('<ul style="color:#b06000;">')
        for a in attention:
            h.append(f"<li>{a}</li>")
        h.append("</ul>")

    h.append("<h3>PAST DUE:</h3>")
    h.append(table(past_due, "#d93025", show_overdue=True)
             if past_due else "<p>None.</p>")

    h.append('<h3 style="margin-top:20px;">UPCOMING:</h3>')
    h.append(table(upcoming) if upcoming else "<p>None.</p>")

    if follow_ups:
        h.append('<h3 style="margin-top:20px;">FOLLOW UPS:</h3>')
        h.append(table(follow_ups, "#6f42c1"))
    return "".join(h)


def gather_from_report():
    """Split the sync's last run into two kinds of trouble.

    ERRORS are the software failing -- a rejected login, a dead network, a
    truncated response. Nobody at the shop can fix those.

    NEEDS ATTENTION is data the shop has to correct in Virtual Framer, like an
    order with no pickup date. The software is working fine; the record is not.
    """
    errors, attention = [], []
    try:
        report = json.loads(sync.REPORT_PATH.read_text())
    except (OSError, ValueError):
        return (["Could not read last_run_report.json — did vf_due_sync.py run?"], [])

    for err in report.get("errors", []):
        errors.append(f"{err.get('stage', 'sync')}: {err.get('message')}")
    for flag in report.get("flags", []):
        if flag.get("severity") != "blocking":
            continue
        attention.append(
            f"{flag.get('reason')} — {flag.get('client') or 'unknown client'}"
            f" (artwork {flag.get('artworkCode') or '?'},"
            f" project {flag.get('project') or '?'}) — no calendar event exists"
        )
    return errors, attention


def send_problem_report(problems):
    """A second, separate email about what went wrong.

    Kept apart from the digest on purpose. The digest is the day's work and
    should read cleanly; this is for whoever has to sort the problem out, and
    it says what happened, what it means and what to do about it.
    """
    count = len(problems)
    subject = (f"Production Digest \u2014 {count} thing"
               f"{'' if count == 1 else 's'} to look at")
    body = (
        "The digest was sent, so today's list is in your inbox as usual.\n"
        "These are the things that did not go smoothly while building it.\n\n"
        + problems.as_text() +
        "----\n"
        "None of this stops the digest arriving. It is here so problems are "
        "visible rather than silent.\n"
    )
    try:
        send_mail(gmail(), ALERT_TO, subject, text=body)
        print(f"Problem report sent to {ALERT_TO} ({count} item(s))")
    except Exception as exc:
        print(f"Could not send the problem report: {type(exc).__name__}: {exc}")


def main():
    dry_run = "--send" not in sys.argv
    problems = Problems()
    if not ALERT_TO:
        sys.exit("ERROR: DIGEST_TO is not set. Put the recipient in .env "
                 "(see .env.example).")
    cal = None
    try:
        token, company_id = sync.get_vf_credentials()
        today = datetime.datetime.now(sync.TZ).date()

        rows = fetch_every_order(token, company_id)
        errors_early = []
        cal = sync.gcal_service()

        # Index the calendar once so each order can be checked against it.
        events = sync.load_existing_events(
            cal,
            today - datetime.timedelta(days=LOOKBACK_DAYS),
            today + datetime.timedelta(days=LOOKAHEAD_DAYS))
        by_code = {}
        for event in events["all"]:
            code = ((event.get("extendedProperties", {}) or {}).get(
                "private", {}) or {}).get("vfJobCode")
            if code:
                by_code[code.strip().upper()] = event

        unknown = sorted({r.get("isDelivered") for r in rows
                          if r.get("isDelivered") not in KNOWN_STATES})
        for state in unknown:
            problems.add(
                what=f"Virtual Framer reported an order status nobody has "
                     f"seen before (isDelivered = {state}).",
                why="The digest knows 2 means the job is still yours, and 1, "
                    "4 and 6 mean it is finished. It does not know what this "
                    "one means, so it has assumed the job is still yours and "
                    "left it listed. It may not belong here.",
                action="Tell whoever maintains this what that status is "
                       "called in Virtual Framer, so it can be handled "
                       "properly.")

        window_lo = today - datetime.timedelta(days=LOOKBACK_DAYS)
        window_hi = today + datetime.timedelta(days=LOOKAHEAD_DAYS)

        outstanding, looked_up = [], 0
        for row in rows:
            event = find_event(row, by_code)
            # Only chase a missing event for orders the digest can actually
            # report on. An old order's event is outside the window quite
            # legitimately, and looking each one up is a request per order.
            if event is None and window_lo <= pickup_of(row) <= window_hi:
                code = (row.get("vfReference") or "").strip().upper()
                if code:
                    try:
                        event = find_event_anywhere(cal, code)
                        if event is not None:
                            looked_up += 1
                    except LookupFailed as exc:
                        problems.add(
                            what=f"Could not check the calendar for "
                                 f"{row.get('clientName')} \u2014 {code}.",
                            why="That job is treated as having no calendar "
                                "event, so Virtual Framer alone decided whether "
                                "it belongs in this digest. If its event is "
                                "marked done it may be listed here wrongly, or "
                                "missing from here wrongly.",
                            action="Look at that one job on the calendar by "
                                   "hand today. If this keeps happening, the "
                                   "Google connection needs looking at.",
                            detail=str(exc))
            if still_outstanding(row, event):
                outstanding.append(row)
        if looked_up:
            print(f"  {looked_up} event(s) found outside the window by direct lookup")
        jobs, flags = sync.to_jobs(outstanding)

        past_due = [{"date": j["pickup_date"], "title": j["title"],
                     "artwork": j.get("artwork"),
                     "days_over": (today - j["pickup_date"]).days}
                    for j in jobs if j["pickup_date"] < today]
        past_due.sort(key=lambda r: r["days_over"], reverse=True)
        upcoming = [{"date": j["pickup_date"], "title": j["title"],
                     "artwork": j.get("artwork")}
                    for j in jobs if j["pickup_date"] >= today]

        follow_ups = collect_follow_ups(cal, today)

        errors, attention = gather_from_report()
        errors += errors_early
        attention += [f"{f['reason']} — {f.get('client') or 'unknown'}"
                      for f in flags if f.get("severity") == "blocking"]
        attention = list(dict.fromkeys(attention))
        errors = list(dict.fromkeys(errors))

        now = datetime.datetime.now(sync.TZ)
        html = build_html(past_due, upcoming, follow_ups, errors, attention, now)
        if not past_due and not errors and not attention:
            subject = (f"\U0001F388 Production Digest — all clear "
                       f"(Upcoming: {len(upcoming)}, Follow Ups: {len(follow_ups)})")
        else:
            bits = [f"Past Due: {len(past_due)}", f"Upcoming: {len(upcoming)}",
                    f"Follow Ups: {len(follow_ups)}"]
            if attention:
                bits.append(f"Needs Attention: {len(attention)}")
            if errors:
                bits.insert(0, f"ERRORS: {len(errors)}")
            subject = "Production Digest (" + ", ".join(bits) + ")"

        print(subject)
        print(f"  Past Due: {len(past_due)}   Upcoming: {len(upcoming)}   "
              f"Follow Ups: {len(follow_ups)}   "
              f"Needs Attention: {len(attention)}   Errors: {len(errors)}")
        for e in errors:
            print(f"    ERROR  {e}")
        for a in attention:
            print(f"    ATTN   {a}")

        # Anything the sync itself reported belongs in the same diagnostic.
        for err in errors:
            problems.add(
                what="The overnight sync reported a failure.",
                why="The pickup calendar may not have been updated, so this "
                    "digest could be missing jobs or showing stale ones.",
                action="Run ./run_daily.sh by hand on the shop computer. If "
                       "it fails again, the message below says why.",
                detail=err)

        if dry_run:
            sync.HERE.joinpath("digest_preview.html").write_text(html)
            print("\nDRY RUN — not sent. Preview: digest_preview.html")
            if problems:
                print(f"\n{len(problems)} problem(s) would also be emailed:\n")
                print(problems.as_text())
            print("Re-run with --send to email it.")
            return

        # The digest goes first and on its own. A problem with one job must
        # not stop the other thirty being reported.
        send_mail(gmail(), ALERT_TO, subject, html=html)
        print(f"Sent to {ALERT_TO}")

        if problems:
            send_problem_report(problems)

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"DIGEST FAILED: {message}")
        if dry_run:
            sys.exit(1)
        # Fallback: a plain-text alert with no HTML, no calendar read, no API
        # call. Deliberately the simplest possible send.
        try:
            body = ("The production digest could not be built or sent.\n\n"
                    f"{message}\n\n"
                    "Pickup events may still be correct; this is the digest "
                    "email only.\n")
            if looks_like_auth_trouble(message):
                body += "\n" + AUTH_FIX_TEXT + "\n"
            send_mail(gmail(), ALERT_TO, "Production Digest FAILED", text=body)
            print(f"Fallback alert sent to {ALERT_TO}")
        except Exception as inner:
            print(f"Fallback alert ALSO failed: {type(inner).__name__}: {inner}")
        sys.exit(1)


if __name__ == "__main__":
    main()
