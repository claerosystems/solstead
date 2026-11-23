#!/usr/bin/env bash
# Download the person/vehicle recognition model into DIR (default: beside config.json), checking
# its SHA-256. Safe to re-run: an intact copy is left alone. Without the model the controller
# still checks motion with its area and shadow filters, just without recognition.
#   controller/deploy/fetch-model.sh [DIR]
# Model: SSD MobileNet v1 (COCO), ONNX model zoo, Apache License 2.0.
set -euo pipefail
DIR="${1:-$HOME/.config/solstead}"
NAME=ssd_mobilenet_v1_12.onnx
URL="https://media.githubusercontent.com/media/onnx/models/main/validated/vision/object_detection_segmentation/ssd-mobilenetv1/model/$NAME"
SHA256=b8fba5e404077d4048d27fcd1667e85e27e192eb9bf51e696c46a3acd7d21058

mkdir -p "$DIR"
if [ -f "$DIR/$NAME" ] && echo "$SHA256  $DIR/$NAME" | sha256sum -c --status; then
  exit 0
fi
TMP="$DIR/$NAME.partial"
if ! curl -fsSL --retry 3 -o "$TMP" "$URL"; then
  rm -f "$TMP"
  echo "WARNING: could not download the recognition model; recognition stays off" >&2
  exit 0
fi
if ! echo "$SHA256  $TMP" | sha256sum -c --status; then
  rm -f "$TMP"
  echo "WARNING: recognition model checksum mismatch; not installed" >&2
  exit 0
fi
mv "$TMP" "$DIR/$NAME"
echo "Recognition model installed in $DIR"
