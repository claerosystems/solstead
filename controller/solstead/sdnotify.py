"""Tell systemd we are alive (Type=notify + WatchdogSec) without any dependency."""

from __future__ import annotations

import os
import socket


def notify(state: str) -> bool:
    """Send a state such as READY=1 or WATCHDOG=1; returns False when not run by systemd."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
        return True
    except OSError:
        return False
