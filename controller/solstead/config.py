"""Controller configuration loaded from a JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MIN_TOKEN_LENGTH = 32
CAMERA_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
DEFAULT_PORT = 8443


class ConfigError(ValueError):
    """The configuration file is missing something or contains a bad value."""


@dataclass(frozen=True)
class Config:
    storage_path: Path
    api_token: str
    tls_cert: Path
    tls_key: Path
    name: str = "Solstead"
    bind: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    camera_url: str | None = None
    clip_seconds: int = 10
    snapshot_width: int = 960
    min_free_bytes: int = 2 * 1024**3
    storage_require_mount: bool = True
    # Event recording (needs camera_url and camera_onvif_url).
    camera_onvif_url: str | None = None
    camera_user: str = ""
    camera_password: str = ""
    motion_topics: tuple[str, ...] = ("Motion",)
    event_pre_seconds: int = 10
    event_post_seconds: int = 30
    event_hold_seconds: int = 10
    event_max_seconds: int = 300
    segment_seconds: int = 2
    buffer_seconds: int = 60
    # Plaintext video (the pre-event buffer and clips being assembled) stays in RAM; unset means
    # /dev/shm. Only sealed recordings are written to the SSD once keys are enrolled.
    buffer_path: Path | None = None
    events_allocation_bytes: int = 20 * 1024**3
    # Live view (uses camera_url when camera_live_url is unset).
    camera_live_url: str | None = None
    live_segment_seconds: int = 1
    live_idle_seconds: int = 20
    live_path: str | None = None
    # Owner-adjustable settings (time-lapse) are stored beside config.json.
    settings_path: Path | None = None
    # Public keys recordings are sealed to, enrolled from the owner's phone.
    recipients_path: Path | None = None
    # Owner-chosen telemetry retention, also beside config.json.
    telemetry_settings_path: Path | None = None
    # The owner's Victron solar charger and its Instant Readout key (mode 0600), beside config.json.
    charger_settings_path: Path | None = None
    # Owner-drawn motion areas and shadow filtering, beside config.json.
    motion_settings_path: Path | None = None
    # A small stream the controller checks motion on; unset uses camera_live_url, then camera_url.
    camera_motion_url: str | None = None
    # Person/vehicle recognition model (deploy/fetch-model.sh puts it beside config.json).
    recognition_model_path: Path | None = None

    @property
    def events_enabled(self) -> bool:
        return bool(self.camera_url and self.camera_onvif_url)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        for key in ("storage_path", "api_token", "tls_cert", "tls_key"):
            if not data.get(key):
                raise ConfigError(f"{key} is required")
        if len(data["api_token"]) < MIN_TOKEN_LENGTH:
            raise ConfigError(f"api_token must contain at least {MIN_TOKEN_LENGTH} characters")
        port = data.get("port", DEFAULT_PORT)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigError("port must be an integer between 1 and 65535")
        clip_seconds = data.get("clip_seconds", 10)
        if not isinstance(clip_seconds, int) or not 1 <= clip_seconds <= 300:
            raise ConfigError("clip_seconds must be an integer between 1 and 300")
        camera_url = data.get("camera_url") or None
        if camera_url is not None and not camera_url.startswith(CAMERA_SCHEMES):
            raise ConfigError("camera_url must be an rtsp:// or http:// stream URL")
        live_url = data.get("camera_live_url") or None
        if live_url is not None and not live_url.startswith(CAMERA_SCHEMES):
            raise ConfigError("camera_live_url must be an rtsp:// or http:// stream URL")
        motion_url = data.get("camera_motion_url") or None
        if motion_url is not None and not motion_url.startswith(CAMERA_SCHEMES):
            raise ConfigError("camera_motion_url must be an rtsp:// or http:// stream URL")
        onvif_url = data.get("camera_onvif_url") or None
        if onvif_url is not None and not onvif_url.startswith(("http://", "https://")):
            raise ConfigError("camera_onvif_url must be an HTTP URL")
        for key, low, high in (
            ("event_pre_seconds", 0, 60),
            ("event_post_seconds", 0, 300),
            ("event_hold_seconds", 0, 120),
            ("event_max_seconds", 10, 3600),
            ("segment_seconds", 1, 10),
            ("buffer_seconds", 10, 600),
            ("live_segment_seconds", 1, 10),
            ("live_idle_seconds", 5, 600),
        ):
            value = data.get(key)
            if value is not None and (not isinstance(value, int) or not low <= value <= high):
                raise ConfigError(f"{key} must be an integer between {low} and {high}")
        topics = data.get("motion_topics", ["Motion"])
        if not isinstance(topics, list) or not all(isinstance(t, str) and t for t in topics):
            raise ConfigError("motion_topics must be a list of strings")
        return cls(
            storage_path=Path(data["storage_path"]),
            api_token=data["api_token"],
            tls_cert=Path(data["tls_cert"]),
            tls_key=Path(data["tls_key"]),
            name=str(data.get("name", "Solstead")),
            bind=str(data.get("bind", "127.0.0.1")),
            port=port,
            camera_url=camera_url,
            clip_seconds=clip_seconds,
            snapshot_width=int(data.get("snapshot_width", 960)),
            min_free_bytes=int(data.get("min_free_bytes", 2 * 1024**3)),
            storage_require_mount=bool(data.get("storage_require_mount", True)),
            camera_onvif_url=onvif_url,
            camera_user=str(data.get("camera_user", "")),
            camera_password=str(data.get("camera_password", "")),
            motion_topics=tuple(topics),
            event_pre_seconds=int(data.get("event_pre_seconds", 10)),
            event_post_seconds=int(data.get("event_post_seconds", 30)),
            event_hold_seconds=int(data.get("event_hold_seconds", 10)),
            event_max_seconds=int(data.get("event_max_seconds", 300)),
            segment_seconds=int(data.get("segment_seconds", 2)),
            buffer_seconds=int(data.get("buffer_seconds", 60)),
            events_allocation_bytes=int(data.get("events_allocation_bytes", 20 * 1024**3)),
            camera_live_url=live_url,
            live_segment_seconds=int(data.get("live_segment_seconds", 1)),
            live_idle_seconds=int(data.get("live_idle_seconds", 20)),
            live_path=data.get("live_path") or None,
            settings_path=Path(data["settings_path"]) if data.get("settings_path") else None,
            buffer_path=Path(data["buffer_path"]) if data.get("buffer_path") else None,
            recipients_path=(
                Path(data["recipients_path"]) if data.get("recipients_path") else None
            ),
            telemetry_settings_path=(
                Path(data["telemetry_settings_path"])
                if data.get("telemetry_settings_path")
                else None
            ),
            charger_settings_path=(
                Path(data["charger_settings_path"]) if data.get("charger_settings_path") else None
            ),
            motion_settings_path=(
                Path(data["motion_settings_path"]) if data.get("motion_settings_path") else None
            ),
            camera_motion_url=motion_url,
            recognition_model_path=(
                Path(data["recognition_model_path"]) if data.get("recognition_model_path") else None
            ),
        )

    @classmethod
    def load(cls, path: Path | str) -> Config:
        path = Path(path)
        try:
            data = json.loads(path.read_text())
        except OSError as error:
            raise ConfigError(f"Cannot read configuration: {error}") from None
        except json.JSONDecodeError as error:
            raise ConfigError(f"Configuration is not valid JSON: {error}") from None
        if not isinstance(data, dict):
            raise ConfigError("Configuration must be a JSON object")
        # Provisioning writes these as null; either way they live beside config.json.
        data["settings_path"] = data.get("settings_path") or str(path.with_name("timelapse.json"))
        data["recipients_path"] = data.get("recipients_path") or str(
            path.with_name("recipients.json")
        )
        data["telemetry_settings_path"] = data.get("telemetry_settings_path") or str(
            path.with_name("telemetry.json")
        )
        data["charger_settings_path"] = data.get("charger_settings_path") or str(
            path.with_name("charger.json")
        )
        data["motion_settings_path"] = data.get("motion_settings_path") or str(
            path.with_name("motion.json")
        )
        data["recognition_model_path"] = data.get("recognition_model_path") or str(
            path.with_name("ssd_mobilenet_v1_12.onnx")
        )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "storage_path", "tls_cert", "tls_key", "settings_path", "buffer_path",
            "recipients_path", "telemetry_settings_path", "charger_settings_path",
            "motion_settings_path", "recognition_model_path",
        ):  # fmt: skip
            if data[key] is not None:
                data[key] = str(data[key])
        data["motion_topics"] = list(self.motion_topics)
        return data
