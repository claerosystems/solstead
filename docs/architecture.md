# Architecture overview

Status: bench prototype with event recording, September 2026. This describes what is built and how the code is
organised; product requirements and open questions live in [product-notes.md](product-notes.md)
and the ADRs under [adr/](adr/).

## Components

```
 iPhone app (SwiftUI)            Controller (Raspberry Pi, Python)                 Camera
 ┌─────────────────────┐   TLS   ┌────────────────────────────────────────┐        ┌────────┐
 │ Solstead app        │ ──────▶ │ api.py ── controller.py ── camera.py   │        │ VIGI   │
 │  └ SolsteadKit      │  :8443  │             │    ├── buffer.py (ffmpeg) ◀─RTSP──│        │
 │     ControllerClient│         │             │    ├── motion.py ── onvif.py ◀─ONVIF─│ events │
 │     PinnedTrust     │         │             │    └── events.py ── clips.py       └────────┘
 └─────────────────────┘         │             └── storage.py ── SSD: events/ (sealed) manual/
                                 │ provisioning.py: token, cert, config.json
                                 └────────────────────────────────────────┘
```

- The controller exposes a small HTTPS API on the local network. There is no cloud, account or
  relay (ADR 0001).
- The phone pairs with the controller by entering three values printed during provisioning:
  the address, the owner token, and the SHA-256 fingerprint of the controller's self-signed
  certificate. The app pins that fingerprint and rejects any other certificate.
- The pairing lives in the phone's Keychain, marked device-only so it is never synced or backed up.
- Recording writes only to the mounted SSD. If the SSD is missing the controller reports it and
  refuses to record rather than filling the OS card.
- Live view is on demand: the controller copies the camera's substream into one-second HLS
  segments in RAM only while a phone is watching, and stops 20 seconds after the last request.
  The app plays it with AVPlayer through a loopback proxy inside the app, because AVPlayer's own
  networking cannot use the pinned session; the proxy fetches every segment through it. A
  full-quality option switches to the main stream.
- Time-lapse takes a frame from the buffered stream on a schedule into a separate size-capped
  allocation, and the controller assembles a day into a video on request. The owner changes the
  interval and window from the app; the controller persists them and reports the retention that
  schedule allows.
- Stored recordings are sealed to the owner's public keys in the age file format (ADR 0003). The
  controller holds only public keys, so it records unattended after a power cut but cannot
  decrypt its own footage; the phone decrypts. Plaintext video (the pre-event buffer and clip
  assembly) stays in RAM.
- Event recording follows the accepted defaults: a clip starts 10 seconds before the first
  detected activity and ends 30 seconds after the last. The camera's own motion detection is the
  trigger, delivered over ONVIF; the controller does no image analysis. The last minute of the
  camera stream is buffered continuously as small segments, and clips are cut from that buffer
  without re-encoding. Events live in a size-capped allocation; the oldest are overwritten.

## Repository layout

- `controller/` is a Python package (`solstead`, standard library plus `cryptography`) with
  pytest tests, a CLI, a systemd unit and an installer. See [controller/README.md](../controller/README.md).
- `ios/SolsteadKit/` is a Swift package with the platform-independent parts of the app: pairing
  validation, certificate pinning, the API models and the client. It tests with `swift test` on
  macOS, so no simulator is needed for unit tests.
- `ios/Solstead/` is the SwiftUI app. The Xcode project uses synchronized folders, so adding a
  file under `ios/Solstead/` adds it to the target; no project edits are required.

## Vocabulary in code

Names follow [CONTEXT.md](../CONTEXT.md): `Controller`, `Camera`, `Storage`, `Recording`, `Pairing`,
`Owner token`. The manual clips of the bench prototype are not yet "protected clips" or events.

## What is next

In the order the product notes prioritise validation:

1. Unattended-operation hardening before the winter trial: camera and SSD loss and return,
   reboot recovery, log rotation, a health history the app can show.
2. Live view over cellular reuses the same HLS path; measure data use per minute and the
   time-to-first-frame target with the VPN in place.
3. Cellular remote access without vendor servers, after the carrier and modem validation in
   [cellular-validation-plan.md](cellular-validation-plan.md).
4. SMS alerts, reduced-power mode and power telemetry once the power path is measured.
5. Encrypted local storage is built (ADR 0003): event clips, time-lapse frames and manual clips
   are sealed to owner-held keys, and the phone decrypts clips and assembles encrypted
   time-lapse days. Next, customer-controlled off-site backup uploads the same sealed files
   (ADR 0002).
