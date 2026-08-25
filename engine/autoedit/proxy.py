"""Proxy media, for footage that will not play back at speed.

Footage that cannot be decoded in real time makes the whole tool look broken in
a particularly misleading way: the edit is correct, the timeline is correct, and
the picture simply stops updating -- so what an editor sees is a freeze frame
where a cut should be, and the natural conclusion is that the tool put a still
there. It did not; the decoder ran out of road.

Measured on the footage that prompted this -- 4K 59.94p 10-bit 4:2:2 HEVC with a
keyframe once per second -- VideoToolbox managed 1.19x real time for decode
alone, before Premiere seeks, scales, composites or paints anything.

ProRes Proxy is what Premiere's own preset uses. It is all-intra, so every frame
is a keyframe and scrubbing costs nothing.

This lives in the engine rather than the helper so that both agree on where a
proxy is. Two definitions of that path would drift, and the failure would be
silent: the helper builds one, the plan points somewhere else, nothing attaches.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

PROXY_DIR = "proxies"
PROXY_HEIGHT = 1080


def proxy_path(work_dir: Path, source: Path) -> Path:
    """Where a source's proxy lives. Keyed by path, size and mtime, so replacing
    a file with a different take does not silently reuse the old proxy."""
    try:
        st = source.stat()
        stamp = f"{source}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        stamp = str(source)
    key = hashlib.sha256(stamp.encode()).hexdigest()[:20]
    return work_dir / PROXY_DIR / f"{source.stem}_{key}.mov"


def build_proxy(source: Path, dest: Path) -> bool:
    """Transcode one file to a 1080p ProRes Proxy. Returns True if it is there."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if not shutil.which("ffmpeg"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".partial.mov")
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-c:v", "prores_ks", "-profile:v", "0",       # 0 = Proxy
        "-vf", f"scale=-2:{PROXY_HEIGHT}",
        "-c:a", "pcm_s16le",
        str(partial),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  proxy: {source.name} failed: {exc}", file=sys.stderr)
        partial.unlink(missing_ok=True)
        return False
    if out.returncode != 0 or not partial.exists():
        print(f"  proxy: {source.name} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        partial.unlink(missing_ok=True)
        return False
    # Rename only once complete, so a killed helper never leaves a half file that
    # looks finished to the next run.
    partial.replace(dest)
    return True
