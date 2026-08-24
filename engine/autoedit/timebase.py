"""Frame-accurate time conversion.

Timeline positions are integer frames, never floats. A 200-cut assembly that
accumulates float error lands clips a frame off, which shows up as one-frame
gaps and overlaps scattered through the sequence -- the kind of defect an editor
finds at 2am, not in review.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import floor

# fps values that are actually rational, keyed by the number people say out loud.
COMMON_RATES: dict[str, tuple[int, int]] = {
    "23.976": (24000, 1001),
    "24":     (24, 1),
    "25":     (25, 1),
    "29.97":  (30000, 1001),
    "30":     (30, 1),
    "50":     (50, 1),
    "59.94":  (60000, 1001),
    "60":     (60, 1),
}


@dataclass(frozen=True)
class Timebase:
    """A frame rate as an exact rational, because 23.976 is not a number."""

    fps_num: int
    fps_den: int = 1
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if self.fps_num <= 0 or self.fps_den <= 0:
            raise ValueError(f"invalid frame rate {self.fps_num}/{self.fps_den}")

    @classmethod
    def from_float(cls, fps: float, drop_frame: bool = False) -> "Timebase":
        """Recover an exact rational from a float reported by ffprobe.

        ffprobe hands back 23.976023976023978; snapping to the nearest known
        broadcast rate is more trustworthy than rationalising the float.
        """
        for _, (num, den) in COMMON_RATES.items():
            if abs(fps - num / den) < 0.01:
                return cls(num, den, drop_frame)
        frac = Fraction(fps).limit_denominator(1001)
        return cls(frac.numerator, frac.denominator, drop_frame)

    @classmethod
    def parse(cls, spec: str, drop_frame: bool = False) -> "Timebase":
        """Parse '30000/1001', '23.976' or '25'."""
        spec = spec.strip()
        if "/" in spec:
            num, den = spec.split("/", 1)
            return cls(int(num), int(den), drop_frame)
        if spec in COMMON_RATES:
            num, den = COMMON_RATES[spec]
            return cls(num, den, drop_frame)
        return cls.from_float(float(spec), drop_frame)

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den

    def to_frames(self, seconds: float) -> int:
        """Seconds -> frame index, rounding to nearest to avoid a systematic
        one-frame-early bias that truncation would introduce."""
        return int(floor(seconds * self.fps_num / self.fps_den + 0.5))

    def to_seconds(self, frames: int) -> float:
        return frames * self.fps_den / self.fps_num

    def frame_duration(self) -> float:
        return self.fps_den / self.fps_num

    def snap(self, seconds: float) -> float:
        """Round a time to the nearest whole frame boundary."""
        return self.to_seconds(self.to_frames(seconds))

    def floor(self, seconds: float) -> float:
        """Largest whole frame boundary at or before `seconds`.

        Needed for out points at the end of a clip: `snap` rounds to nearest and
        can land a frame PAST the end of the media, which Premiere then refuses.
        """
        frames = int(seconds * self.fps_num / self.fps_den + 1e-9)
        return self.to_seconds(frames)

    def to_dict(self) -> dict:
        return {"fpsNum": self.fps_num, "fpsDen": self.fps_den, "dropFrame": self.drop_frame}

    @classmethod
    def from_dict(cls, d: dict) -> "Timebase":
        return cls(int(d["fpsNum"]), int(d.get("fpsDen", 1)), bool(d.get("dropFrame", False)))

    def timecode(self, frames: int) -> str:
        """Frames -> HH:MM:SS:FF. Implements SMPTE drop-frame when enabled."""
        nominal = int(round(self.fps))
        if self.drop_frame and nominal in (30, 60):
            drop = 2 if nominal == 30 else 4
            frames_per_10min = nominal * 600 - 9 * drop
            frames_per_1min = nominal * 60 - drop
            ten_blocks, rem = divmod(frames, frames_per_10min)
            frames += 9 * drop * ten_blocks
            if rem >= drop:
                frames += drop * ((rem - drop) // frames_per_1min)
        ff = frames % nominal
        total_s = frames // nominal
        ss, mm, hh = total_s % 60, (total_s // 60) % 60, total_s // 3600
        sep = ";" if self.drop_frame else ":"
        return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"

    def __str__(self) -> str:
        return f"{self.fps_num}/{self.fps_den} ({self.fps:.3f}fps{', DF' if self.drop_frame else ''})"
