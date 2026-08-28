#!/usr/bin/env bash
# Pull the latest fix and put it where Premiere and launchd will pick it up.
#
# Three things have to happen and all three are easy to forget, which is why
# this exists rather than a paragraph in a README:
#
#   1. the code is pulled
#   2. the PANEL IS COPIED to /Library -- it is a copy, not a symlink, so a
#      pull alone changes nothing an editor can see
#   3. the helper is restarted -- it holds the engine in memory, so a pull
#      alone leaves it running the old code, which has caught me twice
#
# Runs either from a Terminal or from the panel's Update button, which reaches
# it through the helper. `--result <path>` writes a machine-readable answer for
# the second case.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RESULT=""
[ "${1:-}" = "--result" ] && RESULT="${2:-}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

STATUS="failed"
DETAIL=""
BEFORE="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

finish() {
  [ -z "$RESULT" ] && return 0
  AFTER="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  python3 -c 'import json,sys; open(sys.argv[1],"w").write(json.dumps({
      "status": sys.argv[2], "detail": sys.argv[3],
      "before": sys.argv[4], "after": sys.argv[5]}, indent=2))' \
    "$RESULT" "$STATUS" "$DETAIL" "$BEFORE" "$AFTER" 2>/dev/null || true
}
trap finish EXIT

echo
echo "Updating AutoEdit..."

if [ -n "$(git status --porcelain)" ]; then
  STATUS="dirty"
  DETAIL="this checkout has uncommitted changes, so nothing was pulled"
  warn "$DETAIL"
  git status --short
  echo
  echo "  Commit or stash them, then run this again."
  exit 1
fi

# Never wait for a password on a machine with nobody at the keyboard. A private
# repo with no working credential would otherwise hang the helper forever
# rather than failing in a few seconds with something an editor can act on.
if ! GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -oBatchMode=yes" git pull --ff-only; then
  STATUS="unreachable"
  DETAIL="could not reach the repository -- check this machine can sign in to GitHub"
  warn "$DETAIL"
  exit 1
fi

AFTER="$(git rev-parse --short HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  STATUS="current"
  DETAIL="already on the latest version ($AFTER)"
  ok "$DETAIL"
else
  STATUS="updated"
  DETAIL="$BEFORE -> $AFTER"
  ok "code $DETAIL"
  git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/      /'
fi

# The engine is installed with `pip install -e`, so the code is already live --
# but a fix may add a dependency, and that is not.
"$ROOT/.venv/bin/pip" install --quiet -e "$ROOT/engine" 2>/dev/null && ok "engine dependencies"

bash "$ROOT/panel/install.sh" >/dev/null 2>&1 && ok "panel copied to /Library"

# Last, and deliberately. This kills the helper, and when the helper is what
# started this script that kill would take the script with it -- so the helper
# launches it in its own session, out of the process group launchd stops.
if launchctl kickstart -k "gui/$(id -u)/com.company.autoedit.helper" 2>/dev/null; then
  ok "helper restarted"
else
  warn "could not restart the helper -- run ./setup.sh if it is not installed"
fi

echo
echo "One step left, and it is not optional:"
echo
echo "  Quit Premiere and reopen it."
echo
echo "  UXP loads the panel once at startup. Until you restart, Premiere is"
echo "  still running the old panel however many times you reinstall it."
echo
