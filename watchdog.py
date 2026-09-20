"""Alert when the daily sync did not run at all.

The digest can only report a failure if the script actually ran. When GitHub
drops or never fires the scheduled job, nothing runs, nothing is caught, and
nothing is sent -- silence looks exactly like success. This runs later in the
day, asks the GitHub API whether the daily workflow succeeded, and emails if it
did not.

It is still a GitHub schedule, so it cannot detect GitHub being wholly down.
That is what the dead-man's switch is for. This catches the common case: one
slot getting dropped.
"""

import datetime
import json
import os
import sys
import urllib.request

import vf_due_sync as sync
from send_digest import gmail, send_mail, ALERT_TO

REPO = os.environ.get("GITHUB_REPOSITORY", "Oxalisviolacea/sunset-custom-framing")
WORKFLOW = "daily.yml"


def runs_today():
    """Successful runs of the daily workflow since midnight Eastern."""
    today = datetime.datetime.now(sync.TZ).date()
    url = (f"https://api.github.com/repos/{REPO}/actions/workflows/"
           f"{WORKFLOW}/runs?per_page=30")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "sunset-watchdog",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)

    good = []
    for run in body.get("workflow_runs", []):
        started = datetime.datetime.fromisoformat(
            run["created_at"].replace("Z", "+00:00")).astimezone(sync.TZ)
        if started.date() == today and run.get("conclusion") == "success":
            good.append(run)
    return good


def main():
    send = "--send" in sys.argv
    try:
        good = runs_today()
    except Exception as exc:
        print(f"watchdog could not reach the GitHub API: {exc}")
        # Better to shout than to assume everything is fine.
        good = None

    today = datetime.datetime.now(sync.TZ)

    if good:
        print(f"{len(good)} successful run(s) today. Nothing to report.")
        return

    reason = ("The GitHub API could not be reached, so it is unknown whether "
              "the sync ran." if good is None else
              "No successful run of the daily sync happened today.")
    body = (
        "PRODUCTION DIGEST DID NOT RUN\n\n"
        f"{reason}\n\n"
        f"Checked at {today:%b %d, %Y %-I:%M %p} Eastern.\n\n"
        "The pickup calendar has not been updated today and no digest was "
        "sent. Nothing is broken on the calendar -- it simply was not touched.\n\n"
        "WHAT TO DO\n\n"
        "Run it by hand on the shop computer:\n\n"
        "  cd ~/Documents/respositories/sunset-custom-framing\n"
        "  ./run_daily.sh\n\n"
        "That does exactly what the scheduled job would have done.\n\n"
        "This usually means GitHub skipped the scheduled job, which it does "
        "occasionally under load. If it happens several days running, the "
        "schedule itself needs looking at.\n"
    )
    print(body)

    if not send:
        print("(dry run -- pass --send to email this)")
        return

    send_mail(gmail(), ALERT_TO, "Production Digest DID NOT RUN", text=body)
    print(f"Alert sent to {ALERT_TO}")
    sys.exit(1)


if __name__ == "__main__":
    main()
