#!/usr/bin/env bash
# Make this Mac able to run AutoEdit. Safe to re-run.
#
#   ./setup.sh --check                     what is missing, changes nothing
#   ./setup.sh <jobs-folder> <media-root> [music-folder]
#
# There are four moving parts and a colleague needs all four:
#
#   ffmpeg     every probe, audio extract and proxy goes through it
#   the engine a Python venv holding the analysis code and Whisper
#   the panel  the UXP plugin, which Premiere 26 only reads from /Library
#   the helper a background watcher; the panel cannot run a subprocess, so
#              without this, pressing Create Edit does nothing at all
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

CHECK_ONLY=false
[ "${1:-}" = "--check" ] && { CHECK_ONLY=true; shift; }

JOBS="${1:-$HOME/Desktop/AutoEdit-jobs}"
MEDIA="${2:-}"
MUSIC="${3:-}"

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
miss() { printf '  \033[31mmiss\033[0m  %s\n' "$1"; MISSING=$((MISSING+1)); }
note() { printf '        %s\n' "$1"; }
MISSING=0

echo "Checking this machine:"

# --- 1. the host ------------------------------------------------------------
[ "$(uname)" = "Darwin" ] && ok "macOS" || { miss "macOS only"; exit 1; }
if ls -d /Applications/Adobe\ Premiere\ Pro* >/dev/null 2>&1; then
  ok "Premiere Pro installed"
else
  miss "Premiere Pro not found in /Applications"
fi

# --- 2. ffmpeg --------------------------------------------------------------
if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
  ok "ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}')"
else
  miss "ffmpeg and ffprobe are not on PATH"
  note "install with:  brew install ffmpeg"
fi

# --- 3. python --------------------------------------------------------------
PY_OK=false
if command -v python3 >/dev/null; then
  V=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
  if python3 -c 'import sys;exit(0 if sys.version_info>=(3,11) else 1)'; then
    ok "python $V"; PY_OK=true
  else
    miss "python $V is too old; 3.11+ required"
  fi
else
  miss "python3 not found"
fi

# --- 4. the pieces this repo installs ---------------------------------------
[ -x "$ROOT/.venv/bin/autoedit" ] && ok "engine venv" || miss "engine venv not built"
PANEL="/Library/Application Support/Adobe/UXP/Plugins/External/com.company.autoedit_0.1.0"
[ -d "$PANEL" ] && ok "panel installed system-wide" || miss "panel not installed where Premiere 26 looks"
launchctl list 2>/dev/null | grep -q com.company.autoedit.helper \
  && ok "helper running" || miss "helper not running (Create Edit will do nothing)"

if $CHECK_ONLY; then
  echo
  [ "$MISSING" -eq 0 ] && echo "Nothing missing." || echo "$MISSING thing(s) to fix. Re-run without --check to install."
  exit 0
fi

if [ -z "$MEDIA" ]; then
  echo
  echo "usage: ./setup.sh <jobs-folder> <media-root> [music-folder]" >&2
  echo "   eg: ./setup.sh ~/Desktop/AutoEdit-jobs ~/Footage ~/Music/Library" >&2
  exit 2
fi
$PY_OK || { echo "python 3.11+ first" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "install ffmpeg first: brew install ffmpeg" >&2; exit 1; }

echo
echo "Installing:"

# --- engine -----------------------------------------------------------------
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -e "$ROOT/engine" faster-whisper pytest jsonschema PyYAML
ok "engine + Whisper"

# --- panel (needs root; Premiere 26 reads /Library only) --------------------
bash "$ROOT/panel/install.sh" >/dev/null
ok "panel"

# --- helper -----------------------------------------------------------------
mkdir -p "$JOBS"
bash "$ROOT/helper/install.sh" "$JOBS" "$MEDIA" ${MUSIC:+"$MUSIC"} >/dev/null
ok "helper (launchd, starts at login)"

echo
echo "Done. Restart Premiere, then Window > UXP Plugins > AutoEdit."
echo
echo "  jobs : $JOBS"
echo "  media: $MEDIA"
[ -n "$MUSIC" ] && echo "  music: $MUSIC"
echo
echo "The FIRST job on this machine downloads the Whisper model (about 3GB) and"
echo "builds proxies for any footage that needs them. Both are cached afterwards."
