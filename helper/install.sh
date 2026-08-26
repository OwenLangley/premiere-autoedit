#!/usr/bin/env bash
# Install the job watcher as a login item so editors do not have to start it.
#
# This is a background process per editor machine -- the price of letting the
# panel start jobs, since UXP cannot run a subprocess. When the shared NAS
# service arrives the same watcher runs centrally and only the folder it
# watches changes.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

JOBS="${1:-$HOME/Desktop/AutoEdit-jobs}"
MEDIA="${2:-}"
# Optional: a music library outside the footage. Editors can also set this from
# the panel, which writes it to config.json and needs no restart -- this argument
# just seeds a default for a machine set up from the terminal.
MUSIC="${3:-}"
if [ -z "$MEDIA" ]; then
  echo "usage: helper/install.sh <jobs-folder> <media-root> [music-folder]" >&2
  exit 2
fi
MUSIC_ARGS=""
if [ -n "$MUSIC" ]; then
  MUSIC_ARGS="    <string>--music</string><string>$MUSIC</string>"
fi

# launchd does NOT give a job your shell's PATH. It hands out a bare
# /usr/bin:/bin:/usr/sbin:/sbin, which contains no Homebrew, which means no
# ffmpeg and no ffprobe -- and every probe, audio extract and proxy then fails
# inside a background process nobody is watching. Started from a terminal this
# never shows up, because there the helper inherits a working PATH.
#
# Pin the directory ffmpeg is actually in at install time rather than guessing a
# prefix: /opt/homebrew on Apple Silicon, /usr/local on Intel and on hand-rolled
# installs, and neither if someone put it somewhere else.
FFMPEG_DIR=""
if command -v ffmpeg >/dev/null; then
  FFMPEG_DIR="$(cd "$(dirname "$(command -v ffmpeg)")" && pwd):"
fi
HELPER_PATH="${FFMPEG_DIR}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PLIST="$HOME/Library/LaunchAgents/com.company.autoedit.helper.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.company.autoedit.helper</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>$ROOT/helper/watch.py</string>
    <string>--jobs</string><string>$JOBS</string>
    <string>--media</string><string>$MEDIA</string>
$MUSIC_ARGS
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HELPER_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/autoedit-helper.log</string>
  <key>StandardErrorPath</key><string>/tmp/autoedit-helper.log</string>
</dict>
</plist>
PLIST_EOF

# `launchctl load` is the legacy call and, run from a shell that is not the
# login session, it registers the job without ever starting it -- RunAtLoad and
# all. The job then sits there with no PID, and `launchctl list` still prints
# its label, so it looks installed. bootstrap + kickstart is the modern pair and
# actually starts it.
UID_NOW="$(id -u)"
launchctl bootout "gui/$UID_NOW/com.company.autoedit.helper" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NOW" "$PLIST" 2>/dev/null \
  || { launchctl unload "$PLIST" 2>/dev/null || true; launchctl load "$PLIST"; }
launchctl kickstart "gui/$UID_NOW/com.company.autoedit.helper" 2>/dev/null || true

# Trust nothing: confirm it has a real pid before claiming it is running.
sleep 1
if launchctl list | awk -v l=com.company.autoedit.helper '$3==l && $1!="-"{f=1} END{exit !f}'; then
  echo "helper installed and running"
else
  echo "helper installed but NOT running -- check /tmp/autoedit-helper.log" >&2
  exit 1
fi
echo "  jobs : $JOBS"
echo "  media: $MEDIA"
[ -n "$MUSIC" ] && echo "  music: $MUSIC"
echo "  log  : /tmp/autoedit-helper.log"
echo
echo "to stop:  launchctl unload $PLIST"
