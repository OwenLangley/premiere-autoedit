"""Editor-facing options, translated onto the engine's settings.

Everything here exists so an editor can say "30 seconds, vertical, punchier"
without meeting `min_silence` or `scene_threshold`. The recipes stay the source of
editorial truth: pacing SCALES their values rather than replacing them, so tuning a
recipe still moves every job that uses it.

The same translation serves the panel (via a job request) and the CLI (via flags),
so the two cannot drift apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Literal

from .notes import note, trimmed_key
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

# The long edge every generated sequence is built at. Each named aspect above is
# already 1080-class; "Match source" is the odd one out because it has no fixed
# size of its own, and taking the footage's gave a 4K 59.94 10-bit 4:2:2 sequence
# that does not play back -- the picture updates about once a second and reads as
# a series of stills. Editors work at HD and deliver at whatever they deliver at.
HD_LONG_EDGE = 1920


def working_frame_size(width: int, height: int) -> tuple[int, int]:
    """The source's shape, at a size that can actually be played.

    "Match source" is an aspect control -- the panel calls it 元の比率のまま,
    *keep the original ratio* -- so it fixes the shape and not the pixel count.
    Scaling the long edge to HD keeps the shape, plays back, and leaves 1080p
    material at native size instead of upscaling it 2x to fill a 4K frame.
    """
    longest = max(width, height)
    if longest <= HD_LONG_EDGE:
        return width, height
    scale = HD_LONG_EDGE / longest
    # Even dimensions: odd ones break chroma subsampling in most codecs, and
    # Premiere quietly rounds them anyway.
    even = lambda n: max(2, int(round(n / 2)) * 2)
    return even(width * scale), even(height * scale)


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


# A couple of frames at 59.94. Spans handed to the beat quantiser carry this on
# top of their whole-beat length, so that snapping the source points to the frame
# grid cannot leave them a frame short -- which would cost a whole beat.
BEAT_SLACK = 0.04


# How often the picture may change, in beats, when the music leads. Offered as a
# short list rather than a free number: these are the musically meaningful
# values, and a dropdown cannot be typed wrong. `None` follows the pacing
# setting, which is what the recipe intended.
CUT_RATES: list[tuple[int, str]] = [
    (1, "Every beat"),
    (2, "Every 2 beats"),
    (4, "Every bar"),
    (8, "Every 2 bars"),
]
VALID_CUT_RATES = frozenset(n for n, _ in CUT_RATES)


class OptionError(ValueError):
    pass


@dataclass
class JobOptions:
    aspect: str = "source"
    duration_mode: str = "none"
    duration_seconds: float | None = None
    duration_tolerance: float = 0.15
    pacing: str = "standard"
    # Beats per shot when the music leads. None follows the pacing setting; a
    # number is the editor overruling it for this job.
    cut_rate: int | None = None
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
        if self.cut_rate is not None and self.cut_rate not in VALID_CUT_RATES:
            raise OptionError(
                f"cut rate must be one of {sorted(VALID_CUT_RATES)} beats, "
                f"got {self.cut_rate}"
            )

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
            cut_rate=options.get("cutRate") or None,
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
                warnings.append(note("length.shortOfAbout", total=total, seconds=seconds))
            return CutPlan(keeps, plan.drops, warnings)
        target = seconds
    else:
        target = seconds

    if total <= target:
        if mode in ("upTo", "exactly"):
            warnings.append(
                note("length.shortOfTarget", total=total, seconds=seconds)
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
        warnings.append(note(
            trimmed_key(strategy),
            running=f"{running:.0f}", seconds=f"{seconds:.0f}", dropped=dropped,
        ))
    if not kept:
        warnings.append(note("length.nothingFits", seconds=seconds))

    return CutPlan(kept, plan.drops, warnings)


def _spread_across_sources(
    plans: "list[tuple[str, CutPlan]]", seconds: float, min_clip_length: float,
    max_shot: float | None = None, quantum: float | None = None,
    exact_shot: bool = False,
) -> "tuple[set[int], dict[int, float]]":
    """Give every source a share of the target, so all of them appear.

    The tail strategy walked the flattened keeps and stopped when the budget ran
    out, which meant a 15s target was filled entirely from the FIRST file and the
    other five were dropped. An editor who selected six clips did not mean "use
    the first two" -- and the sparse result was the same complaint from the other
    side: fewer sources means fewer, longer shots.

    Each source gets an equal share and, if its first surviving span is longer
    than that share, the span is TRIMMED rather than skipped -- otherwise a file
    whose first sentence runs long contributes nothing at all. Whatever is left
    over is then handed round again, in order, so a short source does not waste
    the budget it could not use.
    """
    ordered = [(mid, list(plan.keeps)) for mid, plan in plans if plan.keeps]
    surviving: set[int] = set()
    trimmed: dict[int, float] = {}
    if not ordered:
        return surviving, trimmed

    share = seconds / len(ordered)
    # Spend the share on even shots rather than max-length ones plus a stub.
    # Capping at max_shot alone gave a 5.3-beat share a 4-beat shot and a 1-beat
    # remainder, which reads as a limp rather than a rhythm; two 2.6-beat shots
    # sit on the grid and feel deliberate.
    if max_shot and max_shot > 0 and exact_shot:
        # The editor named a rate. Use it as written and let the remainder of a
        # share become a shorter final shot -- a pickup, which is musically
        # ordinary. Dividing evenly instead turned "every bar" into 2.3-beat
        # shots, so the control did not do what its label said.
        max_shot = max_shot + (BEAT_SLACK if quantum else 0.0)
    elif max_shot and max_shot > 0:
        per_source_shots = max(1, math.ceil(share / max_shot - 1e-9))
        max_shot = share / per_source_shots
        if quantum and quantum > 0:
            # Whole beats, plus a couple of frames.
            #
            # An even division of the share lands on fractions -- 1.77 beats, say
            # -- and the beat quantiser downstream can only place whole ones. With
            # the span trimmed to exactly 1.77 there is nothing to round UP into,
            # so every shot rounded down and a 15s target came out at 7.6s.
            #
            # Snapping to a whole 2.0 is still not quite enough: the source points
            # get snapped to the source frame grid later, and a span that ends up
            # a single frame short makes the quantiser drop a WHOLE beat. The
            # slack is what stops a one-frame error costing half a second.
            max_shot = max(quantum, round(max_shot / quantum) * quantum) + BEAT_SLACK
    used = 0.0

    for _, keeps in ordered:
        taken = 0.0
        for k in keeps:
            # `max_shot` caps any single shot, so a source spends its share over
            # several spans instead of one long one. Those spans come from
            # different moments in the clip -- the silences between them were
            # removed -- so each join is a real, visible cut rather than an
            # invisible join in continuous footage.
            room = share - taken
            if max_shot:
                room = min(room, max_shot)
            if room < min_clip_length:
                break
            if k.duration <= room:
                surviving.add(id(k))
                taken += k.duration
            else:
                # Trim rather than skip: a source whose first span overruns its
                # share would otherwise never appear at all.
                trimmed[id(k)] = room
                surviving.add(id(k))
                taken += room
            if share - taken < min_clip_length:
                break
        used += taken

    # Hand back what the short sources could not use. This pass TRIMS as well as
    # takes: every remaining span was longer than the leftover budget, so a
    # whole-spans-only pass took nothing at all and a 15s target landed at 11.9s.
    remaining = seconds - used
    progressed = True
    while remaining >= min_clip_length and progressed:
        progressed = False
        for _, keeps in ordered:
            if remaining < min_clip_length:
                break
            for k in keeps:
                if id(k) in surviving:
                    continue
                room = min(remaining, max_shot) if max_shot else remaining
                take = min(k.duration, room)
                if quantum and quantum > 0:
                    # Whole beats plus slack, for the same reason as above.
                    whole = math.floor(take / quantum + 1e-9) * quantum
                    take = min(k.duration, whole + BEAT_SLACK) if whole else take
                if take >= min_clip_length:
                    surviving.add(id(k))
                    if take < k.duration - 1e-9:
                        trimmed[id(k)] = take
                    remaining -= take
                    progressed = True
                break   # only ever the next span in order, never a later one
    return surviving, trimmed


def fit_duration_across(
    plans: list[tuple[str, CutPlan]],
    mode: str,
    seconds: float | None,
    tolerance: float = 0.15,
    min_clip_length: float = 0.4,
    strategy: str = "tail",
    max_shot: float | None = None,
    quantum: float | None = None,
    exact_shot: bool = False,
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
                note("length.shortOfAbout", total=total, seconds=seconds)
            )
        return plans

    if total <= seconds:
        if mode in ("upTo", "exactly"):
            plans[0][1].warnings.append(
                note("length.shortOfTarget", total=total, seconds=seconds)
            )
        return plans

    trimmed_ids: dict[int, float] = {}

    if strategy == "worst":
        ranked = sorted(flat, key=lambda pair: pair[1].confidence)
        while ranked and sum(k.duration for _, k in ranked) > seconds:
            ranked.pop(0)
        surviving = set(id(k) for _, k in ranked)
    elif strategy == "spread":
        surviving, trimmed_ids = _spread_across_sources(
            plans, seconds, min_clip_length, max_shot, quantum, exact_shot
        )
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
    running = sum(
        trimmed_ids.get(id(k), k.duration) for _, k in flat if id(k) in surviving
    )
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
        fmt = lambda x: f"{x:.1f}" if x < 10 else f"{x:.0f}"
        for _, plan in out:
            if plan.keeps or plan.warnings:
                plan.warnings.append(note(
                    trimmed_key(strategy),
                    running=fmt(running), seconds=fmt(seconds), dropped=dropped,
                ))
                break
    return out
