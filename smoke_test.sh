#!/usr/bin/env bash
# Exercises the paths a cron run takes that a local run usually skips.
#
# The 2026-09-23 failure was a NameError on a constant deleted by an edit.
# Local runs never hit it because a cached token means the login code never
# executes. CI has no cache, so it logs in every time and broke immediately.
# Removing the cache here reproduces that.
set -uo pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "== compile =="
"$PY" -m py_compile vf_due_sync.py send_digest.py || exit 1

echo "== names =="
"$PY" check_names.py vf_due_sync.py send_digest.py || exit 1

echo "== dry run with no cached token (forces the login path) =="
mv .vf_token.json .vf_token.json.bak 2>/dev/null || true
"$PY" vf_due_sync.py > /tmp/smoke_sync.log 2>&1; sync_rc=$?
"$PY" send_digest.py > /tmp/smoke_digest.log 2>&1; digest_rc=$?
[ -f .vf_token.json.bak ] && mv .vf_token.json.bak .vf_token.json

for name in sync digest; do
  rc=$([ "$name" = sync ] && echo $sync_rc || echo $digest_rc)
  if [ "$rc" -ne 0 ]; then
    echo "FAILED: $name"
    tail -5 "/tmp/smoke_$name.log"
    exit 1
  fi
done
echo "== both ran clean =="
