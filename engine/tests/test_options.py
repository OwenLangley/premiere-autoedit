"""Editor-facing options.

These encode promises made to an editor in the panel: 'punchy' really does cut
harder than 'relaxed', and a 30-second request really does come back at 30 seconds.
"""

import pytest

from autoedit.detect import CutPlan, Keep
from autoedit.options import (
    ASPECT_FRAMES,
    PACING,
    JobOptions,
    OptionError,
    apply_pacing,
    fit_duration_across,
)
from autoedit.recipe import load_recipe


@pytest.fixture
def base():
    r = load_recipe("promo-silent")
    return r.detection, r.visual


# ------------------------------------------------------------------ pacing


def test_pacing_orders_as_an_editor_would_expect(base):
    """The whole point of the control: punchy cuts harder than relaxed."""
    det, vis = {}, {}
    for name in PACING:
        det[name], vis[name] = apply_pacing(*base, name)

    assert det["punchy"].min_silence < det["standard"].min_silence < det["relaxed"].min_silence
    assert vis["punchy"].shot_duration < vis["standard"].shot_duration < vis["relaxed"].shot_duration
    assert vis["punchy"].beats_per_shot < vis["standard"].beats_per_shot < vis["relaxed"].beats_per_shot


def test_standard_leaves_the_recipe_untouched(base):
    det, vis = apply_pacing(*base, "standard")
    assert det.min_silence == base[0].min_silence
    assert vis.shot_duration == base[1].shot_duration
    assert det.filler_mode == base[0].filler_mode


def test_pacing_scales_rather_than_replaces():
    """Two recipes at the same pacing must still differ, or recipes stop mattering."""
    podcast = load_recipe("podcast-2cam")
    promo = load_recipe("client-promo")
    a, _ = apply_pacing(podcast.detection, podcast.visual, "punchy")
    b, _ = apply_pacing(promo.detection, promo.visual, "punchy")
    assert a.min_silence != b.min_silence


def test_pacing_never_lets_handles_cancel_every_cut(base):
    """min_silence below lead_in+tail means every pause is eaten by its own
    handles and nothing is ever cut -- a silent no-op."""
    for name in PACING:
        det, _ = apply_pacing(*base, name)
        assert det.min_silence > det.lead_in + det.tail


def test_beats_per_shot_never_drops_below_one(base):
    """Half a beat is a stutter, not a pace."""
    for name in PACING:
        _, vis = apply_pacing(*base, name)
        assert vis.beats_per_shot >= 1


# ------------------------------------------------------------------ options


def test_rejects_unknown_values():
    with pytest.raises(OptionError, match="aspect"):
        JobOptions(aspect="widescreen")
    with pytest.raises(OptionError, match="pacing"):
        JobOptions(pacing="frantic")
    with pytest.raises(OptionError, match="duration"):
        JobOptions(duration_mode="upTo")          # no seconds given


def test_parses_a_panel_request():
    o = JobOptions.from_request({
        "aspect": "vertical",
        "duration": {"mode": "exactly", "seconds": 30},
        "pacing": "punchy",
        "look": "brand-punch",
    })
    assert o.frame_size == (1080, 1920)
    assert (o.duration_mode, o.duration_seconds, o.pacing) == ("exactly", 30, "punchy")


def test_empty_request_is_valid_and_neutral():
    o = JobOptions.from_request({})
    assert o.aspect == "source" and o.pacing == "standard" and o.duration_mode == "none"
    assert o.frame_size is None


@pytest.mark.parametrize("aspect,ratio", [
    ("landscape", 16 / 9), ("vertical", 9 / 16), ("square", 1.0), ("portrait45", 4 / 5),
])
def test_frame_sizes_match_their_names(aspect, ratio):
    w, h = ASPECT_FRAMES[aspect]
    assert w / h == pytest.approx(ratio, rel=0.01)


# ------------------------------------------------------------------ duration


def plans(*durations, confidences=None):
    confidences = confidences or [0.9] * len(durations)
    keeps, t = [], 0.0
    for d, c in zip(durations, confidences):
        keeps.append(Keep(t, t + d, f"clip {len(keeps)}", c))
        t += d + 1.0
    return [("A", CutPlan(keeps, [], []))]


def total(out):
    return sum(k.duration for _, p in out for k in p.keeps)


def test_no_target_leaves_the_cut_alone():
    out = fit_duration_across(plans(4, 4, 4), "none", None)
    assert total(out) == pytest.approx(12)


def test_up_to_fills_the_budget_rather_than_undershooting():
    """Dropping only whole clips turned 'up to 3s' into 1.6s, which is not what
    anyone means by that."""
    out = fit_duration_across(plans(2, 2, 2), "upTo", 3.0)
    assert total(out) == pytest.approx(3.0, abs=0.01)


