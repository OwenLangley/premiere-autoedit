#!/usr/bin/env bash
# Install the panel into Premiere's UXP plugin folder.
#
# The folder name must be the manifest id + "_" + version, or Premiere ignores it.
#
# NOTE: Premiere must be RESTARTED after this. Closing and reopening the panel
# does not reload changed JavaScript -- require() caches modules for the life of
# the process.
set -euo pipefail
cd "$(dirname "$0")"

ID=$(python3 -c "import json;print(json.load(open('manifest.json'))['id'])")
VERSION=$(python3 -c "import json;print(json.load(open('manifest.json'))['version'])")
DEST="$HOME/Library/Application Support/Adobe/UXP/Plugins/External/${ID}_${VERSION}"

mkdir -p "$DEST"
rsync -a --delete \
  --exclude node_modules --exclude test --exclude types \
  --exclude tsconfig.json --exclude package-lock.json --exclude install.sh \
  ./ "$DEST/"

echo "installed -> $DEST"
echo
echo "Now restart Premiere, then Window > UXP Plugins > AutoEdit."
echo "If it does not appear, check the load error:"
echo "  grep -i '$ID' ~/Library/Logs/Adobe/Adobe*Premiere*/UXPLogs_*.log | tail -5"
