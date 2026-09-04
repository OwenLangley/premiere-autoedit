#!/usr/bin/env bash
# Collect everything needed to diagnose a problem, into one file to send.
#
# The point is that an editor should never have to know WHICH of the eight
# places this tool writes to is the interesting one. They run this, they get a
# path, they send the file. Everything is read-only: nothing here changes a
# setting, a job or an install.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$HOME/Desktop/autoedit-report-$STAMP"
mkdir -p "$OUT"

say() { printf '  %s\n' "$1"; }
echo
echo "Collecting a diagnostic report..."

# --- who and what -----------------------------------------------------------
{
  echo "collected      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "user           $(whoami)"
  echo "machine        $(sw_vers -productName) $(sw_vers -productVersion) ($(uname -m))"
  echo "repo           $ROOT"
  echo "commit         $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '(not a checkout)')"
  echo "branch         $(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '-')"
  # Named, not counted. "uncommitted 1 file(s)" cost a round trip: a stray
  # .DS_Store and a modified file that blocks every `git pull --ff-only` look
  # identical as a number, and only one of them is the reason an update never
  # arrives. The status codes come along so `??` (untracked, harmless) is
  # distinguishable from ` M` (modified, blocking) at a glance.
  DIRTY="$(git -C "$ROOT" status --porcelain 2>/dev/null)"
  if [ -n "$DIRTY" ]; then
    echo "uncommitted    $(echo "$DIRTY" | wc -l | tr -d ' ') file(s):"
    echo "$DIRTY" | head -10 | sed 's/^/                 /'
  else
    echo "uncommitted    none"
  fi

  # Whether this checkout is actually current, which is THE question when a fix
  # has been pushed and has not arrived. `ls-remote` reads one ref and fetches
  # no objects; the prompt is disabled and the speed limit bounds it, so a
  # machine with no credential or no network fails in seconds instead of
  # hanging a diagnostic script.
  LOCAL_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)"
  BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  REMOTE_SHA="$(GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -oBatchMode=yes" \
      git -C "$ROOT" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 \
      ls-remote origin "$BRANCH" 2>/dev/null | cut -f1)"
  if [ -z "$REMOTE_SHA" ]; then
    echo "up to date     unknown -- could not reach GitHub from this machine"
  elif [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    echo "up to date     yes"
  else
    echo "up to date     NO -- origin/$BRANCH is at ${REMOTE_SHA:0:7}, run ./update.sh"
  fi
  echo "premiere       $(ls -d /Applications/Adobe\ Premiere\ Pro* 2>/dev/null | tail -1 || echo '(not found)')"
  echo "premiere pid   $(pgrep -f 'Adobe Premiere Pro' | head -1 || echo '(not running)')"
  echo "ffmpeg         $(command -v ffmpeg || echo missing) $(ffmpeg -version 2>/dev/null | head -1)"
  echo "python         $("$ROOT/.venv/bin/python" --version 2>&1 || echo '(no venv)')"
  echo
  echo "--- helper (launchd) ---"
  launchctl list 2>/dev/null | grep -i autoedit || echo "(not registered)"
  echo
  echo "--- installed panel ---"
  PANEL_DEST="/Library/Application Support/Adobe/UXP/Plugins/External/com.company.autoedit_0.1.0"
  ls -la "$PANEL_DEST" 2>/dev/null || echo "(panel not installed system-wide)"

  # Three separate ways a fix fails to arrive, and they look the same from the
  # outside: not pulled, pulled but not installed, installed but Premiere still
  # running the old copy. UXP caches modules for the life of the process, so the
  # last one persists through any number of reinstalls.
  echo
  REPO_MAIN="$ROOT/panel/src/main.js"
  for D in "$PANEL_DEST" "$HOME/Library/Application Support/Adobe/UXP/Plugins/External/com.company.autoedit_0.1.0"; do
    [ -d "$D" ] || continue
    if [ -f "$D/src/main.js" ] && \
       [ "$(shasum -a 256 < "$D/src/main.js" 2>/dev/null)" = "$(shasum -a 256 < "$REPO_MAIN" 2>/dev/null)" ]; then
      echo "panel on disk  matches this checkout  ($D)"
    else
      echo "panel on disk  DIFFERS from this checkout -- ./update.sh has not been run since the pull  ($D)"
    fi
  done

  PPID_PREM="$(pgrep -f 'Adobe Premiere Pro' | head -1)"
  if [ -n "$PPID_PREM" ] && [ -f "$PANEL_DEST/src/main.js" ]; then
    LSTART="$(ps -o lstart= -p "$PPID_PREM" 2>/dev/null | sed 's/ *$//')"
    STARTED="$(date -j -f "%a %b %e %T %Y" "$LSTART" +%s 2>/dev/null)"
    INSTALLED="$(stat -f %m "$PANEL_DEST/src/main.js" 2>/dev/null)"
    if [ -n "$STARTED" ] && [ -n "$INSTALLED" ]; then
      if [ "$STARTED" -lt "$INSTALLED" ]; then
        echo "panel running  STALE -- Premiere started before the panel was installed; quit and reopen it"
      else
        echo "panel running  current -- Premiere started after the panel was installed"
      fi
    fi
  fi
} > "$OUT/system.txt" 2>&1
say "system, versions and install state"

