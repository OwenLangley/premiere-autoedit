"""Visual cut planning.

The pure logic (merging, splitting, gating, selection) is tested without ffmpeg
so it stays fast. Two integration tests at the bottom exercise the real thing
against a generated fixture.
"""

from pathlib import Path

import pytest

from autoedit.music import BeatGrid
from autoedit.visual import (
    FrameSample,
    Shot,
    VisualAnalysis,
    VisualSettings,
    _merge_short,
    _split_long,
    plan_visual_cuts,
    score_shots,
)

FIXTURES = Path(__file__).parent / "fixtures"


def sample_run(start, end, motion=4.0, brightness=118.0, sharpness=3.0, step=0.2):
    t, out = start, []
    while t < end:
        out.append(FrameSample(t, motion, brightness, sharpness))
        t += step
    return out


# ------------------------------------------------------------------ shot shaping


def test_merges_flash_frames_into_the_previous_shot():
    """A dissolve produces several tiny detections; each one is not a shot."""
    shots = [Shot(0, 2), Shot(2, 2.1), Shot(2.1, 2.2), Shot(2.2, 5)]
    out = _merge_short(shots, 0.5)
    assert len(out) == 2
    assert out[0] == Shot(0, 2.2)


def test_merges_a_short_opening_shot_forward():
    out = _merge_short([Shot(0, 0.2), Shot(0.2, 4)], 0.5)
    assert out == [Shot(0.0, 4.0)]


def test_splits_long_takes_so_selection_has_choices():
    out = _split_long([Shot(0, 20)], 6.0)
    assert len(out) > 1
    assert all(s.duration <= 6.0 + 1e-6 for s in out)
    assert out[0].start == 0.0 and out[-1].end == pytest.approx(20.0)


def test_leaves_normal_shots_alone():
    shots = [Shot(0, 3), Shot(3, 5)]
    assert _split_long(_merge_short(shots, 0.5), 6.0) == shots


# ------------------------------------------------------------------ quality gates


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"brightness": 4.0}, "too dark"),
        ({"brightness": 254.0}, "blown out"),
        ({"sharpness": 0.05}, "soft focus"),
        ({"motion": 0.0}, "static frame"),
        ({"motion": 90.0}, "camera shake"),
    ],
)
def test_rejects_unusable_shots(kwargs, expected):
    shot = Shot(0, 2)
    scored = score_shots([shot], sample_run(0, 2, **kwargs), VisualSettings())[0]
    assert not scored.usable
    assert expected in scored.rejected


def test_keeps_a_good_shot():
    scored = score_shots([Shot(0, 2)], sample_run(0, 2), VisualSettings())[0]
    assert scored.usable
    assert scored.score > 0.5


def test_black_and_frozen_regions_veto_a_shot():
    good = sample_run(0, 2)
    assert not score_shots([Shot(0, 2)], good, VisualSettings(), black=[Shot(0.5, 1.0)])[0].usable
    assert not score_shots([Shot(0, 2)], good, VisualSettings(), frozen=[Shot(0.5, 1.0)])[0].usable


def test_a_shot_with_no_samples_is_reported_not_silently_kept():
    scored = score_shots([Shot(10, 12)], [], VisualSettings())[0]
    assert not scored.usable


# ------------------------------------------------------------------ cut planning


def good_analysis(n=3, length=3.0):
    shots = []
    for i in range(n):
        shot = Shot(i * length, (i + 1) * length)
        shots.extend(score_shots([shot], sample_run(shot.start, shot.end), VisualSettings()))
    return VisualAnalysis(shots)


def test_produces_one_take_per_usable_shot():
    plan = plan_visual_cuts(good_analysis(3), 9.0, VisualSettings())
    assert len(plan.keeps) == 3
    assert all(k.duration == pytest.approx(1.6, abs=1e-6) for k in plan.keeps)


def test_takes_start_after_the_lead_trim():
    s = VisualSettings(lead_trim=0.25)
    plan = plan_visual_cuts(good_analysis(1), 3.0, s)
    assert plan.keeps[0].start == pytest.approx(0.25)


def test_rejected_shots_appear_as_drops_with_reasons():
    analysis = good_analysis(2)
    analysis.shots.extend(score_shots([Shot(6, 8)], sample_run(6, 8, brightness=2.0), VisualSettings()))
    plan = plan_visual_cuts(analysis, 8.0, VisualSettings())
    assert any("dark" in d.reason for d in plan.drops)


def test_no_usable_shots_yields_no_clips_and_says_so():
    dark = VisualAnalysis(score_shots([Shot(0, 2)], sample_run(0, 2, brightness=1.0), VisualSettings()))
    plan = plan_visual_cuts(dark, 2.0, VisualSettings())
    assert plan.keeps == []
    assert any("no usable shots" in w for w in plan.warnings)


def test_target_duration_stops_selection_early():
    s = VisualSettings(target_duration=3.0)
    plan = plan_visual_cuts(good_analysis(6), 18.0, s)
    assert plan.kept_duration <= 3.0 + s.shot_duration
    assert len(plan.keeps) < 6
    assert any("target" in w for w in plan.warnings)


# ------------------------------------------------------------------ beats


