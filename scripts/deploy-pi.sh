#!/usr/bin/env bash
# Sync this checkout to the Raspberry Pi and run the installer there.
#   scripts/deploy-pi.sh [user@host]      default solstead@solstead-r4b-1.local
# Environment passed through to the installer: SOLSTEAD_HOSTNAME, SOLSTEAD_CAMERA_URL.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-solstead@solstead-r4b-1.local}"
REMOTE_DIR="~/solstead"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude .build --exclude .bench --exclude DerivedData \
  --exclude '__pycache__' --exclude '.DS_Store' --exclude 'ios/' \
  ./ "$TARGET:$REMOTE_DIR/"
ssh -t "$TARGET" "cd $REMOTE_DIR && sudo env ${SOLSTEAD_HOSTNAME:+SOLSTEAD_HOSTNAME=$SOLSTEAD_HOSTNAME} ${SOLSTEAD_CAMERA_URL:+SOLSTEAD_CAMERA_URL='$SOLSTEAD_CAMERA_URL'} controller/deploy/install.sh"
