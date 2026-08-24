"""ffprobe wrapper: what a media file actually is.

Camera files lie in interesting ways -- variable frame rate reported as a flat
rate, timecode buried in a stream-level tag, audio channel counts that disagree
with the container. Everything downstream depends on getting this right, so the
probe reports what it found rather than smoothing it over.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .timebase import Timebase

HASH_CHUNK = 4 * 1024 * 1024   # 4MiB from each end


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    duration: float
    has_video: bool = False
    has_audio: bool = False
    timebase: Timebase | None = None
    width: int = 0
    height: int = 0
    audio_channels: int = 0
    audio_rate: int = 0
    start_timecode: str | None = None
    variable_frame_rate: bool = False
    codec: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width > 0


def _ffprobe_bin() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ProbeError("ffprobe not found on PATH -- install ffmpeg (brew install ffmpeg)")
    return exe


def probe(path: str | Path) -> MediaInfo:
    """Read technical metadata from a media file."""
    p = Path(path)
    if not p.exists():
        raise ProbeError(f"no such file: {p}")

    out = subprocess.run(
        [_ffprobe_bin(), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(p)],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise ProbeError(f"ffprobe failed on {p.name}: {out.stderr.strip()[:400]}")

    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unparseable JSON for {p.name}: {exc}") from exc

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    info = MediaInfo(path=p, duration=float(fmt.get("duration") or 0.0))

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is not None:
        info.has_video = True
        info.codec = video.get("codec_name", "")
        info.width = int(video.get("width") or 0)
        info.height = int(video.get("height") or 0)

        avg, r = video.get("avg_frame_rate", "0/0"), video.get("r_frame_rate", "0/0")
        info.timebase = _rate(r) or _rate(avg)
        if info.timebase is None:
            info.warnings.append(f"{p.name}: no usable frame rate reported; assuming 25fps")
            info.timebase = Timebase(25)

        a_val, r_val = _rate_value(avg), _rate_value(r)
        if a_val and r_val and abs(a_val - r_val) > 0.01:
            info.variable_frame_rate = True
            info.warnings.append(
                f"{p.name}: variable frame rate ({a_val:.3f} avg vs {r_val:.3f} nominal). "
                "Transcode to a constant rate before cutting, or clips will drift out of sync."
            )
        if not info.duration:
            info.duration = float(video.get("duration") or 0.0)

    if audio is not None:
        info.has_audio = True
        info.audio_channels = int(audio.get("channels") or 0)
        info.audio_rate = int(audio.get("sample_rate") or 0)
        if not info.duration:
            info.duration = float(audio.get("duration") or 0.0)

    for source in (*streams, fmt):
        tc = (source.get("tags") or {}).get("timecode")
        if tc:
            info.start_timecode = tc
            break

    if not info.has_video and not info.has_audio:
        raise ProbeError(f"{p.name} contains no audio or video streams")
    if info.duration <= 0:
        raise ProbeError(f"{p.name} reports zero duration")
    if not info.has_audio:
        info.warnings.append(f"{p.name}: no audio track, so it cannot be transcript-cut")

    return info


def _rate_value(spec: str | None) -> float | None:
    if not spec or spec in ("0/0", "N/A"):
        return None
    try:
        num, den = spec.split("/")
        den_i = int(den)
        return int(num) / den_i if den_i else None
    except (ValueError, ZeroDivisionError):
        return None


def _rate(spec: str | None) -> Timebase | None:
    val = _rate_value(spec)
    return Timebase.from_float(val) if val and val > 0 else None


def content_hash(path: str | Path) -> str:
    """Cheap stable identity for a media file.

    Hashes size plus the first and last 4MiB. This is identity, not integrity --
    it exists so an EditPlan can be matched back to its source after the media
    moves from a local drive to the NAS, without reading 200GB of rushes.
    """
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with p.open("rb") as fh:
        h.update(fh.read(HASH_CHUNK))
        if size > HASH_CHUNK * 2:
            fh.seek(-HASH_CHUNK, 2)
            h.update(fh.read(HASH_CHUNK))
    return f"sha256:{h.hexdigest()[:32]}"
