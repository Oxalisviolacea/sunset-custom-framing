#!/usr/bin/env bash
# Daily run: put missing pickups on the calendar, then email the digest.
#
# Order matters. The sync runs first so the digest reflects a calendar that is
# already up to date, and so a sync failure lands in last_run_report.json in
# time for the digest to report it. The sync is insert-only and idempotent, so
# running this daily adds nothing once every order has an event.
set -uo pipefail
cd "$(dirname "$0")"

# Local runs use the venv; CI installs into the system interpreter. A stock
# macOS has python3 but no `python`, so check for both.
if [ -x ./.venv/bin/python ]; then
  PY=./.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "No Python found. Install it, or build the venv:" >&2
  echo "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

"$PY" vf_due_sync.py --live
sync_status=$?

# The digest runs even when the sync fails -- that is how anyone finds out.
"$PY" send_digest.py --send
digest_status=$?

if [ "$sync_status" -ne 0 ] || [ "$digest_status" -ne 0 ]; then
  echo "run_daily: sync=$sync_status digest=$digest_status"
  exit 1
fi
