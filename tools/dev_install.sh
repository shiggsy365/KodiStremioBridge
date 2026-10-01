#!/usr/bin/env bash
# Symlink the add-on into a local Kodi install for live development.
# Usage: tools/dev_install.sh [kodi_home]   (default: ~/.kodi)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KODI_HOME="${1:-$HOME/.kodi}"
TARGET="$KODI_HOME/addons/plugin.video.stremiobridge"
mkdir -p "$KODI_HOME/addons"
if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
    echo "$TARGET exists and is not a symlink; remove it first." >&2
    exit 1
fi
ln -sfn "$ROOT/plugin.video.stremiobridge" "$TARGET"
echo "Linked $TARGET -> $ROOT/plugin.video.stremiobridge"
echo "Restart Kodi (or enable the add-on under My add-ons) to pick it up."
