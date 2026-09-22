#!/usr/bin/env bash
# Build the app, install it in an iOS simulator and launch it already paired with the bench
# controller started by scripts/bench-controller.sh. Usage: scripts/sim-run.sh ["iPhone 17 Pro"]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d /Applications/Xcode.app ] && export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
DEVICE="${1:-iPhone 17 Pro}"
CONFIG=.bench/etc/config.json
[ -f "$CONFIG" ] || { echo "Start scripts/bench-controller.sh first." >&2; exit 1; }

UDID=$(xcrun simctl list devices available -j | python3 -c "
import json,sys; devs=json.load(sys.stdin)['devices']
print(next(d['udid'] for r in devs.values() for d in r if d['name']=='$DEVICE'))")
xcrun simctl boot "$UDID" 2>/dev/null || true
xcrun simctl bootstatus "$UDID" -b >/dev/null

xcodebuild -project ios/Solstead.xcodeproj -scheme Solstead -destination "id=$UDID" \
  -derivedDataPath .build/DerivedData CODE_SIGNING_ALLOWED=NO build | grep -E 'error:|BUILD'
APP=.build/DerivedData/Build/Products/Debug-iphonesimulator/Solstead.app
xcrun simctl terminate "$UDID" com.claerosystems.solstead 2>/dev/null || true
xcrun simctl install "$UDID" "$APP"
open -a Simulator
SIMCTL_CHILD_SOLSTEAD_URL=https://localhost:8443 \
SIMCTL_CHILD_SOLSTEAD_TOKEN="$(python3 -c "import json;print(json.load(open('$CONFIG'))['api_token'])")" \
SIMCTL_CHILD_SOLSTEAD_FINGERPRINT="$(controller/.venv/bin/solstead fingerprint .bench/etc/controller.crt)" \
xcrun simctl launch "$UDID" com.claerosystems.solstead
echo "Unsigned simulator builds cannot write the Keychain (-34018); the app still connects."
