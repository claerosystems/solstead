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
| `solstead/buffer.py` | Continuous pre-event buffer: ffmpeg writes 2-second MPEG-TS segments, the last minute is kept. |
| `solstead/onvif.py` | Minimal ONVIF pull-point client for the camera's motion events (WS-UsernameToken auth). |
| `solstead/motion.py` | Background thread that subscribes, pulls, renews and feeds the recorder. |
| `solstead/events.py` | Event recorder state machine and the size-capped events allocation. |
| `solstead/clips.py` | Assembles a clip from buffered segments with ffmpeg's concat demuxer. |
| `solstead/live.py` | On-demand live view: ffmpeg copies a camera stream into 1-second HLS segments in RAM. |
| `solstead/timelapse.py` | Scheduled frames in their own allocation, day listing, MP4 export, owner-adjustable settings. |
| `solstead/health.py` | Health log: incidents in memory and in `<storage>/health.jsonl`, capped. |
| `solstead/sdnotify.py` | systemd readiness and watchdog pings without a dependency. |
| `solstead/thermal.py` | CPU temperature against the Pi 4's throttle points and its under-voltage/throttle flags. |
| `solstead/api.py` | The HTTP handler: bearer-token auth and the `/v1` routes. |
| `solstead/server.py` | TLS 1.2+ server around the handler. |
| `solstead/provisioning.py` | Generates the owner token, self-signed certificate and config. |
| `solstead/cli.py` | `solstead serve | provision | fingerprint`. |
| `deploy/` | systemd units, the Pi installers (system-wide and user-level), and the camera isolation firewall. |
| `tools/fake_camera.py` | A fake camera for bench work: MPEG-TS test pattern over HTTP plus ONVIF motion bursts. |
| `tests/` | pytest suite; ffmpeg is faked, openssl and TLS are real. |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/pytest
.venv/bin/solstead provision --dir /tmp/solstead --hostname localhost --storage /tmp/solstead/ssd --no-require-mount
.venv/bin/solstead serve /tmp/solstead/config.json
```

`python3 -m solstead.server CONFIG` still works for the bench setup used in September 2026.

Without a camera on hand, `scripts/bench-controller.sh --fake` from the repository root starts the
fake camera and a controller wired to it; `curl http://127.0.0.1:2020/trigger` fires a motion burst.

## Event recording

When both `camera_url` and `camera_onvif_url` are set the controller keeps a rolling buffer of the
camera stream and subscribes to the camera's ONVIF motion events:

1. `buffer.py` runs one ffmpeg process that copies the stream (no transcoding) into
   `<storage>/buffer/` as 2-second MPEG-TS segments named by wall-clock start time and prunes
   segments older than `buffer_seconds`, except footage an open event still needs.
2. `motion.py` pulls ONVIF notifications whose topic contains one of `motion_topics` and reads the
   `IsMotion`/`State` boolean. Cameras that only ever report "on" are handled by
   `event_hold_seconds`: activity is assumed to last that long after the last report.
3. `events.py` opens an event at the first activity, extends it while activity continues, and
   closes it `event_post_seconds` after the last activity. Overlapping bursts therefore merge into
   one clip. Events longer than `event_max_seconds` are cut and a new one started.
4. Once the buffer holds the post-roll, the segments covering
   `[first activity - event_pre_seconds, last activity + event_post_seconds]` are concatenated
   into `<storage>/events/<id>.mp4` with a `<id>.json` sidecar. When the events allocation is
   exceeded the oldest events are deleted.

While the buffer runs, snapshots and manual test clips are taken from it rather than opening the
camera a second time.

The camera's clock matters because its notifications carry the camera's own timestamps. The
watcher sets the camera's clock from the controller over ONVIF (`SetSystemDateAndTime`, manual
mode) at start and every six hours, reports the observed skew in the status `events` block, and
falls back to the controller's receipt time for any notification more than five seconds off.
An isolated camera has no NTP, so this is what keeps its timestamps and video overlay honest.

## Live view

`POST /v1/live?quality=low|high` starts one ffmpeg process that copies the camera stream (the
`camera_live_url` substream for `low`, `camera_url` for `high`) into fragmented-MP4 HLS with
`live_segment_seconds` segments under `live_path` (default `/dev/shm/solstead-live`, so nothing
touches the SSD). The call returns once two segments exist, normally in about five seconds. The
app then fetches `/v1/live/index.m3u8`, `init.mp4` and `seg_NNNNN.m4s` through the same
authenticated API; every fetch counts as a viewer, and the stream stops by itself
`live_idle_seconds` after the last one, or immediately on `DELETE /v1/live`. Switching quality
restarts the stream. There is no transcoding, so CPU use is negligible and the same path will
serve remote viewing later. Keyframe spacing sets the segment length: set the substream GOP to
one second's worth of frames (`tools/camera_encoder.py --token minor --gov-length 25`).

