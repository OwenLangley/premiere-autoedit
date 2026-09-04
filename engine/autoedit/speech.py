"""Cutting around what people are saying.

The picture path scores shots on sharpness, motion, exposure and crop risk, and
knows nothing about speech. On footage of a coach shouting instructions that
produced cuts landing mid-word -- measured on a real plan, three of them, one
of them inside a single character.

Two settings, and they are separate on purpose:

* **Protecting speech** changes only WHERE a cut lands. Nothing is added or
  removed; a boundary that would fall inside someone's sentence moves to the
  edge of it. Safe enough to turn on whenever the words matter.
* **Removing silence** changes WHAT survives. It drops material, so it is opt-in
  with an allowance the editor sets, and it says how much it took.

The rule that does the work is **whole utterance in, or whole utterance out**.
Never half. A clip that starts halfway through a word sounds broken in a way no
amount of good framing rescues, and the fix is always to move the boundary --
the only question is which way.
"""

from __future__ import annotations

from dataclasses import replace

from .detect import CutPlan, Drop, Keep
from .notes import note
from .transcript import Transcript

# Silence long enough to separate one utterance from the next. Shorter than a
# sentence break, because the unit being protected is a phrase someone would be
# annoyed to hear cut in half -- not a paragraph.
UTTERANCE_GAP = 0.45

# How far a boundary may move to get out of someone's sentence. Beyond this the
# utterance is excluded instead of included: a cut that drifts a second and a
# half to save a word has stopped being the cut anyone chose.
MAX_SHIFT = 1.5

# How far OUTSIDE the utterance a moved boundary is placed.
#
# Not cosmetic. `append_cuts` snaps every source point to the source frame grid
# afterwards, and it rounds to nearest -- so a boundary sitting exactly on a
# word's edge rounds straight back inside it. Measured: protect moved a cut to
# 1.1600, the word began at 1.1600, and snapping at 59.94fps put it at 1.1678 --
# 7.8ms inside the word it had just been moved out of. Eleven cuts were reported
# as protected and ten of them were still mid-word.
#
# One frame at 24fps is 41.7ms, so 50ms clears the rounding at every rate this
# tool supports. It is spent on silence either way: the boundary moves away from
# the speech, never into it.
SNAP_MARGIN = 0.05


def utterances(transcript: Transcript, gap: float = UTTERANCE_GAP) -> list[tuple[float, float]]:
    """Continuous speech, as (start, end) spans in SOURCE time.

    Maximal runs of words separated by less than `gap`. One utterance is the
    thing that must not be cut in half.
    """
    spans: list[tuple[float, float]] = []
    for word in transcript.words:
        if spans and word.start - spans[-1][1] < gap:
            spans[-1] = (spans[-1][0], max(spans[-1][1], word.end))
        else:
            spans.append((word.start, word.end))
    return spans


def _containing(spans: list[tuple[float, float]], at: float) -> tuple[float, float] | None:
    """The utterance this moment falls strictly inside, if any.

    Strictly: a boundary sitting exactly on an utterance edge is already out of
    the way, and nudging it would move a cut that was fine.
    """
    for start, end in spans:
        if start < at < end:
            return start, end
        if start > at:
            break                       # spans are in order; no later one can contain it
    return None


