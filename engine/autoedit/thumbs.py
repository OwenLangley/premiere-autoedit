"""Stills for the shot list, so an editor can see what they are choosing.

A list of filenames and timecodes is not a way to pick a shot. An editor
recognises footage by looking at it, and the whole point of offering alternates
is that they can be compared at a glance -- which means the comparison has to be
visual or the feature is just a slower way to guess.

Kept next to `proxy.py` and keyed the same way, for the same reason: the engine
decides where a still lives and the helper builds it there. Two definitions of
that path would drift, and the failure would be silent -- files on disk that
nothing points at, and a panel showing empty boxes.

A still is cheap. One frame, scaled down, JPEG: a few tens of KB and a fraction
of a second, against minutes and gigabytes for a proxy. That difference is why
these are built for every span rather than only for footage that needs help.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

THUMB_DIR = "thumbs"
# Wide enough to recognise a shot on a 400px panel at two or three across, and
# still small enough that a hundred of them are a few MB. Height follows the
# source, so vertical rushes are not squashed into a landscape box.
THUMB_WIDTH = 320
THUMB_QUALITY = 4          # ffmpeg -q:v, 2 is best, 31 is worst


def thumb_path(work_dir: Path, source: Path, at_seconds: float) -> Path:
    """Where the still for one moment of one source lives.

    Keyed on the file's identity AND the timestamp: two spans of the same clip
    are different pictures, and a key that ignored the offset would give the
    whole clip one still and make every alternate look identical.
    """
    try:
        st = source.stat()
        stamp = f"{source}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        stamp = str(source)
    # The recipe is part of the identity, as with proxies. Changing the size or
    # quality below must produce new files rather than silently reusing the old
    # ones -- that is exactly how undersized proxies survived the fix meant to
    # replace them.
    stamp = f"{stamp}|{at_seconds:.3f}|v1|{THUMB_WIDTH}|q{THUMB_QUALITY}"
    key = hashlib.sha256(stamp.encode()).hexdigest()[:20]
    return work_dir / THUMB_DIR / f"{source.stem}_{key}.jpg"


def build_thumb(source: Path, dest: Path, at_seconds: float) -> bool:
    """Extract one frame. Returns True if the file is there afterwards."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if not shutil.which("ffmpeg"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        # -ss BEFORE -i seeks by keyframe, which is approximate but effectively
        # instant. For a thumbnail that is the right trade: landing on the
        # nearest keyframe instead of the exact frame changes the picture by a
        # fraction of a second, and seeking accurately would mean decoding from
        # the previous keyframe -- up to a second of 4K per still, times
        # hundreds of stills.
        "-ss", f"{max(0.0, at_seconds):.3f}",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale={THUMB_WIDTH}:-2",
        "-q:v", str(THUMB_QUALITY),
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        # A missing still is a row without a picture, not a broken job. Never
        # let this take an edit down.
        return False
    return dest.exists() and dest.stat().st_size > 0


def sample_point(start: float, end: float) -> float:
    """Which moment of a span to show.

    A shade after the start rather than the middle. The start is what the cut
    would actually open on, so it is the frame that answers "what will this look
    like" -- but the very first frame of a shot is often mid-transition or still
    settling, so this steps just inside it.
    """
    span = max(0.0, end - start)
    return start + min(0.35, span * 0.25)
