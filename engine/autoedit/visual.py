"""Visual analysis for footage with no usable speech.

The transcript path in `detect.py` cannot help with promos, b-roll or any silent
material: there is nothing to read. This module derives the same kind of decision
from the pictures instead -- where the shots are, and which of them are worth
keeping.

Everything here runs through ffmpeg. That is deliberate: it is already a hard
dependency, it is fast, and it avoids pulling OpenCV in for what amounts to three
scalar measurements per frame.

  motion     <- signalstats YDIF   (inter-frame luma difference)
  brightness <- signalstats YAVG   (average luma; catches black and crushed shots)
  sharpness  <- edgedetect + YAVG  (edge density; a blurred frame has few edges)
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .detect import KIND_SILENCE, CutPlan, Drop, Keep
from .music import BeatGrid
from .notes import note

_PTS = re.compile(r"pts_time:([0-9.]+)")
_KV = re.compile(r"lavfi\.(?:signalstats\.)?([A-Za-z_]+)=([-0-9.eE]+)")
_BLACK = re.compile(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([0-9.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([0-9.]+)")


class VisualError(RuntimeError):
    pass


@dataclass(frozen=True)
class Shot:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class FrameSample:
    time: float
    motion: float = 0.0
    brightness: float = 0.0
    sharpness: float = 0.0
    centre_sharpness: float = 0.0   # edge density inside the centre crop only


@dataclass
class ScoredShot:
    shot: Shot
    motion: float
    brightness: float
    sharpness: float
    score: float
    rejected: str | None = None      # why it was dropped, for the panel to show
    crop_risk: float = 0.0           # 0 = detail is centred, 1 = it is all at the edges

    @property
    def usable(self) -> bool:
        return self.rejected is None


@dataclass
class VisualSettings:
    """Recipe-tunable. Defaults lean towards keeping material: a promo assembly
    that quietly discards the one good shot is worse than one that keeps a dull
    one the editor can delete."""

    # Shot detection
    scene_threshold: float = 0.30    # ffmpeg scene score; lower finds more cuts
    min_shot: float = 0.50           # shorter detections are merged into neighbours
    max_shot: float = 6.0            # longer shots get subdivided
    sample_fps: float = 5.0          # analysis sampling rate, not output rate
    analysis_width: int = 640        # analyse downscaled; 4K is ~40x slower for no gain

    # Quality gates
    drop_black: bool = True
    min_brightness: float = 16.0     # YAVG 0-255; below this is effectively black
    max_brightness: float = 250.0    # blown out
    min_sharpness: float = 0.40      # edge density floor; below reads as soft
    min_motion: float = 0.05         # below this the frame is frozen
    max_motion: float = 30.0         # whip pans and camera shake

    # Trimming
    lead_trim: float = 0.15          # drop the head of a shot, where it settles
    tail_trim: float = 0.10

    # Selection
    shot_duration: float = 1.60      # nominal take length when there is no beat grid
    min_clip_length: float = 0.40
    target_duration: float | None = None   # stop once the cut reaches this length

    # Beats
    snap_to_beats: bool = True
    # Beats per shot. A float, because the editor's cut rate offers 0.5 --
    # twice per beat -- and an int silently rounded that to 1 or 0.
    beats_per_shot: float = 4        # one bar at 4/4
    # Calibrated against measured values rather than guessed. On the grid-fit
    # metric (see detect_beats), four real tracks scored 0.19, 0.27, 0.38 and
    # 0.47, while speech recordings with no beat at all scored 0.09 and 0.14.
    # The old 0.25 was tuned for a different metric entirely and rejected a track
    # whose beat the editor could hear perfectly well.
    min_beat_confidence: float = 0.15


def _normalise(settings: "VisualSettings") -> str:
    """Filter prefix applied before every measurement.

    `format=yuv420p` converts to 8-bit. Without it, 10-bit footage (which is what
    most modern cameras record) reports signalstats on a 0-1023 scale, so a
    normally-exposed shot reads as luma 359 and gets rejected as "blown out" --
    measured on real Sony HEVC footage, where it rejected every single shot.

    `scale` is purely for speed: these are three scalar averages per frame, and
    computing them at 4K took 2m38s for 19 seconds of footage.
    """
    return f"scale={settings.analysis_width}:-2:flags=fast_bilinear,format=yuv420p"


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise VisualError("ffmpeg not found on PATH (brew install ffmpeg)")
    return exe


def _run(args: list[str], timeout: int = 1800, hwaccel: bool = True) -> str:
    # VideoToolbox roughly halves CPU on HEVC, which is what modern cameras shoot.
    # Harmless when unavailable: ffmpeg falls back to software silently.
    prefix = ["-hwaccel", "videotoolbox"] if hwaccel else []
    result = subprocess.run(
        [_ffmpeg(), *prefix, *args], capture_output=True, text=True, timeout=timeout
    )
    # Filters split their output between the two streams: showinfo and
    # blackdetect log to stderr, while `metadata=print:file=-` writes to stdout.
    # Reading only one silently loses half the measurements.
    return (result.stdout or "") + "\n" + (result.stderr or "")


# ------------------------------------------------------------------ shots


@dataclass
class StructurePass:
    """Everything one decode of the file can tell us about its structure."""

    shots: list[Shot] = field(default_factory=list)
    black: list[Shot] = field(default_factory=list)
    frozen: list[Shot] = field(default_factory=list)


def detect_structure(path: str, duration: float, settings: VisualSettings) -> StructurePass:
    """Shot boundaries, black passages and frozen passages in a single pass.

    These were three separate ffmpeg invocations. Decoding is the entire cost --
    the filters themselves are trivial -- so running them as one chain cuts the
    work by two thirds. On 4K HEVC that is the difference between minutes and
    tens of seconds.
    """
    out = _run([
        "-i", str(path),
        "-filter:v",
        f"{_normalise(settings)},"
        f"blackdetect=d=0.1:pix_th=0.10,"
        f"freezedetect=n=-60dB:d=0.5,"
        f"select='gt(scene,{settings.scene_threshold})',showinfo",
        "-f", "null", "-",
    ])

    cuts = sorted({float(m) for m in _PTS.findall(out)})
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    shots = [Shot(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > 1e-6]
    shots = _split_long(_merge_short(shots, settings.min_shot), settings.max_shot)

    black = [Shot(float(a), float(b)) for a, b in _BLACK.findall(out)]
    starts = [float(x) for x in _FREEZE_START.findall(out)]
    ends = [float(x) for x in _FREEZE_END.findall(out)]
    frozen = [Shot(a, b) for a, b in zip(starts, ends)]

    return StructurePass(shots, black, frozen)


def detect_shots(path: str, duration: float, settings: VisualSettings) -> list[Shot]:
    """Shot boundaries only. Prefer detect_structure, which costs the same decode."""
    return detect_structure(path, duration, settings).shots


def _merge_short(shots: list[Shot], minimum: float) -> list[Shot]:
    """Fold sub-minimum shots into the previous one.

    A flash frame or a dissolve produces several tiny detections; treating each
    as a shot would fill the timeline with unusable fragments.
    """
    if not shots:
        return []
    out = [shots[0]]
    for s in shots[1:]:
        if s.duration < minimum:
            out[-1] = Shot(out[-1].start, s.end)
        else:
            out.append(s)
    if len(out) > 1 and out[0].duration < minimum:
        out[1] = Shot(out[0].start, out[1].end)
        out.pop(0)
    return out


def _split_long(shots: list[Shot], maximum: float) -> list[Shot]:
    """Subdivide long takes so selection has something to choose between."""
    out: list[Shot] = []
    for s in shots:
        if s.duration <= maximum:
            out.append(s)
            continue
        # ceil, not round: rounding down leaves pieces LONGER than the maximum
        # we were asked to enforce (a 20s shot at max 6s became 3 x 6.67s).
        pieces = max(2, math.ceil(s.duration / maximum - 1e-9))
        step = s.duration / pieces
        for i in range(pieces):
            out.append(Shot(s.start + i * step, s.start + (i + 1) * step))
    return out


# ------------------------------------------------------------------ frame stats


def analyse_frames(
    path: str, settings: VisualSettings, centre_ratio: float | None = None
) -> list[FrameSample]:
    """Per-sample motion, brightness and sharpness.

    Two ffmpeg passes, because edgedetect replaces the image and would corrupt
    the brightness reading if chained into the same signalstats.
    """
    fps = settings.sample_fps
    norm = _normalise(settings)
    base = _parse_metadata(_run([
        "-i", str(path),
        "-vf", f"fps={fps},{norm},signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]))
    edges = _parse_metadata(_run([
        "-i", str(path),
        "-vf", f"fps={fps},{norm},edgedetect=low=0.1:high=0.3,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ]))

    edge_by_time = {round(t, 3): vals.get("YAVG", 0.0) for t, vals in edges}

    # Edge density inside just the centre crop. Comparing it to the whole frame is
    # what tells us whether a centre crop would throw the subject away.
    centre_by_time: dict[float, float] = {}
    if centre_ratio and 0 < centre_ratio < 1:
        centre = _parse_metadata(_run([
            "-i", str(path),
            "-vf",
            f"fps={fps},{norm},crop=iw*{centre_ratio:.4f}:ih:(iw-iw*{centre_ratio:.4f})/2:0,"
            f"edgedetect=low=0.1:high=0.3,signalstats,metadata=print:file=-",
            "-f", "null", "-",
        ]))
        centre_by_time = {round(t, 3): vals.get("YAVG", 0.0) for t, vals in centre}

    samples: list[FrameSample] = []
    for t, vals in base:
        key = round(t, 3)
        samples.append(FrameSample(
            time=t,
            motion=vals.get("YDIF", 0.0),
            brightness=vals.get("YAVG", 0.0),
            # Normalised roughly to 0-10; raw edge luma is small and non-linear.
            sharpness=edge_by_time.get(key, 0.0),
            centre_sharpness=centre_by_time.get(key, 0.0),
        ))
    return samples


def _parse_metadata(stderr: str) -> list[tuple[float, dict[str, float]]]:
    """Parse `metadata=print` output into (pts_time, {KEY: value})."""
    out: list[tuple[float, dict[str, float]]] = []
    current_t: float | None = None
    current: dict[str, float] = {}
    for line in stderr.splitlines():
        pts = _PTS.search(line)
        if pts:
            if current_t is not None:
                out.append((current_t, current))
            current_t, current = float(pts.group(1)), {}
            continue
        for key, value in _KV.findall(line):
            try:
                current[key] = float(value)
            except ValueError:
                pass
    if current_t is not None:
        out.append((current_t, current))
    return out


def detect_black(path: str, min_duration: float = 0.1,
                 settings: "VisualSettings | None" = None) -> list[Shot]:
    settings = settings or VisualSettings()
    out = _run([
        "-i", str(path),
        "-vf", f"{_normalise(settings)},blackdetect=d={min_duration}:pix_th=0.10",
        "-f", "null", "-",
    ])
    return [Shot(float(a), float(b)) for a, b in _BLACK.findall(out)]


def detect_freeze(path: str, min_duration: float = 0.5,
                  settings: "VisualSettings | None" = None) -> list[Shot]:
    """Frozen/static passages -- a stalled camera or a held frame."""
    settings = settings or VisualSettings()
    out = _run([
        "-i", str(path),
        "-vf", f"{_normalise(settings)},freezedetect=n=-60dB:d={min_duration}",
        "-f", "null", "-",
    ])
    starts = [float(x) for x in _FREEZE_START.findall(out)]
    ends = [float(x) for x in _FREEZE_END.findall(out)]
    return [Shot(s, e) for s, e in zip(starts, ends)]


# ------------------------------------------------------------------ scoring


def _overlaps(a: Shot, b: Shot) -> bool:
    return a.start < b.end and b.start < a.end


def score_shots(
    shots: list[Shot],
    samples: list[FrameSample],
    settings: VisualSettings,
    black: list[Shot] | None = None,
    frozen: list[Shot] | None = None,
    centre_ratio: float = 0.0,
) -> list[ScoredShot]:
    """Average each shot's measurements and decide whether it is usable."""
    black = black or []
    frozen = frozen or []
    scored: list[ScoredShot] = []

    for shot in shots:
        inside = [s for s in samples if shot.start <= s.time < shot.end]
        if not inside:
            nearest = min(samples, key=lambda s: abs(s.time - shot.start), default=None)
            inside = [nearest] if nearest else []
        if not inside:
            scored.append(ScoredShot(shot, 0, 0, 0, 0.0, "no analysable frames"))
            continue

        motion = sum(s.motion for s in inside) / len(inside)
        brightness = sum(s.brightness for s in inside) / len(inside)
        sharpness = sum(s.sharpness for s in inside) / len(inside)

        reason: str | None = None
        if settings.drop_black and any(_overlaps(shot, b) for b in black):
            reason = "black frames"
        elif any(_overlaps(shot, f) for f in frozen):
            reason = "frozen frame"
        elif brightness < settings.min_brightness:
            reason = f"too dark (luma {brightness:.0f})"
        elif brightness > settings.max_brightness:
            reason = f"blown out (luma {brightness:.0f})"
        elif sharpness < settings.min_sharpness:
            reason = f"soft focus (edges {sharpness:.2f})"
        elif motion < settings.min_motion:
            reason = "static frame"
        elif motion > settings.max_motion:
            reason = f"camera shake (motion {motion:.0f})"

        # Favour sharp, well-exposed shots with some movement but not chaos.
        motion_fit = 1.0 - min(1.0, abs(motion - 4.0) / 12.0)
        exposure_fit = 1.0 - min(1.0, abs(brightness - 118.0) / 118.0)
        score = round(0.5 * min(1.0, sharpness / 3.0) + 0.3 * motion_fit + 0.2 * exposure_fit, 4)

        # How much of the frame's detail survives a centre crop. A shot whose
        # subject sits off to one side keeps far less edge energy than its share of
        # the area, and that is exactly the shot an editor needs to look at.
        crop_risk = 0.0
        if centre_ratio and sharpness > 0.01:
            centre = sum(s.centre_sharpness for s in inside) / len(inside)
            retained = centre / sharpness
            crop_risk = max(0.0, min(1.0, 1.0 - retained / centre_ratio))

        scored.append(ScoredShot(shot, round(motion, 3), round(brightness, 2),
                                 round(sharpness, 3), score, reason, round(crop_risk, 3)))
    return scored


