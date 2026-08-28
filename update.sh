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

# No pre-flight purity check. There used to be one and it was wrong twice: it
# counted untracked files, and then, once that was fixed, it still stopped an
# editor who could not pull the fix to the thing that was stopping them.
#
# `git pull --ff-only` already refuses safely. It fails only when a local change
# would actually be overwritten, it names the files itself, and it is right
# about which ones matter -- which is more than the guard managed. Let it
# decide, and pass its own words along.

# Never wait for a password on a machine with nobody at the keyboard. A private
# repo with no working credential would otherwise hang the helper forever
# rather than failing in a few seconds with something an editor can act on.
PULL_OUT="$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -oBatchMode=yes" \
            git pull --ff-only 2>&1)"
PULL_RC=$?
echo "$PULL_OUT" | sed 's/^/  /'
if [ $PULL_RC -ne 0 ]; then
  if echo "$PULL_OUT" | grep -qi "local changes\|would be overwritten\|commit your changes"; then
    STATUS="dirty"
    DETAIL="local changes block the update: $(echo "$PULL_OUT" | grep -i '^\s*[a-zA-Z].*\.' | head -3 | tr '\n' ' ')"
    echo
    echo "  Those files differ from the repository. If you did not change them"
    echo "  on purpose -- and on an editing machine you almost certainly did not:"
    echo
    # `git restore .` rather than `git checkout -- .`: the double dash does not
    # survive being pasted through a chat app, which turns it into an en dash or
    # eats it, and the result is "fatal: 'ff-only' does not appear to be a git
    # repository" -- a message about a mangled flag that reads like a broken
    # repository. Nothing here should contain a `--` that a human has to retype.
    echo "    git restore . && ./update.sh"
    echo
    echo "  To keep them instead: git stash"
  else
    STATUS="unreachable"
    DETAIL="could not reach the repository -- check this machine can sign in to GitHub"
    warn "$DETAIL"
  fi
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
