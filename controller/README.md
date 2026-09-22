# Solstead controller

The service that runs on the Raspberry Pi inside the controller. Standard library only, so it runs
on Raspberry Pi OS without extra packages; ffmpeg does the camera work.

## Layout

| Module | Responsibility |
| --- | --- |
| `solstead/config.py` | Load and validate `config.json`. |
| `solstead/storage.py` | The recording SSD: mount and free-space checks, clip files, listing. |
| `solstead/camera.py` | Snapshots and clips from the RTSP camera through ffmpeg. |
| `solstead/controller.py` | Ties storage and camera together; builds the status report. |
| `solstead/api.py` | The HTTP handler: bearer-token auth and the `/v1` routes. |
| `solstead/server.py` | TLS 1.2+ server around the handler. |
| `solstead/provisioning.py` | Generates the owner token, self-signed certificate and config. |
| `solstead/cli.py` | `solstead serve | provision | fingerprint`. |
| `deploy/` | systemd unit and the Pi installer. |
| `tests/` | pytest suite; ffmpeg is faked, openssl and TLS are real. |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/pytest
.venv/bin/solstead provision --dir /tmp/solstead --hostname localhost --storage /tmp/solstead/ssd --no-require-mount
.venv/bin/solstead serve /tmp/solstead/config.json
```

`python3 -m solstead.server CONFIG` still works for the bench setup used in September 2026.

## Configuration (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `storage_path` | required | Recording SSD mount point. |
| `storage_require_mount` | `true` | Refuse to record unless `storage_path` is a mount point, so a missing SSD never fills the OS card. |
| `api_token` | required | Owner bearer token, at least 32 characters. |
| `tls_cert`, `tls_key` | required | PEM certificate and key served to the app. |
| `name` | `Solstead` | Shown in the app. |
| `bind`, `port` | `127.0.0.1`, `8443` | Listen address. Provisioning sets `0.0.0.0` for a real controller. |
| `camera_url` | none | `rtsp://` URL including any camera credentials. |
| `clip_seconds` | `10` | Length of a manual test clip. |
| `snapshot_width` | `960` | Snapshot width in pixels; height keeps the aspect ratio. |
| `min_free_bytes` | 2 GiB | Recording pauses below this much free space. |

The camera URL contains credentials. ffmpeg's stderr is therefore never returned to the app or logged.

## API v1

All requests need `Authorization: Bearer <api_token>`. Responses are JSON unless noted and carry
`Cache-Control: no-store`.

| Method and path | Result |
| --- | --- |
| `GET /v1/status` | Name, version, uptime, CPU temperature, storage readiness and usage, camera state. |
| `GET /v1/camera/snapshot` | One JPEG frame (`image/jpeg`). |
| `GET /v1/recordings` | `{"recordings": [{id, size_bytes, created_unix}, …]}`, newest first, up to 200. |
| `POST /v1/recordings` | Records a `clip_seconds` clip to the SSD; returns its entry with status 201. |

Errors: 401 without a valid token, 404 unknown route, 503 with `{"error": "…"}` when the camera or
storage is unavailable.

## Raspberry Pi deployment

`deploy/install.sh` installs ffmpeg and Python, creates a `solstead` system user, installs the
package into `/opt/solstead/venv`, provisions `/etc/solstead` on first run, and enables the
`solstead.service` systemd unit. Set `SOLSTEAD_HOSTNAME` and `SOLSTEAD_CAMERA_URL` in the
environment to override the defaults during provisioning. Mount the SSD at `/mnt/solstead`
before starting the service; the unit declares `RequiresMountsFor=/mnt/solstead`.

Useful commands on the Pi:

```bash
sudo systemctl status solstead
sudo journalctl -u solstead -f
sudo /opt/solstead/venv/bin/solstead fingerprint /etc/solstead/controller.crt
```
