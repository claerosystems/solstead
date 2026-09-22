"""Create the owner token, self-signed TLS certificate and configuration for a controller."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from .config import Config

CERT_DAYS = 3650


class ProvisioningError(Exception):
    pass


def certificate_fingerprint(pem: str) -> str:
    """SHA-256 hex digest of the DER certificate, as the app expects for pinning."""
    try:
        body = pem.split("-----BEGIN CERTIFICATE-----")[1].split("-----END CERTIFICATE-----")[0]
    except IndexError:
        raise ProvisioningError("Not a PEM certificate") from None
    return hashlib.sha256(base64.b64decode("".join(body.split()))).hexdigest()


def generate_certificate(hostname: str, cert: Path, key: Path, days: int = CERT_DAYS) -> None:
    args = [
        "openssl", "req", "-x509", "-nodes", "-newkey", "ec",
        "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-keyout", str(key), "-out", str(cert), "-days", str(days),
        "-subj", f"/CN={hostname}", "-addext", f"subjectAltName=DNS:{hostname}",
    ]  # fmt: skip
    try:
        subprocess.run(args, check=True, capture_output=True)
    except FileNotFoundError:
        raise ProvisioningError("openssl is required to create the certificate") from None
    except subprocess.CalledProcessError as error:
        raise ProvisioningError(error.stderr.decode(errors="replace").strip()) from None
    os.chmod(key, 0o600)


def provision(
    directory: Path,
    hostname: str,
    storage_path: Path,
    name: str = "Solstead",
    bind: str = "0.0.0.0",
    port: int = 8443,
    camera_url: str | None = None,
    require_mount: bool = True,
) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    if config_path.exists():
        raise ProvisioningError(f"{config_path} already exists; remove it to re-provision")
    cert, key = directory / "controller.crt", directory / "controller.key"
    generate_certificate(hostname, cert, key)
    config = Config(
        storage_path=Path(storage_path),
        api_token=secrets.token_urlsafe(48),
        tls_cert=cert,
        tls_key=key,
        name=name,
        bind=bind,
        port=port,
        camera_url=camera_url,
        storage_require_mount=require_mount,
    )
    config_path.write_text(json.dumps(config.to_dict(), indent=2) + "\n")
    os.chmod(config_path, 0o600)
    return {
        "config": str(config_path),
        "url": f"https://{hostname}:{port}",
        "token": config.api_token,
        "fingerprint": certificate_fingerprint(cert.read_text()),
    }
