#!/usr/bin/env bash
# Run a Solstead controller on this laptop for app development.
#   scripts/bench-controller.sh                      # manual recording only, no camera
#   scripts/bench-controller.sh --fake               # fake RTSP camera + ONVIF motion bursts (needs ffmpeg)
#   scripts/bench-controller.sh rtsp://u:p@cam/s1 [http://cam:2020/onvif/device_service user password [rtsp://u:p@cam/s2]]
# Provisions into .bench/ on first run; the token and fingerprint live in .bench/etc/config.json.
# Serves on https://localhost:8443 so the iOS simulator can connect.
set -euo pipefail
cd "$(dirname "$0")/.."
BENCH="$PWD/.bench"
PY=controller/.venv/bin
[ -x "$PY/solstead" ] || make controller-setup

CAMERA_URL="" LIVE_URL="" ONVIF_URL="" CAM_USER="" CAM_PASS=""
if [ "${1:-}" = "--fake" ]; then
  CAMERA_URL=http://127.0.0.1:8554/live
  LIVE_URL=http://127.0.0.1:8555/sub
  ONVIF_URL=http://127.0.0.1:2020/onvif/device_service
  python3 controller/tools/fake_camera.py --burst-every "${FAKE_BURST_EVERY:-60}" --burst-seconds "${FAKE_BURST_SECONDS:-10}" &
  FAKE_PID=$!
  trap 'kill $FAKE_PID 2>/dev/null || true' EXIT INT TERM
elif [ -n "${1:-}" ]; then
  CAMERA_URL=$1; ONVIF_URL=${2:-}; CAM_USER=${3:-}; CAM_PASS=${4:-}; LIVE_URL=${5:-}
fi

mkdir -p "$BENCH/ssd"
if [ ! -f "$BENCH/etc/config.json" ]; then
  "$PY/solstead" provision --dir "$BENCH/etc" --hostname localhost --storage "$BENCH/ssd" \
    --bind 127.0.0.1 --port 8443 --name "Bench controller" --no-require-mount \
    ${CAMERA_URL:+--camera-url "$CAMERA_URL"} ${ONVIF_URL:+--camera-onvif-url "$ONVIF_URL"} \
    ${CAM_USER:+--camera-user "$CAM_USER"} ${CAM_PASS:+--camera-password "$CAM_PASS"}
else
  echo "Using existing $BENCH/etc/config.json"
  echo "  url          https://localhost:8443"
  echo "  fingerprint  $("$PY/solstead" fingerprint "$BENCH/etc/controller.crt")"
  echo "  token        $(python3 -c "import json;print(json.load(open('$BENCH/etc/config.json'))['api_token'])")"
fi
if [ -n "$CAMERA_URL" ]; then
  python3 - "$BENCH/etc/config.json" "$CAMERA_URL" "$ONVIF_URL" "$CAM_USER" "$CAM_PASS" "$LIVE_URL" <<'PY'
import json, sys
path, camera, onvif, user, password, live = sys.argv[1:]
config = json.load(open(path))
config.update(camera_url=camera, camera_onvif_url=onvif or None, camera_user=user, camera_password=password,
              camera_live_url=live or None, events_allocation_bytes=config.get("events_allocation_bytes", 2 * 1024**3))
json.dump(config, open(path, "w"), indent=2)
PY
fi
"$PY/solstead" serve "$BENCH/etc/config.json" &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null || true; kill ${FAKE_PID:-} 2>/dev/null || true' EXIT INT TERM
wait $SERVE_PID
