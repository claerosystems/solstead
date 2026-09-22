#!/usr/bin/env python3
"""Show or change a camera's video encoder settings over ONVIF (Media 1.0).

    python3 tools/camera_encoder.py MEDIA_URL USER PASSWORD                 # list configurations
    python3 tools/camera_encoder.py MEDIA_URL USER PASSWORD --token TOKEN --gov-length 50 [--fps 25] [--bitrate 4096]

The controller cuts clips at keyframes, so a short GOP (GovLength, in frames) makes the
pre-roll and segment boundaries precise. 50 frames at 25 fps is a keyframe every 2 seconds.
"""

from __future__ import annotations

import argparse
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solstead.onvif import (  # noqa: E402
    OnvifError,
    default_poster,
    envelope,
    find_local,
    local,
    parse_fault,
    username_token,
)

MEDIA = "http://www.onvif.org/ver10/media/wsdl"
SCHEMA = "http://www.onvif.org/ver10/schema"
ET.register_namespace("tt", SCHEMA)
ET.register_namespace("trt", MEDIA)


def call(url: str, user: str, password: str, name: str, body_xml: str = "") -> ET.Element:
    body = f'<trt:{name} xmlns:trt="{MEDIA}">{body_xml}</trt:{name}>'
    response = default_poster(
        url, envelope(f"{MEDIA}/{name}", url, body, username_token(user, password)), 15
    )
    root = ET.fromstring(response)
    fault = parse_fault(root)
    if fault:
        raise OnvifError(f"{name}: {fault}")
    return root


def text(element: ET.Element | None, name: str) -> str:
    found = find_local(element, name) if element is not None else None
    return (found.text or "").strip() if found is not None else ""


def describe(config: ET.Element) -> str:
    resolution = find_local(config, "Resolution")
    rate = find_local(config, "RateControl")
    h264 = find_local(config, "H264")
    return (
        f"{config.get('token'):14} {text(config, 'Name'):14} {text(config, 'Encoding'):5} "
        f"{text(resolution, 'Width')}x{text(resolution, 'Height'):6} "
        f"{text(rate, 'FrameRateLimit'):>3} fps  {text(rate, 'BitrateLimit'):>6} kbit/s  "
        f"GOP {text(h264, 'GovLength'):>4} frames  quality {text(config, 'Quality')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("media_url")
    parser.add_argument("user")
    parser.add_argument("password")
    parser.add_argument("--token", help="configuration token to change")
    parser.add_argument("--gov-length", type=int)
    parser.add_argument("--fps", type=int)
    parser.add_argument("--bitrate", type=int, help="kbit/s")
    args = parser.parse_args()

    root = call(args.media_url, args.user, args.password, "GetVideoEncoderConfigurations")
    configs = [c for c in root.iter() if local(c.tag) == "Configurations"]
    print("== Video encoder configurations ==")
    for config in configs:
        print("  " + describe(config))
    if not args.token:
        return 0

    chosen = next((c for c in configs if c.get("token") == args.token), None)
    if chosen is None:
        print(f"no configuration with token {args.token}", file=sys.stderr)
        return 1
    updated = copy.deepcopy(chosen)
    updated.tag = f"{{{MEDIA}}}Configuration"
    if args.gov_length is not None:
        find_local(find_local(updated, "H264"), "GovLength").text = str(args.gov_length)
    if args.fps is not None:
        find_local(find_local(updated, "RateControl"), "FrameRateLimit").text = str(args.fps)
    if args.bitrate is not None:
        find_local(find_local(updated, "RateControl"), "BitrateLimit").text = str(args.bitrate)
    body = (
        ET.tostring(updated, encoding="unicode")
        + f'<trt:ForcePersistence xmlns:trt="{MEDIA}">true</trt:ForcePersistence>'
    )
    call(args.media_url, args.user, args.password, "SetVideoEncoderConfiguration", body)
    print("== After ==")
    root = call(args.media_url, args.user, args.password, "GetVideoEncoderConfigurations")
    for config in (c for c in root.iter() if local(c.tag) == "Configurations"):
        if config.get("token") == args.token:
            print("  " + describe(config))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OnvifError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
