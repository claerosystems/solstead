# Architecture overview

Status: bench prototype, September 2026. This describes what is built and how the code is
organised; product requirements and open questions live in [product-notes.md](product-notes.md)
and the ADRs under [adr/](adr/).

## Components

```
 iPhone app (SwiftUI)            Controller (Raspberry Pi, Python)          Camera
 ┌─────────────────────┐   TLS   ┌──────────────────────────────────┐  RTSP  ┌────────┐
 │ Solstead app        │ ──────▶ │ api.py  ── controller.py ── camera.py ─────▶ │ VIGI   │
 │  └ SolsteadKit      │  :8443  │            │                     (ffmpeg) │        │
 │     ControllerClient│         │            └── storage.py ── SSD          └────────┘
 │     PinnedTrust     │         │ provisioning.py: token, cert, config.json
 └─────────────────────┘         └──────────────────────────────────┘
```

- The controller exposes a small HTTPS API on the local network. There is no cloud, account or
  relay (ADR 0001).
- The phone pairs with the controller by entering three values printed during provisioning:
  the address, the owner token, and the SHA-256 fingerprint of the controller's self-signed
  certificate. The app pins that fingerprint and rejects any other certificate.
- The pairing lives in the phone's Keychain, marked device-only so it is never synced or backed up.
- Recording writes only to the mounted SSD. If the SSD is missing the controller reports it and
  refuses to record rather than filling the OS card.

## Repository layout

- `controller/` is a standard-library Python package (`solstead`) with pytest tests, a CLI, a
  systemd unit and an installer. See [controller/README.md](../controller/README.md).
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

1. Event recording with pre/post buffers from the camera's motion events, and the separate
   event and time-lapse storage allocations.
2. Live view over the local network (low-resolution by default).
3. Cellular remote access without vendor servers, after the carrier and modem validation in
   [cellular-validation-plan.md](cellular-validation-plan.md).
4. SMS alerts, reduced-power mode and power telemetry once the power path is measured.
5. Encrypted local storage and customer-controlled off-site backup (ADR 0002).
