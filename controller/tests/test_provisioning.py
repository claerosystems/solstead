import json
import os
import shutil
import stat

import pytest

from solstead.config import Config
from solstead.provisioning import ProvisioningError, certificate_fingerprint, provision

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl required")


def test_fingerprint_rejects_non_pem():
    with pytest.raises(ProvisioningError):
        certificate_fingerprint("not a cert")


@needs_openssl
def test_provision_writes_private_config(tmp_path, storage_root):
    result = provision(tmp_path / "etc", "solstead-r4b-1.local", storage_root, port=8443,
                       camera_url="rtsp://cam/1")  # fmt: skip
    config = Config.load(result["config"])
    assert config.camera_url == "rtsp://cam/1" and config.bind == "0.0.0.0"
    assert len(result["token"]) >= 32 and len(result["fingerprint"]) == 64
    assert result["url"] == "https://solstead-r4b-1.local:8443"
    for name in ("config.json", "controller.key"):
        mode = stat.S_IMODE(os.stat(tmp_path / "etc" / name).st_mode)
        assert mode == 0o600, name
    written = json.loads((tmp_path / "etc" / "config.json").read_text())
    assert written["api_token"] == result["token"]


@needs_openssl
def test_provision_refuses_overwrite(tmp_path, storage_root):
    provision(tmp_path / "etc", "a.local", storage_root)
    with pytest.raises(ProvisioningError, match="already exists"):
        provision(tmp_path / "etc", "a.local", storage_root)


@needs_openssl
def test_cli_provision_and_fingerprint(tmp_path, storage_root, capsys):
    from solstead.cli import main

    code = main(["provision", "--dir", str(tmp_path / "etc"), "--hostname", "b.local",
                 "--storage", str(storage_root), "--no-require-mount"])  # fmt: skip
    assert code == 0
    out = capsys.readouterr().out
    assert "fingerprint" in out and "token" in out
    assert main(["fingerprint", str(tmp_path / "etc" / "controller.crt")]) == 0
    assert len(capsys.readouterr().out.strip()) == 64


def test_cli_serve_bad_config(tmp_path, capsys):
    from solstead.cli import main

    assert main(["serve", str(tmp_path / "missing.json")]) == 1
    assert "solstead:" in capsys.readouterr().err