def _speech_in(spans: list[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of speech inside a window."""
    return sum(max(0.0, min(end, e) - max(start, s)) for s, e in spans)


# What fraction of a span has to be speech before the span counts as someone
# talking. Two thirds, not "any": a b-roll shot of a kitchen with one shouted
# word over it is not an interview, and a sit-down answer has pauses in it.
SPOKEN_COVERAGE = 0.66


def is_spoken(transcript: Transcript, start: float, end: float,
              coverage: float = SPOKEN_COVERAGE) -> bool:
    """True when this span is mostly someone talking.

    Used to tell an interview take apart from b-roll, so a reference shot that
    holds on a person is served by footage of a person. Reads the transcript the
    job already made -- nothing is decoded or transcribed for this.
    """
    if end <= start:
        return False
    return _speech_in(utterances(transcript), start, end) / (end - start) >= coverage


def silences(transcript: Transcript, media_duration: float) -> list[tuple[float, float]]:
    """The quiet between one word and the next, including before and after.

    The second tier of protection. Continuous speech does not divide into
    sentences a clip can fit inside: measured on real footage, a coach shouting
    instructions produced utterances of 4.7 and 5.9 seconds against takes of
    1.6, so "keep the whole sentence" had nowhere to put the boundary and gave
    up, leaving it mid-word.

    Cutting between two words inside a long sentence is ordinary editing.
    Cutting through the middle of one never is.
    """
    out: list[tuple[float, float]] = []
    at = 0.0
    for word in transcript.words:
        if word.start > at:
            out.append((at, word.start))
        at = max(at, word.end)
    if media_duration > at:
        out.append((at, media_duration))
    return out


def _quiet_near(quiet: list[tuple[float, float]], at: float) -> list[float]:
    """Safe moments near `at`, one on each side. Empty when there are none.

    The MIDDLE of a gap, not its edge. An edge is a millisecond from being
    inside the word again, and every boundary here is snapped to a frame
    afterwards -- rounding to nearest, in whichever direction it likes.

    Both sides, not just the nearest. A boundary may only move in the direction
    that does not lengthen the take, and offering one candidate meant that when
    the nearest gap happened to lie the wrong way there was no answer at all --
    the take was left with a cut inside a word for want of looking 80ms further.
    """
    before = [(s + e) / 2 for s, e in quiet if (s + e) / 2 <= at]
    after = [(s + e) / 2 for s, e in quiet if (s + e) / 2 > at]
    out = []
    if before:
        out.append(before[-1])
    if after:
        out.append(after[0])
    return out


def protect(
    cuts: CutPlan,
    transcript: Transcript,
    *,
    media_duration: float,
    min_length: float = 0.4,
    max_shift: float = MAX_SHIFT,
    gap: float = UTTERANCE_GAP,
    margin: float = SNAP_MARGIN,
    may_grow: bool = False,
) -> tuple[CutPlan, int, int]:
    """Move cut boundaries out of the middle of anyone's sentence.

    Returns the adjusted plan, how many boundaries moved, and how many are
    still inside a word because nothing could be done about them.

    The last number is not always zero and pretending otherwise would be the
    lie. Whisper sometimes reports a run of words with no silence between them
    at all -- end of one exactly the start of the next -- and then there is no
    moment in that stretch that is not inside a word. On real footage of
    continuous shouted Japanese this left 2 boundaries of 20, down from 11.

    A boundary inside an utterance goes OUT of it, to whichever edge is nearer
    -- so the sentence is either wholly in or wholly out, and the edit changes
    as little as possible. Always reaching forward to include would lengthen
    every clip that happened to end in speech, which fights the duration the
    editor asked for; the guarantee that matters is that no word is cut, not
    which side of it the cut lands on. A move further than `max_shift` is
    refused and the other edge used instead.

    Either way the boundary ends up `margin` clear of the utterance, which is
    what survives the frame-snapping that happens afterwards.
    """
    spans = utterances(transcript, gap)
    if not spans or not cuts.keeps:
        return cuts, 0, 0
    quiet = silences(transcript, media_duration)
    words = [(w.start, w.end) for w in transcript.words]

    def candidates(at: float, is_start: bool) -> list[float]:
        """Where this boundary could go, best first.

        Both edges of the utterance, then the nearest gap between two words.
        Ordered by how far the cut has to move, because the smallest change to
        the edit that gets out of the word is the right one.
        """
        inside = _containing(spans, at)
        if not inside:
            return [at]
        options = [inside[0] - margin, inside[1] + margin]
        options.extend(_quiet_near(quiet, at))
        options = [o for o in options if abs(o - at) <= max_shift] or options[:1]
        return sorted(set(options), key=lambda o: abs(o - at))

    def outside_a_word(at: float) -> bool:
        return not any(w0 < at < w1 for w0, w1 in words)

    moved = 0
    stuck = 0
    out: list[Keep] = []
    for keep in cuts.keeps:
        # Every combination of where each edge could go, scored. The winner is
        # the one that gets both edges out of a word WITHOUT MAKING THE TAKE
        # LONGER, moving as little as possible.
        #
        # `may_grow` says whether the caller can still re-fit afterwards.
        #
        # The first pass may grow a take, because reaching out to keep a whole
        # sentence is the better cut and the duration fit that follows will
        # reclaim the time. The LAST pass may not: nothing runs after it, so
        # anything it grows grows the finished edit. Four jobs asking for
        # exactly 15s once came out at 16.28, 16.28, 20.17 and 24.54 -- and
        # forbidding growth everywhere then cost the other half, 10.24s against
        # the same 15s, because every take shrank and nothing gave it back.
        best = None
        for a in candidates(keep.start, True):
            for b in candidates(keep.end, False):
                lo, hi = max(0.0, a), min(media_duration, b)
                if hi - lo < min_length or hi <= lo:
                    continue
                if not may_grow and hi - lo > keep.duration + 1e-9:
                    continue
                score = (
                    not (outside_a_word(lo) and outside_a_word(hi)),
                    abs(lo - keep.start) + abs(hi - keep.end),
                )
                if best is None or score < best[0]:
                    best = (score, lo, hi)
        start, end = (best[1], best[2]) if best else (keep.start, keep.end)

        if (start, end) != (keep.start, keep.end):
            moved += 1
        stuck += sum(1 for edge in (start, end)
                     if any(s < edge < e for s, e in words))
        out.append(keep if (start, end) == (keep.start, keep.end)
                   else replace(keep, start=start, end=end,
                                word_count=len(transcript.slice(start, end))))

    return replace(cuts, keeps=out), moved, stuck


def drop_silence(
    cuts: CutPlan,
    transcript: Transcript,
    *,
    allowed: float,
    min_length: float = 0.4,
    gap: float = UTTERANCE_GAP,
) -> tuple[CutPlan, float]:
    """Trim silence at the head and tail of each take down to `allowed`.

    Returns the adjusted plan and the seconds removed.

    Head and tail only. A silence in the MIDDLE of a take would have to split it
    in two, which changes the shot count, the pacing and the beat alignment --
    a different feature, and one that should not arrive as a side effect of a
    slider labelled "allow this much silence".

    A take with no speech at all is dropped outright: the editor asked for
    silence to go, and a shot that is nothing but silence is the clearest case
    there is.
    """
    spans = utterances(transcript, gap)
    if not cuts.keeps:
        return cuts, 0.0

    removed = 0.0
    keeps: list[Keep] = []
    drops = list(cuts.drops)
    for keep in cuts.keeps:
        inside = [(max(s, keep.start), min(e, keep.end))
                  for s, e in spans if e > keep.start and s < keep.end]
        if not inside:
            removed += keep.duration
            drops.append(Drop(start=keep.start, end=keep.end,
                              reason=str(note("silence.shotDropped"))))
            continue

        start = max(keep.start, inside[0][0] - allowed)
        end = min(keep.end, inside[-1][1] + allowed)
        if end - start < min_length:
            keeps.append(keep)
            continue
        removed += (start - keep.start) + (keep.end - end)
        keeps.append(replace(keep, start=start, end=end,
                             word_count=len(transcript.slice(start, end))))

    return replace(cuts, keeps=keeps, drops=drops), removed