# --- engine packages --------------------------------------------------------
"$ROOT/.venv/bin/pip" list --format=freeze > "$OUT/python-packages.txt" 2>&1 && say "python packages"

# --- helper log -------------------------------------------------------------
# /tmp, so it does not survive a reboot. That is worth knowing when it is empty.
if [ -f /tmp/autoedit-helper.log ]; then
  tail -c 400000 /tmp/autoedit-helper.log > "$OUT/helper.log" 2>&1
  say "helper log ($(wc -l < "$OUT/helper.log" | tr -d ' ') lines)"
else
  echo "no /tmp/autoedit-helper.log -- the helper has not run since the last reboot" \
    > "$OUT/helper.log"
  say "helper log (missing -- recorded as such)"
fi

# --- the jobs themselves ----------------------------------------------------
JOBS="$(plutil -extract ProgramArguments json -o - \
  ~/Library/LaunchAgents/com.company.autoedit.helper.plist 2>/dev/null \
  | python3 -c 'import json,sys
a=json.load(sys.stdin)
print(a[a.index("--jobs")+1] if "--jobs" in a else "")' 2>/dev/null)"

if [ -n "$JOBS" ] && [ -d "$JOBS" ]; then
  mkdir -p "$OUT/jobs"
  echo "$JOBS" > "$OUT/jobs/WHERE.txt"
  # The last handful of jobs, newest first. Plans are large and mostly
  # uninteresting; the request says what was asked for and the status and
  # receipt say what happened, which is where nearly every answer is.
  ls -t "$JOBS"/*.request.json 2>/dev/null | head -8 | while read -r f; do
    base="${f%.request.json}"
    for ext in request.json status.json receipt.json srt; do
      [ -f "$base.$ext" ] && cp "$base.$ext" "$OUT/jobs/" 2>/dev/null
    done
    # One plan, the newest, because it is the only one worth its size.
    if [ ! -f "$OUT/jobs/newest.editplan.json" ] && [ -f "$base.editplan.json" ]; then
      cp "$base.editplan.json" "$OUT/jobs/newest.editplan.json" 2>/dev/null
    fi
  done
  cp "$JOBS/capabilities.json" "$OUT/jobs/" 2>/dev/null
  say "last $(ls "$OUT/jobs"/*.request.json 2>/dev/null | wc -l | tr -d ' ') job(s)"
else
  echo "could not read the jobs folder from the helper plist" > "$OUT/jobs-missing.txt"
  say "jobs folder (could not be located -- recorded as such)"
fi

# --- self-test, if it has ever been run -------------------------------------
[ -f /tmp/autoedit-selftest/report.json ] \
  && cp /tmp/autoedit-selftest/report.json "$OUT/selftest.json" 2>/dev/null \
  && say "self-test report"

# --- Premiere's own UXP log -------------------------------------------------
LOG="$(ls -t ~/Library/Logs/Adobe/Adobe*Premiere*/UXPLogs_*.log 2>/dev/null | head -1)"
[ -n "$LOG" ] && tail -c 200000 "$LOG" > "$OUT/premiere-uxp.log" 2>&1 && say "Premiere UXP log"

# --- one file to send -------------------------------------------------------
ZIP="$OUT.zip"
( cd "$(dirname "$OUT")" && zip -qr "$(basename "$ZIP")" "$(basename "$OUT")" ) && rm -rf "$OUT"

echo
echo "Report written to:"
echo "  $ZIP"
echo
echo "Send that file to whoever maintains this. It contains your media"
echo "FILENAMES and folder paths, the text of any subtitles generated, and what"
echo "you typed in the prompt box -- but no video, no audio and no credentials."
echo
