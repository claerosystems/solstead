#!/usr/bin/env python3
"""Discover what an ONVIF camera offers before configuring the controller.

    python3 tools/probe_camera.py http://192.168.4.10:2020/onvif/device_service USER PASSWORD [--pull 30]

Prints device information, media profiles with their RTSP stream URIs, the event topics the camera
advertises, and (with --pull) live notifications for N seconds so you can wave at the camera and
see which topic and data item report motion. Stdlib only, so it runs on the Pi.
"""

from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solstead.onvif import (  # noqa: E402
    OnvifError,
    PullPointClient,
    default_poster,
    envelope,
    find_local,
    local,
    parse_fault,
    username_token,
)

DEVICE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA = "http://www.onvif.org/ver10/media/wsdl"
EVENTS = "http://www.onvif.org/ver10/events/wsdl"


def call(
    url: str, user: str, password: str, namespace: str, name: str, body_xml: str = ""
) -> ET.Element:
    action = f"{namespace}/{name}"
    body = f'<tns:{name} xmlns:tns="{namespace}">{body_xml}</tns:{name}>'
    response = default_poster(url, envelope(action, url, body, username_token(user, password)), 15)
    root = ET.fromstring(response)
    fault = parse_fault(root)
    if fault:
        raise OnvifError(f"{name}: {fault}")
    return root


def text(element: ET.Element | None, name: str) -> str:
    found = find_local(element, name) if element is not None else None
    return (found.text or "").strip() if found is not None else ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("service_url")
    parser.add_argument("user")
    parser.add_argument("password")
    parser.add_argument("--pull", type=int, default=0, help="seconds to watch live notifications")
    args = parser.parse_args()
    url, user, password = args.service_url, args.user, args.password

    print("== Device ==")
    try:
        info = call(url, user, password, DEVICE, "GetDeviceInformation")
        for key in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"):
            print(f"  {key:16} {text(info, key)}")
    except OnvifError as error:
        print(f"  failed: {error}")

    media_url = url
    events_url = url
    try:
        services = call(
            url,
            user,
            password,
            DEVICE,
            "GetServices",
            "<tns:IncludeCapability>false</tns:IncludeCapability>",
        )
        print("== Services ==")
        for service in services.iter():
            if local(service.tag) == "Service":
                namespace, address = text(service, "Namespace"), text(service, "XAddr")
                print(f"  {namespace:50} {address}")
                if namespace == MEDIA:
                    media_url = address
                if namespace == EVENTS:
                    events_url = address
    except OnvifError as error:
        print(f"  GetServices failed: {error}")

    print("== Media profiles ==")
    try:
        profiles = call(media_url, user, password, MEDIA, "GetProfiles")
        for profile in profiles.iter():
            if local(profile.tag) != "Profiles":
                continue
            token = profile.get("token", "")
            video = find_local(profile, "VideoEncoderConfiguration")
            encoding = text(video, "Encoding")
            resolution = find_local(video, "Resolution") if video is not None else None
            size = (
                f"{text(resolution, 'Width')}x{text(resolution, 'Height')}"
                if resolution is not None
                else "?"
            )
            rate = (
                text(find_local(video, "RateControl"), "FrameRateLimit")
                if video is not None
                else ""
            )
            try:
                uri_response = call(
                    media_url, user, password, MEDIA, "GetStreamUri",
                    '<tns:StreamSetup><tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
                    '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema"><tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
                    f"</tns:StreamSetup><tns:ProfileToken>{token}</tns:ProfileToken>",
                )  # fmt: skip
                uri = text(uri_response, "Uri")
            except OnvifError as error:
                uri = f"(GetStreamUri failed: {error})"
            print(
                f"  {text(profile, 'Name') or token:20} {encoding:6} {size:10} {rate:>3} fps  {uri}"
            )
    except OnvifError as error:
        print(f"  GetProfiles failed: {error}")

    print("== Event topics ==")
    try:
        props = call(events_url, user, password, EVENTS, "GetEventProperties")
        topic_set = find_local(props, "TopicSet")

        def walk(element: ET.Element, path: list[str]) -> None:
            for child in element:
                name = local(child.tag)
                if name == "MessageDescription":
                    items = [
                        f"{i.get('Name')}:{i.get('Type', '').rsplit(':', 1)[-1]}"
                        for i in child.iter()
                        if local(i.tag) == "SimpleItemDescription"
                    ]
                    print(f"  {'/'.join(path):55} {' '.join(items)}")
                elif child.get("topic") == "true" or len(child):
                    walk(child, path + [name])

        if topic_set is not None:
            walk(topic_set, [])
        else:
            print("  (no TopicSet returned)")
    except OnvifError as error:
        print(f"  GetEventProperties failed: {error}")

    if args.pull:
        print(f"== Live notifications for {args.pull}s (all topics) ==")
        client = PullPointClient(events_url, user, password, topics=("",))
        try:
            client.subscribe()
            deadline = time.time() + args.pull
            while time.time() < deadline:
                client.renew_if_due()
                for event in client.pull(5):
                    print(
                        f"  {time.strftime('%H:%M:%S', time.localtime(event.at))} {event.topic:55} active={event.active} ({event.operation})"
                    )
        except OnvifError as error:
            print(f"  failed: {error}")
        finally:
            client.unsubscribe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
