"""Cutting around what people are saying.

The picture path scores shots on sharpness and motion and knows nothing about
speech. On real footage of a coach shouting instructions it put three cut
boundaries inside a word, one of them inside a single character.
"""

import pytest

from autoedit.detect import CutPlan, Keep
from autoedit.speech import (
    SNAP_MARGIN, drop_silence, protect, silences, utterances,
)
from autoedit.transcript import Transcript, Word


def words(*specs):
    return [Word(text=t, start=s, end=e) for t, s, e in specs]


def tx(*specs):
    return Transcript(media_id="A", words=list(words(*specs)))


def plan(*spans):
    return CutPlan(keeps=[Keep(start=s, end=e, reason="shot") for s, e in spans],
                   drops=[], warnings=[])


# ------------------------------------------------------------- utterances

def test_words_close_together_are_one_utterance():
    t = tx(("a", 0.0, 0.3), ("b", 0.35, 0.6), ("c", 0.65, 0.9))
    assert utterances(t) == [(0.0, 0.9)]


def test_a_long_pause_separates_utterances():
    t = tx(("a", 0.0, 0.3), ("b", 2.0, 2.3))
    assert utterances(t) == [(0.0, 0.3), (2.0, 2.3)]


def test_no_words_means_no_utterances():
    assert utterances(Transcript(media_id="A")) == []


# ---------------------------------------------------------------- protect

def test_a_cut_inside_a_word_is_moved_out_of_it():
    """The reported defect: a boundary landing mid-word.

    Measured on a real plan -- "cut out at 10.76s lands INSIDE '距'".
    """
    t = tx(("hello", 1.0, 1.4), ("there", 1.45, 2.0))
    got, moved, _ = protect(plan((0.0, 1.6)), t, media_duration=10.0)
    assert moved == 1
    # Out of "there" and into the gap between the two words. NOT forward to 2.05:
    # that would lengthen the take, and nothing re-fits the edit afterwards.
    assert got.keeps[0].end == pytest.approx(1.425)
    assert got.keeps[0].duration <= 1.6


def test_a_whole_utterance_goes_in_or_out_never_half():
    t = tx(("word", 5.0, 5.4))
    for cut in (5.1, 5.2, 5.3):
        got, _, _ = protect(plan((0.0, cut)), t, media_duration=10.0)
        end = got.keeps[0].end
        assert end <= 5.0 or end >= 5.4, f"cut at {cut} left {end}, inside the word"


def test_a_boundary_already_in_silence_is_left_alone():
    # Moving a cut that was fine is its own defect.
    t = tx(("a", 1.0, 1.4), ("b", 3.0, 3.4))
    got, moved, _ = protect(plan((0.0, 2.0)), t, media_duration=10.0)
    assert moved == 0
    assert got.keeps[0].end == 2.0


def test_a_far_boundary_excludes_the_utterance_rather_than_reaching():
    """Beyond max_shift the sentence is dropped, not chased.

    A cut that drifts to save a word has stopped being the cut anyone chose.
    """
    t = tx(("a", 1.0, 1.2), ("verylong", 1.25, 9.0))
    got, _, _ = protect(plan((0.0, 1.5)), t, media_duration=20.0, max_shift=1.5)
    # Reaching forward to 9.0 is 7.5s away and would lengthen the take besides,
    # so the cut lands in the gap between the two words instead.
    assert got.keeps[0].end == pytest.approx(1.225)
    assert got.keeps[0].duration <= 1.5


def test_a_keep_that_would_collapse_is_left_as_it_was():
    # A boundary that moved through its own clip is worse than one that cut a
    # word.
    t = tx(("all", 0.0, 5.0))
    got, _, _ = protect(plan((1.0, 1.5)), t, media_duration=10.0, min_length=0.4)
    assert got.keeps[0].start == 1.0 and got.keeps[0].end == 1.5


def test_boundaries_stay_inside_the_media():
    t = tx(("edge", 0.0, 1.0))
    got, _, _ = protect(plan((0.5, 2.0)), t, media_duration=1.8)
    assert got.keeps[0].start >= 0.0
    assert got.keeps[0].end <= 1.8