def grid(bpm=120.0, duration=20.0, confidence=0.8):
    interval = 60.0 / bpm
    beats = [round(i * interval, 6) for i in range(int(duration / interval))]
    return BeatGrid(bpm=bpm, beats=beats, downbeats=beats[::4], confidence=confidence)


def test_cuts_land_on_beats_when_a_grid_is_supplied():
    g = grid()
    plan = plan_visual_cuts(good_analysis(3), 9.0, VisualSettings(), beats=g)
    for k in plan.keeps:
        assert min(abs(k.start - b) for b in g.beats) < 1e-6


def test_take_length_is_a_whole_number_of_beats():
    g = grid(bpm=120.0)           # 0.5s per beat
    s = VisualSettings(beats_per_shot=2, lead_trim=0.0, tail_trim=0.0)
    plan = plan_visual_cuts(good_analysis(2, length=5.0), 10.0, s, beats=g)
    assert plan.keeps[0].duration == pytest.approx(1.0, abs=1e-6)


def test_beats_snap_forward_never_before_the_shot():
    """A backwards snap would start a cut before its own shot begins."""
    g = grid()
    plan = plan_visual_cuts(good_analysis(3), 9.0, VisualSettings(), beats=g)
    for keep, scored in zip(plan.keeps, good_analysis(3).shots):
        assert keep.start >= scored.shot.start - 1e-6


def test_low_confidence_beats_are_ignored_rather_than_trusted():
    """Cutting to a wrong grid is worse than not cutting to one."""
    g = grid(confidence=0.05)
    s = VisualSettings(min_beat_confidence=0.25)
    plan = plan_visual_cuts(good_analysis(2), 6.0, s, beats=g)
    assert any("confidence" in w for w in plan.warnings)
    assert all(k.duration == pytest.approx(s.shot_duration, abs=1e-6) for k in plan.keeps)


def test_empty_beat_grid_falls_back_to_fixed_takes():
    plan = plan_visual_cuts(good_analysis(2), 6.0, VisualSettings(), beats=BeatGrid(120.0, [], [], 0.9))
    assert len(plan.keeps) == 2
    assert any("no beats" in w for w in plan.warnings)


# ------------------------------------------------------------------ integration


@pytest.mark.skipif(not (FIXTURES / "sample_2997_vertical_silent.mp4").exists(),
                    reason="run engine/tests/fixtures/generate.sh first")
def test_real_silent_clip_is_analysable():
    from autoedit.visual import analyse
    result = analyse(str(FIXTURES / "sample_2997_vertical_silent.mp4"), 4.0)
    assert result.shots, "expected at least one shot"
    assert all(s.shot.end > s.shot.start for s in result.shots)


# ------------------------------------------------------------------ crop risk


def samples_with_centre(start, end, full, centre, step=0.2):
    t, out = start, []
    while t < end:
        out.append(FrameSample(t, 4.0, 118.0, full, centre))
        t += step
    return out


def test_centred_detail_is_not_flagged():
    """A centre crop keeping its full share of the detail is safe."""
    ratio = 0.32
    scored = score_shots([Shot(0, 2)], samples_with_centre(0, 2, 3.0, 3.0 * ratio),
                         VisualSettings(), centre_ratio=ratio)[0]
    assert scored.crop_risk < 0.05


def test_detail_at_the_edges_is_flagged():
    """The shot a vertical crop would ruin: subject off to one side."""
    scored = score_shots([Shot(0, 2)], samples_with_centre(0, 2, 3.0, 0.0),
                         VisualSettings(), centre_ratio=0.32)[0]
    assert scored.crop_risk > 0.9


def test_crop_risk_is_zero_without_a_reframe():
    """No aspect change means nothing is being thrown away."""
    scored = score_shots([Shot(0, 2)], samples_with_centre(0, 2, 3.0, 1.0),
                         VisualSettings())[0]
    assert scored.crop_risk == 0.0


def test_crop_risk_is_bounded():
    for centre in (0.0, 0.5, 3.0, 99.0):
        scored = score_shots([Shot(0, 2)], samples_with_centre(0, 2, 3.0, centre),
                             VisualSettings(), centre_ratio=0.32)[0]
        assert 0.0 <= scored.crop_risk <= 1.0


def test_a_featureless_shot_is_not_flagged():
    """Nothing to lose in a flat frame; flagging it would be noise."""
    scored = score_shots([Shot(0, 2)], samples_with_centre(0, 2, 0.001, 0.0),
                         VisualSettings(), centre_ratio=0.32)[0]
    assert scored.crop_risk == 0.0


def test_measurements_round_trip_through_the_cache():
    """Cached measurements must preserve crop data, or a re-score silently loses
    every flag."""
    from autoedit.visual import Measurements
    m = Measurements(
        shots=[Shot(0, 2)],
        samples=samples_with_centre(0, 2, 3.0, 0.5),
        centre_ratio=0.32,
    )
    back = Measurements.from_dict(m.to_dict())
    assert back.centre_ratio == pytest.approx(0.32)
    assert back.samples[0].centre_sharpness == pytest.approx(0.5)
    assert len(back.samples) == len(m.samples)
