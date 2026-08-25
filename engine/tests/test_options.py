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
