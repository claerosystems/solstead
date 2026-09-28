#!/usr/bin/env bash
# Keep a WireGuard tunnel from the controller to the owner's hub (see wireguard-hub.sh), so the
# phone can reach the API from anywhere although the carrier puts the controller behind NAT.
# The tunnel goes out over whichever link is up (Wi-Fi on the bench, cellular in the field) and is
# kept open with keepalives. Over the tunnel phones (10.77.0.3-99) reach only the API port and admin
# laptops (10.77.0.100-149) the API and SSH; nothing else is reachable.
#
# Run as root on the Pi:  sudo controller/deploy/wireguard-controller.sh HUB_ADDRESS HUB_PUBLIC_KEY
# Prints the controller's public key (also kept in /etc/wireguard-controller.pub); add it on the hub with
#   wireguard-hub.sh add-peer controller 10.77.0.2 <key>
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 HUB_ADDRESS HUB_PUBLIC_KEY" >&2; exit 1; }
HUB=$1
HUB_KEY=$2
NET=10.77.0
PORT=51820
API_PORT=8443
CONF=/etc/wireguard/wg0.conf
RULES=/etc/nftables.d/solstead-tunnel.nft
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
command -v nft >/dev/null || { echo "nftables is not installed (apt install nftables)" >&2; exit 1; }

mkdir -p /etc/nftables.d
cat > "$RULES" <<NFT
# Solstead: over the tunnel, phones may reach the controller API; admin laptops the API and SSH.
table inet solstead_tunnel
delete table inet solstead_tunnel
table inet solstead_tunnel {
	chain input {
		type filter hook input priority -10; policy accept;
		iifname "wg0" ct state established,related accept
		iifname "wg0" ip saddr $NET.3-$NET.99 tcp dport $API_PORT accept
		iifname "wg0" ip saddr $NET.100-$NET.149 tcp dport { 22, $API_PORT } accept
		iifname "wg0" counter drop comment "nothing else over the tunnel"
	}
}
NFT
grep -q 'include "/etc/nftables.d/\*.nft"' /etc/nftables.conf || printf '\ninclude "/etc/nftables.d/*.nft"\n' >> /etc/nftables.conf
nft -f "$RULES"
systemctl enable nftables >/dev/null 2>&1 || true

command -v wg >/dev/null || apt-get install -y wireguard-tools
umask 077
[ -f /etc/wireguard/controller.key ] || wg genkey > /etc/wireguard/controller.key
cat > "$CONF" <<CONF
[Interface]
Address = $NET.2/24
PrivateKey = $(cat /etc/wireguard/controller.key)
# LTE links carry less than 1500 bytes (1460 on Public Mobile), and WireGuard adds 60, so the
# default 1420 drops every full-size packet over cellular. 1280 also keeps TCP to the API small
# in both directions.
MTU = 1280

[Peer]
PublicKey = $HUB_KEY
Endpoint = $HUB:$PORT
AllowedIPs = $NET.0/24
PersistentKeepalive = 25
CONF
systemctl enable wg-quick@wg0 >/dev/null 2>&1
systemctl restart wg-quick@wg0
# The public half is not secret; keep a readable copy so it can be fetched without root.
wg pubkey < /etc/wireguard/controller.key > /etc/wireguard-controller.pub
chmod 644 /etc/wireguard-controller.pub
echo "Tunnel up at $NET.2. Controller public key (add it on the hub), also in /etc/wireguard-controller.pub:"
cat /etc/wireguard-controller.pub
