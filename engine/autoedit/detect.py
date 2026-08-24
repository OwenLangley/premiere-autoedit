"""Cut planning: word-level transcript -> the spans worth keeping.

Pure functions over timestamps. No ffmpeg, no Premiere, no network -- which is
what makes the editorially risky logic in this file fully testable.

Two rules run through the whole module:

1. **Never lose speech.** When a constraint is violated, the fix is always to
   cancel a cut, never to discard content. A tool that silently eats a sentence
   gets uninstalled.
2. **Never cut what we cannot read.** Low-confidence transcript regions are
   protected, not guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .transcript import Transcript, Word

EPS = 1e-6

# Consecutive dropped words are separated by a few milliseconds of inter-word gap.
# Without a tolerance those become separate drops with an unusable sliver between them.
MERGE_TOLERANCE = 0.05

KIND_SILENCE = "silence"
KIND_FILLER = "filler"
KIND_STUTTER = "stutter"

# Sounds, not words. Removing these is almost never controversial.
CONSERVATIVE_FILLERS: frozenset[str] = frozenset(
    {"um", "umm", "ummm", "uh", "uhh", "uhhh", "erm", "er", "err", "ah", "ahh", "mm", "mmm", "hmm", "huh", "eh"}
)

# Real words used as filler. Removing these changes voice and sometimes meaning
# ("I like it" vs "it was, like, big"), so this set is opt-in per recipe.
AGGRESSIVE_FILLERS: frozenset[str] = CONSERVATIVE_FILLERS | frozenset(
    {"like", "basically", "actually", "literally", "honestly", "obviously", "right", "so"}
)

# Matched as contiguous runs. Aggressive mode only, same reasoning.
MULTIWORD_FILLERS: tuple[tuple[str, ...], ...] = (
    ("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of"), ("you", "see"),
)


@dataclass(frozen=True)
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Drop(Span):
    """A region to remove, and why.

    `hard_start` / `hard_end` mark an edge that a handle must not cross. A filler
    cut claims its exact word boundaries; if a neighbouring silence cut later
    merges with it and then applies its own handle, the handle would reach back
    across that boundary and re-include the tail of the very filler we removed.
    """

    kind: str = KIND_SILENCE
    reason: str = ""
    hard_start: bool = False
    hard_end: bool = False


@dataclass(frozen=True)
class Keep(Span):
    """A region to place on the timeline."""

    reason: str = ""
    confidence: float = 1.0
    word_count: int = 0


@dataclass
class DetectionSettings:
    """Recipe-tunable knobs. Defaults are deliberately gentle: an under-cut
    rough assembly costs an editor a few minutes, an over-cut one costs their
    trust in the whole system."""

    # Silence
    min_silence: float = 0.40        # gaps shorter than this are speech rhythm, not dead air
    trim_head_tail: bool = True
    min_removed: float = 0.08        # a cut must remove at least this much to be worth the seam

    # Handles -- breathing room retained either side of a silence cut
    lead_in: float = 0.12
    tail: float = 0.08

    # Fillers
    filler_mode: str = "conservative"   # off | conservative | aggressive
    extra_fillers: frozenset[str] = frozenset()
    keep_fillers: frozenset[str] = frozenset()   # escape hatch for false positives
    filler_pad: float = 0.0             # expand filler cuts slightly to catch consonant tails

    # Stutters / false starts
    remove_stutters: bool = True
    stutter_max_gap: float = 0.60    # wider than this is deliberate repetition, not a stumble

    # Safety
    min_confidence: float = 0.55     # below this we refuse to cut, and say so
    protect_margin: float = 0.25     # also veto cuts *adjacent* to unreliable speech
    min_clip_length: float = 0.35    # shorter surviving clips are dissolved back together

    # Seams
    crossfade: float = 0.0834        # ~2 frames at 24fps; set per-recipe from the timebase

    def filler_lexicon(self) -> frozenset[str]:
        if self.filler_mode == "off":
            base: frozenset[str] = frozenset()
        elif self.filler_mode == "aggressive":
            base = AGGRESSIVE_FILLERS
        else:
            base = CONSERVATIVE_FILLERS
        return (base | self.extra_fillers) - self.keep_fillers


@dataclass
class CutPlan:
    keeps: list[Keep] = field(default_factory=list)
    drops: list[Drop] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def kept_duration(self) -> float:
        return sum(k.duration for k in self.keeps)

    @property
    def removed_duration(self) -> float:
        return sum(d.duration for d in self.drops)

    def summary(self, source_duration: float) -> str:
        pct = (self.removed_duration / source_duration * 100) if source_duration else 0.0
        return (
            f"{len(self.keeps)} clips, kept {self.kept_duration:.1f}s of "
            f"{source_duration:.1f}s (removed {pct:.0f}%)"
        )


# ---------------------------------------------------------------- classifiers


def _filler_indices(words: list[Word], settings: DetectionSettings) -> dict[int, str]:
    """Indices of filler words -> the reason string."""
    lexicon = settings.filler_lexicon()
    if not lexicon:
        return {}

    found: dict[int, str] = {}
    norms = [w.norm for w in words]

    for i, n in enumerate(norms):
        if n and n in lexicon:
            found[i] = f"filler {words[i].text!r}"

    if settings.filler_mode == "aggressive":
        for phrase in MULTIWORD_FILLERS:
            span = len(phrase)
            for i in range(len(norms) - span + 1):
                if tuple(norms[i : i + span]) == phrase:
                    # Only when spoken as one unit -- a real clause has pauses in it.
                    if all(words[j + 1].start - words[j].end < 0.25 for j in range(i, i + span - 1)):
                        for j in range(i, i + span):
                            found.setdefault(j, f"filler {' '.join(phrase)!r}")
    return found


def _stutter_indices(words: list[Word], settings: DetectionSettings) -> dict[int, str]:
    """False starts: 'th- the', 'I I I think'. Keeps the completed attempt."""
    if not settings.remove_stutters:
        return {}

    found: dict[int, str] = {}
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if b.start - a.end > settings.stutter_max_gap:
            continue
        na, nb = a.norm, b.norm
        if not na or not nb:
            continue
        truncated = na.endswith("-") and nb.startswith(na.rstrip("-")[:2] or nb)
        repeated = na == nb
        partial = len(na) < len(nb) and nb.startswith(na) and len(na) >= 2
        if truncated or repeated or partial:
            found[i] = f"false start {a.text!r}"   # drop the first, keep the completed one
    return found


def _protected_regions(words: list[Word], settings: DetectionSettings) -> list[Span]:
    """Regions the transcript is too unreliable to cut inside.

    Cutting on a misheard word is worse than leaving a pause in, so a
    low-confidence run vetoes every cut that touches it.
    """
    margin = settings.protect_margin
    regions: list[Span] = []
    run: list[Word] = []

    def _flush() -> None:
        # Padded outwards: if the transcript is unreliable here then so are its
        # word boundaries, so a cut landing just outside the run can still slice
        # into real audio.
        regions.append(Span(max(0.0, run[0].start - margin), run[-1].end + margin))

    for w in words:
        if w.confidence < settings.min_confidence:
            run.append(w)
        elif run:
            _flush()
            run = []
    if run:
        _flush()
    return regions


# ------------------------------------------------------------------ interval helpers


def _overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end - EPS and b.start < a.end - EPS


def _merge_drops(drops: list[Drop]) -> list[Drop]:
    """Coalesce overlapping/touching drops, preserving the dominant reason."""
    if not drops:
        return []
    ordered = sorted(drops, key=lambda d: (d.start, d.end))
    out = [ordered[0]]
    for d in ordered[1:]:
        last = out[-1]
        if d.start <= last.end + MERGE_TOLERANCE:
            kind = last.kind if last.duration >= d.duration else d.kind
            reason = last.reason if last.duration >= d.duration else d.reason
            # The merged region inherits hardness from whichever constituent owns
            # each outer edge, so a filler swallowed by a silence run keeps its
            # boundary protected.
            end_owner = last if last.end >= d.end else d
            out[-1] = Drop(
                last.start, max(last.end, d.end), kind, reason,
                last.hard_start, end_owner.hard_end,
            )
        else:
            out.append(d)
    return out


def _apply_handles(drop: Drop, settings: DetectionSettings, media_duration: float) -> Drop | None:
    """Shrink a drop so the surrounding clips keep breathing room.

    Handles apply to silence only. A filler cut is trimmed tight on purpose --
    padding a 250ms "um" by 200ms of handles would leave most of the "um" in,
    which is the single easiest way to make this feature look broken.

    A handle only exists to protect a neighbouring clip, so a drop running to the
    head or tail of the media gets no handle on that side. Without this, trimming
    lead-in silence leaves a sub-frame sliver of black at 00:00 which then trips
    the min-clip-length rule and cancels the trim entirely.
    """
    if drop.kind != KIND_SILENCE:
        if settings.filler_pad <= 0:
            return drop
        return replace(drop, start=drop.start - settings.filler_pad, end=drop.end + settings.filler_pad)

    at_head = drop.start <= EPS
    at_tail = drop.end >= media_duration - EPS

    start = drop.start if (at_head or drop.hard_start) else drop.start + settings.tail
    end = drop.end if (at_tail or drop.hard_end) else drop.end - settings.lead_in
    if end - start < settings.min_removed:
        return None   # too short to be worth a seam once handles are honoured
    return replace(drop, start=start, end=end)


def _complement(drops: list[Drop], lo: float, hi: float) -> list[Span]:
    """Everything in [lo, hi] not covered by a drop."""
    keeps: list[Span] = []
    cursor = lo
    for d in sorted(drops, key=lambda x: x.start):
        if d.start > cursor + EPS:
            keeps.append(Span(cursor, min(d.start, hi)))
        cursor = max(cursor, d.end)
        if cursor >= hi:
            break
    if cursor < hi - EPS:
        keeps.append(Span(cursor, hi))
    return [k for k in keeps if k.duration > EPS]


# ------------------------------------------------------------------ main entry


def plan_cuts(
    transcript: Transcript,
    media_duration: float,
    settings: DetectionSettings | None = None,
) -> CutPlan:
    """Decide which spans of a source clip survive into the rough cut."""
    settings = settings or DetectionSettings()
    words = transcript.words
    warnings: list[str] = []

    if not words:
        return CutPlan(
            keeps=[Keep(0.0, media_duration, "no transcript - clip used whole", 1.0, 0)],
            warnings=["no words in transcript; clip passed through uncut"],
        )

    problems = transcript.validate()
    if problems:
        warnings.extend(problems)

    fillers = _filler_indices(words, settings)
    stutters = _stutter_indices(words, settings)
    protected = _protected_regions(words, settings)

    raw: list[Drop] = []
    for i, reason in fillers.items():
        raw.append(Drop(words[i].start, words[i].end, KIND_FILLER, reason, True, True))
    for i, reason in stutters.items():
        if i not in fillers:
            raw.append(Drop(words[i].start, words[i].end, KIND_STUTTER, reason, True, True))

    for a, b in zip(words, words[1:]):
        gap = b.start - a.end
        if gap >= settings.min_silence:
            raw.append(Drop(a.end, b.start, KIND_SILENCE, f"{gap:.2f}s pause"))

    if settings.trim_head_tail:
        if words[0].start > EPS:
            raw.append(Drop(0.0, words[0].start, KIND_SILENCE, "lead-in silence"))
        if words[-1].end < media_duration - EPS:
            raw.append(Drop(words[-1].end, media_duration, KIND_SILENCE, "trailing silence"))

    if protected:
        vetoed = [d for d in raw if any(_overlaps(d, p) for p in protected)]
        if vetoed:
            warnings.append(
                f"{len(vetoed)} cut(s) skipped over low-confidence speech "
                f"(below {settings.min_confidence:g}); review those sections by hand"
            )
        raw = [d for d in raw if d not in vetoed]

    # Converge: repeatedly cancel the cut responsible for a too-short clip.
    # Each pass removes exactly one drop, so this terminates.
    work = _merge_drops(raw)
    guard = 4 * len(work) + 16   # absorption is monotonic; this only bounds pathological input
    while True:
        handled: list[tuple[Drop, int]] = []
        for idx, d in enumerate(work):
            adjusted = _apply_handles(d, settings, media_duration)
            if adjusted is not None:
                handled.append((adjusted, idx))

        keeps = _complement([h for h, _ in handled], 0.0, media_duration)

        # A short keep containing no words is not content -- it is leftover
        # geometry between two adjacent cuts. Absorb it rather than cancelling a
        # cut to protect it.
        sliver = next(
            (k for k in keeps
             if k.duration < settings.min_clip_length and not transcript.slice(k.start, k.end)),
            None,
        )
        if sliver is not None and guard > 0:
            guard -= 1
            work = _merge_drops(work + [Drop(sliver.start, sliver.end, KIND_SILENCE, "inter-cut gap")])
            continue

        victim: int | None = None
        for k in keeps:
            if k.duration >= settings.min_clip_length - EPS:
                continue
            if not transcript.slice(k.start, k.end):
                continue
            neighbours = [
                (adj, idx) for adj, idx in handled
                if abs(adj.end - k.start) < EPS or abs(adj.start - k.end) < EPS
            ]
            if neighbours:
                # Cancel the least valuable neighbouring cut. Removing a filler is a
                # deliberate editorial win; trimming silence is cosmetic. When one has
                # to go, give up the silence trim -- otherwise a stray pause near an
                # "um" quietly resurrects the "um".
                def _sacrifice_rank(pair: tuple[Drop, int]) -> tuple[int, float]:
                    adj = pair[0]
                    return (0 if adj.kind == KIND_SILENCE else 1, adj.duration)

                victim = min(neighbours, key=_sacrifice_rank)[1]
                break

        if victim is None:
            break
        work.pop(victim)

    final_drops = [h for h, _ in handled]
    keep_spans = _complement(final_drops, 0.0, media_duration)

    # Attribute each clip to the cut that produced it, so the panel can explain itself.
    keeps_out: list[Keep] = []
    for k in keep_spans:
        inside = transcript.slice(k.start, k.end)
        conf = sum(w.confidence for w in inside) / len(inside) if inside else 1.0
        preceding = [d for d in final_drops if abs(d.end - k.start) < EPS]
        reason = preceding[0].reason if preceding else "opening"
        keeps_out.append(Keep(k.start, k.end, reason, round(conf, 4), len(inside)))

    dropped_pct = 1.0 - (sum(k.duration for k in keeps_out) / media_duration if media_duration else 1.0)
    if dropped_pct > 0.6:
        warnings.append(
            f"cut removed {dropped_pct * 100:.0f}% of the source -- unusually aggressive, "
            "check min_silence and filler_mode before trusting this assembly"
        )

    return CutPlan(keeps=keeps_out, drops=final_drops, warnings=warnings)
