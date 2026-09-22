#!/usr/bin/env bash
# Isolate the camera segment: the camera may talk to the Pi only, never to the Internet or the LAN.
# Run as root on the Pi:  sudo controller/deploy/camera-isolation.sh [eth0]
# Installs an nftables table that drops all forwarding to or from the camera interface and DNS
# from it, persists it via /etc/nftables.conf, and prints the live ruleset with packet counters.
set -euo pipefail
IFACE="${1:-eth0}"
RULES=/etc/nftables.d/solstead-camera.nft
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
command -v nft >/dev/null || { echo "nftables is not installed (apt install nftables)" >&2; exit 1; }

mkdir -p /etc/nftables.d
cat > "$RULES" <<NFT
# Solstead: the camera on $IFACE is reachable from the controller only.
table inet solstead_camera
delete table inet solstead_camera
table inet solstead_camera {
	chain forward {
		type filter hook forward priority -10; policy accept;
		iifname "$IFACE" counter drop comment "camera -> anywhere else"
		oifname "$IFACE" counter drop comment "anywhere else -> camera"
	}
	chain input {
		type filter hook input priority -10; policy accept;
		iifname "$IFACE" udp dport 53 counter drop comment "no DNS for the camera"
		iifname "$IFACE" tcp dport 53 counter drop comment "no DNS for the camera"
	}
}
NFT

grep -q 'include "/etc/nftables.d/\*.nft"' /etc/nftables.conf || printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
nft -f "$RULES"
systemctl enable nftables >/dev/null 2>&1 || true
echo "Rules loaded from $RULES and enabled at boot. Waiting 20 s for the camera to try something..."
sleep 20
nft list table inet solstead_camera
