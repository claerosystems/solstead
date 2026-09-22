"""Background thread that pulls the camera's motion events and feeds the recorder."""

from __future__ import annotations

import threading
import time

from .events import EventRecorder
from .onvif import MAX_CLOCK_SKEW_SECONDS, OnvifError, PullPointClient, set_camera_time

CLOCK_SYNC_SECONDS = 6 * 3600


class MotionWatcher:
    def __init__(self, client: PullPointClient, recorder: EventRecorder, pull_seconds: int = 10,
                 backoff_seconds: float = 10.0):  # fmt: skip
        self.client = client
        self.recorder = recorder
        self.pull_seconds = pull_seconds
        self.backoff_seconds = backoff_seconds
        self.subscribed = False
        self.last_message_unix: int | None = None
        self.last_error: str | None = None
        self.errors = 0
        self.last_skew_seconds: float | None = None
        self.next_clock_sync = 0.0
        self.clock_synced_unix: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="motion-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(self.pull_seconds + 15)
            self._thread = None
        self.client.unsubscribe()
        self.subscribed = False

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except OnvifError as error:
                self.subscribed = False
                self.errors += 1
                self.last_error = str(error)
                self._stop.wait(self.backoff_seconds)

    def step(self) -> None:
        """One subscribe/renew/pull cycle. Raises OnvifError on any camera problem."""
        if time.time() >= self.next_clock_sync:
            self.sync_clock()
        if not self.subscribed:
            self.client.subscribe()
            self.subscribed = True
        self.client.renew_if_due()
        for event in self.client.pull(self.pull_seconds):
            now = time.time()
            self.last_message_unix = int(now)
            self.last_skew_seconds = round(event.at - now, 1)
            # A camera clock that has drifted must not move events on our timeline.
            at = event.at if abs(event.at - now) <= MAX_CLOCK_SKEW_SECONDS else now
            self.recorder.motion(event.active, at, event.topic)
        self.last_error = None

    def sync_clock(self) -> None:
        """Set the camera's clock from ours; retried on the next step if it fails."""
        self.next_clock_sync = time.time() + CLOCK_SYNC_SECONDS
        try:
            set_camera_time(
                self.client.service_url, self.client.user, self.client.password, self.client.poster
            )
            self.clock_synced_unix = int(time.time())
        except OnvifError as error:
            self.last_error = f"Clock sync failed: {error}"
            self.next_clock_sync = time.time() + 600
