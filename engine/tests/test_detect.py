"""Behavioural tests for cut planning.

These encode editorial policy, not just code paths. When one fails, the question
is "did we mean to change the edit?" -- not "which line broke?".
"""

import pytest

from autoedit.detect import (
    KIND_FILLER,
    KIND_SILENCE,
    KIND_STUTTER,
    CutPlan,
    DetectionSettings,
    Keep,
    plan_cuts,
)
from autoedit.transcript import Transcript


def read(transcript: Transcript, plan) -> list[str]:
    """The words that survive, in order -- what the editor actually hears."""
    return [w.text for k in plan.keeps for w in transcript.slice(k.start, k.end)]


# ------------------------------------------------------------------ fillers


def test_removes_hesitation_sounds_by_default(mktranscript):
    t = mktranscript([("I", 0.0), ("um", 0.4), ("think", 0.8), ("uh", 1.2), ("so", 1.6)])
    plan = plan_cuts(t, 2.5)
    assert read(t, plan) == ["I", "think", "so"]


def test_leaves_real_words_alone_in_conservative_mode(mktranscript):
    """'like' is a verb before it is a filler. Conservative mode must not guess."""
    t = mktranscript([("I", 0.0), ("like", 0.4), ("it", 0.8)])
    plan = plan_cuts(t, 1.5)
    assert read(t, plan) == ["I", "like", "it"]


def test_aggressive_mode_removes_discourse_fillers(mktranscript):
    t = mktranscript([("it", 0.0), ("was", 0.4), ("like", 0.8), ("huge", 1.2)])
    plan = plan_cuts(t, 2.0, DetectionSettings(filler_mode="aggressive"))
    assert read(t, plan) == ["it", "was", "huge"]


def test_aggressive_mode_removes_multiword_fillers_spoken_as_a_unit(mktranscript):
    t = mktranscript([
        ("it", 0.00, 0.20), ("was", 0.22, 0.20), ("you", 0.44, 0.15),
        ("know", 0.60, 0.18), ("fine", 0.80, 0.30),
    ])
    plan = plan_cuts(t, 1.5, DetectionSettings(filler_mode="aggressive"))
    assert read(t, plan) == ["it", "was", "fine"]


def test_multiword_filler_with_a_pause_inside_is_a_real_clause(mktranscript):
    """'you ... know' with a beat between them is speech, not filler."""
    t = mktranscript([("you", 0.0, 0.2), ("know", 0.9, 0.3), ("him", 1.4, 0.3)])
    plan = plan_cuts(t, 2.0, DetectionSettings(filler_mode="aggressive", min_silence=5.0))
    assert read(t, plan) == ["you", "know", "him"]


def test_filler_mode_off_disables_filler_cuts(mktranscript):
    t = mktranscript([("I", 0.0), ("um", 0.4), ("think", 0.8)])
    plan = plan_cuts(t, 1.5, DetectionSettings(filler_mode="off"))
    assert read(t, plan) == ["I", "um", "think"]
    assert not [d for d in plan.drops if d.kind == KIND_FILLER]


def test_keep_fillers_is_an_escape_hatch_for_false_positives(mktranscript):
    t = mktranscript([("the", 0.0), ("ah", 0.4), ("moment", 0.8)])
    plan = plan_cuts(t, 1.5, DetectionSettings(keep_fillers=frozenset({"ah"})))
    assert "ah" in read(t, plan)


def test_filler_cuts_are_tight_not_padded(mktranscript):
    """Handles on a filler would leave most of the filler in -- the classic way
    this feature looks broken."""
    t = mktranscript([("I", 0.0), ("um", 0.4, 0.3), ("think", 0.8)])
    plan = plan_cuts(t, 1.5, DetectionSettings(lead_in=0.2, tail=0.2))
    filler = [d for d in plan.drops if d.kind == KIND_FILLER][0]
    assert filler.start == pytest.approx(0.4)
    assert filler.end == pytest.approx(0.7)


# ------------------------------------------------------------------ stutters


def test_removes_false_start_and_keeps_the_completed_word(mktranscript):
    t = mktranscript([("that-", 0.0, 0.2), ("that", 0.25, 0.3), ("works", 0.6)])
    plan = plan_cuts(t, 1.5)
    assert read(t, plan) == ["that", "works"]


def test_removes_immediate_repetition(mktranscript):
    t = mktranscript([("I", 0.0, 0.15), ("I", 0.18, 0.2), ("agree", 0.45)])
    plan = plan_cuts(t, 1.2)
    assert read(t, plan) == ["I", "agree"]


