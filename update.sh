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
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

echo
echo "Updating AutoEdit..."

BEFORE="$(git rev-parse --short HEAD)"

if [ -n "$(git status --porcelain)" ]; then
  warn "you have local changes; they are kept and the update stops here"
  git status --short
  echo
  echo "  Commit or stash them, then run this again."
  exit 1
fi

git pull --ff-only
AFTER="$(git rev-parse --short HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
  ok "already up to date ($AFTER)"
else
  ok "code $BEFORE -> $AFTER"
  git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/      /'
fi

# The engine is installed with `pip install -e`, so the code is live -- but a
# fix may add a dependency, and that is not.
"$ROOT/.venv/bin/pip" install --quiet -e "$ROOT/engine" 2>/dev/null && ok "engine dependencies"

bash "$ROOT/panel/install.sh" >/dev/null && ok "panel copied to /Library"

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
