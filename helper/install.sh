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
if [ -z "$MEDIA" ]; then
  echo "usage: helper/install.sh <jobs-folder> <media-root>" >&2
  exit 2
fi

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
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/autoedit-helper.log</string>
  <key>StandardErrorPath</key><string>/tmp/autoedit-helper.log</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "helper installed and running"
echo "  jobs : $JOBS"
echo "  media: $MEDIA"
echo "  log  : /tmp/autoedit-helper.log"
echo
echo "to stop:  launchctl unload $PLIST"
