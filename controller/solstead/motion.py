"""Background thread that pulls the camera's motion events and feeds the recorder."""

from __future__ import annotations

import threading
import time

from .events import EventRecorder
from .onvif import OnvifError, PullPointClient


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
        if not self.subscribed:
            self.client.subscribe()
            self.subscribed = True
        self.client.renew_if_due()
        for event in self.client.pull(self.pull_seconds):
            self.last_message_unix = int(time.time())
            self.recorder.motion(event.active, event.at, event.topic)
        self.last_error = None
