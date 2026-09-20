# Sunset pickup sync — setup

Everything you need is in this folder. No GitHub account required.

## What is in here

    vf_due_sync.py      adds missing pickup events to the calendar
    send_digest.py      emails the production digest
    run_daily.sh        runs both, the same way the 10am job does
    requirements.txt    the Python packages needed
    README.md           full documentation
    .env                Virtual Framer login and calendar id   (credential)
    credentials.json    Google OAuth client                     (credential)
    token.json          Google sign-in                          (credential)

The three credentials are real. Do not email them, post them, or put them in
a shared drive.

## Setup

### 1. Move this folder somewhere permanent

Downloads is fine to start, but it will get cleaned out eventually. Something
like `~/Documents/sunset-custom-framing` is better. Then open Terminal and go
there — you can type `cd ` (with a space) and drag the folder onto the
Terminal window to fill in the path.

### 2. Build the Python environment

macOS already has Python 3, which is enough.

    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt

That takes a minute and only has to be done once.

### 3. Check it works — this changes nothing

    ./.venv/bin/python vf_due_sync.py

You should see a list of orders, nearly all saying `exists`, ending with
`Dry run complete — nothing was written.` A few `would_insert` lines are
normal — that just means new orders came in since the last run.

Preview the email without sending it:

    ./.venv/bin/python send_digest.py

That writes `digest_preview.html`. Double-click it to open in a browser.

## Making it actually do something

    ./.venv/bin/python vf_due_sync.py --live     # add missing calendar events
    ./.venv/bin/python send_digest.py --send     # email the digest now
    ./run_daily.sh                               # both, same as the 10am job

Without `--live` or `--send`, nothing is written or emailed. That is on
purpose — you can run the plain versions any time to look around safely.

## You do not have to run any of this

The digest already goes out automatically at 10am Eastern every day, from a
scheduled job that does not depend on anyone's laptop being on. This folder is
so you can check things by hand or fix something when it breaks.

## Changing the credentials

`.env` is a plain text file. Open it in any editor:

    VF_USERNAME=...        the Virtual Framer login
    VF_PASSWORD=...        change this when the pickup-system password changes
    VF_CALENDAR_ID=...     leave alone unless the calendar itself changes

No quotes around the values. Save, and the next run uses it.

**Important:** editing this only affects runs on your own Mac. The automatic
10am job keeps its own copy, so ask whoever set this up to update that too, or
the daily email will start failing.

## About token.json

It is signed in as DIGEST_TO_ADDRESS. Anything the scripts do —
calendar events, the digest email — happens as that account, not as you.

To use your own Google account instead, delete `token.json` and run:

    ./.venv/bin/python vf_due_sync.py --auth

It prints a URL. Open it in a browser signed in to your
your-workspace.example account and approve. You must be in that Workspace.

## Worth knowing

- The sync only ever **adds** calendar events. It never edits or deletes, so
  anything marked "DONE - " by hand stays exactly as it is.
- Running it twice adds nothing the second time.
- An order stays in the digest until it is marked delivered in Virtual Framer.
  Marking it done on the calendar alone is not enough — that is deliberate.
- If something breaks, the digest email says so at the top, and tells you how
  to fix it if it is a sign-in problem.
