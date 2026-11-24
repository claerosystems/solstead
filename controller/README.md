# Solstead controller

The service that runs on the Raspberry Pi inside the controller. The standard library plus
`cryptography` (for sealing recordings); ffmpeg does the camera work.

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
| `solstead/verifier.py` | Checks the camera's motion reports on a small stream: motion areas, shadows, lighting changes. |
| `solstead/recognition.py` | Person, vehicle and animal detection (onnxruntime) for motion checking. |
| `solstead/zones.py` | The owner's motion areas and shadow settings (`motion.json`). |
| `solstead/events.py` | Event recorder state machine and the size-capped events allocation. |
| `solstead/clips.py` | Assembles a clip from buffered segments with ffmpeg's concat demuxer. |
| `solstead/sealing.py` | Seals files to public keys in the age v1 format (X25519, ChaCha20-Poly1305). |
| `solstead/recipients.py` | The enrolled public keys (device and recovery) that recordings are sealed to. |
| `solstead/live.py` | On-demand live view: ffmpeg copies a camera stream into 1-second HLS segments in RAM. |
| `solstead/timelapse.py` | Scheduled frames in their own allocation (sealed per frame when keys are enrolled), day listing, MP4 export of plain days, owner-adjustable settings. |
| `solstead/health.py` | Health log: incidents in memory and in `<storage>/health.jsonl`, capped. |
| `solstead/sdnotify.py` | systemd readiness and watchdog pings without a dependency. |
| `solstead/network.py` | Reads the active uplink (lowest-metric default route) and Wi-Fi and modem signal. |
| `solstead/uplink.py` | The owner's preferred uplink, applied as NetworkManager route metrics (`uplink.json`). |
| `solstead/thermal.py` | CPU temperature against the Pi 4's throttle points and its under-voltage/throttle flags. |
| `solstead/api.py` | The HTTP handler: bearer-token auth and the `/v1` routes. |
| `solstead/server.py` | TLS 1.2+ server around the handler. |
| `solstead/provisioning.py` | Generates the owner token, self-signed certificate and config. |
| `solstead/cli.py` | `solstead serve | provision | fingerprint | keygen | decrypt`. |
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
   `/dev/shm/solstead-buffer/segments/` (RAM) as 2-second MPEG-TS segments named by wall-clock start time and prunes
   segments older than `buffer_seconds`, except footage an open event still needs.
2. `motion.py` pulls ONVIF notifications whose topic contains one of `motion_topics` and reads the
   `IsMotion`/`State` boolean. Cameras that only ever report "on" are handled by
   `event_hold_seconds`: activity is assumed to last that long after the last report.
3. `verifier.py` checks each camera report before the recorder hears it (see below).
4. `events.py` opens an event at the first activity, extends it while activity continues, and
   closes it `event_post_seconds` after the last activity. Overlapping bursts therefore merge into
   one clip. Events longer than `event_max_seconds` are cut and a new one started.
5. Once the buffer holds the post-roll, the segments covering
   `[first activity - event_pre_seconds, last activity + event_post_seconds]` are concatenated
   into `<storage>/events/<id>.mp4` with a `<id>.json` sidecar. When the events allocation is
   exceeded the oldest events are deleted.

### Motion areas and shadows

The camera's own detector reacts to anything that changes in the picture, including passing
traffic and moving shadows. The controller therefore checks each report itself:

