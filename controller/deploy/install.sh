#!/usr/bin/env bash
# Install or update the Solstead controller on a Raspberry Pi (Raspberry Pi OS Bookworm or later).
# Run from a checkout of the repository as root:  sudo controller/deploy/install.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/solstead
ETC=/etc/solstead
STORAGE=/mnt/solstead
HOSTNAME_FQDN="${SOLSTEAD_HOSTNAME:-$(hostname).local}"

if [ "$(id -u)" -ne 0 ]; then echo "run as root (sudo)" >&2; exit 1; fi

apt-get update -qq
apt-get install -y -qq ffmpeg python3 python3-venv openssl curl iputils-ping

id solstead >/dev/null 2>&1 || useradd --system --home "$PREFIX" --shell /usr/sbin/nologin solstead
mkdir -p "$PREFIX" "$ETC" "$STORAGE"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$HERE[ble]"
# Recognition (onnxruntime) lets motion checking record only people and vehicles. Optional: if no
# wheel exists for this Pi, motion checking still works with its area and shadow filters.
"$PREFIX/venv/bin/pip" install --quiet "onnxruntime>=1.17" || echo "WARNING: onnxruntime not installed; recognition off" >&2
# Bluetooth reads the Victron solar charger; Raspberry Pi images may ship with it switched off.
rfkill unblock bluetooth 2>/dev/null || true

if [ ! -f "$ETC/config.json" ]; then
  "$PREFIX/venv/bin/solstead" provision --dir "$ETC" --hostname "$HOSTNAME_FQDN" --storage "$STORAGE" \
    ${SOLSTEAD_CAMERA_URL:+--camera-url "$SOLSTEAD_CAMERA_URL"}
  echo
  echo "Record the token and fingerprint above now; they are only shown once here."
fi
"$HERE/deploy/fetch-model.sh" "$ETC"
chown -R solstead:solstead "$ETC"
chmod 700 "$ETC"

if ! mountpoint -q "$STORAGE"; then
  echo "WARNING: $STORAGE is not a mount point. Mount the recording SSD there (see controller/README.md)." >&2
fi
chown solstead:solstead "$STORAGE" 2>/dev/null || true

install -m 644 "$HERE/deploy/solstead.service" /etc/systemd/system/solstead.service
systemctl daemon-reload
systemctl enable --now solstead.service
systemctl --no-pager --lines=5 status solstead.service || true
