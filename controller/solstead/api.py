"""HTTP API served to the companion app over the local network."""

from __future__ import annotations

import hmac
import json
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .controller import Unavailable

JSON = "application/json"
CHUNK = 256 * 1024
EVENT_CLIP = re.compile(r"^/v1/events/([A-Za-z0-9._-]+)/clip$")


class Handler(BaseHTTPRequestHandler):
    server_version = f"Solstead/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, status: int, payload, content_type: str = JSON) -> None:
        data = json.dumps(payload).encode() if content_type == JSON else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_file(self, path: Path, content_type: str) -> None:
        """Serve a file with single-range support so video players can seek."""
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        header = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip()) if header else None
        if match:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            elif last:
                start, end = max(size - int(last), 0), size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as file:
            file.seek(start)
            remaining = length
            while remaining > 0:
                chunk = file.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def authorized(self) -> bool:
        expected = "Bearer " + self.server.controller.config.api_token
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied.encode(), expected.encode())

    def handle_request(self) -> None:
        if not self.authorized():
            return self.reply(401, {"error": "Owner token required"})
        path = urlsplit(self.path).path.rstrip("/")
        controller = self.server.controller
        get = self.command in ("GET", "HEAD")
        try:
            if get and path == "/v1/status":
                return self.reply(200, controller.status())
            if get and path == "/v1/camera/snapshot":
                return self.reply(200, controller.snapshot(), "image/jpeg")
            if get and path == "/v1/recordings":
                return self.reply(200, {"recordings": controller.recordings()})
            if self.command == "POST" and path == "/v1/recordings":
                return self.reply(201, controller.record())
            if get and path == "/v1/events":
                return self.reply(200, {"events": controller.events()})
            clip = EVENT_CLIP.match(path)
            if get and clip:
                found = controller.event_clip(clip.group(1))
                if not found:
                    return self.reply(404, {"error": "Event not found"})
                return self.send_file(found[1], "video/mp4")
            return self.reply(404, {"error": "Not found"})
        except Unavailable as error:
            return self.reply(503, {"error": str(error)})
        except OSError:
            return self.reply(503, {"error": "Storage operation failed"})

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
