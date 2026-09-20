# Handing this to another person

## What to send

Three credential files, by AirDrop or another private channel. Not email, not
Slack, not git.

    .env                Virtual Framer login and the calendar id
    credentials.json    Google OAuth client
    token.json          the Google sign-in already approved for
                        DIGEST_TO_ADDRESS

**Do not AirDrop the whole project folder.** It contains `.venv`, which holds
absolute paths to this machine and will not work anywhere else, plus log files
and a customer PDF. Send the three files above; the code comes from git.

To assemble them:

    cd ~/Documents/respositories/sunset-custom-framing
    mkdir -p ~/Desktop/sunset-sync-handoff
    cp .env credentials.json token.json HANDOFF.md ~/Desktop/sunset-sync-handoff/
    chmod 600 ~/Desktop/sunset-sync-handoff/*

AirDrop that folder, then delete it from your Desktop.

The other person also needs collaborator access to
https://github.com/Oxalisviolacea/sunset-custom-framing — it is private.

---

## Setup on the receiving Mac

### 1. Get the code

    git clone https://github.com/Oxalisviolacea/sunset-custom-framing.git
    cd sunset-custom-framing

### 2. Copy the three credential files into that folder

All three are in `.gitignore`, so they cannot be committed by accident.

### 3. Build the Python environment

macOS ships Python 3.9, which is enough. If `python3` is missing, install it
from python.org or with Homebrew.

    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt

### 4. Check it works — this changes nothing

    ./.venv/bin/python vf_due_sync.py

Expect a list of orders all saying `exists`, then
`Dry run complete — nothing was written.` A few `would_insert` lines are fine;
that just means new orders came in.

Preview the email without sending it:

    ./.venv/bin/python send_digest.py

That writes `digest_preview.html`. Open it in a browser.

### 5. Only when you want it to actually do something

    ./.venv/bin/python vf_due_sync.py --live     # adds missing calendar events
    ./.venv/bin/python send_digest.py --send     # emails the digest
    ./run_daily.sh                               # both, same as the daily job

---

## About token.json

It is signed in as DIGEST_TO_ADDRESS. Anything done with it —
calendar events, the digest email — happens as that account, not as you.

To use your own Google account instead, delete `token.json` and run:

    ./.venv/bin/python vf_due_sync.py --auth

You must be in the your-workspace.example Workspace for that to work.

## Worth knowing

- The daily run already happens automatically at 10am Eastern via GitHub
  Actions. Nothing has to run on anyone's laptop for that to keep working.
- The sync only ever *adds* calendar events. It never edits or deletes, so
  anything marked "DONE - " by hand stays as it is.
- Running it twice adds nothing the second time.
- Full documentation is in README.md.