- `verifier.py` runs one more ffmpeg that decodes `camera_motion_url` (else `camera_live_url`, else
  `camera_url`; use the camera's small substream, never the main stream) to 256×144 RGB at four
  frames a second, and keeps a slowly learned background of the scene.
- Only pixels inside the owner's motion areas (`zones.py`, stored in `motion.json`) count. No areas
  means the whole view.
- With `ignore_shadows`, a changed pixel is dismissed as shadow when it got darker by a factor of
  0.35–0.92, by about the same factor as its neighbours (the surface's texture survives), and, in a
  colour picture, without changing colour. Under infrared only the first two tests apply.
- When more than half the watched area changes at once (a cloud, infrared switching, headlights
  sweeping) the frame is treated as a lighting change and the background relearned.
- Isolated pixels are dropped, and the largest connected object must reach a size set by the
  `sensitivity` (low, medium, high) in two or three of the last three frames.

- With `recognize` on (the default), movement that passes those checks is shown to a person,
  vehicle and animal detector (`recognition.py`: SSD MobileNet v1 trained on COCO, from the ONNX
  model zoo under the Apache License 2.0, run with onnxruntime on two threads). The checker then
  agrees only when one of the owner's chosen `objects` is found with at least 50% confidence and
  its box's bottom middle (or centre) lies in an area. A recognition keeps confirming for 15 seconds.
  To keep the Pi's load down the detector sees 512×288 frames, at most once a second, and only
  while there is movement. The installers add onnxruntime and run `deploy/fetch-model.sh`, which
  downloads the 29 MB model beside `config.json` and checks its SHA-256. Without either, recognition
  reports why in `check.recognition.reason` and the motion filters decide alone. Events confirmed
  this way are labelled with the kind found (`trigger` is `person`, `vehicle` or `animal`).

The recorder hears about motion only while the camera reports motion and the checker agrees; the
start is backdated to the camera's report (at most `event_pre_seconds`), so the pre-roll is intact.
If numpy is missing, the stream fails, or no frame has arrived for 10 seconds, camera reports pass
straight through as before, so a checker failure can never stop recording. The status `events`
block reports `motion_check` with the counts of confirmed, ignored and passed-through reports.

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

With encryption keys enrolled, each frame is sealed in memory as it is captured and stored as
`HHMMSS.jpg.age`, one age file per frame. The controller cannot open those, so
`GET /v1/timelapse/{date}/video.mp4` answers 409 for any day with a sealed frame and the app
downloads the frames, decrypts them and assembles the video on the phone. Days of plain frames
are still exported here.

## Encryption at rest

See [ADR 0003](../docs/adr/0003-seal-recordings-to-owner-held-keys.md). The owner's phone enrols
public keys with `PUT /v1/encryption`: at least one `device` key and exactly one `recovery` key,
as age `age1…` strings. They are stored in `recipients.json` beside `config.json`, never on the SSD.
From then on each event clip is assembled in RAM, sealed to every enrolled key, and written to the
SSD as `<id>.mp4.age`; plaintext video never reaches the SSD. Clips recorded before enrolment are
sealed one at a time in the background (their old blocks may survive on the drive). The
controller holds no key that can decrypt anything.

A new phone restores access with the recovery key: the app enrols its own key, then for each
sealed clip fetches the header, adds a stanza for itself using the recovery key, and puts the
header back (`/v1/events/{id}/header`). Only headers travel; the controller still never sees a
private key. Encryption is the owner's choice (a switch in the app's Settings). `DELETE /v1/encryption` turns it
off; clips already sealed stay sealed, since the controller cannot decrypt them. Until keys are
enrolled, event clips are stored unencrypted as before and `GET /v1/status` reports
`encryption.enrolled: false`.

Time-lapse frames (see Time-lapse) and manual test clips (`manual/<id>.mp4.age`, assembled in RAM
like event clips) are sealed the same way once keys are enrolled, and those stored before
enrolment are sealed in the background; enrolment also deletes cached plaintext time-lapse
exports. Restoring access re-wraps frame headers a day at a time
(`/v1/timelapse/{date}/headers`).

Any age implementation opens a sealed clip. For bench checks without the app:

```bash
.venv/bin/solstead keygen > key.txt            # prints the public key on its first line
.venv/bin/solstead decrypt key.txt clip.mp4.age clip.mp4
age -d -i key.txt clip.mp4.age > clip.mp4      # the same with the age tool
```

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
- **Solar charger.** With the `ble` extra (bleak) the controller listens for Victron Instant
  Readout broadcasts over BlueZ; no pairing or PIN is involved. Once the owner picks their
  charger and gives its advertisement key (`PUT /v1/charger`, kept in `charger.json` beside
  `config.json`, mode 0600), every 10 s the latest reading adds battery voltage and current,
  solar watts, load-output watts and charge state to telemetry, and telemetry history gains daily
  harvested and used watt-hours. The health log records the charger going quiet for 5 minutes,
  charger errors, and the battery falling below 12.0 V (cleared above 12.6 V). If the Pi's
  Bluetooth is switched off, `rfkill unblock bluetooth` turns it on.
- **Preferred link.** With both Wi-Fi and cellular set up, the owner picks which one carries
  traffic (`PUT /v1/network {"preferred": "wifi"|"cellular"}`). Both stay connected: the
  controller sets `ipv4.route-metric` to 600 on the preferred link's NetworkManager profiles
  (every Wi-Fi profile, or `solstead-cellular`) and 700 on the other's, then applies it: `nmcli device reapply` for Wi-Fi, which keeps the link up, and
  `nmcli connection up` for cellular, because reapplying a ModemManager device drops its IPv4
  address (cellular is down for a few seconds while Wi-Fi carries traffic). If the
  preferred link disconnects, NetworkManager withdraws its route and traffic moves to the other
  by itself. The metrics live in the profiles, so they hold across reboots; the choice is also
  kept in `uplink.json` beside `config.json` and re-applied at start in case a profile was added
  since. Until the owner picks, NetworkManager's defaults (Wi-Fi first) are left alone. Ethernet
  (100) still wins on the bench. The controller needs polkit permission for this, which
  `deploy/cellular.sh` installs.
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
| `camera_motion_url` | none | Small stream the controller checks motion on; falls back to `camera_live_url`, then `camera_url`. |
| `motion_settings_path` | `motion.json` beside `config.json` | Owner-drawn motion areas, shadow filtering and sensitivity. |
| `recognition_model_path` | `ssd_mobilenet_v1_12.onnx` beside `config.json` | Person, vehicle and animal detector used by motion checking. |
| `live_segment_seconds`, `live_idle_seconds` | `1`, `20` | HLS segment length and how long live view survives without a viewer. |
| `live_path` | `/dev/shm/solstead-live` | Where live segments are written; should be RAM. |
| `buffer_path` | `/dev/shm/solstead-buffer` | Pre-event buffer and clip assembly; must be RAM so plaintext never reaches the SSD. |
| `recipients_path` | `recipients.json` beside `config.json` | Enrolled public keys recordings are sealed to. |
| `uplink_settings_path` | `uplink.json` beside `config.json` | The owner's preferred uplink. |
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
| `GET /v1/recordings` | `{"recordings": [{id, size_bytes, created_unix, encrypted}, …]}`, newest first, up to 200. |
| `POST /v1/recordings` | Records a `clip_seconds` clip to the SSD (sealed as `<id>.mp4.age` when keys are enrolled); returns its entry with status 201. |
| `GET /v1/recordings/{id}` | The recording's MP4 (`video/mp4`), or when sealed the age file as-is (`application/octet-stream`) for the phone to decrypt, with `Range` support. A plain `{id}` sealed since it was listed returns the sealed file. `HEAD` works too. |
| `GET /v1/events[?limit=N]` | `{"events": [{id, started_unix, ended_unix, clip_from_unix, clip_to_unix, size_bytes, trigger, created_unix, encrypted, duration_seconds}, …]}`, newest first. |
| `DELETE /v1/events/{id}` | The owner deletes an event: clip and record are removed and the deletion is logged. 404 if unknown. |
| `GET /v1/events/{id}/clip` | The event's MP4 (`video/mp4`), or when `encrypted` the sealed age file (`application/octet-stream`) for the phone to decrypt, with `Range` support. `HEAD` works too. |
| `GET /v1/events/{id}/header` | Just the age header of a sealed clip (a few hundred bytes), for a key holder to re-wrap. 404 for unencrypted clips. |
| `PUT /v1/events/{id}/header` | Replace a sealed clip's header with a re-wrapped one (raw bytes). Refused unless the body is exactly one age header that keeps every existing stanza; the payload is never touched. Returns the updated event. |
| `GET /v1/encryption` | `{enrolled, format, recipients: [{role, label, public_key, added_unix}], unencrypted_events, unencrypted_frames}`. Also in the status as `encryption`. |
| `PUT /v1/encryption` | Replace the enrolled keys: `{"recipients": [{"role": "device"\|"recovery", "label", "public_key": "age1…"}]}`. Affects new recordings only. 400 if the set is invalid. |
| `DELETE /v1/encryption` | Turn encryption off: forget the enrolled keys so new recordings are stored unencrypted. Clips already sealed stay sealed. |
| `GET /v1/network` | The `network` block of the status: `uplink` (`wifi`, `cellular`, `ethernet`, `other` or null) and its `interface`, `wifi` and `cellular` signal, `preferred` (`wifi` or `cellular`), and `fallback`/`fallback_reason` (traffic left the preferred link because it failed). |
| `PUT /v1/network` | `{"preferred": "wifi"\|"cellular"}`: make that link carry traffic while the other stands by. Returns the network block, re-read after the change. 400 for another value, 409 if that link has no NetworkManager connection, 503 if NetworkManager refuses or is missing. |
| `GET /v1/motion` | `{zones: [{name, points: [[x, y], …]}], ignore_shadows, sensitivity, verify, recognize, objects, check: {…}}`. Points are fractions of the picture, (0, 0) top left. `check` reports whether checking is running, what it sees now, confirmed, ignored and passed-through counts, and `recognition` (available, reason, runs, inference time, last kind and score). |
| `PUT /v1/motion` | Change any of `zones` (up to 8 areas of 3–16 points), `ignore_shadows`, `sensitivity` (`low`, `medium`, `high`), `recognize`, `objects` (one or more of `person`, `vehicle`, `animal`) and `verify` (false records every camera report unchecked). Applied at once and saved to `motion.json`. 400 if invalid. |
| `GET /v1/timelapse` | Settings, counts, estimated retention, and the list of days with frames (`date`, `frames`, `bytes`, `encrypted_frames`). |
| `PUT /v1/timelapse` | Change `enabled`, `interval_seconds`, `window` (and `width`, `allocation_bytes`, `export_fps`); JSON body. |
| `GET /v1/timelapse/{date}` | That day's frames (`day`, `name`, `unix`, `size_bytes`, `encrypted`). |
| `GET /v1/timelapse/{date}/{HHMMSS}.jpg[.age]` | One frame: a JPEG, or for `.age` the sealed file (`application/octet-stream`) for the phone to decrypt. |
| `GET /v1/timelapse/{date}/video.mp4` | That day's frames assembled into an H.264 video. 409 with `{"encrypted": true}` if any frame is sealed; the phone assembles those. |
| `GET /v1/timelapse/{date}/headers` | `{"date", "headers": {name: base64 age header}}` for the day's sealed frames, for a key holder to re-wrap. |
| `PUT /v1/timelapse/{date}/headers` | Replace sealed frames' headers: `{"headers": {name: base64}}`, up to 4 MiB. Each must keep every existing stanza; frames pruned meanwhile are skipped. Returns `{"date", "replaced"}`. |
| `POST /v1/live?quality=low` | Start or keep live view (`low` or `high`); returns when playable, with the playlist path. |
| `GET /v1/live/index.m3u8`, `GET /v1/live/{segment}` | The HLS playlist, `init.mp4` and `seg_NNNNN.m4s` segments. Each fetch keeps the stream alive. |
| `DELETE /v1/live` | Stop live view now. |
| `GET /v1/charger` | The Victron solar charger in use (`configured`, `name`, `address`, `scanning`, `error`, `key_mismatch`, latest `reading`) and `seen`: Victron devices heard in the last ten minutes. The key is never returned. |
| `PUT /v1/charger` | Read this charger from now on: `{"address", "key"}` with its 32-character Instant Readout key. 400 unless the key decrypts a broadcast heard from that address. |
| `DELETE /v1/charger` | Stop reading the charger and delete its key. |

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
