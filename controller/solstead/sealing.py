"""Seal recordings to the owner's public keys in the age v1 file format (age-encryption.org/v1).

The controller holds only public keys ("recipients"), so it can keep recording unattended after
a power cut while nothing on it, or on its SSD, can decrypt what it has written. Any age
implementation, such as the `age` command-line tool, decrypts a sealed file with an identity
(the owner's recovery key or a device key), so footage stays recoverable without Solstead.

Only X25519 recipients are supported. open_file()/open_bytes() exist for tests and bench tools;
the controller itself never holds an identity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SUFFIX = ".age"
INTRO = b"age-encryption.org/v1\n"
X25519_INFO = b"age-encryption.org/v1/X25519"
CHUNK = 64 * 1024
TAG = 16
RECIPIENT_HRP = "age"
IDENTITY_HRP = "age-secret-key-"


class SealingError(ValueError):
    """A key or sealed file is malformed, or no identity matches it."""


# ---- Bech32 (BIP 173), as age uses for key encoding, without the 90-character limit ----------

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values: Iterable[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            check ^= generator[i] if (top >> i) & 1 else 0
    return check


def _expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert(data: Iterable[int], source: int, target: int, pad: bool) -> list[int]:
    acc = bits = 0
    out = []
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & ((1 << target) - 1))
    if pad and bits:
        out.append((acc << (target - bits)) & ((1 << target) - 1))
    elif not pad and (bits >= source or (acc << (target - bits)) & ((1 << target) - 1)):
        raise SealingError("Invalid key padding")
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    words = _convert(data, 8, 5, True)
    check = _polymod(_expand(hrp) + words + [0] * 6) ^ 1
    words += [(check >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[w] for w in words)


def bech32_decode(text: str) -> tuple[str, bytes]:
    if text.lower() != text and text.upper() != text:
        raise SealingError("Key mixes upper and lower case")
    text = text.lower()
    split = text.rfind("1")
    if split < 1 or len(text) - split < 7:
        raise SealingError("Not a Bech32 key")
    hrp, rest = text[:split], text[split + 1 :]
    if any(c not in _CHARSET for c in rest):
        raise SealingError("Key contains an invalid character")
    words = [_CHARSET.index(c) for c in rest]
    if _polymod(_expand(hrp) + words) != 1:
        raise SealingError("Key checksum does not match; check it was copied correctly")
    return hrp, bytes(_convert(words[:-6], 5, 8, False))


# ---- keys -------------------------------------------------------------------------------------


def parse_recipient(text: str) -> bytes:
    """The 32-byte X25519 public key from an age1… recipient string."""
    hrp, key = bech32_decode(text.strip())
    if hrp != RECIPIENT_HRP or len(key) != 32:
        raise SealingError("Not an age X25519 recipient (age1…)")
    return key


def parse_identity(text: str) -> bytes:
    hrp, key = bech32_decode(text.strip())
    if hrp != IDENTITY_HRP or len(key) != 32:
        raise SealingError("Not an age X25519 identity (AGE-SECRET-KEY-1…)")
    return key


def generate_identity() -> tuple[str, str]:
    """(identity, recipient) strings for a new key pair. Bench and test use only."""
    secret = X25519PrivateKey.generate()
    raw = secret.private_bytes_raw()
    return bech32_encode(IDENTITY_HRP, raw).upper(), recipient_for(raw)


def recipient_for(secret: bytes) -> str:
    public = X25519PrivateKey.from_private_bytes(secret).public_key()
    return bech32_encode(RECIPIENT_HRP, public.public_bytes(Encoding.Raw, PublicFormat.Raw))


# ---- primitives -------------------------------------------------------------------------------


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(ikm)


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data).rstrip(b"=")


def _unb64(data: bytes) -> bytes:
    if data.endswith(b"=") or b"\n" in data or b"\r" in data:
        raise SealingError("Non-canonical base64 in header")
    try:
        decoded = base64.b64decode(data + b"=" * (-len(data) % 4), validate=True)
    except ValueError:
        raise SealingError("Invalid base64 in header") from None
    if _b64(decoded) != data:
        raise SealingError("Non-canonical base64 in header")
    return decoded


def _wrap(file_key: bytes, recipient: bytes) -> bytes:
    ephemeral = X25519PrivateKey.generate()
    share = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    key = _hkdf(shared, share + recipient, X25519_INFO)
    body = ChaCha20Poly1305(key).encrypt(bytes(12), file_key, None)
    return b"-> X25519 " + _b64(share) + b"\n" + _b64(body) + b"\n"


def _header(file_key: bytes, recipients: list[bytes]) -> bytes:
    if not recipients:
        raise SealingError("No recipients to seal to")
    head = INTRO + b"".join(_wrap(file_key, r) for r in recipients) + b"---"
    mac = hmac.new(_hkdf(file_key, b"", b"header"), head, hashlib.sha256).digest()
    return head + b" " + _b64(mac) + b"\n"


def _chunk_nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


# ---- sealing ----------------------------------------------------------------------------------


def seal_stream(source: BinaryIO, target: BinaryIO, recipients: list[bytes]) -> None:
    file_key = os.urandom(16)
    nonce = os.urandom(16)
    target.write(_header(file_key, recipients))
    target.write(nonce)
    aead = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    counter = 0
    chunk = source.read(CHUNK)
    while True:
        following = source.read(CHUNK) if len(chunk) == CHUNK else b""
        last = not following
        target.write(aead.encrypt(_chunk_nonce(counter, last), chunk, None))
        if last:
            return
        chunk, counter = following, counter + 1


def seal_file(source: Path, target: Path, recipients: list[bytes]) -> None:
    """Encrypt source into target, flushed to disk. Removes a half-written target on failure."""
    try:
        with source.open("rb") as plain, target.open("wb") as sealed:
            seal_stream(plain, sealed, recipients)
            sealed.flush()
            os.fsync(sealed.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def seal_bytes(data: bytes, recipients: list[bytes]) -> bytes:
    from io import BytesIO

    out = BytesIO()
    seal_stream(BytesIO(data), out, recipients)
    return out.getvalue()


# ---- opening (tests and bench tools; the controller never holds an identity) ------------------


def _read_line(source: BinaryIO) -> bytes:
    line = source.readline(4096)
    if not line.endswith(b"\n"):
        raise SealingError("Truncated or malformed header")
    return line[:-1]


def _unwrap(args: list[bytes], body: bytes, secret: bytes) -> bytes | None:
    if len(args) != 2 or args[0] != b"X25519":
        return None
    share = _unb64(args[1])
    if len(share) != 32 or len(body) != 32:
        raise SealingError("Malformed X25519 stanza")
    private = X25519PrivateKey.from_private_bytes(secret)
    shared = private.exchange(X25519PublicKey.from_public_bytes(share))
    if shared == bytes(32):
        raise SealingError("Invalid X25519 share")
    ours = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = _hkdf(shared, share + ours, X25519_INFO)
    try:
        return ChaCha20Poly1305(key).decrypt(bytes(12), body, None)
    except Exception:
        return None


MAX_HEADER = 16 * 1024


def read_header(source: BinaryIO) -> tuple[bytes, list[bytes]]:
    """The header through its MAC line, and each stanza's raw bytes. Needs no key.

    Used to swap a file's header for one a key holder re-wrapped, without touching the payload.
    """
    header = _read_line(source) + b"\n"
    if header != INTRO:
        raise SealingError("Not an age v1 file")
    stanzas = []
    while True:
        line = _read_line(source)
        if line.startswith(b"--- "):
            _unb64(line[4:])
            header += line + b"\n"
            return header, stanzas
        if not line.startswith(b"-> "):
            raise SealingError("Malformed header stanza")
        stanza = line + b"\n"
        while True:
            part = _read_line(source)
            if part:
                _unb64(part)  # validates; stanza bodies stay opaque to the controller
            stanza += part + b"\n"
            if len(part) < 64:
                break
        stanzas.append(stanza)
        header += stanza
        if len(header) > MAX_HEADER:
            raise SealingError("Header is too long")


def write_rewrapped(path: Path, new_header: bytes, target: Path) -> None:
    """Write path's payload under a re-wrapped header into target, flushed to disk.

    The new header must keep every existing stanza. The controller cannot check the header MAC
    (it has no key), but it can refuse anything that would drop a recipient or touch the
    payload. Removes a half-written target on failure; committing it is the caller's job.
    """
    from io import BytesIO

    if len(new_header) > MAX_HEADER:
        raise SealingError("Header is too long")
    parsed, stanzas = read_header(BytesIO(new_header))
    if parsed != new_header:
        raise SealingError("Body must be exactly one age header")
    with path.open("rb") as old:
        _, old_stanzas = read_header(old)
        if any(s not in stanzas for s in old_stanzas):
            raise SealingError("The new header must keep every existing key")
        try:
            with target.open("wb") as new:
                new.write(new_header)
                while chunk := old.read(1024 * 1024):
                    new.write(chunk)
                new.flush()
                os.fsync(new.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise


def open_stream(source: BinaryIO, target: BinaryIO, identity: bytes) -> None:
    head = _read_line(source) + b"\n"
    if head != INTRO:
        raise SealingError("Not an age v1 file")
    file_key = None
    while True:
        line = _read_line(source)
        if line.startswith(b"---"):
            break
        if not line.startswith(b"-> "):
            raise SealingError("Malformed header stanza")
        head += line + b"\n"
        body = b""
        while True:
            part = _read_line(source)
            head += part + b"\n"
            body += _unb64(part) if part else b""
            if len(part) < 64:
                break
        file_key = file_key or _unwrap(line[3:].split(b" "), body, identity)
    if not line.startswith(b"--- "):
        raise SealingError("Malformed header MAC line")
    if file_key is None:
        raise SealingError("This key cannot open the file")
    head += b"---"
    expected = hmac.new(_hkdf(file_key, b"", b"header"), head, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(line[4:])):
        raise SealingError("Header has been altered")
    nonce = source.read(16)
    if len(nonce) != 16:
        raise SealingError("Truncated file")
    aead = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    counter = 0
    chunk = source.read(CHUNK + TAG)
    while True:
        following = source.read(CHUNK + TAG) if len(chunk) == CHUNK + TAG else b""
        last = not following
        try:
            plain = aead.decrypt(_chunk_nonce(counter, last), chunk, None)
        except Exception:
            raise SealingError("Payload has been altered or truncated") from None
        if not plain and (counter or not last):
            raise SealingError("Empty final chunk")
        target.write(plain)
        if last:
            return
        chunk, counter = following, counter + 1


def rewrap_header(header: bytes, identity: bytes, add: bytes) -> bytes:
    """Add a recipient to a header using a key that already opens it. Tests and bench tools;
    the phone does this when restoring access from the recovery key."""
    from io import BytesIO

    _, stanzas = read_header(BytesIO(header))
    file_key = None
    for stanza in stanzas:
        lines = stanza.rstrip(b"\n").split(b"\n")
        body = b"".join(_unb64(x) for x in lines[1:] if x)
        file_key = file_key or _unwrap(lines[0][3:].split(b" "), body, identity)
    if file_key is None:
        raise SealingError("This key cannot open the file")
    mac_key = _hkdf(file_key, b"", b"header")
    old = INTRO + b"".join(stanzas) + b"---"
    old_mac = _unb64(header[len(old) + 1 : -1])
    if not hmac.compare_digest(hmac.new(mac_key, old, hashlib.sha256).digest(), old_mac):
        raise SealingError("Header has been altered")
    head = INTRO + b"".join(stanzas) + _wrap(file_key, add) + b"---"
    return head + b" " + _b64(hmac.new(mac_key, head, hashlib.sha256).digest()) + b"\n"


def open_bytes(data: bytes, identity: bytes) -> bytes:
    from io import BytesIO

    out = BytesIO()
    open_stream(BytesIO(data), out, identity)
    return out.getvalue()
