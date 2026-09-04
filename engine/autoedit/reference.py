"""Measuring a reference video, so an edit can be cut to match it.

"Cut it like this one" is how an editorial brief usually arrives -- a TikTok, a
client's last promo, a competitor's ad. This reads such a video and takes three
things off it:

* **its rhythm** -- how many shots, how long each one runs, in what order;
* **what each shot looks like** -- one still per shot, embedded, so the editor's
  own footage can be matched to it;
* **its shape and length** -- vertical or wide, and how long the whole thing is.

**The reference never appears in the output.** It is measured and discarded, and
only its structure survives into the edit. That is the difference between a
reference and a source.

Almost nothing here is new. `visual.measure`/`analyse` already find shots in any
file, `thumbs` already makes a still per span, and `describe` already embeds
one. The work is turning what they return into the `Beat`s that
`story.build_story_plans` already knows how to lay out -- and `Beat.weight`,
which has been threaded end to end since the story feature shipped and has been
1.0 every time, is exactly the per-shot share of runtime a reference implies.
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass, field
from pathlib import Path

from .probe import ProbeError, content_hash, probe
from .story import Beat
from .thumbs import build_thumb, sample_point, thumb_path
from .visual import (
    Measurements, VisualError, VisualSettings, analyse, measure, measurement_key,
)

# A reference with more cuts than this is not a rhythm anyone can match with a
# handful of clips; it is a montage of its own. Past this point the tool would be
# asking the footage for eighty distinct shots and reporting seventy-four
# failures, which helps nobody.
MAX_REFERENCE_SHOTS = 40

# Below this a "shot" is a flash frame or a detection artefact, not a beat of the
# edit. `visual.min_shot` already merges short detections, but a reference cut on
# frames rather than on story can still leave slivers.
MIN_REFERENCE_SHOT = 0.25


class ReferenceError(RuntimeError):
    """The reference could not be read or understood."""


@dataclass
class ReferenceShot:
    """One shot of the reference: when it runs, and what it looks like."""

    start: float
    end: float
    still: Path | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Reference:
    path: Path
    duration: float
    shots: list[ReferenceShot] = field(default_factory=list)
    # Named aspect, or None when it matches nothing the tool offers.
    aspect: str | None = None
    width: int = 0
    height: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def cut_count(self) -> int:
        return len(self.shots)

    @property
    def stills(self) -> list[Path]:
        return [s.still for s in self.shots if s.still]


def aspect_of(width: int, height: int) -> str | None:
    """The tool's name for this shape, or None if it is not one of them.

    Compared by ratio rather than by size, and loosely: a 1080x1920 phone video
    and a 720x1280 screen recording are the same editorial shape, and a reference
    is being used for its shape, not its resolution.
    """
    if width <= 0 or height <= 0:
        return None
    ratio = width / height
    for name, (w, h) in (("vertical", (1080, 1920)), ("square", (1080, 1080)),
                         ("portrait45", (1080, 1350)), ("landscape", (1920, 1080))):
        if abs(ratio - w / h) <= 0.08:
            return name
    return None


def _cached_measure(
    path: Path, duration: float, settings: VisualSettings,
    work_dir: Path, no_cache: bool,
) -> Measurements:
    """`measure`, but only once per file per setting.

    Reading a reference is the slowest thing in a reference job -- `measure`
    decodes the whole video three times, and a four-minute one is minutes of
    work. It ran on every attempt, so changing a length or a recipe and pressing
    Create again paid the whole cost a second time for a file that had not
    changed.

    The footage path has cached this since it shipped (`cli.py`); the reference
    path called `measure` directly. Same key shape, same directory, so a file
    used as both is measured once for each set of settings and no more.

    Only the decode is cached. Scoring is recomputed every run, which is what
    keeps re-tuning instant.
    """
    key = hashlib.sha256(
        json.dumps([content_hash(path), measurement_key(settings), None],
                   sort_keys=True).encode()
    ).hexdigest()[:24]
    cached = work_dir / "visual" / f"{key}.json"
    if cached.exists() and not no_cache:
        try:
            return Measurements.from_dict(json.loads(cached.read_text()))
        except (OSError, ValueError, KeyError):
            # A truncated cache file is not a reason to fail a job. Measure again
            # and overwrite it.
            pass
    measured = measure(str(path), duration, settings, None)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(measured.to_dict()))
    except OSError:
        pass          # an unwritable cache is slow, not broken
    return measured


def analyse_reference(
    path: Path | str,
    work_dir: Path,
    settings: VisualSettings | None = None,
    max_shots: int = MAX_REFERENCE_SHOTS,
    no_cache: bool = False,
) -> Reference:
    """Read a reference video's structure.

    Uses the same shot detection the footage goes through, so "a shot" means the
    same thing on both sides of the match. Raises `ReferenceError` for anything
    an editor can act on -- an unreadable file, a video with no cuts in it.
    """
    path = Path(path)
    try:
        info = probe(path)
    except ProbeError as exc:
        raise ReferenceError(f"could not read the reference video: {exc}") from exc
    if not info.has_video:
        raise ReferenceError(f"{path.name} has no video track to take a structure from")

    # Tuned for reading someone else's cuts, not for finding natural ones.
    #
    # `min_shot` 0.5 merges anything shorter into its neighbour, which is right
    # for footage -- a half-second detection there is usually a flash or a
    # wobble. In a reference it is a half-second CUT, and a fast-cut social video
    # is mostly those: merging them reports a rhythm nobody edited.
    #
    # `max_shot` 6.0 subdivides anything longer, which is right when the tool is
    # deciding how long to hold a shot. Here it would invent cuts the reference
    # does not contain -- a ten-second hold is one shot, and splitting it into
    # two fives copies a rhythm that was never there.
    settings = settings or VisualSettings(
        min_shot=MIN_REFERENCE_SHOT,
        max_shot=float(max(30.0, MAX_REFERENCE_SHOTS)),
    )
    try:
        measured = _cached_measure(path, info.duration, settings, work_dir, no_cache)
        analysis = analyse(str(path), info.duration, settings, measured)
    except VisualError as exc:
        raise ReferenceError(f"could not analyse the reference video: {exc}") from exc

    warnings: list[str] = []
    # EVERY shot, not only the ones that pass the quality gates. A reference is
    # being read for its rhythm, and a dark or soft shot in it is still a beat of
    # that rhythm -- rejecting it would silently shorten the pattern being
    # copied. The gates exist to stop bad footage reaching a timeline, and no
    # frame of this file ever will.
    spans = [s.shot for s in analysis.shots if s.shot.duration >= MIN_REFERENCE_SHOT]
    # Fewer than two shots is not a rhythm. Detection returns the whole file as
    # one shot when it finds no cuts, so "no shots" and "no cuts" arrive here
    # looking identical -- and both mean there is nothing to copy.
    #
    # Both causes are named because they have different fixes and an editor
    # cannot tell them apart by looking: a genuine single take, or cuts too soft
    # for the detector. A dissolve-heavy edit is the second one.
    if len(spans) < 2:
        raise ReferenceError(
            f"{path.name}: no cuts were found in it. If it is one continuous "
            f"take there is no cutting pattern to copy; if it is cut, the "
            f"transitions may be too soft to detect"
        )

    if len(spans) > max_shots:
        warnings.append(
            f"the reference has {len(spans)} shots; only the first {max_shots} "
            f"were used"
        )
        spans = spans[:max_shots]

    shots = []
    for span in spans:
        at = sample_point(span.start, span.end)
        tp = thumb_path(work_dir, path, at)
        shots.append(ReferenceShot(
            start=span.start, end=span.end,
            still=tp if build_thumb(path, tp, at) else None,
        ))

    missing = sum(1 for s in shots if s.still is None)
    if missing:
        # Not fatal: a shot with no still can still contribute its LENGTH to the
        # rhythm, it just cannot be matched on content.
        warnings.append(f"{missing} reference shot(s) yielded no still to match against")

    return Reference(
        path=path,
        duration=info.duration,
        shots=shots,
        aspect=aspect_of(info.display_width, info.display_height),
        width=info.display_width,
        height=info.display_height,
        warnings=warnings,
    )


def beats_from(reference: Reference) -> list[Beat]:
    """The reference's shots as beats, weighted by how long each one runs.

    The weight is the whole trick. `build_story_plans` divides the target runtime
    across matched beats in proportion to `Beat.weight`, so weighting by the
    reference's own shot lengths reproduces its pacing -- a half-second cut stays
    a half-second cut and a four-second hold stays a hold. Nothing else in the
    allocation has to change.

    The label is a timecode. It is what an editor sees in a warning about a shot
    the footage could not serve, and "0:04-0:06" locates that in the reference
    they are looking at; a descriptor guessed by a vision model would not.
    """
    return [
        Beat(id=f"r{i + 1}", text=_label(i, shot), weight=max(0.01, shot.duration))
        for i, shot in enumerate(reference.shots)
    ]


def _label(index: int, shot: ReferenceShot) -> str:
    return f"shot {index + 1} ({_clock(shot.start)}-{_clock(shot.end)})"


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


# --- matching a reference's shots to the editor's footage --------------------
#
# MEASURED, on this repo's own library: a reference from the same shoot scored
# 0.842-0.918 against the footage (median best-match 0.877), and three unrelated
# personal videos scored 0.547-0.649 (medians 0.557, 0.588, 0.635). A clean gap
# of about 0.19, and this floor sits in the middle of it.
#
# The story path's text distractors cannot do this job. The same measurement put
# them at 0.226-0.272 -- so against image-to-image similarities of 0.55 and up
# they lose every comparison, every reference shot gets a match whether it
# deserves one or not, and "the footage does not contain this" stops being a
# possible answer. CLIP's image embeddings sit in a narrow cone; borrowing a
# threshold calibrated for image-to-text is measuring a different thing.
#
# An absolute floor is the weakest part of this design and this project has been
# bitten by absolute floors before, which is why `reference_report()` puts the
# observed band in a warning on every job: if the numbers on someone's footage
# do not look like the ones above, that is visible rather than inferred.
REFERENCE_MATCH_FLOOR = 0.75


@dataclass
class ShotMatch:
    """Which footage span was chosen for one reference shot, and how well."""

    reference_index: int
    footage_index: int | None = None
    score: float = 0.0
    reused: bool = False

    @property
    def matched(self) -> bool:
        return self.footage_index is not None


def match_shots(
    reference_vectors,
    footage_vectors,
    floor: float = REFERENCE_MATCH_FLOOR,
) -> list[ShotMatch]:
    """Pick a footage span for each reference shot, best first.

    Deliberately NOT `story.assign_beats`, for two reasons.

    It runs the other way round. `assign_beats` gives every *footage* shot to the
    beat that wants it most, which is right when the beats are a running order
    someone wrote and the question is what fills them. Here the reference is the
    fixed thing: each of ITS shots needs a span, and a footage span nobody wants
    is simply unused.

    And it allows reuse. `assign_beats` never shares a shot -- a deliberate
    decision recorded in its docstring. A thirty-cut reference against six clips
    has no choice but to reuse, and refusing would report twenty-four failures
    for a job that is working exactly as asked. Unused spans are preferred while
    any remain, so reuse starts only once the footage is exhausted, and every
    reuse is marked.
    """
    import numpy as np

    out: list[ShotMatch] = []
    if reference_vectors is None or len(reference_vectors) == 0:
        return out
    if footage_vectors is None or len(footage_vectors) == 0:
        return [ShotMatch(reference_index=i) for i in range(len(reference_vectors))]

    sims = np.asarray(reference_vectors) @ np.asarray(footage_vectors).T
    used: set[int] = set()
    for i, row in enumerate(sims):
        order = np.argsort(-row)
        fresh = [j for j in order if j not in used]
        # Prefer a span nothing has taken yet; fall back to the whole pool once
        # they are gone. Both are still subject to the floor.
        pick = fresh[0] if fresh else int(order[0])
        score = float(row[pick])
        if score < floor:
            out.append(ShotMatch(reference_index=i, score=score))
            continue
        out.append(ShotMatch(reference_index=i, footage_index=int(pick),
                             score=score, reused=pick in used))
        used.add(int(pick))
    return out


def reference_report(matches: list[ShotMatch]) -> dict:
    """The numbers behind a match, for the warning that carries them.

    The floor is absolute and absolute floors do not transfer between libraries.
    Putting the observed band on every job means a floor that is wrong for
    somebody's footage shows up as numbers an editor can read, rather than as an
    edit that quietly ignored the reference.
    """
    scores = [m.score for m in matches]
    matched = [m for m in matches if m.matched]
    return {
        "shots": len(matches),
        "matched": len(matched),
        "reused": sum(1 for m in matched if m.reused),
        "best": round(max(scores), 3) if scores else 0.0,
        "worst": round(min(scores), 3) if scores else 0.0,
        "floor": REFERENCE_MATCH_FLOOR,
    }