## Time-lapse

While the camera is configured and storage is ready, the controller saves one JPEG frame every
`interval_seconds` (default 300) during a local-time `window` (default `06:00-18:00`) under
`<storage>/timelapse/YYYY-MM-DD/HHMMSS.jpg`, taken from the buffered stream at `width` pixels
(default 1920). Frames live in their own `allocation_bytes` (default 4 GiB); the oldest are
deleted when it fills, so time-lapse can never crowd out event clips. Captures align to interval
boundaries and resume from existing frames after a restart. `GET /v1/timelapse/{date}/video.mp4`
assembles that day's frames with libx264 at `export_fps` and caches the result for a day under
`<storage>/timelapse-exports/`. These settings are owner-adjustable from the app through
`PUT /v1/timelapse` and persist in `timelapse.json` next to `config.json`; the status reports an
estimated retention in days from the current schedule and average frame size.

## Unattended operation

The controller is meant to run for weeks without a visit, so it watches itself:

- **Watchdog.** Both units are `Type=notify` with `WatchdogSec=90`; the controller loop pings
  systemd every half second. A hung process is killed and restarted (`Restart=always`,
  `StartLimitIntervalSec=0`). Tested on the Pi by freezing the process: restarted in 97 s.
- **Camera loss.** The buffer supervisor restarts ffmpeg five seconds after it exits; the
  motion watcher resubscribes with a ten-second backoff. Each restart is logged.
- **Storage loss.** Every five seconds the loop checks the SSD. If it is gone, recording stops
  (so nothing lands on the OS card) and an error is logged; when it returns, recording restarts
  and that is logged too. The health file itself is only written while the SSD is present.
- **Clock.** A system time before 2026 means the clock is not set; the status flags it, and the
  controller will not push that time to the camera.
- **Temperature and power.** Every ten seconds the controller reads the SoC temperature and
  `vcgencmd get_throttled`. The Pi 4 caps its clock at 80 °C and throttles hard at 85 °C, so
  75 °C is logged as a warning and 80 °C as an error; under-voltage and throttling are logged
  when they begin and end. The status carries a `thermal` block with the reading, thresholds,
  flags and a good/warning/danger level.
- **Health log.** `GET /v1/health?level=info|warning|error&limit=N` returns incidents newest
  first (start, recording started/stopped, camera stream restarts, camera errors and recovery,
  storage lost/returned, events recorded, live view, time-lapse errors). The status carries a
  `health` block with `clock_ok`, counts for the last day and the latest warnings. Logs also go
  to journald and to `controller.log` (rotating, 2 MB × 3) next to `config.json`.

## Camera isolation

The controller hands the camera its address (NetworkManager shared mode on the camera's Ethernet
port), which by default would also route the camera to the Internet through the controller.
`deploy/camera-isolation.sh` (run with sudo) installs an nftables table that drops all forwarding
to or from the camera interface and DNS from it, persisted through `/etc/nftables.conf`. The
camera can then talk to the controller only. Check it with
`sudo nft list table inet solstead_camera`; the drop rules carry packet counters.

The bench fake camera is an MPEG-TS stream over HTTP; real cameras use `rtsp://`.

## Configuration (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `storage_path` | required | Recording SSD mount point. |
| `storage_require_mount` | `true` | Refuse to record unless `storage_path` is a mount point, so a missing SSD never fills the OS card. |
| `api_token` | required | Owner bearer token, at least 32 characters. |
| `tls_cert`, `tls_key` | required | PEM certificate and key served to the app. |
| `name` | `Solstead` | Shown in the app. |
| `bind`, `port` | `127.0.0.1`, `8443` | Listen address. Provisioning sets `0.0.0.0` for a real controller. |
| `camera_url` | none | `rtsp://` stream URL including any camera credentials (`http://` MPEG-TS also accepted). |
| `camera_onvif_url` | none | ONVIF event service, e.g. `http://camera:2020/onvif/device_service`. Enables event recording. |
| `camera_user`, `camera_password` | empty | ONVIF credentials (WS-UsernameToken digest). |
| `motion_topics` | `["Motion"]` | Substrings an ONVIF topic must contain to count as motion. |
| `event_pre_seconds`, `event_post_seconds` | `10`, `30` | Footage kept before first and after last activity. |
| `event_hold_seconds` | `10` | Assumed activity length after an "on" report when the camera never reports "off". |
| `event_max_seconds` | `300` | Longest single event before it is cut. |
| `segment_seconds`, `buffer_seconds` | `2`, `60` | Buffer segment length and how much to keep. |
| `events_allocation_bytes` | 20 GiB | Size cap for `<storage>/events`; oldest events are overwritten. |
| `camera_live_url` | none | Lower-resolution stream for live view; falls back to `camera_url`. |
| `live_segment_seconds`, `live_idle_seconds` | `1`, `20` | HLS segment length and how long live view survives without a viewer. |
| `live_path` | `/dev/shm/solstead-live` | Where live segments are written; should be RAM. |
| `clip_seconds` | `10` | Length of a manual test clip. |
| `snapshot_width` | `960` | Snapshot width in pixels; height keeps the aspect ratio. |
| `min_free_bytes` | 2 GiB | Recording pauses below this much free space. |

