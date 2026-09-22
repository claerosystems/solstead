"""Minimal ONVIF event client: pull-point subscription for the camera's motion events.

Only the standard library is used. SOAP bodies are built by hand and parsed by local name
so the many prefix variations cameras use do not matter.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

Poster = Callable[[str, bytes, float], bytes]

SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
WSA = "http://www.w3.org/2005/08/addressing"
WSNT = "http://docs.oasis-open.org/wsn/b-2"
TEV = "http://www.onvif.org/ver10/events/wsdl"

CREATE_ACTION = (
    "http://www.onvif.org/ver10/events/wsdl/EventPortType/CreatePullPointSubscriptionRequest"
)
PULL_ACTION = "http://www.onvif.org/ver10/events/wsdl/PullPointSubscription/PullMessagesRequest"
RENEW_ACTION = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/RenewRequest"
UNSUBSCRIBE_ACTION = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"


class OnvifError(Exception):
    """The camera rejected a request or could not be reached. Never contains credentials."""


@dataclass(frozen=True)
class MotionEvent:
    topic: str
    at: float  # unix seconds
    active: bool
    operation: str = ""  # Initialized, Changed, Deleted


def default_poster(url: str, body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": 'application/soap+xml; charset=utf-8'},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        # ONVIF faults come back as HTTP 400/500 with a SOAP body worth reading.
        body = error.read()
        if body:
            return body
        raise OnvifError(f"Camera answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise OnvifError(f"Camera unreachable: {type(error).__name__}") from None


def username_token(user: str, password: str, now: float | None = None) -> str:
    """WS-Security UsernameToken with PasswordDigest, as ONVIF requires."""
    nonce = os.urandom(16)
    created = datetime.fromtimestamp(now or time.time(), UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest())
    return (
        f'<wsse:Security xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}"><wsse:UsernameToken>'
        f"<wsse:Username>{escape(user)}</wsse:Username>"
        f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest.decode()}</wsse:Password>'
        f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>"
    )


def envelope(action: str, to: str, body: str, security: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{SOAP_ENV}" xmlns:wsa="{WSA}">'
        f'<s:Header>{security}<wsa:Action s:mustUnderstand="1">{action}</wsa:Action>'
        f'<wsa:To s:mustUnderstand="1">{escape(to)}</wsa:To>'
        f"<wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>"
        f"<wsa:ReplyTo><wsa:Address>{WSA}/anonymous</wsa:Address></wsa:ReplyTo></s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_local(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if local(child.tag) == name:
            return child
    return None


def parse_utc(value: str) -> float:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return time.time()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def parse_fault(root: ET.Element) -> str | None:
    fault = find_local(root, "Fault")
    if fault is None:
        return None
    reason = find_local(fault, "Text")
    return (reason.text or "SOAP fault").strip() if reason is not None else "SOAP fault"


def parse_motion_events(xml: bytes, topics: tuple[str, ...] = ("Motion",)) -> list[MotionEvent]:
    """Extract motion state changes from a PullMessagesResponse.

    A notification counts when its topic contains one of `topics` and its data carries a boolean
    named IsMotion, State, or Value. The camera's UtcTime is used, falling back to now.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise OnvifError("Camera sent malformed XML") from None
    fault = parse_fault(root)
    if fault:
        raise OnvifError(f"Camera fault: {fault}")
    events: list[MotionEvent] = []
    for notification in root.iter():
        if local(notification.tag) != "NotificationMessage":
            continue
        topic_element = find_local(notification, "Topic")
        topic = (topic_element.text or "").strip() if topic_element is not None else ""
        if not any(wanted in topic for wanted in topics):
            continue
        message = find_local(notification, "Message")
        inner = None
        if message is not None:
            for child in message.iter():
                if local(child.tag) == "Message" and child is not message:
                    inner = child
                    break
        if inner is None:
            continue
        at = parse_utc(inner.get("UtcTime", ""))
        operation = inner.get("PropertyOperation", "")
        data = find_local(inner, "Data")
        if data is None:
            continue
        values = {
            item.get("Name", ""): item.get("Value", "")
            for item in data.iter()
            if local(item.tag) == "SimpleItem"
        }
        for key in ("IsMotion", "State", "Value"):
            if key in values:
                events.append(
                    MotionEvent(topic, at, values[key].strip().lower() == "true", operation)
                )
                break
    return events


def parse_subscription_address(xml: bytes) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise OnvifError("Camera sent malformed XML") from None
    fault = parse_fault(root)
    if fault:
        raise OnvifError(f"Camera fault: {fault}")
    reference = find_local(root, "SubscriptionReference")
    address = find_local(reference, "Address") if reference is not None else None
    if address is None or not (address.text or "").strip():
        raise OnvifError("Camera did not return a subscription address")
    return address.text.strip()


class PullPointClient:
    """One pull-point subscription. Call subscribe(), then pull() in a loop; renew() as needed."""

    def __init__(self, service_url: str, user: str, password: str, poster: Poster = default_poster,
                 topics: tuple[str, ...] = ("Motion",), termination_seconds: int = 60):  # fmt: skip
        self.service_url = service_url
        self.user = user
        self.password = password
        self.poster = poster
        self.topics = topics
        self.termination_seconds = termination_seconds
        self.subscription: str | None = None
        self.renew_deadline = 0.0

    def _call(self, action: str, to: str, body: str, timeout: float) -> bytes:
        security = username_token(self.user, self.password) if self.user else ""
        return self.poster(to, envelope(action, to, body, security), timeout)

    def subscribe(self) -> str:
        body = (
            f'<tev:CreatePullPointSubscription xmlns:tev="{TEV}">'
            f"<tev:InitialTerminationTime>PT{self.termination_seconds}S</tev:InitialTerminationTime>"
            "</tev:CreatePullPointSubscription>"
        )
        response = self._call(CREATE_ACTION, self.service_url, body, 15)
        self.subscription = parse_subscription_address(response)
        self.renew_deadline = time.time() + self.termination_seconds / 2
        return self.subscription

    def pull(self, timeout_seconds: int = 10, limit: int = 100) -> list[MotionEvent]:
        if not self.subscription:
            raise OnvifError("Not subscribed")
        body = (
            f'<tev:PullMessages xmlns:tev="{TEV}"><tev:Timeout>PT{timeout_seconds}S</tev:Timeout>'
            f"<tev:MessageLimit>{limit}</tev:MessageLimit></tev:PullMessages>"
        )
        response = self._call(PULL_ACTION, self.subscription, body, timeout_seconds + 10)
        return parse_motion_events(response, self.topics)

    def renew_if_due(self, now: float | None = None) -> bool:
        now = now or time.time()
        if not self.subscription or now < self.renew_deadline:
            return False
        body = (
            f'<wsnt:Renew xmlns:wsnt="{WSNT}"><wsnt:TerminationTime>PT{self.termination_seconds}S'
            "</wsnt:TerminationTime></wsnt:Renew>"
        )
        response = self._call(RENEW_ACTION, self.subscription, body, 15)
        fault = parse_fault(ET.fromstring(response)) if response.strip() else None
        if fault:
            raise OnvifError(f"Camera fault on renew: {fault}")
        self.renew_deadline = now + self.termination_seconds / 2
        return True

    def unsubscribe(self) -> None:
        if not self.subscription:
            return
        try:
            self._call(
                UNSUBSCRIBE_ACTION, self.subscription, f'<wsnt:Unsubscribe xmlns:wsnt="{WSNT}"/>', 5
            )
        except OnvifError:
            pass
        finally:
            self.subscription = None
