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

# A safety limit, not an editorial one.
#
# This was 40, chosen for social-video references against a handful of clips.
# It was wrong for long form in a way that was invisible: a 27-minute YouTube
# reference has 239 shots, the first 40 of which cover 244s. Keeping those 40
# and still targeting the full 1652s stretched every section 6.8x -- a shot the
# reference held for 36s was asked to hold for 245s -- so the "rhythm" being
# copied was one no editor had cut.
#
# 400 is high enough that no ordinary reference is truncated (239 here, and a
# fast 60s TikTok has perhaps 80), and low enough to bound the still and
# embedding work if someone points this at a two-hour film. When it IS hit, the
# target is scaled to the shots actually kept rather than left at the whole
# video's length, so the pacing stays the pacing that was measured.
MAX_REFERENCE_SHOTS = 400

# Below this a "shot" is a flash frame or a detection artefact, not a beat of the
# edit. `visual.min_shot` already merges short detections, but a reference cut on
# frames rather than on story can still leave slivers.
MIN_REFERENCE_SHOT = 0.25

# The longest hold read as ONE shot. Above this the detector subdivides, which
# for a reference would invent cuts it does not contain -- so it is set well
# past any real hold rather than at the 6s the footage path uses to decide how
# long to sit on a clip. An interview take runs minutes and is still one shot.
MAX_REFERENCE_HOLD = 600.0

# How many extra spans a single reference shot may pull in to fill a long
# section. Bounded because the list is per reference shot and a long reference
# has hundreds of them; 60 spans of even 3s is three minutes, longer than any
# single section of a sane edit.
MAX_ALTERNATES = 60

# --------------------------------------------------------------- shot roles
#
# What a reference shot is FOR, read off its own shape. A reference has audio,
# but transcribing someone else's 27-minute video to learn something its cutting
# already says would be minutes of Whisper for an answer that is free: an
# opening, an ending and a sit-down interview are all HOLDS, and b-roll is not.
ROLE_OPENING = "opening"
ROLE_ENDING = "ending"
ROLE_INTERVIEW = "interview"
ROLE_CUT = "cut"

# The roles that want someone talking on screen. Kept as a set rather than a
# test on the name so a role can be added without hunting for comparisons.
SPOKEN_ROLES = frozenset({ROLE_OPENING, ROLE_ENDING, ROLE_INTERVIEW})

# A shot is a hold when it runs at least twice the median AND at least this many
# seconds. Both are needed: the ratio alone calls a 1.2s shot a hold in a video
# cut at 0.6s, and the floor alone calls everything a hold in a slow film.
HOLD_FACTOR = 2.0
HOLD_MIN = 4.0

# How near an end a hold has to be to be that end's bookend, as a fraction of
# the reference's running time. A MINIMUM: learned cues can push it outwards,
# because a tenth is a guess and the phrase that actually starts the sign-off is
# evidence.
BOOKEND_FRACTION = 0.10

# How far learned evidence may push it. Past a third, "the ending" has stopped
# being an ending -- and a store that has learned something too common would
# otherwise swallow the film.
BOOKEND_LIMIT = 0.35


# Below this fraction of speech, a shot is nobody talking. Not 0.0: a caption
# cue overhanging a cut by a few frames should not make a silent shot spoken.
QUIET_COVERAGE = 0.15

# Caption tracks read beside a reference. The helper writes these next to a
# downloaded video; for a local file an editor can drop one there by hand, the
# same "look beside the media" convention `transcribe.SidecarProvider` already
# uses for footage.
CAPTION_SUFFIXES = (".vtt", ".srt")


def _ends_of(transcript, duration: float) -> tuple[str, str, str]:
    """What was said in the first tenth, the last tenth, and everything between.

    The middle is returned because it is the negative set: a phrase that also
    appears mid-video is not a cue. That rule is what disqualifies `こんにちは`
    on a reference where the only three occurrences are at eighteen minutes.
    """
    head_end = duration * BOOKEND_FRACTION
    tail_start = duration * (1.0 - BOOKEND_FRACTION)
    head, tail, middle = [], [], []
    for w in transcript.words:
        if w.end <= head_end:
            head.append(w.text)
        elif w.start >= tail_start:
            tail.append(w.text)
        else:
            middle.append(w.text)
    return "".join(head), "".join(tail), "".join(middle)


