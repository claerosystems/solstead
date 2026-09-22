import base64
import hashlib
import re
import xml.etree.ElementTree as ET

import pytest

from solstead.onvif import (
    MotionEvent,
    OnvifError,
    PullPointClient,
    envelope,
    parse_motion_events,
    parse_subscription_address,
    parse_utc,
    username_token,
)

PULL_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
  xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:tt="http://www.onvif.org/ver10/schema"
  xmlns:tev="http://www.onvif.org/ver10/events/wsdl" xmlns:tns1="http://www.onvif.org/ver10/topics">
<SOAP-ENV:Body><tev:PullMessagesResponse>
<tev:CurrentTime>2026-09-22T14:00:10Z</tev:CurrentTime>
<tev:TerminationTime>2026-09-22T14:01:00Z</tev:TerminationTime>
<wsnt:NotificationMessage>
  <wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>
  <wsnt:Message><tt:Message UtcTime="2026-09-22T14:00:05Z" PropertyOperation="Changed">
    <tt:Source><tt:SimpleItem Name="VideoSourceConfigurationToken" Value="vsconf"/></tt:Source>
    <tt:Data><tt:SimpleItem Name="IsMotion" Value="true"/></tt:Data>
  </tt:Message></wsnt:Message>
</wsnt:NotificationMessage>
<wsnt:NotificationMessage>
  <wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">tns1:VideoSource/MotionAlarm</wsnt:Topic>
  <wsnt:Message><tt:Message UtcTime="2026-09-22T14:00:09Z" PropertyOperation="Changed">
    <tt:Data><tt:SimpleItem Name="State" Value="false"/></tt:Data>
  </tt:Message></wsnt:Message>
</wsnt:NotificationMessage>
<wsnt:NotificationMessage>
  <wsnt:Topic>tns1:Device/Trigger/DigitalInput</wsnt:Topic>
  <wsnt:Message><tt:Message UtcTime="2026-09-22T14:00:09Z">
    <tt:Data><tt:SimpleItem Name="LogicalState" Value="true"/></tt:Data>
  </tt:Message></wsnt:Message>
</wsnt:NotificationMessage>
</tev:PullMessagesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

SUBSCRIBE_RESPONSE = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:tev="http://www.onvif.org/ver10/events/wsdl" xmlns:wsa="http://www.w3.org/2005/08/addressing">
<s:Body><tev:CreatePullPointSubscriptionResponse>
<tev:SubscriptionReference><wsa:Address>http://192.168.4.10:2020/onvif/Subscription?Idx=1</wsa:Address></tev:SubscriptionReference>
</tev:CreatePullPointSubscriptionResponse></s:Body></s:Envelope>"""

FAULT = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><s:Fault>
<s:Code><s:Value>s:Sender</s:Value></s:Code><s:Reason><s:Text xml:lang="en">Sender not authorized</s:Text></s:Reason>
</s:Fault></s:Body></s:Envelope>"""


def test_parse_motion_events_picks_motion_topics_only():
    events = parse_motion_events(PULL_RESPONSE)
    assert events == [
        MotionEvent(
            "tns1:RuleEngine/CellMotionDetector/Motion",
            parse_utc("2026-09-22T14:00:05Z"),
            True,
            "Changed",
        ),
        MotionEvent(
            "tns1:VideoSource/MotionAlarm", parse_utc("2026-09-22T14:00:09Z"), False, "Changed"
        ),
    ]


def test_parse_motion_events_with_custom_topic():
    events = parse_motion_events(PULL_RESPONSE, topics=("CellMotionDetector",))
    assert len(events) == 1 and events[0].active


def test_parse_utc():
    assert parse_utc("2026-09-22T14:00:05Z") == 1790085605.0
    assert parse_utc("2026-09-22T14:00:05.250+00:00") == 1790085605.25
    assert abs(parse_utc("garbage") - __import__("time").time()) < 5


def test_fault_and_garbage():
    with pytest.raises(OnvifError, match="not authorized"):
        parse_motion_events(FAULT)
    with pytest.raises(OnvifError, match="malformed"):
        parse_motion_events(b"<not xml")
    with pytest.raises(OnvifError, match="subscription address"):
        parse_subscription_address(b"<a/>")


