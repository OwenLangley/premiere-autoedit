#!/usr/bin/env bash
# Make this Mac able to run AutoEdit. Safe to re-run.
#
#   ./setup.sh --check                     what is missing, changes nothing
#   ./setup.sh <media-root> [music-folder]
#   ./setup.sh --jobs <folder> <media-root> [music-folder]
#
# The MEDIA root is the only thing an editor has to decide -- it is their
# footage and only they know where it lives. The jobs folder is plumbing, so
# this script creates one for them at ~/Desktop/AutoEdit-jobs and wires the
# helper to it. --jobs overrides that, which is what a team pointing everyone at
# one folder on shared storage wants. Re-running keeps whatever folder the
# machine is already using, so an update never quietly moves an editor's jobs.
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

PLIST="$HOME/Library/LaunchAgents/com.company.autoedit.helper.plist"
# Already set up? Then that folder is the editor's answer, not ours.
JOBS="$(python3 - "$PLIST" <<'EOF' 2>/dev/null || true
import plistlib, sys
try:
    args = plistlib.load(open(sys.argv[1], "rb"))["ProgramArguments"]
    print(args[args.index("--jobs") + 1])
except Exception:
    pass
EOF
)"
[ -n "$JOBS" ] || JOBS="$HOME/Desktop/AutoEdit-jobs"

CHECK_ONLY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=true; shift ;;
    --jobs)  JOBS="${2:-}"; [ -n "$JOBS" ] || { echo "--jobs needs a folder" >&2; exit 2; }; shift 2 ;;
    -*)      echo "unknown option: $1" >&2; exit 2 ;;
    *)       break ;;
  esac
done

MEDIA="${1:-}"
MUSIC="${2:-}"

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
  note "brew install ffmpeg"
  command -v brew >/dev/null || note "...and Homebrew first, from https://brew.sh"
fi

# --- 3. python --------------------------------------------------------------
PY_OK=false
if command -v python3 >/dev/null; then
  V=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
  if python3 -c 'import sys;exit(0 if sys.version_info>=(3,11) else 1)'; then
    ok "python $V"; PY_OK=true
  else
    # macOS ships 3.9 and always will; this is a brew install, not an upgrade.
    miss "python $V is too old; 3.11+ required"
    note "brew install python@3.11"
  fi
else
  miss "python3 not found"
  note "brew install python@3.11"
fi

# --- 4. the pieces this repo installs ---------------------------------------
[ -x "$ROOT/.venv/bin/autoedit" ] && ok "engine venv" || miss "engine venv not built"
PANEL="/Library/Application Support/Adobe/UXP/Plugins/External/com.company.autoedit_0.1.0"
[ -d "$PANEL" ] && ok "panel installed system-wide" || miss "panel not installed where Premiere 26 looks"
# `launchctl list` prints the label for a job that is merely REGISTERED, with a
# "-" where the pid goes. Grepping for the label therefore reports a helper that
# has never run as healthy -- which is the exact failure this check exists to
# catch. Insist on a real pid.
if launchctl list 2>/dev/null \
   | awk -v l=com.company.autoedit.helper '$3==l && $1!="-"{f=1} END{exit !f}'; then
  ok "helper running"
  # It runs, but launchd hands out a bare PATH with no Homebrew in it. Without
  # ffmpeg the helper starts, watches, accepts jobs and fails every one of them.
  #
  # Read the plist, not `launchctl print` -- that prints PATH twice, once for the
  # default environment and once for the job's own, and picking the wrong line
  # means this check reports a broken PATH on a machine that is fine.
  HELPER_PATH=$(python3 - "$PLIST" <<'EOF' 2>/dev/null || true
import plistlib, sys
try:
    print(plistlib.load(open(sys.argv[1], "rb"))["EnvironmentVariables"]["PATH"])
except Exception:
    pass
EOF
)
  # No PATH in the plist means the job inherits launchd's bare one, which is the
  # broken case -- so an empty answer must fail the check, not skip it.
  if ! PATH="${HELPER_PATH:-/usr/bin:/bin:/usr/sbin:/sbin}" command -v ffmpeg >/dev/null 2>&1; then
    HELPER_PATH="${HELPER_PATH:-launchd default, no Homebrew}"
    miss "the helper cannot see ffmpeg (its PATH: $HELPER_PATH)"
    note "re-run setup to rewrite it; every job would fail with nothing in the panel"
  fi
else
  miss "helper not running (Create Edit will do nothing)"
fi

if $CHECK_ONLY; then
  echo
  [ "$MISSING" -eq 0 ] && echo "Nothing missing." || echo "$MISSING thing(s) to fix. Re-run without --check to install."
  exit 0
fi

if [ -z "$MEDIA" ]; then
  echo
  echo "Point this at your footage and it will do the rest:" >&2
  echo >&2
  echo "  ./setup.sh <media-root> [music-folder]" >&2
  echo "  ./setup.sh ~/Footage ~/Music/Library" >&2
  echo >&2
  echo "<media-root> is the folder your rushes live in. Everything below it is" >&2
  echo "indexed, so give the top of the tree, not one shoot." >&2
  echo "The jobs folder is made for you at: $JOBS" >&2
  echo "(--jobs <folder> if your team shares one instead.)" >&2
  exit 2
fi
if [ ! -d "$MEDIA" ]; then
  echo "media root does not exist: $MEDIA" >&2
  exit 2
fi
MEDIA="$(cd "$MEDIA" && pwd)"
if [ -n "$MUSIC" ]; then
  [ -d "$MUSIC" ] || { echo "music folder does not exist: $MUSIC" >&2; exit 2; }
  MUSIC="$(cd "$MUSIC" && pwd)"
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
# install.sh explains itself before asking for a password, but its output is
# swallowed here -- so say it first, or the prompt arrives naked.
if [ ! -w "$PANEL" ] 2>/dev/null && [ ! -w "$(dirname "$PANEL")" ]; then
  echo
  echo "  macOS will now ask for your password. Premiere 26 only loads plugins"
  echo "  from /Library, which needs administrator rights to write to once."
  echo "  Updates after this do not ask again."
fi
bash "$ROOT/panel/install.sh" >/dev/null
ok "panel"

# --- the jobs folder, made for them -----------------------------------------
# The panel and the helper meet in here: the panel drops a request, the helper
# writes back the plan, the receipt and the media/music indexes.
if [ -d "$JOBS" ]; then
  ok "jobs folder (already there)"
else
  mkdir -p "$JOBS"
  ok "jobs folder created"
fi

# --- helper -----------------------------------------------------------------
bash "$ROOT/helper/install.sh" "$JOBS" "$MEDIA" ${MUSIC:+"$MUSIC"} >/dev/null
ok "helper (launchd, starts at login)"

echo
echo "  jobs : $JOBS"
echo "  media: $MEDIA"
[ -n "$MUSIC" ] && echo "  music: $MUSIC"
echo
echo "Two steps left, both in Premiere:"
echo
echo "  1. Restart Premiere, then Window > UXP Plugins > AutoEdit."
echo "  2. In the panel: Setup > Jobs folder > Choose, and pick"
echo "       $JOBS"
echo
echo "Step 2 is unavoidable. A UXP plugin cannot be handed a path -- it only"
echo "gets folder access the editor grants through the picker itself. Once"
echo "chosen it is remembered, including across updates."
echo
echo "The FIRST job on this machine downloads the Whisper model (about 3GB) and"
echo "builds proxies for any footage that needs them. Both are cached afterwards."