def captions_beside(path: Path):
    """The reference's own caption track, if one is sitting next to it.

    Returns a cue-level `Transcript` or None. Cue-level is the point: it says
    WHERE someone is speaking, which is all a reference is read for, and it
    costs seconds against the minutes Whisper would spend on the same file.
    Never give one to anything that needs word timings.
    """
    from .transcribe import parse_vtt

    found = sorted(
        f for suffix in CAPTION_SUFFIXES
        for f in path.parent.glob(f"{path.stem}*{suffix}")
    )
    best = None
    for track in found:
        # `<stem>.ja.vtt` -> "ja"; a bare `<stem>.vtt` -> unknown, call it en.
        middle = track.name[len(path.stem):-len(track.suffix)].strip(".")
        language = middle.split("-")[0] if middle else "en"
        try:
            heard = parse_vtt(track.read_text(encoding="utf8", errors="replace"),
                              "reference", language or "en")
        except (OSError, ValueError):
            continue          # an unreadable track is no track, not a failure
        if not heard.words:
            continue
        # The one that says the most, not the one that sorts first. YouTube
        # ships auto-translations alongside the original, and `.en.vtt` sorts
        # before `.ja.vtt` -- on a Japanese reference that quietly picked a
        # machine translation and then learned English phrases from it. The
        # original is the denser track: 467 cues against 393 here.
        covered = sum(w.end - w.start for w in heard.words)
        if best is None or covered > best[0]:
            best = (covered, heard)
    return best[1] if best else None


