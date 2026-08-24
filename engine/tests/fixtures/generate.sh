#!/usr/bin/env bash
# Regenerate the media fixtures.
#
# The .mp4 files are a few MB each and are not committed -- they are pure
# ffmpeg output, so regenerating is cheaper and more honest than storing them.
# The .transcript.json sidecars ARE committed: they are hand-authored test data.
set -euo pipefail
cd "$(dirname "$0")"

ffmpeg -y -loglevel error \
  -f lavfi -i "testsrc2=size=1920x1080:rate=25:duration=7" \
  -f lavfi -i "sine=frequency=440:duration=7:sample_rate=48000" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -ac 2 -shortest \
  sample_25fps_1080p.mp4

ffmpeg -y -loglevel error \
  -f lavfi -i "testsrc2=size=1080x1920:rate=30000/1001:duration=4" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p -an \
  sample_2997_vertical_silent.mp4

ffmpeg -y -loglevel error \
  -f lavfi -i "sine=frequency=220:duration=5:sample_rate=48000" \
  -c:a aac -ac 1 sample_audio_only.m4a

echo "fixtures regenerated in $(pwd)"
