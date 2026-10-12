#!/usr/bin/env sh
# Render crawler_architecture.html to milsearch_crawler_architecture.png with headless Edge.
# The page loads rough.js and the Kalam font from the internet.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
EDGE="${EDGE:-/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe}"
WIN_DIR="$(cygpath -w "$DIR" 2>/dev/null || echo "$DIR")"
"$EDGE" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1640,900 \
  --virtual-time-budget=10000 \
  --screenshot="$WIN_DIR\\milsearch_crawler_architecture.png" \
  "file:///$(echo "$WIN_DIR" | sed 's#\\#/#g')/crawler_architecture.html"
