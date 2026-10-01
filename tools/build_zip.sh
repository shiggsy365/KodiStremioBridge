#!/usr/bin/env bash
# Build an installable zip in dist/ (Kodi: Add-ons > Install from zip file).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID=plugin.video.stremiobridge
VERSION=$(python3 -c "import sys,xml.etree.ElementTree as E; print(E.parse(sys.argv[1]).getroot().get('version'))" "$ROOT/$ID/addon.xml")
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/$ID-$VERSION.zip"
rm -f "$OUT"
(cd "$ROOT" && zip -qr "$OUT" "$ID" -x '*/__pycache__/*' '*.pyc')
echo "$OUT"
