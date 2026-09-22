<img src="docs/brand/solstead-mark.svg" alt="Solstead mark: a roof line over a half sun on the horizon" width="96">

# Solstead

A modular, solar-powered outdoor security system whose customers own and control their data.
No company-operated services, ever: the controller records locally, the phone talks to the
controller directly, and remote features run over the customer's own cellular plan.

This repository holds everything: product decisions, the controller software that runs on the
Raspberry Pi, and the iPhone companion app.

| Path | What it is |
| --- | --- |
| [`controller/`](controller/README.md) | Python service for the Raspberry Pi controller: TLS API, camera capture via ffmpeg, SSD recording, provisioning CLI, systemd deployment. |
| [`ios/`](ios/) | iPhone app (`Solstead.xcodeproj`) and the `SolsteadKit` Swift package it is built on (models, pinned-TLS client, pairing validation, unit tests). |
| [`docs/`](docs/) | Product notes, feasibility reviews, cellular and camera research, ADRs, brand assets, and the [architecture overview](docs/architecture.md). |
| [`CONTEXT.md`](CONTEXT.md) | The project's vocabulary. Use these words in code, UI and docs. |
| [`scripts/`](scripts/) | Developer helpers, such as running a bench controller on a laptop. |

## Quick start

Controller (any machine with Python 3.11+ and ffmpeg; Raspberry Pi OS Bookworm for real use):

```bash
make controller-setup     # creates controller/.venv with dev tools
make controller-test      # ruff + pytest
```

Bench controller on your Mac, for developing the app without a Pi:

```bash
scripts/bench-controller.sh                 # provisions into .bench/ and serves on https://localhost:8443
scripts/bench-controller.sh rtsp://user:pw@camera/stream1   # with a real camera
```

iOS app (Xcode 16 or newer; this repo was set up with Xcode 26):

```bash
make ios-test             # SolsteadKit unit tests via swift test
make ios-build            # build the app for the simulator
scripts/sim-run.sh        # build, install and launch in a simulator, pre-paired with the bench controller
open ios/Solstead.xcodeproj
```

Debug builds read `SOLSTEAD_URL`, `SOLSTEAD_TOKEN` and `SOLSTEAD_FINGERPRINT` from the launch
environment to prefill the pairing form; `scripts/sim-run.sh` uses this.

To run on a physical iPhone, select your team under Signing & Capabilities in Xcode. The bundle
identifier is `com.claerosystems.solstead`.

## Deploying to a Raspberry Pi

1. Flash Raspberry Pi OS (64-bit, Bookworm or later), enable SSH, and mount the recording SSD at
   `/mnt/solstead` via `/etc/fstab`.
2. Clone this repository on the Pi and run `sudo controller/deploy/install.sh`.
3. The installer provisions a self-signed certificate and owner token and prints the pairing
   details once. Enter the address, token and fingerprint in the app.

Details, configuration keys and the API contract are in [controller/README.md](controller/README.md)
and [docs/architecture.md](docs/architecture.md).

## Current scope

This is the bench prototype: status, snapshots and manual 10-second test clips over the local
network. Event detection, live video, remote access over cellular, SMS alerts, encrypted local
storage and off-site backup are decided as requirements in `docs/` but not yet implemented.

## Development conventions

- Continuous integration runs the controller tests on Linux and the iOS tests and build on macOS
  for every push and pull request (`.github/workflows/`).
- Never log or return ffmpeg's stderr: camera URLs carry credentials.
- Secrets never live in the repo. `config.json`, keys and certificates are ignored by git.
- Keep `CONTEXT.md` vocabulary in code and UI copy (controller, camera, owner, viewer, protected clip).