def test_spaced_repetition_is_deliberate_emphasis(mktranscript):
    """'No. No. Absolutely not.' is rhetoric, not a stumble."""
    t = mktranscript([("no", 0.0, 0.3), ("no", 1.2, 0.3), ("never", 2.4, 0.4)])
    plan = plan_cuts(t, 3.2, DetectionSettings(min_silence=5.0))
    assert read(t, plan) == ["no", "no", "never"]


def test_stutter_removal_can_be_disabled(mktranscript):
    t = mktranscript([("I", 0.0, 0.15), ("I", 0.18, 0.2), ("agree", 0.45)])
    plan = plan_cuts(t, 1.2, DetectionSettings(remove_stutters=False))
    assert read(t, plan) == ["I", "I", "agree"]


# ------------------------------------------------------------------ silence


def test_trims_a_long_pause_but_leaves_handles(mktranscript):
    t = mktranscript([("before", 0.0, 0.4), ("after", 3.0, 0.4)])
    s = DetectionSettings(lead_in=0.12, tail=0.08, trim_head_tail=False)
    plan = plan_cuts(t, 3.8, s)
    gap = [d for d in plan.drops if d.kind == KIND_SILENCE][0]
    assert gap.start == pytest.approx(0.40 + 0.08)   # tail handle after 'before'
    assert gap.end == pytest.approx(3.00 - 0.12)     # lead-in handle before 'after'


def test_short_pauses_are_speech_rhythm_and_survive(mktranscript):
    t = mktranscript([("a", 0.0, 0.2), ("b", 0.45, 0.2), ("c", 0.90, 0.2)])
    plan = plan_cuts(t, 1.3, DetectionSettings(min_silence=0.40, trim_head_tail=False))
    assert plan.drops == []
    assert len(plan.keeps) == 1


def test_trims_lead_in_and_trailing_silence(mktranscript):
    t = mktranscript([("hello", 2.0, 0.5)])
    plan = plan_cuts(t, 6.0)
    assert plan.keeps[0].start > 1.0
    assert plan.keeps[-1].end < 5.0


def test_head_trim_is_not_cancelled_by_min_clip_length(mktranscript):
    """Regression: a handle applied at frame 0 left a sliver that tripped the
    min-clip-length rule and silently cancelled the whole head trim."""
    t = mktranscript([("word", 1.5, 0.4)])
    plan = plan_cuts(t, 3.0, DetectionSettings(tail=0.5, lead_in=0.12))
    assert plan.keeps[0].start == pytest.approx(1.38)


# ------------------------------------------------------------------ safety


def test_never_discards_speech(mktranscript):
    """The core safety property. Every non-filler, non-stutter word must survive."""
    t = mktranscript([
        ("the", 0.0, 0.2), ("um", 0.3, 0.2), ("quick", 0.6, 0.3), ("brown", 1.0, 0.3),
        ("fox", 3.5, 0.3), ("uh", 3.9, 0.2), ("jumps", 4.2, 0.4), ("over", 4.7, 0.3),
    ])
    plan = plan_cuts(t, 6.0)
    survived = read(t, plan)
    for expected in ["the", "quick", "brown", "fox", "jumps", "over"]:
        assert expected in survived, f"lost {expected!r} -- content loss is never acceptable"


def test_refuses_to_cut_inside_low_confidence_speech(mktranscript):
    """Cutting on a misheard word is worse than leaving a pause in."""
    t = mktranscript([
        ("clear", 0.0, 0.3, 0.99),
        ("mumble", 1.5, 0.4, 0.20),   # transcript is not trustworthy here
        ("clear", 3.5, 0.3, 0.99),
    ])
    plan = plan_cuts(t, 4.5, DetectionSettings(min_confidence=0.55, trim_head_tail=False))
    assert plan.drops == []
    assert any("low-confidence" in w for w in plan.warnings)


def test_low_confidence_does_not_block_cuts_elsewhere(mktranscript):
    t = mktranscript([
        ("a", 0.0, 0.3, 0.99), ("b", 3.0, 0.3, 0.99),      # clean gap, cuttable
        ("mumble", 6.0, 0.3, 0.10),                          # bad patch, far away
    ])
    plan = plan_cuts(t, 7.0, DetectionSettings(trim_head_tail=False))
    assert any(d.kind == KIND_SILENCE for d in plan.drops)


