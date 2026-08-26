#!/usr/bin/env bash
# Install the panel where Premiere actually looks for it.
#
# Premiere 26 (upic 2.6.0) scans SYSTEM-WIDE locations only:
#
#   /Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json
#   /Library/Application Support/Adobe/UXP/Plugins/External          <- fallback scan
#
# It does NOT look under ~/Library, which is where this script used to install
# and where earlier Premiere versions did look. When Premiere updated, the panel
# silently vanished from Window > UXP Plugins with nothing in the menu to say
# why -- the files were all still there, in a folder nothing reads any more.
#
# /Library needs root, so this asks for sudo. That is the whole reason the old
# path was chosen; it is not worth a plugin that cannot be found.
#
# The folder name must be the manifest id + "_" + version, or Premiere ignores it.
#
# NOTE: Premiere must be RESTARTED afterwards. Closing and reopening the panel
# does not reload changed JavaScript -- require() caches modules for the life of
# the process.
set -euo pipefail
cd "$(dirname "$0")"

ID=$(python3 -c "import json;print(json.load(open('manifest.json'))['id'])")
VERSION=$(python3 -c "import json;print(json.load(open('manifest.json'))['version'])")
SYSTEM_ROOT="/Library/Application Support/Adobe/UXP/Plugins/External"
DEST="$SYSTEM_ROOT/${ID}_${VERSION}"

# Only the FIRST install needs root, to create the folder under /Library. After
# that the plugin directory belongs to the installing user, so day-to-day updates
# are just a copy -- asking for a password on every code change would be enough
# friction that people stop running it.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if [ -d "$DEST" ]; then
    [ -w "$DEST" ] || SUDO="sudo"
  elif [ ! -w "$SYSTEM_ROOT" ]; then
    SUDO="sudo"
  fi
fi
if [ -n "$SUDO" ]; then
  echo "First install here needs administrator rights (creating $SYSTEM_ROOT)."
fi

$SUDO mkdir -p "$DEST"
$SUDO rsync -a --delete \
  --exclude node_modules --exclude test --exclude types \
  --exclude tsconfig.json --exclude package-lock.json --exclude install.sh \
  ./ "$DEST/"
# Readable by everyone; Premiere runs as the logged-in user, not as root.
$SUDO chmod -R a+rX "$DEST"
[ -n "$SUDO" ] && $SUDO chown -R "$(id -u):$(id -g)" "$DEST" || true

echo "installed -> $DEST"

# Older Premiere builds read the per-user folder. Keeping it in step costs
# nothing and means downgrading does not silently lose the panel.
USER_DEST="$HOME/Library/Application Support/Adobe/UXP/Plugins/External/${ID}_${VERSION}"
if [ -d "$(dirname "$USER_DEST")" ]; then
  mkdir -p "$USER_DEST"
  rsync -a --delete \
    --exclude node_modules --exclude test --exclude types \
    --exclude tsconfig.json --exclude package-lock.json --exclude install.sh \
    ./ "$USER_DEST/"
  echo "           also -> $USER_DEST  (for older Premiere builds)"
fi

echo
echo "Now restart Premiere, then Window > UXP Plugins > AutoEdit."
echo "If it does not appear, the log says where Premiere looked:"
echo "  grep 'upic::' ~/Library/Logs/Adobe/Adobe*Premiere*/UXPLogs_*.log | tail -8"
