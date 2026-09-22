"""The public keys recordings are sealed to, enrolled by the owner's phone.

Stored beside config.json on the controller's own card, never on the recording SSD. Holding
these gives no ability to decrypt anything; they only decide who can.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .sealing import SealingError, parse_recipient

ROLES = ("device", "recovery")
MAX_RECIPIENTS = 8
MAX_LABEL = 60


class RecipientsError(ValueError):
    pass


@dataclass(frozen=True)
class Recipient:
    role: str  # "device": a phone holding its key in its Keychain; "recovery": the printed key
    label: str
    public_key: str  # age1…
    added_unix: int


def parse(payload: object, now: float | None = None) -> list[Recipient]:
    """Validate an enrolment: at least one device key and exactly one recovery key."""
    items = payload.get("recipients") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not 1 < len(items) <= MAX_RECIPIENTS:
        raise RecipientsError(f"recipients must be a list of 2 to {MAX_RECIPIENTS} keys")
    added = int(time.time() if now is None else now)
    found: list[Recipient] = []
    for item in items:
        if not isinstance(item, dict):
            raise RecipientsError("Each recipient must be an object")
        role, label, key = item.get("role"), item.get("label", ""), item.get("public_key")
        if role not in ROLES:
            raise RecipientsError("role must be 'device' or 'recovery'")
        if not isinstance(label, str) or len(label) > MAX_LABEL:
            raise RecipientsError(f"label must be text of at most {MAX_LABEL} characters")
        if not isinstance(key, str):
            raise RecipientsError("public_key must be an age1… string")
        try:
            parse_recipient(key)
        except SealingError as error:
            raise RecipientsError(str(error)) from None
        found.append(Recipient(role, label.strip(), key.strip().lower(), added))
    if len({r.public_key for r in found}) != len(found):
        raise RecipientsError("The same key appears twice")
    if sum(r.role == "recovery" for r in found) != 1:
        raise RecipientsError("Exactly one recovery key is required")
    if not any(r.role == "device" for r in found):
        raise RecipientsError("At least one device key is required")
    return found


class RecipientStore:
    def __init__(self, path: Path | None):
        self.path = path
        self.recipients: list[Recipient] = self._load()

    def _load(self) -> list[Recipient]:
        if not self.path or not self.path.is_file():
            return []
        # A damaged file must not silently turn encryption off, so let errors propagate.
        data = json.loads(self.path.read_text())
        return [Recipient(**item) for item in data["recipients"]]

    @property
    def enrolled(self) -> bool:
        return bool(self.recipients)

    def keys(self) -> list[bytes]:
        return [parse_recipient(r.public_key) for r in self.recipients]

    def replace(self, recipients: list[Recipient]) -> None:
        if not self.path:
            raise RecipientsError("This controller has nowhere to store keys")
        partial = self.path.with_name(self.path.name + ".partial")
        partial.write_text(json.dumps({"recipients": [asdict(r) for r in recipients]}, indent=2))
        with partial.open("rb") as file:
            os.fsync(file.fileno())
        partial.replace(self.path)
        self.recipients = list(recipients)

    def status(self) -> dict:
        return {
            "enrolled": self.enrolled,
            "format": "age-encryption.org/v1",
            "recipients": [asdict(r) for r in self.recipients],
        }
