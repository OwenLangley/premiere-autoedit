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

# A 4-second click track at exactly 120 BPM. Beat detection needs a fixture with
# a KNOWN answer -- everything else in a beat test is self-referential.
python3 - <<'PYEOF'
import wave, struct, math
sr, bpm, dur = 44100, 120.0, 4.0
n, step = int(sr*dur), int(sr*60/120.0)
s = [0.0]*n
for i in range(0, n, step):
    for k in range(min(1200, n-i)):
        s[i+k] += math.sin(2*math.pi*1000*k/sr) * math.exp(-k/400.0)
with wave.open('sample_music.wav','w') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(b''.join(struct.pack('<h', int(max(-1,min(1,x))*20000)) for x in s))
PYEOF