def test_subscription_address():
    assert (
        parse_subscription_address(SUBSCRIBE_RESPONSE)
        == "http://192.168.4.10:2020/onvif/Subscription?Idx=1"
    )


def test_username_token_digest_is_valid():
    token = username_token("admin", "s3cret", now=1790085605)
    nonce = base64.b64decode(re.search(r"<wsse:Nonce[^>]*>([^<]+)", token).group(1))
    created = re.search(r"<wsu:Created>([^<]+)", token).group(1)
    digest = re.search(r"<wsse:Password[^>]*>([^<]+)", token).group(1)
    expected = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + b"s3cret").digest()
    ).decode()
    assert digest == expected and created == "2026-09-22T14:00:05.000Z"
    assert "s3cret" not in token


def test_envelope_is_well_formed_and_addressed():
    xml = envelope("urn:action", "http://cam/onvif", "<x/>", username_token("u", "p"))
    root = ET.fromstring(xml)
    text = xml.decode()
    assert "urn:action" in text and "http://cam/onvif" in text and root.tag.endswith("Envelope")


class FakeCamera:
    """Answers SOAP posts by action, recording each call."""

    def __init__(self):
        self.calls = []
        self.pull_responses = [PULL_RESPONSE]

    def __call__(self, url, body, timeout):
        text = body.decode()
        action = re.search(r"<wsa:Action[^>]*>([^<]+)", text).group(1).rsplit("/", 1)[-1]
        self.calls.append((url, action))
        if action == "CreatePullPointSubscriptionRequest":
            return SUBSCRIBE_RESPONSE
        if action == "PullMessagesRequest":
            return (
                self.pull_responses.pop(0)
                if self.pull_responses
                else PULL_RESPONSE.replace(b"NotificationMessage", b"Nothing")
            )
        if action == "RenewRequest":
            return b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body/></s:Envelope>'
        if action == "UnsubscribeRequest":
            return b""
        if action == "SetSystemDateAndTime":
            return b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body/></s:Envelope>'
        raise AssertionError(action)


def test_pull_point_client_flow():
    camera = FakeCamera()
    client = PullPointClient(
        "http://cam:2020/onvif/device_service", "admin", "pw", poster=camera, termination_seconds=60
    )
    with pytest.raises(OnvifError, match="Not subscribed"):
        client.pull()
    address = client.subscribe()
    assert address.startswith("http://192.168.4.10:2020/onvif/Subscription")
    events = client.pull()
    assert [e.active for e in events] == [True, False]
    assert client.renew_if_due(now=client.renew_deadline - 1) is False
    assert client.renew_if_due(now=client.renew_deadline + 1) is True
    client.unsubscribe()
    assert client.subscription is None
    assert [c[1] for c in camera.calls] == [
        "CreatePullPointSubscriptionRequest", "PullMessagesRequest", "RenewRequest", "UnsubscribeRequest",
    ]  # fmt: skip
    # Pull, renew and unsubscribe go to the subscription address, not the service URL.
    assert (
        camera.calls[1][0] == address
        and camera.calls[0][0] == "http://cam:2020/onvif/device_service"
    )


def test_set_camera_time_sends_utc_manual_time():
    from solstead.onvif import set_camera_time, set_time_body

    body = set_time_body(1790085605)  # 2026-09-22T14:00:05Z
    assert "<tds:DateTimeType>Manual</tds:DateTimeType>" in body
    assert "<tt:Hour>14</tt:Hour><tt:Minute>0</tt:Minute><tt:Second>5</tt:Second>" in body
    assert "<tt:Year>2026</tt:Year><tt:Month>9</tt:Month><tt:Day>22</tt:Day>" in body
    camera = FakeCamera()
    set_camera_time("http://cam/onvif", "u", "p", poster=camera, now=1790085605)
    assert camera.calls == [("http://cam/onvif", "SetSystemDateAndTime")]
    with pytest.raises(OnvifError, match="SetSystemDateAndTime"):
        set_camera_time("http://cam/onvif", "u", "p", poster=lambda *a: FAULT)