def test_never_cuts_mid_word(mktranscript):
    t = mktranscript([("alpha", 0.0, 0.5), ("beta", 2.5, 0.5), ("gamma", 5.0, 0.5)])
    plan = plan_cuts(t, 6.0)
    for w in t.words:
        for k in plan.keeps:
            overlaps = w.start < k.end and k.start < w.end
            if overlaps:
                assert k.start <= w.start + 1e-6 and w.end <= k.end + 1e-6, (
                    f"clip boundary falls inside {w.text!r}"
                )


def test_no_surviving_clip_is_shorter_than_the_floor(mktranscript):
    t = mktranscript([(t_, i * 0.5, 0.15) for i, t_ in enumerate("abcdefgh")])
    s = DetectionSettings(min_silence=0.30, min_clip_length=0.35, trim_head_tail=False)
    plan = plan_cuts(t, 4.5, s)
    for k in plan.keeps:
        assert k.duration >= s.min_clip_length - 1e-6


def test_warns_when_the_cut_is_unusually_aggressive(mktranscript):
    t = mktranscript([("word", 9.0, 0.3)])
    plan = plan_cuts(t, 10.0)
    assert any("aggressive" in w for w in plan.warnings)


def test_empty_transcript_passes_the_clip_through_untouched(mktranscript):
    t = mktranscript([])
    plan = plan_cuts(t, 5.0)
    assert len(plan.keeps) == 1
    assert plan.keeps[0].start == 0.0 and plan.keeps[0].end == 5.0
    assert plan.warnings


def test_flags_out_of_order_timestamps(mktranscript):
    t = mktranscript([("b", 2.0, 0.3), ("a", 0.5, 0.3)])
    plan = plan_cuts(t, 3.0)
    assert any("chronological" in w for w in plan.warnings)


# ------------------------------------------------------------------ determinism


def test_is_deterministic(mktranscript):
    spec = [("a", 0.0), ("um", 0.5), ("b", 1.0), ("c", 4.0), ("uh", 4.5), ("d", 5.0)]
    runs = [plan_cuts(mktranscript(spec), 6.5) for _ in range(5)]
    first = [(k.start, k.end) for k in runs[0].keeps]
    for r in runs[1:]:
        assert [(k.start, k.end) for k in r.keeps] == first


def test_keeps_and_drops_tile_the_source_without_gaps_or_overlaps(mktranscript):
    t = mktranscript([("a", 0.0, 0.3), ("um", 0.5, 0.3), ("b", 1.0, 0.3), ("c", 4.0, 0.3)])
    plan = plan_cuts(t, 5.0)
    spans = sorted([(k.start, k.end) for k in plan.keeps] + [(d.start, d.end) for d in plan.drops])
    assert spans[0][0] == pytest.approx(0.0)
    assert spans[-1][1] == pytest.approx(5.0)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end == pytest.approx(start), "source timeline must tile exactly"


def test_handle_does_not_leak_a_filler_back_into_the_next_clip(mktranscript):
    """Regression: a filler adjacent to a pause merges into the silence run, and
    the silence handle then reached back across the filler boundary, leaving the
    tail of the 'uh' at the head of the following clip."""
    t = mktranscript([
        ("trust", 3.4, 0.46),
        ("uh", 4.90, 0.18),          # filler, immediately after a long pause
        ("nothing", 5.22, 0.48),
    ])
    plan = plan_cuts(t, 7.0, DetectionSettings(min_silence=0.5, lead_in=0.15, tail=0.10))
    after_gap = [k for k in plan.keeps if k.start > 4.0][0]
    assert after_gap.start >= 5.08 - 1e-6, (
        f"clip starts at {after_gap.start:.3f}, inside the 'uh' (4.90-5.08)"
    )
    assert "uh" not in read(t, plan)


def test_filler_at_the_head_of_a_clip_is_fully_removed(mktranscript):
    """Same failure in the other direction: a filler immediately *before* a pause."""
    t = mktranscript([
        ("word", 0.0, 0.4),
        ("um", 0.45, 0.25),
        ("next", 3.0, 0.4),
    ])
    plan = plan_cuts(t, 4.0, DetectionSettings(min_silence=0.5, lead_in=0.15, tail=0.10))
    first = plan.keeps[0]
    assert first.end <= 0.45 + 1e-6, f"clip ends at {first.end:.3f}, inside the 'um'"
    assert "um" not in read(t, plan)


def test_summary_reports_removal_from_what_was_kept():
    """Regression: summing the drop list reported 'removed 0%' beside
    'kept 1.6s of 5.5s', because visual cut planning only records drops for
    shots it rejects outright."""
    plan = CutPlan(keeps=[Keep(0.0, 1.6)], drops=[])
    assert "removed 71%" in plan.summary(5.5)
