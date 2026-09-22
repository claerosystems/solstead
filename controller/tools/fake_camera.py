#!/usr/bin/env python3
"""Fake camera for bench work: an RTSP test pattern plus ONVIF motion events on a schedule.

    python3 tools/fake_camera.py [--stream-port 8554] [--onvif-port 2020]
                                 [--burst-every 60] [--burst-seconds 10]

Point a controller at http://127.0.0.1:8554/live (MPEG-TS over HTTP; ffmpeg's RTSP listener is
unreliable), camera_live_url http://127.0.0.1:8555/sub, and http://127.0.0.1:2020/onvif/device_service. GET http://127.0.0.1:2020/trigger
starts a motion burst immediately. The stream accepts one client at a time, which is what the
controller's segment buffer uses; snapshots and manual clips come from the buffer.
"""

from __future__ import annotations

import argparse
import atexit
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOAP = "http://www.w3.org/2003/05/soap-envelope"


def utc(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Motion:
    def __init__(self, every: float, seconds: float):
        self.every, self.seconds = every, seconds
        self.active = False
        self.changes: queue.Queue[tuple[float, bool]] = queue.Queue()
        self.trigger_now = threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def set(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.changes.put((time.time(), active))
            print(f"motion {'ON ' if active else 'off'} {utc(time.time())}", flush=True)

    def run(self) -> None:
        while True:
            self.trigger_now.wait(self.every)
            self.trigger_now.clear()
            self.set(True)
            time.sleep(self.seconds)
            self.set(False)


class Handler(BaseHTTPRequestHandler):
    motion: Motion
    onvif_port: int

    def log_message(self, *_):
        pass

    def send(
        self, body: str, status: int = 200, content_type: str = "application/soap+xml"
    ) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/trigger"):
            self.motion.trigger_now.set()
            return self.send("motion burst triggered\n", content_type="text/plain")
        self.send("fake camera\n", content_type="text/plain")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode(errors="replace")
        action = re.search(r"<wsa:Action[^>]*>([^<]+)", body)
        name = action.group(1).rsplit("/", 1)[-1] if action else ""
        if name == "CreatePullPointSubscriptionRequest":
            self.motion.changes.put((time.time(), self.motion.active))  # initial state
            return self.send(
                f'<s:Envelope xmlns:s="{SOAP}" xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
                ' xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Body>'
                "<tev:CreatePullPointSubscriptionResponse><tev:SubscriptionReference>"
                f"<wsa:Address>http://127.0.0.1:{self.onvif_port}/onvif/subscription</wsa:Address>"
                "</tev:SubscriptionReference></tev:CreatePullPointSubscriptionResponse></s:Body></s:Envelope>"
            )
        if name == "PullMessagesRequest":
            timeout = re.search(r"<tev:Timeout>PT(\d+)S", body)
            deadline = time.time() + (int(timeout.group(1)) if timeout else 10)
            messages = []
            try:
                messages.append(self.motion.changes.get(timeout=max(deadline - time.time(), 0)))
                while True:
                    messages.append(self.motion.changes.get_nowait())
            except queue.Empty:
                pass
            notifications = "".join(
                "<wsnt:NotificationMessage><wsnt:Topic>tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>"
                f'<wsnt:Message><tt:Message UtcTime="{utc(at)}" PropertyOperation="Changed">'
                f'<tt:Data><tt:SimpleItem Name="IsMotion" Value="{"true" if active else "false"}"/></tt:Data>'
                "</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
                for at, active in messages
            )
            return self.send(
                f'<s:Envelope xmlns:s="{SOAP}" xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
                ' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:tt="http://www.onvif.org/ver10/schema"'
                ' xmlns:tns1="http://www.onvif.org/ver10/topics"><s:Body><tev:PullMessagesResponse>'
                f"<tev:CurrentTime>{utc(time.time())}</tev:CurrentTime>"
                f"<tev:TerminationTime>{utc(time.time() + 60)}</tev:TerminationTime>"
                f"{notifications}</tev:PullMessagesResponse></s:Body></s:Envelope>"
            )
        return self.send(f'<s:Envelope xmlns:s="{SOAP}"><s:Body/></s:Envelope>')


def stream_forever(port: int, size: str = "640x360", name: str = "live") -> None:
    args = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-re",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "15",
        "-pix_fmt", "yuv420p", "-f", "mpegts", "-listen", "1", f"http://127.0.0.1:{port}/{name}",
    ]  # fmt: skip
    while True:
        print(f"stream listening on http://127.0.0.1:{port}/{name}", flush=True)
        child = subprocess.Popen(args)
        CHILDREN.append(child)
        child.wait()
        CHILDREN.remove(child)
        time.sleep(1)


CHILDREN: list[subprocess.Popen] = []


def stop_children(*_) -> None:
    for child in list(CHILDREN):
        child.terminate()
    raise SystemExit(0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--stream-port", type=int, default=8554)
    parser.add_argument(
        "--substream-port", type=int, default=8555, help="lower-resolution stream for live view"
    )
    parser.add_argument("--onvif-port", type=int, default=2020)
    parser.add_argument("--burst-every", type=float, default=60)
    parser.add_argument("--burst-seconds", type=float, default=10)
    args = parser.parse_args()
    Handler.motion = Motion(args.burst_every, args.burst_seconds)
    Handler.onvif_port = args.onvif_port
    atexit.register(stop_children)
    signal.signal(signal.SIGTERM, stop_children)
    threading.Thread(target=stream_forever, args=(args.stream_port,), daemon=True).start()
    threading.Thread(
        target=stream_forever, args=(args.substream_port, "320x180", "sub"), daemon=True
    ).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.onvif_port), Handler)
    print(f"onvif listening on http://127.0.0.1:{args.onvif_port}/onvif/device_service", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
