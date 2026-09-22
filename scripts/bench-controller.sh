#!/usr/bin/env bash
# Run a Solstead controller on this laptop for app development.
# Usage: scripts/bench-controller.sh [rtsp://camera-url]
# Provisions into .bench/ on first run (token and fingerprint are printed once and kept in
# .bench/etc/config.json), then serves on https://localhost:8443 so the iOS simulator can connect.
set -euo pipefail
cd "$(dirname "$0")/.."
BENCH="$PWD/.bench"
PY=controller/.venv/bin
[ -x "$PY/solstead" ] || make controller-setup
if [ ! -f "$BENCH/etc/config.json" ]; then
  mkdir -p "$BENCH/ssd"
  "$PY/solstead" provision --dir "$BENCH/etc" --hostname localhost --storage "$BENCH/ssd" \
    --bind 127.0.0.1 --port 8443 --name "Bench controller" --no-require-mount \
    ${1:+--camera-url "$1"}
else
  echo "Using existing $BENCH/etc/config.json"
  echo "  url          https://localhost:8443"
  echo "  fingerprint  $("$PY/solstead" fingerprint "$BENCH/etc/controller.crt")"
  echo "  token        $(python3 -c "import json;print(json.load(open('$BENCH/etc/config.json'))['api_token'])")"
fi
exec "$PY/solstead" serve "$BENCH/etc/config.json"
