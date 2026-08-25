"""Editor-facing options, translated onto the engine's settings.

Everything here exists so an editor can say "30 seconds, vertical, punchier"
without meeting `min_silence` or `scene_threshold`. The recipes stay the source of
editorial truth: pacing SCALES their values rather than replacing them, so tuning a
recipe still moves every job that uses it.

The same translation serves the panel (via a job request) and the CLI (via flags),
so the two cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from .detect import CutPlan, DetectionSettings, Keep
from .visual import VisualSettings

Aspect = Literal["source", "landscape", "vertical", "square", "portrait45"]
Pacing = Literal["relaxed", "standard", "punchy"]
DurationMode = Literal["none", "upTo", "exactly", "about"]

# Frame sizes an editor can choose. 1080-based because that is what these are
# delivered at; the source is usually 4K and gets scaled down to fill.
ASPECT_FRAMES: dict[str, tuple[int, int]] = {
    "landscape": (1920, 1080),
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "portrait45": (1080, 1350),
}

ASPECT_LABELS: dict[str, str] = {
    "source": "Match source",
    "landscape": "Landscape 16:9",
    "vertical": "Vertical 9:16",
    "square": "Square 1:1",
    "portrait45": "Portrait 4:5",
}


@dataclass(frozen=True)
class PacingProfile:
    """Multipliers applied to a recipe's timing values."""

    label: str
    silence: float
    clip_length: float
    shot_length: float
    filler_mode: str | None = None   # None leaves the recipe's own choice alone
    # When cutting to music the beat interval is fixed by the track, so shot_length
    # does nothing. Pacing has to change how many BEATS each shot holds instead --
    # otherwise every pacing setting produces an identical cut, which is exactly
    # what happened the first time this was measured.
    beats: float = 1.0


PACING: dict[str, PacingProfile] = {
    "relaxed": PacingProfile("Relaxed", 1.4, 1.3, 1.3, "conservative", beats=2.0),
    "standard": PacingProfile("Standard", 1.0, 1.0, 1.0, None, beats=1.0),
    "punchy": PacingProfile("Punchy", 0.6, 0.7, 0.7, "aggressive", beats=0.5),
}


class OptionError(ValueError):
    pass


@dataclass
class JobOptions:
    aspect: str = "source"
    duration_mode: str = "none"
    duration_seconds: float | None = None
    duration_tolerance: float = 0.15
    pacing: str = "standard"
    look: str | None = None
    music: str = "auto"
    visual: bool = False
    model: str | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if self.aspect not in ASPECT_LABELS:
            raise OptionError(
                f"unknown aspect {self.aspect!r}; choose from {', '.join(ASPECT_LABELS)}"
            )
        if self.pacing not in PACING:
            raise OptionError(
                f"unknown pacing {self.pacing!r}; choose from {', '.join(PACING)}"
            )
        if self.duration_mode not in ("none", "upTo", "exactly", "about"):
            raise OptionError(f"unknown duration mode {self.duration_mode!r}")
        if self.duration_mode != "none" and not self.duration_seconds:
            raise OptionError(f"duration mode {self.duration_mode!r} needs a length in seconds")
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise OptionError("duration must be positive")

    @property
    def frame_size(self) -> tuple[int, int] | None:
        return ASPECT_FRAMES.get(self.aspect)

    @classmethod
    def from_request(cls, options: dict[str, Any] | None) -> "JobOptions":
        """Build from the `options` block of a job request."""
        options = options or {}
        duration = options.get("duration") or {}
        return cls(
            aspect=options.get("aspect", "source"),
            duration_mode=duration.get("mode", "none"),
            duration_seconds=duration.get("seconds"),
            duration_tolerance=duration.get("tolerance", 0.15),
            pacing=options.get("pacing", "standard"),
            look=options.get("look"),
            music=options.get("music", "auto"),
            visual=bool(options.get("visual", False)),
            model=options.get("model"),
            language=options.get("language"),
        )