def test_up_to_never_exceeds_the_target():
    out = fit_duration_across(plans(2, 2, 2), "upTo", 5.0)
    assert total(out) <= 5.0 + 1e-6


def test_exactly_hits_the_target():
    out = fit_duration_across(plans(2, 2, 2), "exactly", 5.0)
    assert total(out) == pytest.approx(5.0, abs=0.01)


def test_about_leaves_a_near_enough_cut_alone():
    out = fit_duration_across(plans(3, 3), "about", 6.5, tolerance=0.15)
    assert total(out) == pytest.approx(6.0)


def test_short_material_warns_rather_than_pretending():
    out = fit_duration_across(plans(2), "upTo", 30.0)
    assert any("not enough usable material" in w for _, p in out for w in p.warnings)


def test_worst_strategy_keeps_the_best_clips():
    out = fit_duration_across(
        plans(2, 2, 2, confidences=[0.2, 0.95, 0.9]), "upTo", 4.0, strategy="worst"
    )
    kept = [k.confidence for _, p in out for k in p.keeps]
    assert 0.2 not in kept


def test_tail_strategy_keeps_the_opening():
    """A narrative edit cannot start in the middle."""
    out = fit_duration_across(
        plans(2, 2, 2, confidences=[0.2, 0.95, 0.9]), "upTo", 4.0, strategy="tail"
    )
    assert [k.reason for _, p in out for k in p.keeps][0] == "clip 0"


def test_trimming_is_reported():
    out = fit_duration_across(plans(4, 4, 4), "upTo", 5.0)
    assert any("trimmed" in w for _, p in out for w in p.warnings)


def test_fitting_across_sources_does_not_split_the_budget_evenly():
    """One file usually carries most of the usable material."""
    two = [
        ("A", CutPlan([Keep(0, 5, "a", 0.9)], [], [])),
        ("B", CutPlan([Keep(0, 1, "b", 0.9)], [], [])),
    ]
    out = fit_duration_across(two, "upTo", 5.0)
    assert total(out) == pytest.approx(5.0, abs=0.01)


# --- the working frame size -------------------------------------------------
#
# "Match source" used to take the footage's full dimensions, which turned a bin
# of 4K 59.94 10-bit 4:2:2 HEVC into a 4K 59.94 sequence. It did not play back:
# the picture updated about once a second and read as a series of stills. It also
# upscaled the one 1080p camera by 2x to fill the frame.

from autoedit.options import working_frame_size


def test_4k_comes_down_to_hd():
    assert working_frame_size(3840, 2160) == (1920, 1080)


def test_vertical_4k_comes_down_by_its_long_edge():
    assert working_frame_size(2160, 3840) == (1080, 1920)


def test_hd_is_left_exactly_alone():
    # The 1080p camera in a mixed bin must not be resampled at all.
    assert working_frame_size(1920, 1080) == (1920, 1080)


def test_smaller_than_hd_is_not_upscaled():
    assert working_frame_size(1280, 720) == (1280, 720)


def test_the_shape_survives_the_scaling():
    for w, h in [(3840, 2160), (4096, 2160), (2880, 2160), (6144, 3456), (2160, 3840)]:
        out_w, out_h = working_frame_size(w, h)
        assert abs((out_w / out_h) - (w / h)) < 0.01, f"{w}x{h} changed shape"
        assert max(out_w, out_h) <= 1920


def test_dimensions_come_out_even():
    # Odd dimensions break chroma subsampling in most codecs.
    for w, h in [(4096, 2160), (3840, 1606), (5464, 3070), (2049, 1081)]:
        out_w, out_h = working_frame_size(w, h)
        assert out_w % 2 == 0 and out_h % 2 == 0, f"{w}x{h} -> {out_w}x{out_h}"


# --- spreading a target across every source ---------------------------------
#
# The complaint: "the edits are only using two clips". A 15s target was filled
# from the first file's spans and the other five were dropped whole -- and the
# same defect made the cut sparse, because fewer sources means fewer, longer
# shots.

from autoedit.detect import CutPlan, Keep  # noqa: E402


def _plan(*spans):
    return CutPlan(keeps=[Keep(a, b, "t", 1.0, 1) for a, b in spans], warnings=[])


def _sources(n, span=(0.0, 6.0)):
    return [(f"C{i}", _plan(span, (10.0, 13.0), (20.0, 22.0))) for i in range(n)]


def test_every_selected_clip_appears():
    out = fit_duration_across(_sources(6), "exactly", 15.0, min_clip_length=0.35,
                              strategy="spread")
    used = [mid for mid, plan in out if plan.keeps]
    assert len(used) == 6, f"only {len(used)} of 6 sources survived"