def classify_roles(shots: list["ReferenceShot"], duration: float,
                   transcript=None, store=None) -> None:
    """Label each reference shot with what it is doing, in place.

    **Position does most of the work, and that is the point.** An opening is at
    the opening. Searching the whole video for something that says "intro"
    invents problems: measured on a real 27-minute reference, `こんにちは` --
    the most obvious greeting in the language -- appears three times, all of
    them at 18 minutes, where someone is greeting customers. Read as a cue it
    would have moved the opening to the middle of the film. Restricted to the
    first and last tenth, it never fires at all, which is correct.

    **A transcript refines this by its SILENCE, not its speech.** On a finished
    video 74% of the runtime had someone talking, spread evenly, never below 29%
    in any two-minute window -- because voiceover plays over b-roll. So "has
    speech" separates nothing. Its absence does: 19% of that reference sat in 23
    silent stretches of four seconds or more, and those are exactly the shots
    shape alone calls a hold and labels `interview`.

    Raw footage is the other way round -- narration is not in it yet -- which is
    why `speech.is_spoken` works on that side and this one reads the quiet.

    Without a transcript the shape-only reading runs unchanged.
    """
    if not shots:
        return
    ordered = sorted(sh.duration for sh in shots)
    median = ordered[len(ordered) // 2] or 0.0
    threshold = max(HOLD_MIN, median * HOLD_FACTOR)
    head = duration * BOOKEND_FRACTION
    tail = duration * (1.0 - BOOKEND_FRACTION)

    spans: list[tuple[float, float]] = []
    if transcript is not None and getattr(transcript, "words", None):
        from .speech import utterances
        spans = utterances(transcript)

    # Learned phrases move the boundary rather than labelling a shot. Position
    # already decides WHAT a shot is; what a tenth cannot know is WHERE the
    # sign-off actually starts, and the phrase that starts it says so.
    if store is not None and transcript is not None:
        language = getattr(transcript, "language", "en")
        limit_head = duration * BOOKEND_LIMIT
        limit_tail = duration * (1.0 - BOOKEND_LIMIT)
        for word in getattr(transcript, "words", []):
            role = store.role_of(word.text, language)
            if role == ROLE_OPENING and head < word.end <= limit_head:
                head = word.end
            elif role == ROLE_ENDING and limit_tail <= word.start < tail:
                tail = word.start

    def quiet(sh) -> bool:
        """True when nobody is speaking over this shot. False with no transcript,
        so every branch below reads as it did before one existed."""
        if not spans or sh.duration <= 0:
            return False
        heard = sum(max(0.0, min(sh.end, e) - max(sh.start, st)) for st, e in spans)
        return heard / sh.duration < QUIET_COVERAGE

    for sh in shots:
        silent = quiet(sh)
        if silent:
            # Nobody is talking over it, however long it holds. This is the
            # correction the transcript buys: a sixty-second silent hold is
            # b-roll, and shape alone called it an interview.
            sh.role = ROLE_CUT
        elif sh.start <= head:
            # In the opening tenth, anything with speech over it belongs to the
            # opening -- a cold open is cut fast and is still the opening. Length
            # only decides roles in the middle, where position says nothing.
            sh.role = ROLE_OPENING if spans else (
                ROLE_OPENING if sh.duration >= threshold else ROLE_CUT)
        elif sh.end >= tail:
            sh.role = ROLE_ENDING if spans else (
                ROLE_ENDING if sh.duration >= threshold else ROLE_CUT)
        elif sh.duration >= threshold:
            sh.role = ROLE_INTERVIEW
        else:
            sh.role = ROLE_CUT


class ReferenceError(RuntimeError):
    """The reference could not be read or understood."""


@dataclass
class ReferenceShot:
    """One shot of the reference: when it runs, and what it looks like."""

    start: float
    end: float
    still: Path | None = None
    # What this shot is doing -- see `classify_roles`. Defaults to a plain cut so
    # a shot built by hand (every test fixture) needs no ceremony.
    role: str = "cut"

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
    # `max_shot` is a hold length, not a count. It was derived from
    # MAX_REFERENCE_SHOTS, which happened to give 40s while that constant meant
    # "at most 40 shots"; raising the cap to 400 would have turned it into a
    # 400-second ceiling that subdivides nothing. They were never the same idea.
    settings = settings or VisualSettings(
        min_shot=MIN_REFERENCE_SHOT,
        max_shot=MAX_REFERENCE_HOLD,
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

    full_duration = info.duration
    if len(spans) > max_shots:
        spans = spans[:max_shots]
        # The target follows the shots, or the rhythm stops being the rhythm.
        # Keeping the whole video's length while using a prefix of its shots
        # stretched every section by the ratio between them -- silently, since
        # both numbers were individually correct.
        full_duration = spans[-1].end
        warnings.append(
            f"the reference has more than {max_shots} shots; the first "
            f"{max_shots} were used, up to {full_duration:.0f}s of it"
        )

    shots = []
    for span in spans:
        at = sample_point(span.start, span.end)
        tp = thumb_path(work_dir, path, at)
        shots.append(ReferenceShot(
            start=span.start, end=span.end,
            still=tp if build_thumb(path, tp, at) else None,
        ))

    heard = captions_beside(path)
    store = None
    if heard is not None:
        from .cues import CueStore
        store = CueStore.load(work_dir)
        # Learn from this reference BEFORE reading it, so a second run of the
        # same job sees what the first one learned. `observe` is keyed on the
        # reference, so re-running never counts it twice.
        head_text, tail_text, middle_text = _ends_of(heard, full_duration)
        store.observe(heard.language, head_text, tail_text, middle_text,
                      content_hash(path))
        store.save(work_dir)
    classify_roles(shots, full_duration, heard, store)
    holds = [s for s in shots if s.role in SPOKEN_ROLES]
    if heard:
        warnings.append(
            f"read its captions ({heard.language}) to tell talking from b-roll: "
            f"{len(holds)} of {len(shots)} shot(s) have someone speaking"
        )
    elif holds:
        warnings.append(
            f"read {len(holds)} shot(s) as someone talking, from shot lengths "
            f"alone -- no caption track was found beside the reference"
        )

    missing = sum(1 for s in shots if s.still is None)
    if missing:
        # Not fatal: a shot with no still can still contribute its LENGTH to the
        # rhythm, it just cannot be matched on content.
        warnings.append(f"{missing} reference shot(s) yielded no still to match against")

    return Reference(
        path=path,
        duration=full_duration,
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
    # Further spans that also resemble this reference shot, best first, for when
    # its section is longer than one span can fill. Empty is the ordinary case.
    alternates: list[int] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.footage_index is not None


# How hard a role agreement pulls. The image-to-image band that matters runs
# about 0.55-0.99, so 0.05 reorders spans that are genuinely close without
# letting a role override what the picture says. It biases the ORDER only --
# the floor is still tested against the raw similarity, or "the footage does not
# contain this" would quietly start depending on who was talking.
ROLE_BONUS = 0.05


def match_shots(
    reference_vectors,
    footage_vectors,
    floor: float = REFERENCE_MATCH_FLOOR,
    wants_speech: list[bool] | None = None,
    is_spoken: list[bool] | None = None,
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

    # A reference shot that holds on someone talking should be served by footage
    # of someone talking, and a two-second cut should not be. Applied to the
    # ranking, never to the floor.
    spoken = np.asarray(is_spoken, dtype=bool) if is_spoken is not None else None
    if spoken is not None and len(spoken) != sims.shape[1]:
        spoken = None                       # lengths disagree: ignore, do not guess

    used: set[int] = set()
    for i, row in enumerate(sims):
        rank = row
        if spoken is not None and wants_speech is not None and i < len(wants_speech):
            agrees = spoken if wants_speech[i] else ~spoken
            rank = row + np.where(agrees, ROLE_BONUS, 0.0)
        order = np.argsort(-rank)
        fresh = [j for j in order if j not in used]
        # Prefer a span nothing has taken yet; fall back to the whole pool once
        # they are gone. Both are still subject to the floor.
        pick = fresh[0] if fresh else int(order[0])
        score = float(row[pick])
        if score < floor:
            out.append(ShotMatch(reference_index=i, score=score))
            continue
        # Everything else that clears the floor, best first. A section that has
        # to hold longer than one span runs is filled from these rather than
        # coming up short: one span per reference shot put a hard ceiling on the
        # whole edit at (number of shots) x (longest span), which is why a
        # 28-minute target came out at 2m46s.
        spare = [int(j) for j in order[1:] if row[j] >= floor and int(j) != pick]
        out.append(ShotMatch(reference_index=i, footage_index=int(pick),
                             score=score, reused=pick in used,
                             alternates=spare[:MAX_ALTERNATES]))
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