# ------------------------------------------------------------------ pacing


def apply_pacing(
    detection: DetectionSettings, visual: VisualSettings, pacing: str
) -> tuple[DetectionSettings, VisualSettings]:
    """Scale a recipe's timing by the chosen pacing.

    Multiplying rather than overriding matters: a recipe tuned for podcasts and one
    tuned for social should still feel different at the same pacing setting.
    """
    profile = PACING[pacing]

    det = replace(
        detection,
        min_silence=round(detection.min_silence * profile.silence, 4),
        min_clip_length=round(detection.min_clip_length * profile.clip_length, 4),
        filler_mode=profile.filler_mode or detection.filler_mode,
    )
    # min_silence must still clear the handles, or every cut cancels itself.
    floor = det.lead_in + det.tail + 0.05
    if det.min_silence <= floor:
        det = replace(det, min_silence=round(floor, 4))

    vis = replace(
        visual,
        shot_duration=round(visual.shot_duration * profile.shot_length, 4),
        min_clip_length=round(visual.min_clip_length * profile.clip_length, 4),
        # At least one beat: a half-beat cut is a stutter, not a pace.
        beats_per_shot=max(1, round(visual.beats_per_shot * profile.beats)),
    )
    return det, vis


# ------------------------------------------------------------------ duration


def fit_duration(
    plan: CutPlan,
    mode: str,
    seconds: float | None,
    tolerance: float = 0.15,
    min_clip_length: float = 0.4,
    strategy: str = "tail",
) -> CutPlan:
    """Trim a cut to the requested length.

    Works on a CutPlan, so it serves the transcript and visual paths identically.

    `strategy` decides what gets dropped when there is too much:
      "tail"  -- drop from the end. Right for speech, where the edit is a narrative
                 and the opening matters most.
      "worst" -- drop the lowest-scoring clips. Right for b-roll, where shots are
                 interchangeable and quality is the only ordering that means anything.
    """
    if mode == "none" or not seconds:
        return plan

    keeps = list(plan.keeps)
    total = sum(k.duration for k in keeps)
    warnings = list(plan.warnings)

    if mode == "about":
        upper = seconds * (1 + tolerance)
        if total <= upper:
            if total < seconds * (1 - tolerance):
                warnings.append(
                    f"came out at {total:.0f}s against a target of about {seconds:.0f}s -- "
                    "there was not enough usable material to reach it"
                )
            return CutPlan(keeps, plan.drops, warnings)
        target = seconds
    else:
        target = seconds

    if total <= target:
        if mode in ("upTo", "exactly"):
            warnings.append(
                f"came out at {total:.0f}s against a {seconds:.0f}s target -- "
                "there was not enough usable material to fill it"
            )
        return CutPlan(keeps, plan.drops, warnings)

    if strategy == "worst":
        # Drop weakest first, then restore chronological order for the timeline.
        ordered = sorted(keeps, key=lambda k: k.confidence)
        while ordered and sum(k.duration for k in ordered) > target:
            ordered.pop(0)
        kept = sorted(ordered, key=lambda k: k.start)
    else:
        kept, running = [], 0.0
        for keep in keeps:
            if running + keep.duration > target:
                break
            kept.append(keep)
            running += keep.duration

    dropped = len(keeps) - len(kept)
    running = sum(k.duration for k in kept)

    if mode == "exactly" and kept:
        shortfall = target - running
        if shortfall > 0:
            # Extend the last clip if its source allows, otherwise accept short.
            last = kept[-1]
            kept[-1] = Keep(last.start, last.end + shortfall, last.reason,
                            last.confidence, last.word_count)
            running = target
        elif shortfall < 0:
            last = kept[-1]
            trimmed = last.duration + shortfall
            if trimmed >= min_clip_length:
                kept[-1] = Keep(last.start, last.start + trimmed, last.reason,
                                last.confidence, last.word_count)
                running = target

    if dropped:
        warnings.append(
            f"trimmed to {running:.0f}s for the {seconds:.0f}s target "
            f"({dropped} clip(s) dropped, {'lowest quality first' if strategy == 'worst' else 'from the end'})"
        )
    if not kept:
        warnings.append(
            f"nothing fits inside {seconds:.0f}s -- the shortest available clip is longer than the target"
        )

    return CutPlan(kept, plan.drops, warnings)