def test_no_speech_changes_nothing():
    got, moved, _ = protect(plan((0.0, 2.0)), Transcript(media_id="A"), media_duration=10.0)
    assert moved == 0 and got.keeps[0].end == 2.0


# ----------------------------------------------------------- drop silence

def test_silence_beyond_the_allowance_is_trimmed():
    t = tx(("hi", 4.0, 4.5))
    got, removed = drop_silence(plan((0.0, 10.0)), t, allowed=0.5)
    assert got.keeps[0].start == pytest.approx(3.5)
    assert got.keeps[0].end == pytest.approx(5.0)
    assert removed == pytest.approx(8.5)


def test_a_bigger_allowance_keeps_more():
    t = tx(("hi", 4.0, 4.5))
    got, _ = drop_silence(plan((0.0, 10.0)), t, allowed=2.0)
    assert got.keeps[0].start == pytest.approx(2.0)
    assert got.keeps[0].end == pytest.approx(6.5)


def test_a_shot_with_nothing_said_is_dropped_and_recorded():
    t = tx(("hi", 20.0, 20.5))
    got, removed = drop_silence(plan((0.0, 5.0)), t, allowed=0.5)
    assert got.keeps == []
    assert len(got.drops) == 1
    assert removed == pytest.approx(5.0)


def test_trimming_never_makes_a_take_too_short_to_use():
    t = tx(("hi", 4.0, 4.02))
    got, _ = drop_silence(plan((0.0, 10.0)), t, allowed=0.0, min_length=0.4)
    assert got.keeps[0].start == 0.0 and got.keeps[0].end == 10.0


def test_an_allowance_of_zero_keeps_only_the_speech():
    t = tx(("one", 2.0, 2.5), ("two", 2.6, 3.5))
    got, _ = drop_silence(plan((0.0, 8.0)), t, allowed=0.0)
    assert got.keeps[0].start == pytest.approx(2.0)
    assert got.keeps[0].end == pytest.approx(3.5)


def test_the_two_passes_compose_without_re_breaking_a_word():
    """Trimming then protecting must not put a boundary back inside a word."""
    t = tx(("one", 2.0, 2.5), ("two", 5.0, 6.0))
    trimmed, _ = drop_silence(plan((0.0, 8.0)), t, allowed=0.4)
    got, _, _ = protect(trimmed, t, media_duration=8.0)
    spans = utterances(t)
    for keep in got.keeps:
        for edge in (keep.start, keep.end):
            assert not any(s < edge < e for s, e in spans), f"{edge} is inside a word"


def test_a_protected_boundary_survives_frame_snapping():
    """The bug that made the first fix useless.

    `append_cuts` snaps every source point to the frame grid, rounding to
    nearest. A boundary left exactly on a word's edge rounds back inside it:
    protect moved a cut to 1.1600, the word began at 1.1600, and snapping at
    59.94fps produced 1.1678 -- 7.8ms inside the word. Eleven cuts were
    reported protected and ten were still mid-word.
    """
    fps = 60000 / 1001
    t = tx(("word", 1.16, 1.40), ("next", 1.5, 3.5))
    got, _, _ = protect(plan((0.0, 1.1678)), t, media_duration=10.0)
    spans = utterances(t)
    for keep in got.keeps:
        for edge in (keep.start, keep.end):
            snapped = round(edge * fps) / fps
            assert not any(s < snapped < e for s, e in spans), (
                f"{edge} snapped to {snapped}, inside a word again")


@pytest.mark.parametrize("fps", [24, 25, 30000 / 1001, 50, 60000 / 1001])
def test_protection_survives_snapping_at_every_supported_rate(fps):
    t = tx(("a", 1.0, 1.5), ("b", 4.0, 4.6))
    spans = utterances(t)
    for cut in (1.2, 1.45, 4.1, 4.55):
        got, _, _ = protect(plan((0.0, cut)), t, media_duration=10.0)
        for keep in got.keeps:
            for edge in (keep.start, keep.end):
                snapped = round(edge * fps) / fps
                assert not any(s < snapped < e for s, e in spans), (
                    f"{fps:.2f}fps: cut {cut} -> {edge} -> {snapped}, inside a word")


