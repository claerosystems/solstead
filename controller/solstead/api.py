"""HTTP API served to the companion app over the local network."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from . import __version__
from .controller import Unavailable

JSON = "application/json"


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

    def authorized(self) -> bool:
        expected = "Bearer " + self.server.controller.config.api_token
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied.encode(), expected.encode())

    def handle_request(self) -> None:
        if not self.authorized():
            return self.reply(401, {"error": "Owner token required"})
        path = urlsplit(self.path).path.rstrip("/")
        controller = self.server.controller
        try:
            if self.command == "GET" and path == "/v1/status":
                return self.reply(200, controller.status())
            if self.command == "GET" and path == "/v1/camera/snapshot":
                return self.reply(200, controller.snapshot(), "image/jpeg")
            if self.command == "GET" and path == "/v1/recordings":
                return self.reply(200, {"recordings": controller.recordings()})
            if self.command == "POST" and path == "/v1/recordings":
                return self.reply(201, controller.record())
            return self.reply(404, {"error": "Not found"})
        except Unavailable as error:
            return self.reply(503, {"error": str(error)})
        except OSError:
            return self.reply(503, {"error": "Storage operation failed"})

    do_GET = handle_request
    do_POST = handle_request