The camera URL contains credentials. ffmpeg's stderr is therefore never returned to the app or logged.

## API v1

All requests need `Authorization: Bearer <api_token>`. Responses are JSON unless noted and carry
`Cache-Control: no-store`.

| Method and path | Result |
| --- | --- |
| `GET /v1/status` | Name, version, uptime, CPU temperature, storage readiness and usage, camera state, an `events` block (running, buffer age, motion active, camera subscription, counts, allocation), a `live` block (available, active, quality, ready), and a `timelapse` block (settings and counts). |
| `GET /v1/camera/snapshot[?width=N]` | One JPEG frame (`image/jpeg`) at `snapshot_width` by default; `width=0` returns the camera's full resolution, taken from the buffer. |
| `GET /v1/recordings` | `{"recordings": [{id, size_bytes, created_unix}, …]}`, newest first, up to 200. |
| `POST /v1/recordings` | Records a `clip_seconds` clip to the SSD; returns its entry with status 201. |
| `GET /v1/events` | `{"events": [{id, started_unix, ended_unix, clip_from_unix, clip_to_unix, size_bytes, trigger, created_unix, duration_seconds}, …]}`, newest first. |
| `GET /v1/events/{id}/clip` | The event's MP4 (`video/mp4`) with `Range` support so players can seek. `HEAD` works too. |
| `GET /v1/timelapse` | Settings, counts, estimated retention, and the list of days with frames. |
| `PUT /v1/timelapse` | Change `enabled`, `interval_seconds`, `window` (and `width`, `allocation_bytes`, `export_fps`); JSON body. |
| `GET /v1/timelapse/{date}` | That day's frames (`name`, `unix`, `size_bytes`). |
| `GET /v1/timelapse/{date}/{HHMMSS}.jpg` | One frame. |
| `GET /v1/timelapse/{date}/video.mp4` | That day's frames assembled into an H.264 video. |
| `POST /v1/live?quality=low` | Start or keep live view (`low` or `high`); returns when playable, with the playlist path. |
| `GET /v1/live/index.m3u8`, `GET /v1/live/{segment}` | The HLS playlist, `init.mp4` and `seg_NNNNN.m4s` segments. Each fetch keeps the stream alive. |
| `DELETE /v1/live` | Stop live view now. |

Errors: 401 without a valid token, 404 unknown route, 503 with `{"error": "…"}` when the camera or
storage is unavailable.

## Raspberry Pi deployment

Two installers exist. `deploy/install-user.sh` needs no root: it installs a virtualenv under the
checkout, provisions `~/.config/solstead`, and runs the controller as a systemd user service
(`systemctl --user status solstead`, `journalctl --user -u solstead`); the user needs
`loginctl enable-linger` so the service starts at boot. This is what the bench Pi runs.
`deploy/install.sh` is the system-wide equivalent under a dedicated `solstead` user.

`deploy/install.sh` installs ffmpeg and Python, creates a `solstead` system user, installs the
package into `/opt/solstead/venv`, provisions `/etc/solstead` on first run, and enables the
`solstead.service` systemd unit. Set `SOLSTEAD_HOSTNAME` and `SOLSTEAD_CAMERA_URL` in the
environment to override the defaults during provisioning, then add the `camera_onvif_url`,
`camera_user` and `camera_password` keys to `/etc/solstead/config.json` and restart the service
to turn on event recording. Mount the SSD at `/mnt/solstead`
before starting the service; the unit declares `RequiresMountsFor=/mnt/solstead`.

Useful commands on the Pi:

```bash
sudo systemctl status solstead
sudo journalctl -u solstead -f
sudo /opt/solstead/venv/bin/solstead fingerprint /etc/solstead/controller.crt
```
