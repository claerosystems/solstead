"""Command line entry point: `solstead serve|provision|fingerprint`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError
from .provisioning import ProvisioningError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solstead", description="Solstead controller")
    parser.add_argument("--version", action="version", version=f"solstead {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the controller API")
    serve.add_argument("config", type=Path, help="path to config.json")

    prov = commands.add_parser("provision", help="create token, certificate and config")
    prov.add_argument("--dir", type=Path, default=Path("/etc/solstead"), help="output directory")
    prov.add_argument("--hostname", required=True, help="e.g. solstead-r4b-1.local")
    prov.add_argument("--storage", type=Path, default=Path("/mnt/solstead"))
    prov.add_argument("--name", default="Solstead")
    prov.add_argument("--bind", default="0.0.0.0")
    prov.add_argument("--port", type=int, default=8443)
    prov.add_argument("--camera-url", default=None, help="rtsp:// URL; may be set later")
    prov.add_argument(
        "--camera-onvif-url", default=None, help="http://camera:2020/onvif/device_service"
    )
    prov.add_argument("--camera-user", default="")
    prov.add_argument("--camera-password", default="")
    prov.add_argument(
        "--no-require-mount", action="store_true",
        help="allow a plain folder as storage (bench use on a laptop only)",
    )  # fmt: skip

    finger = commands.add_parser("fingerprint", help="print a certificate's SHA-256 fingerprint")
    finger.add_argument("certificate", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            from .config import Config
            from .server import serve

            serve(Config.load(args.config))
        elif args.command == "provision":
            from .provisioning import provision

            result = provision(
                args.dir, args.hostname, args.storage, args.name, args.bind, args.port,
                args.camera_url, require_mount=not args.no_require_mount,
                camera_onvif_url=args.camera_onvif_url, camera_user=args.camera_user,
                camera_password=args.camera_password,
            )  # fmt: skip
            print("Provisioned. Enter these in the Solstead app; keep the token private.")
            for key in ("config", "url", "token", "fingerprint"):
                print(f"  {key:12} {result[key]}")
        elif args.command == "fingerprint":
            from .provisioning import certificate_fingerprint

            print(certificate_fingerprint(args.certificate.read_text()))
    except (ConfigError, ProvisioningError, OSError) as error:
        print(f"solstead: {error}", file=sys.stderr)
        return 1
    return 0
