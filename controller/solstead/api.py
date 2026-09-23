"""HTTP API served to the companion app over the local network."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import __version__
from .controller import Unavailable
from .recipients import RecipientsError
from .sealing import MAX_HEADER, SealingError
from .timelapse import TimeLapseError, TimeLapseSealed

JSON = "application/json"
SEALED = "application/octet-stream"  # an age-encryption.org/v1 file the phone decrypts
CHUNK = 256 * 1024
EVENT_CLIP = re.compile(r"^/v1/events/([A-Za-z0-9._-]+)/clip$")
EVENT = re.compile(r"^/v1/events/([A-Za-z0-9._-]+)$")
EVENT_HEADER = re.compile(r"^/v1/events/([A-Za-z0-9._-]+)/header$")
LIVE_FILE = re.compile(r"^/v1/live/([A-Za-z0-9._-]+)$")
TIMELAPSE_DAY = re.compile(r"^/v1/timelapse/(\d{4}-\d{2}-\d{2})$")
TIMELAPSE_FRAME = re.compile(r"^/v1/timelapse/(\d{4}-\d{2}-\d{2})/(\d{6}\.jpg(?:\.age)?)$")
TIMELAPSE_HEADERS = re.compile(r"^/v1/timelapse/(\d{4}-\d{2}-\d{2})/headers$")
HEADERS_BODY_MAX = 4 * 1024 * 1024  # a batch of re-wrapped frame headers
TIMELAPSE_VIDEO = re.compile(r"^/v1/timelapse/(\d{4}-\d{2}-\d{2})/video\.mp4$")
LIVE_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mp4": "video/mp4",
    ".m4s": "video/iso.segment",
}


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

    def send_file(self, path: Path, content_type: str, cache: bool = True) -> None:
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
        self.send_header("Cache-Control", "private, max-age=3600" if cache else "no-store")
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

    def read_json(self, limit: int = 64 * 1024) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            raise TimeLapseError("A JSON body is required")
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            raise TimeLapseError("Body is not valid JSON") from None
        if not isinstance(data, dict):
            raise TimeLapseError("Body must be a JSON object")
        return data

    def authorized(self) -> bool:
        expected = "Bearer " + self.server.controller.config.api_token
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied.encode(), expected.encode())

    def handle_request(self) -> None:
        if not self.authorized():
            return self.reply(401, {"error": "Owner token required"})
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/")
        query = dict(parse_qsl(parts.query))
        controller = self.server.controller
        get = self.command in ("GET", "HEAD")
        try:
            if get and path == "/v1/status":
                return self.reply(200, controller.status())
            if get and path == "/v1/camera/snapshot":
                width = None
                if "width" in query:
                    try:
                        width = int(query["width"])
                    except ValueError:
                        width = -1
                    if not 0 <= width <= 8192:
                        return self.reply(400, {"error": "width must be 0 (full) to 8192"})
                return self.reply(200, controller.snapshot(width), "image/jpeg")
            if get and path == "/v1/recordings":
                return self.reply(200, {"recordings": controller.recordings()})
            if self.command == "POST" and path == "/v1/recordings":
                return self.reply(201, controller.record())
            if get and path == "/v1/events":
                limit = min(max(int(query.get("limit", "200") or 200), 1), 100000)
                return self.reply(200, {"events": controller.events(limit)})
            if get and path == "/v1/encryption":
                return self.reply(200, controller.encryption_status())
            if self.command == "PUT" and path == "/v1/encryption":
                return self.reply(200, controller.enroll(self.read_json()))
            if self.command == "DELETE" and path == "/v1/encryption":
                return self.reply(200, controller.disable_encryption())
            if get and path == "/v1/health":
                level = query.get("level", "info")
                if level not in ("info", "warning", "error"):
                    return self.reply(400, {"error": "level must be info, warning or error"})
                limit = min(max(int(query.get("limit", "100") or 100), 1), 500)
                return self.reply(200, {"entries": controller.health.recent(limit, level)})
            if get and path == "/v1/timelapse":
                return self.reply(
                    200, {**controller.timelapse.status(), "days": controller.timelapse.days()}
                )
            if self.command == "PUT" and path == "/v1/timelapse":
                controller.update_timelapse(self.read_json())
                timelapse = controller.timelapse
                return self.reply(200, {**timelapse.status(), "days": timelapse.days()})
            day = TIMELAPSE_DAY.match(path)
            if get and day:
                frames = controller.timelapse.frames(day.group(1))
                if not frames:
                    return self.reply(404, {"error": "No frames for that day"})
                return self.reply(
                    200, {"date": day.group(1), "frames": [f.__dict__ for f in frames]}
                )
            frame = TIMELAPSE_FRAME.match(path)
            if get and frame:
                found = controller.timelapse.frame_path(frame.group(1), frame.group(2))
                if not found:
                    return self.reply(404, {"error": "Frame not found"})
                return self.send_file(found, SEALED if found.suffix == ".age" else "image/jpeg")
            video = TIMELAPSE_VIDEO.match(path)
            if get and video:
                try:
                    exported = controller.timelapse.export_video(video.group(1))
                except TimeLapseSealed as error:
                    return self.reply(409, {"error": str(error), "encrypted": True})
                return self.send_file(exported, "video/mp4")
            headers = TIMELAPSE_HEADERS.match(path)
            if headers and get:
                found = controller.timelapse_headers(headers.group(1))
                encoded = {name: base64.b64encode(h).decode() for name, h in found}
                return self.reply(200, {"date": headers.group(1), "headers": encoded})
            if headers and self.command == "PUT":
                supplied = self.read_json(HEADERS_BODY_MAX).get("headers")
                if not isinstance(supplied, dict) or not all(
                    isinstance(v, str) for v in supplied.values()
                ):
                    raise TimeLapseError("headers must map frame names to base64 age headers")
                try:
                    decoded = {k: base64.b64decode(v, validate=True) for k, v in supplied.items()}
                except binascii.Error:
                    raise TimeLapseError("headers must be base64") from None
                count = controller.replace_timelapse_headers(headers.group(1), decoded)
                return self.reply(200, {"date": headers.group(1), "replaced": count})
            if self.command == "POST" and path == "/v1/live":
                quality = query.get("quality", "low")
                if quality not in ("low", "high"):
                    return self.reply(400, {"error": "quality must be low or high"})
                return self.reply(200, controller.start_live(quality))
            if self.command == "DELETE" and path == "/v1/live":
                controller.stop_live()
                return self.reply(200, {"active": False})
            live = LIVE_FILE.match(path)
            if get and live:
                found = controller.live_file(live.group(1))
                if not found:
                    return self.reply(404, {"error": "Live view is not running"})
                content_type = LIVE_TYPES.get(found.suffix, "application/octet-stream")
                return self.send_file(found, content_type, cache=False)
            event = EVENT.match(path)
            if event and self.command == "DELETE":
                if not controller.delete_event(event.group(1)):
                    return self.reply(404, {"error": "Event not found"})
                return self.reply(200, {"deleted": event.group(1)})
            header = EVENT_HEADER.match(path)
            if header and get:
                found = controller.event_header(header.group(1))
                if found is None:
                    return self.reply(404, {"error": "No encrypted clip for that event"})
                return self.reply(200, found, SEALED)
            if header and self.command == "PUT":
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_HEADER:
                    return self.reply(400, {"error": "An age header body is required"})
                updated = controller.replace_event_header(header.group(1), self.rfile.read(length))
                if updated is None:
                    return self.reply(404, {"error": "No encrypted clip for that event"})
                return self.reply(200, updated)
            clip = EVENT_CLIP.match(path)
            if get and clip:
                found = controller.event_clip(clip.group(1))
                if not found:
                    return self.reply(404, {"error": "Event not found"})
                return self.send_file(found[1], SEALED if found[0].encrypted else "video/mp4")
            return self.reply(404, {"error": "Not found"})
        except (TimeLapseError, RecipientsError, SealingError) as error:
            return self.reply(400, {"error": str(error)})
        except Unavailable as error:
            return self.reply(503, {"error": str(error)})
        except OSError:
            return self.reply(503, {"error": "Storage operation failed"})
        except Exception:  # never drop the connection or leak internals to the app
            traceback.print_exc()
            return self.reply(500, {"error": "Controller error; see the controller log"})

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request
