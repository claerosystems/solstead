"""Controller configuration loaded from a JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MIN_TOKEN_LENGTH = 32
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
        if camera_url is not None and not camera_url.startswith(("rtsp://", "rtsps://")):
            raise ConfigError("camera_url must be an RTSP URL")
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
        )

    @classmethod
    def load(cls, path: Path | str) -> Config:
        try:
            data = json.loads(Path(path).read_text())
        except OSError as error:
            raise ConfigError(f"Cannot read configuration: {error}") from None
        except json.JSONDecodeError as error:
            raise ConfigError(f"Configuration is not valid JSON: {error}") from None
        if not isinstance(data, dict):
            raise ConfigError("Configuration must be a JSON object")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("storage_path", "tls_cert", "tls_key"):
            data[key] = str(data[key])
        return data