def fit_duration_across(
    plans: list[tuple[str, CutPlan]],
    mode: str,
    seconds: float | None,
    tolerance: float = 0.15,
    min_clip_length: float = 0.4,
    strategy: str = "tail",
) -> list[tuple[str, CutPlan]]:
    """Fit a duration target across every source in the job.

    A length applies to the finished sequence, not to each clip, so this has to
    see all of them at once. Splitting the target evenly between sources would be
    wrong: one file often carries most of the usable material.
    """
    if mode == "none" or not seconds or not plans:
        return plans

    flat = [(media_id, keep) for media_id, plan in plans for keep in plan.keeps]
    total = sum(k.duration for _, k in flat)

    if mode == "about" and total <= seconds * (1 + tolerance):
        if total < seconds * (1 - tolerance):
            plans[0][1].warnings.append(
                f"came out at {total:.0f}s against a target of about {seconds:.0f}s -- "
                "there was not enough usable material to reach it"
            )
        return plans

    if total <= seconds:
        if mode in ("upTo", "exactly"):
            plans[0][1].warnings.append(
                f"came out at {total:.0f}s against a {seconds:.0f}s target -- "
                "there was not enough usable material to fill it"
            )
        return plans

    trimmed_ids: dict[int, float] = {}

    if strategy == "worst":
        ranked = sorted(flat, key=lambda pair: pair[1].confidence)
        while ranked and sum(k.duration for _, k in ranked) > seconds:
            ranked.pop(0)
        surviving = set(id(k) for _, k in ranked)
    else:
        surviving, running = set(), 0.0
        for media_id, keep in flat:
            if running + keep.duration > seconds:
                break
            surviving.add(id(keep))
            running += keep.duration

    # Fill the remaining budget with a trimmed clip rather than leaving it unused.
    # Dropping only whole clips turned "up to 3s" into 1.6s, which is not what
    # anyone means by that.
    running = sum(k.duration for _, k in flat if id(k) in surviving)
    headroom = seconds - running
    if headroom >= min_clip_length:
        for _, keep in flat:
            if id(keep) in surviving:
                continue
            trimmed_ids[id(keep)] = headroom
            surviving.add(id(keep))
            running += headroom
            break

    dropped = len(flat) - len(surviving)
    out: list[tuple[str, CutPlan]] = []
    for media_id, plan in plans:
        kept = []
        for k in plan.keeps:
            if id(k) not in surviving:
                continue
            limit = trimmed_ids.get(id(k))
            kept.append(
                Keep(k.start, k.start + limit, k.reason, k.confidence, k.word_count)
                if limit else k
            )
        out.append((media_id, CutPlan(kept, plan.drops, list(plan.warnings))))

    running = sum(k.duration for _, p in out for k in p.keeps)

    if mode == "exactly" and running < seconds:
        for _, plan in reversed(out):
            if plan.keeps:
                last = plan.keeps[-1]
                plan.keeps[-1] = Keep(
                    last.start, last.end + (seconds - running),
                    last.reason, last.confidence, last.word_count,
                )
                running = seconds
                break

    if dropped:
        target_note = "lowest quality first" if strategy == "worst" else "from the end"
        fmt = lambda x: f"{x:.1f}" if x < 10 else f"{x:.0f}"
        for _, plan in out:
            if plan.keeps or plan.warnings:
                plan.warnings.append(
                    f"trimmed to {fmt(running)}s for the {fmt(seconds)}s target "
                    f"({dropped} clip(s) dropped, {target_note})"
                )
                break
    return out
