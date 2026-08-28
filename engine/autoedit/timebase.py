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


def holds_exactly(sequence: Timebase, source: Timebase) -> bool:
    """Can a whole number of `source` frames always be a whole number of
    `sequence` frames?

    This is the question that decides whether an assembly can be frame-accurate
    at all. A clip is chosen as a span of SOURCE frames and placed as a span of
    SEQUENCE frames; if the ratio between the two rates is not a whole number,
    some spans convert to a fractional count and somebody has to round. The plan
    rounds one way, Premiere rounds the other, and the clip lands a frame off --
    which reads on screen as a black flash between two shots.

    59.94 footage in a 59.94 sequence holds exactly (ratio 1), and in a 29.97
    sequence it does not (ratio 1/2, so any odd number of source frames is half
    a sequence frame). 29.97 footage in a 59.94 sequence holds exactly (ratio 2).
    59.94 footage in a 30.000 sequence -- the case that shipped -- has a ratio of
    1001/2000, which is exact for almost no span at all.
    """
    ratio = Fraction(sequence.fps_num * source.fps_den, sequence.fps_den * source.fps_num)
    return ratio.denominator == 1


# The fastest sequence Premiere will make from a generated preset.
#
# Every one of the 392 presets Adobe ships tops out at 60: 23.976, 24, 25,
# 29.97, 30, 48, 50, 59.94, 60 and nothing above. A colleague shooting 120fps
# got a 119.880 sequence preset, Premiere declined to create the sequence, and
# the build produced nothing at all -- no timeline, no receipt, no error an
# editor could act on.
#
# Nothing is lost by capping. 120fps footage is shot for slow motion and
# delivered at 30 or 60; a sequence faster than the delivery format buys an
# editor nothing and costs them a sequence that exists.
MAX_SEQUENCE_FPS = 60.0


def _within_reach(candidate: "Timebase") -> bool:
    return candidate.fps <= MAX_SEQUENCE_FPS + 1e-6


def choose_timebase(
    preferred: Timebase, sources: "list[Timebase]"
) -> "tuple[Timebase, Timebase | None]":
    """Pick the sequence frame rate, given what the footage actually is.

    The recipe states a rate because delivery specs are real -- a vertical short
    is cut at 30. But a rate the footage cannot land on exactly is not a delivery
    spec, it is a defect generator, and the footage is the fact here while the
    recipe is a preference.

    So: keep the recipe's rate when every source holds exactly in it, and
    otherwise take the rate that the most footage can live in. Returns the chosen
    timebase and, when it is not the recipe's, the rate that was displaced -- so
    the caller can say so rather than quietly changing the delivery format.
    """
    usable = [tb for tb in sources if tb is not None]
    # The cap is checked BEFORE this shortcut, or a 120fps recipe on 120fps
    # footage sails past it: everything holds exactly, and the result is a
    # sequence Premiere will not create.
    if _within_reach(preferred) and (
            not usable or all(holds_exactly(preferred, tb) for tb in usable)):
        return preferred, None

    # Candidates are the rates in play. Anything else would be inventing a third
    # rate that matches neither the recipe nor the footage -- except that a rate
    # Premiere cannot build is not a candidate at all, however well it fits.
    candidates = [c for c in [preferred] + list(dict.fromkeys(usable))
                  if _within_reach(c)]
    if not candidates:
        # Everything in play is too fast: 120fps footage cut to a 120fps recipe.
        # Halve the fastest until it is buildable, which keeps the relationship
        # to the source frames exact rather than inventing an unrelated rate.
        fastest = max(usable, key=lambda tb: tb.fps)
        num, den = fastest.fps_num, fastest.fps_den
        while num / den > MAX_SEQUENCE_FPS + 1e-6:
            den *= 2
        candidates = [Timebase(num, den, fastest.drop_frame)]

    def score(candidate: Timebase) -> tuple:
        held = sum(1 for tb in usable if holds_exactly(candidate, tb))
        # Ties go to the recipe's rate, then to the faster one: a 59.94 sequence
        # keeps every frame of 59.94 footage, where 29.97 throws half of them away.
        return (held, candidate == preferred, candidate.fps)

    best = max(candidates, key=score)
    return (best, None) if best == preferred else (best, preferred)
