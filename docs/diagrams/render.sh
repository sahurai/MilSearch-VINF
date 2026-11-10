#!/usr/bin/env sh
# Render the diagram sources to PNG with headless Edge:
#   crawler_architecture.html -> milsearch_crawler_architecture.png
#   spark_pipeline.html       -> milsearch_spark_pipeline.png
# The pages load rough.js and the Kalam font from the internet.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
EDGE="${EDGE:-/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe}"
WIN_DIR="$(cygpath -w "$DIR" 2>/dev/null || echo "$DIR")"
URL_DIR="$(cygpath -m "$DIR" 2>/dev/null || echo "$DIR")"
render() {
  "$EDGE" --headless=new --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=2 --window-size=1640,"$3" \
    --virtual-time-budget=10000 \
    --screenshot="$WIN_DIR/$2" \
    "file:///$URL_DIR/$1"
}
render "${1:-crawler_architecture.html}" "${2:-milsearch_crawler_architecture.png}" "${3:-900}"