def test_a_source_whose_first_span_overruns_its_share_is_trimmed_not_dropped():
    # Share is 15/6 = 2.5s but every first span is 6s. Skipping them would drop
    # every source; trimming keeps them all.
    out = fit_duration_across(_sources(6), "exactly", 15.0, min_clip_length=0.35,
                              strategy="spread")
    for mid, plan in out:
        assert plan.keeps, f"{mid} contributed nothing"
        assert plan.keeps[0].duration <= 2.6


def test_the_target_is_still_respected():
    out = fit_duration_across(_sources(6), "exactly", 15.0, min_clip_length=0.35,
                              strategy="spread")
    total = sum(k.duration for _, plan in out for k in plan.keeps)
    assert total <= 15.5, f"overshot the target at {total}s"


def test_leftover_budget_goes_back_to_sources_that_can_use_it():
    # Two tiny sources and one long one: the long one should absorb the slack
    # rather than leaving the edit short.
    plans = [("tiny1", _plan((0.0, 0.5))), ("tiny2", _plan((0.0, 0.5))),
             ("long", _plan((0.0, 4.0), (5.0, 9.0), (10.0, 14.0)))]
    out = fit_duration_across(plans, "exactly", 12.0, min_clip_length=0.35,
                              strategy="spread")
    total = sum(k.duration for _, plan in out for k in plan.keeps)
    assert total > 8.0, f"left {12.0 - total:.1f}s of the target unused"


def test_tail_is_unchanged_for_speech_recipes():
    # A narrative keeps its opening; that behaviour must not have moved.
    out = fit_duration_across(_sources(6), "exactly", 15.0, min_clip_length=0.35,
                              strategy="tail")
    used = [mid for mid, plan in out if plan.keeps]
    assert len(used) < 6


def test_a_shot_cap_turns_one_long_shot_into_several():
    """The other half of "only 3 or 4 cuts".

    Without a cap each source spent its whole share on one shot, so a six-clip
    edit had six cuts however punchy the pacing was.
    """
    plans = [(f"C{i}", _plan((0.0, 6.0), (8.0, 12.0), (14.0, 18.0))) for i in range(3)]
    loose = fit_duration_across(plans, "exactly", 12.0, min_clip_length=0.35,
                                strategy="spread")
    tight = fit_duration_across(plans, "exactly", 12.0, min_clip_length=0.35,
                                strategy="spread", max_shot=1.0)
    n_loose = sum(len(p.keeps) for _, p in loose)
    n_tight = sum(len(p.keeps) for _, p in tight)
    assert n_tight > n_loose, f"the cap made no difference ({n_tight} vs {n_loose})"


def test_shots_land_on_whole_beats_when_a_quantum_is_given():
    beat = 0.5
    plans = [(f"C{i}", _plan((0.0, 9.0))) for i in range(3)]
    # "upTo" rather than "exactly": exact mode deliberately stretches the LAST
    # clip to land on the target, and that stretch is not beat-aligned.
    out = fit_duration_across(plans, "upTo", 12.0, min_clip_length=0.35,
                              strategy="spread", max_shot=2.0, quantum=beat)
    for _, plan in out:
        for k in plan.keeps:
            beats = k.duration / beat
            # A couple of frames of slack is deliberate -- see BEAT_SLACK.
            assert abs(beats - round(beats)) < 0.15, f"{k.duration}s is {beats} beats"


def test_the_cap_does_not_starve_the_target():
    """Capping shot length must not quietly halve the edit.

    Trimming spans to exactly a whole number of beats did: frame snapping left
    them a frame short, the beat quantiser dropped a whole beat from each, and a
    15s target came out at 7.6s.
    """
    # Sources with plenty of separate spans, which is the realistic case -- the
    # silences between them are what make each join a visible cut.
    plans = [(f"C{i}", _plan(*[(t, t + 2.0) for t in range(0, 30, 4)])) for i in range(4)]
    out = fit_duration_across(plans, "upTo", 16.0, min_clip_length=0.35,
                              strategy="spread", max_shot=1.0, quantum=0.5)
    total = sum(k.duration for _, plan in out for k in plan.keeps)
    assert total > 15.0, f"delivered only {total:.1f}s of a 16s target"


def test_a_source_with_one_long_span_contributes_one_shot_and_no_more():
    """A deliberate limit, recorded so it is not mistaken for a bug.

    Filling a share from a single span would mean slicing it into consecutive
    pieces -- and consecutive pieces of the same span are laid end to end, so the
    join between them shows nothing. That would inflate the cut count with
    invisible cuts, which is worse than coming up short and saying so. The
    shortfall is reported instead (see the delivered-length check in cli.py).
    """
    plans = [(f"C{i}", _plan((0.0, 20.0))) for i in range(4)]
    out = fit_duration_across(plans, "upTo", 16.0, min_clip_length=0.35,
                              strategy="spread", max_shot=1.0, quantum=0.5)
    assert all(len(plan.keeps) == 1 for _, plan in out)
