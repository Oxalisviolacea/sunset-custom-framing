#!/usr/bin/env bash
# Daily run: put missing pickups on the calendar, then email the digest.
#
# Order matters. The sync runs first so the digest reflects a calendar that is
# already up to date, and so a sync failure lands in last_run_report.json in
# time for the digest to report it. The sync is insert-only and idempotent, so
# running this daily adds nothing once every order has an event.
set -uo pipefail
cd "$(dirname "$0")"

./.venv/bin/python vf_due_sync.py --live
sync_status=$?

# The digest runs even when the sync fails -- that is how anyone finds out.
./.venv/bin/python send_digest.py --send
digest_status=$?

exit $(( sync_status || digest_status ))