@dataclass
class Measurements:
    """The expensive half of visual analysis: everything that needs a decode.

    Split out from scoring so it can be cached. Scoring depends on the quality
    thresholds, which an editor changes constantly while tuning pacing; decoding
    depends only on the file and how finely it is sampled. Re-deciding should not
    mean re-watching 4K footage for a minute.
    """

    shots: list[Shot] = field(default_factory=list)
    samples: list[FrameSample] = field(default_factory=list)
    black: list[Shot] = field(default_factory=list)
    frozen: list[Shot] = field(default_factory=list)
    centre_ratio: float = 0.0

    def to_dict(self) -> dict:
        span = lambda s: {"start": round(s.start, 4), "end": round(s.end, 4)}
        return {
            "shots": [span(s) for s in self.shots],
            "black": [span(s) for s in self.black],
            "frozen": [span(s) for s in self.frozen],
            "centre_ratio": self.centre_ratio,
            "samples": [
                {"t": round(f.time, 4), "m": round(f.motion, 4),
                 "b": round(f.brightness, 3), "s": round(f.sharpness, 4),
                 "c": round(f.centre_sharpness, 4)}
                for f in self.samples
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Measurements":
        span = lambda x: Shot(float(x["start"]), float(x["end"]))
        return cls(
            shots=[span(x) for x in d.get("shots", [])],
            black=[span(x) for x in d.get("black", [])],
            frozen=[span(x) for x in d.get("frozen", [])],
            samples=[
                FrameSample(float(x["t"]), float(x["m"]), float(x["b"]), float(x["s"]),
                            float(x.get("c", 0.0)))
                for x in d.get("samples", [])
            ],
            centre_ratio=float(d.get("centre_ratio", 0.0)),
        )


def measurement_key(settings: VisualSettings) -> dict:
    """Only the settings that change what gets DECODED belong in the cache key.

    Quality thresholds deliberately excluded: they affect scoring, which is
    recomputed on every run precisely so tuning them stays instant.
    """
    return {
        "scene_threshold": settings.scene_threshold,
        "min_shot": settings.min_shot,
        "max_shot": settings.max_shot,
        "sample_fps": settings.sample_fps,
        "analysis_width": settings.analysis_width,
    }


def measure(
    path: str, duration: float, settings: VisualSettings, centre_ratio: float | None = None
) -> Measurements:
    """Run every decode-bound measurement over one clip.

    `centre_ratio` is the fraction of the width a centre crop would keep. Pass it
    when the output is a different shape from the source, and each shot gains a
    crop-risk score.
    """
    structure = detect_structure(path, duration, settings)
    return Measurements(
        shots=structure.shots,
        samples=analyse_frames(path, settings, centre_ratio),
        black=structure.black,
        frozen=structure.frozen,
        centre_ratio=centre_ratio or 0.0,
    )


@dataclass
class VisualAnalysis:
    shots: list[ScoredShot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> list[ScoredShot]:
        return [s for s in self.shots if s.usable]


def analyse(
    path: str,
    duration: float,
    settings: VisualSettings | None = None,
    measurements: Measurements | None = None,
) -> VisualAnalysis:
    """Full visual pass over one clip.

    Pass `measurements` to skip the decode entirely and just re-score.
    """
    settings = settings or VisualSettings()
    warnings: list[str] = []

    measured = measurements or measure(path, duration, settings)
    if not measured.shots:
        return VisualAnalysis([], ["no shots detected"])

    if not measured.samples:
        warnings.append(note("visual.noSamples"))

    scored = score_shots(
        measured.shots, measured.samples, settings,
        measured.black if settings.drop_black else [],
        measured.frozen,
        measured.centre_ratio,
    )

    rejected = [s for s in scored if not s.usable]
    if rejected and len(rejected) == len(scored):
        warnings.append(note("visual.allShotsFailed"))
    elif rejected:
        warnings.append(note("visual.shotsRejected", rejected=len(rejected), total=len(scored)))

    return VisualAnalysis(scored, warnings)


# ------------------------------------------------------------------ cut planning


def plan_visual_cuts(
    analysis: VisualAnalysis,
    media_duration: float,
    settings: VisualSettings | None = None,
    beats: "BeatGrid | None" = None,
) -> CutPlan:
    """Turn scored shots into the same CutPlan the transcript path produces.

    This is the point of the contract-first design: the panel, the plan builder
    and the schema have no idea whether cuts came from speech or from pictures.

    With a beat grid, cut points land on beats and each take runs a whole number
    of beats -- which is how a music-led promo is actually cut. Without one, takes
    are a fixed nominal length from the recipe.
    """
    settings = settings or VisualSettings()
    warnings = list(analysis.warnings)
    keeps: list[Keep] = []
    drops: list[Drop] = []

    usable = [s for s in analysis.shots if s.usable]
    if not usable:
        return CutPlan([], [], warnings + ["no usable shots after quality filtering"])

    use_beats = bool(beats and settings.snap_to_beats and beats.beats)
    if beats and settings.snap_to_beats and not beats.beats:
        warnings.append(note("visual.noBeats"))
    if use_beats and beats.confidence < settings.min_beat_confidence:
        use_beats = False
        warnings.append(note(
            "visual.lowBeatConfidence",
            confidence=beats.confidence, threshold=settings.min_beat_confidence,
        ))

    take = settings.shot_duration
    if use_beats:
        take = beats.beat_interval * max(0.25, settings.beats_per_shot)

    # The requested shot length is the floor, whatever the recipe's minimum says.
    #
    # Otherwise a rate the editor chose can be rejected wholesale by a threshold
    # meant to catch fragments: at 153.8 BPM one beat is 0.390s, the recipes all
    # set min_clip_length to 0.40, and every shot in a six-clip job was dropped
    # as "too short" for being exactly the length that was asked for. The job
    # then failed with "every shot failed a quality gate", which was not true --
    # they had all passed.
    #
    # A shot cut to the beat is not a fragment. The minimum still guards
    # everything it was written for, because nothing else shortens a take below
    # it.
    minimum = settings.min_clip_length
    if use_beats and take < minimum:
        warnings.append(note(
            "visual.rateBelowMinimum", take=take, minimum=minimum))
        minimum = take

    total = 0.0
    for index, scored in enumerate(usable):
        shot = scored.shot
        start = shot.start + settings.lead_trim
        limit = shot.end - settings.tail_trim
        if limit - start < minimum:
            # Trimming ate the shot; use it whole rather than lose it.
            start, limit = shot.start, shot.end

        on_beat = False
        if use_beats:
            # Forward to the next beat: the nearest one is often just before the
            # shot's own first usable frame, and a cut cannot start before that.
            nxt = beats.next_beat(start)
            if nxt is not None and nxt < limit - minimum:
                start, on_beat = nxt, True

        end = min(start + take, limit)
        if end - start < minimum:
            drops.append(Drop(shot.start, shot.end, KIND_SILENCE, "shot too short after trimming"))
            continue

        reason = (
            f"shot {index + 1} of {len(usable)}, quality {scored.score:.2f}"
            + (f", on beat at {start:.2f}s" if on_beat else "")
        )
        keeps.append(Keep(start, end, reason, round(min(1.0, scored.score + 0.2), 4), 0))
        total += end - start

        if settings.target_duration and total >= settings.target_duration:
            remaining = len(usable) - index - 1
            if remaining:
                warnings.append(note(
                    "visual.targetReachedEarly",
                    seconds=settings.target_duration, remaining=remaining,
                ))
            break

    for scored in analysis.shots:
        if not scored.usable:
            drops.append(Drop(scored.shot.start, scored.shot.end, KIND_SILENCE,
                              scored.rejected or "rejected"))

    if not keeps:
        warnings.append(note("visual.nothingSurvived"))
    return CutPlan(keeps, drops, warnings)
