#!/bin/bash
# Rebuild the site WITH authenticated my-team access and publish it.
#
# Run on a timer by ~/Library/LaunchAgents/com.baldozz.fpl-agent.update.plist.
# CI can't see the FPL login, so only a local run like this one knows about
# transfers made since the last deadline; it also refreshes state/my_team.json
# so the next CI build doesn't regress to last-deadline picks.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
GIT=/usr/bin/git
LOCK=/tmp/fpl-agent-build.lock
cd "$REPO" || exit 1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# One build at a time (the --serve refresh button can fire concurrently).
if ! mkdir "$LOCK" 2>/dev/null; then
  # Clear a lock left behind by a crashed run (older than 15 min).
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +15 2>/dev/null)" ]; then
    log "clearing stale lock"; rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
  else
    log "another build is running — skipping"; exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Never touch the repo if there is un-committed SOURCE work; generated files
# (docs/, state/) are ours to overwrite.
DIRTY=$($GIT status --porcelain | grep -vE '^.. (docs/|state/)' || true)
if [ -n "$DIRTY" ]; then
  log "uncommitted source changes — building locally but NOT pushing:"; echo "$DIRTY"
  PUSH=no
else
  PUSH=yes
  $GIT checkout -- docs state 2>/dev/null
  $GIT pull --rebase -q || { log "pull failed — skipping this run"; exit 0; }
fi

# Reuse the running server's rebuild when it's up (shares its lock), else build directly.
if curl -s -o /dev/null --max-time 3 http://localhost:8765/; then
  log "rebuilding via local server"
  curl -s -X POST --max-time 600 http://localhost:8765/refresh \
    | "$PY" -c "import sys,json;j=json.load(sys.stdin);print('ok' if j['ok'] else 'FAILED');print('\n'.join(l for l in j['log'].splitlines() if l.startswith(('[my-team','Unified'))))"
else
  log "rebuilding directly"
  "$PY" -m fpl_agent --site --html 2>&1 | grep -E '^\[my-team|^Unified' || true
fi

[ "$PUSH" = yes ] || exit 0
$GIT add docs state
if $GIT diff --cached --quiet; then
  log "no change to publish"; exit 0
fi
$GIT commit -q -m "chore: scheduled site update [skip ci]" && $GIT push -q \
  && log "pushed" || log "push failed (check credentials)"
