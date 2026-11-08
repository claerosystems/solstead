#!/usr/bin/env bash
# Install or update the controller for the current user only, without sudo.
# Run on the Pi from the synced checkout:  controller/deploy/install-user.sh
# Needs: python3 with venv, ffmpeg, openssl, a writable storage folder, and
# `loginctl enable-linger` already set so the user service runs at boot.
#   SOLSTEAD_STORAGE   recording folder (default /mnt/recordings)
#   SOLSTEAD_HOSTNAME  name in the certificate (default <hostname>.local)
#   SOLSTEAD_CAMERA_URL, SOLSTEAD_ONVIF_URL, SOLSTEAD_CAMERA_USER, SOLSTEAD_CAMERA_PASSWORD
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ETC="$HOME/.config/solstead"
STORAGE="${SOLSTEAD_STORAGE:-/mnt/recordings}"
HOST="${SOLSTEAD_HOSTNAME:-$(hostname).local}"

for tool in python3 ffmpeg openssl; do command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }; done
[ -d "$HERE/.venv" ] || python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install --quiet --upgrade pip
# The ble extra reads a Victron solar charger over Bluetooth. If the Pi's Bluetooth is
# switched off, `rfkill unblock bluetooth` turns it on (no root needed on Raspberry Pi OS).
"$HERE/.venv/bin/pip" install --quiet -e "$HERE[ble]"

mkdir -p "$ETC"
chmod 700 "$ETC"
if [ ! -f "$ETC/config.json" ]; then
  "$HERE/.venv/bin/solstead" provision --dir "$ETC" --hostname "$HOST" --storage "$STORAGE" \
    ${SOLSTEAD_CAMERA_URL:+--camera-url "$SOLSTEAD_CAMERA_URL"} \
    ${SOLSTEAD_ONVIF_URL:+--camera-onvif-url "$SOLSTEAD_ONVIF_URL"} \
    ${SOLSTEAD_CAMERA_USER:+--camera-user "$SOLSTEAD_CAMERA_USER"} \
    ${SOLSTEAD_CAMERA_PASSWORD:+--camera-password "$SOLSTEAD_CAMERA_PASSWORD"}
fi
mountpoint -q "$STORAGE" || echo "WARNING: $STORAGE is not a mount point" >&2

mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$HERE/deploy/solstead-user.service" "$HOME/.config/systemd/user/solstead.service"
systemctl --user daemon-reload
systemctl --user enable --now solstead.service
systemctl --user restart solstead.service
sleep 2
systemctl --user --no-pager --lines=5 status solstead.service || true
