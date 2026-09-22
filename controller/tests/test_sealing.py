from __future__ import annotations

import os

import pytest

from solstead import sealing
from solstead.sealing import SealingError

# From the age specification's test keys (age-encryption.org/v1, C2SP).
SPEC_IDENTITY = "AGE-SECRET-KEY-1GFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPQ4EGAEX"
SPEC_RECIPIENT = "age1zvkyg2lqzraa2lnjvqej32nkuu0ues2s82hzrye869xeexvn73equnujwj"


def keypair():
    identity, recipient = sealing.generate_identity()
    return sealing.parse_identity(identity), sealing.parse_recipient(recipient)


def test_spec_identity_derives_spec_recipient():
    assert sealing.recipient_for(sealing.parse_identity(SPEC_IDENTITY)) == SPEC_RECIPIENT


def test_bech32_rejects_typos_and_wrong_kinds():
    typo = SPEC_RECIPIENT[:-1] + ("q" if SPEC_RECIPIENT[-1] != "q" else "p")
    with pytest.raises(SealingError, match="checksum"):
        sealing.parse_recipient(typo)
    with pytest.raises(SealingError):
        sealing.parse_recipient(SPEC_IDENTITY)
    with pytest.raises(SealingError):
        sealing.parse_identity(SPEC_RECIPIENT)


@pytest.mark.parametrize("size", [0, 1, 64 * 1024 - 1, 64 * 1024, 64 * 1024 + 1, 3 * 64 * 1024])
def test_round_trip_across_chunk_boundaries(size):
    secret, public = keypair()
    data = os.urandom(size)
    sealed = sealing.seal_bytes(data, [public])
    assert size < 16 or data not in sealed
    assert sealing.open_bytes(sealed, secret) == data


def test_every_recipient_can_open_and_others_cannot():
    device, device_pub = keypair()
    recovery, recovery_pub = keypair()
    stranger, _ = keypair()
    sealed = sealing.seal_bytes(b"clip", [device_pub, recovery_pub])
    assert sealing.open_bytes(sealed, device) == b"clip"
    assert sealing.open_bytes(sealed, recovery) == b"clip"
    with pytest.raises(SealingError, match="cannot open"):
        sealing.open_bytes(sealed, stranger)


def test_tampering_is_detected():
    secret, public = keypair()
    sealed = bytearray(sealing.seal_bytes(os.urandom(100_000), [public]))
    flipped = bytes(sealed[:-5]) + bytes([sealed[-5] ^ 1]) + bytes(sealed[-4:])
    with pytest.raises(SealingError):
        sealing.open_bytes(flipped, secret)
    with pytest.raises(SealingError):
        sealing.open_bytes(bytes(sealed[: -64 * 1024]), secret)  # truncated at a chunk boundary


def test_seal_requires_a_recipient():
    with pytest.raises(SealingError):
        sealing.seal_bytes(b"x", [])


def test_seal_file_removes_partial_output_on_failure(tmp_path):
    target = tmp_path / "out.age"
    with pytest.raises(FileNotFoundError):
        sealing.seal_file(tmp_path / "missing", target, [keypair()[1]])
    assert not target.exists()


# ---- interoperability with an independent age implementation (rage, via pyrage) --------------

pyrage = pytest.importorskip("pyrage")


@pytest.mark.parametrize("size", [0, 5, 64 * 1024, 200_000])
def test_rage_opens_what_we_seal(size):
    identity = pyrage.x25519.Identity.generate()
    data = os.urandom(size)
    recipients = [sealing.parse_recipient(str(identity.to_public())), keypair()[1]]
    assert pyrage.decrypt(sealing.seal_bytes(data, recipients), [identity]) == data


@pytest.mark.parametrize("size", [0, 5, 64 * 1024, 200_000])
def test_we_open_what_rage_seals(size):
    identity, recipient = sealing.generate_identity()
    data = os.urandom(size)
    sealed = pyrage.encrypt(data, [pyrage.x25519.Recipient.from_str(recipient)])
    assert sealing.open_bytes(sealed, sealing.parse_identity(identity)) == data