def test_continuous_speech_still_gets_a_boundary_between_words():
    """The case that made the first two attempts useless.

    A coach shouting without pausing produces one utterance metres long -- 5.9s
    measured, against takes of 1.6s. "Keep the whole sentence" has nowhere to
    put the boundary, so it gave up and left the cut mid-word. Between two words
    is ordinary editing; through the middle of one never is.
    """
    spoken = [(f"w{i}", 1.0 + i * 0.3, 1.0 + i * 0.3 + 0.22) for i in range(20)]
    t = tx(*spoken)
    assert utterances(t) == [(1.0, 1.0 + 19 * 0.3 + 0.22)], "should be one long utterance"

    got, moved, _ = protect(plan((2.0, 3.6)), t, media_duration=10.0, min_length=0.4)
    assert moved == 1
    keep = got.keeps[0]
    assert keep.duration >= 0.4
    for edge in (keep.start, keep.end):
        assert not any(w.start < edge < w.end for w in t.words), f"{edge} is inside a word"


def test_the_word_gap_fallback_survives_frame_snapping():
    spoken = [(f"w{i}", 1.0 + i * 0.3, 1.0 + i * 0.3 + 0.22) for i in range(20)]
    t = tx(*spoken)
    for fps in (24, 25, 30000 / 1001, 50, 60000 / 1001):
        got, _, _ = protect(plan((2.0, 3.6)), t, media_duration=10.0, min_length=0.4)
        for keep in got.keeps:
            for edge in (keep.start, keep.end):
                snapped = round(edge * fps) / fps
                assert not any(w.start < snapped < w.end for w in t.words), (
                    f"{fps:.2f}fps: {edge} -> {snapped} landed inside a word")


def test_protection_never_lengthens_a_take():
    """The rule the finished edit depends on.

    This pass runs AFTER the duration fitting -- it has to, or the fitting moves
    boundaries back inside a word -- so nothing re-fits afterwards and a clip
    that grows here grows the film. Before this rule existed, four jobs asking
    for exactly 15s came out at 16.28, 16.28, 20.17 and 24.54 seconds.
    """
    t = tx(("short", 1.0, 1.3))
    for cut in (1.05, 1.1, 1.2, 1.25, 1.29):
        got, _, _ = protect(plan((0.0, cut)), t, media_duration=10.0)
        assert got.keeps[0].duration <= cut + 1e-9, (
            f"cut at {cut} grew to {got.keeps[0].duration}")


def test_no_take_grows_on_a_dense_transcript():
    # The realistic case: many short words, many candidate boundaries, and every
    # combination of them still has to respect the length.
    spoken = [(f"w{i}", 1.0 + i * 0.3, 1.0 + i * 0.3 + 0.22) for i in range(30)]
    t = tx(*spoken)
    original = plan((2.0, 3.6), (4.1, 5.9), (6.05, 7.0))
    got, _, _ = protect(original, t, media_duration=20.0, min_length=0.4)
    for before, after in zip(original.keeps, got.keeps):
        assert after.duration <= before.duration + 1e-9


def test_words_with_no_gap_between_them_are_reported_not_hidden():
    """Whisper sometimes emits a run with zero silence in it.

    One word ends exactly where the next begins, so there is no moment in that
    stretch that is not inside a word and no honest place to cut. Measured on
    real footage: 2 boundaries of 20 after protection, down from 11. The count
    exists so that stays visible instead of looking like success.
    """
    # Forty words end-to-end filling the whole clip: one utterance far longer
    # than max_shift in both directions, and not one gap inside it.
    spoken = [(f"w{i}", i * 0.5, (i + 1) * 0.5) for i in range(40)]
    t = tx(*spoken)
    assert silences(t, 20.0) == [], "the premise: no gap anywhere to cut in"

    got, moved, stuck = protect(plan((5.2, 6.6)), t, media_duration=20.0,
                                min_length=0.4)
    assert stuck == 2 and moved == 0
    assert got.keeps[0].start == 5.2 and got.keeps[0].end == 6.6


def test_nothing_stuck_when_there_are_gaps_to_use():
    t = tx(("a", 1.0, 1.4), ("b", 2.0, 2.4))
    _, _, stuck = protect(plan((1.2, 2.2)), t, media_duration=5.0, min_length=0.4)
    assert stuck == 0
