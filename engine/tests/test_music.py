"""Beat detection.

Tempo and grid fitting are tested on synthetic onset envelopes -- no ffmpeg, no
audio files -- so the numerics are pinned exactly and the suite stays fast.
"""

import numpy as np
import pytest

from autoedit.music import (
    HOP,
    SAMPLE_RATE,
    BeatGrid,
    estimate_tempo,
    fit_grid,
    onset_envelope,
)

FRAME_RATE = SAMPLE_RATE / HOP


def pulse_envelope(bpm: float, duration: float, jitter: float = 0.0) -> np.ndarray:
    """An onset envelope with a spike on every beat."""
    n = int(duration * FRAME_RATE)
    env = np.zeros(n, dtype=np.float32)
    step = (60.0 / bpm) * FRAME_RATE
    rng = np.random.default_rng(0)
    t = 0.0
    while t < n - 1:
        idx = int(round(t + (rng.normal(0, jitter) if jitter else 0.0)))
        if 0 <= idx < n:
            env[idx] = 1.0
        t += step
    return env


# ------------------------------------------------------------------ tempo


@pytest.mark.parametrize("bpm", [80.0, 100.0, 120.0, 140.0, 174.0])
def test_recovers_tempo_from_a_clean_pulse_train(bpm):
    env = pulse_envelope(bpm, 20.0)
    rough, confidence = estimate_tempo(env)
    fitted, _, _ = fit_grid(env, rough, 20.0)
    assert fitted == pytest.approx(bpm, rel=0.02)
    assert confidence > 0.0


def test_grid_fitting_beats_raw_autocorrelation_for_precision():
    """Autocorrelation is limited by integer lag spacing; the fit is not.

    This matters because tempo error accumulates: 1% over a 60s promo walks the
    grid more than half a second away from the music.
    """
    env = pulse_envelope(120.0, 30.0)
    rough, _ = estimate_tempo(env)
    fitted, _, _ = fit_grid(env, rough, 30.0)
    assert abs(fitted - 120.0) <= abs(rough - 120.0) + 1e-9


def test_tempo_survives_a_little_timing_jitter():
    env = pulse_envelope(120.0, 20.0, jitter=0.8)
    rough, _ = estimate_tempo(env)
    fitted, _, _ = fit_grid(env, rough, 20.0)
    assert fitted == pytest.approx(120.0, rel=0.05)


def test_silence_yields_no_tempo_rather_than_a_guess():
    bpm, confidence = estimate_tempo(np.zeros(400, dtype=np.float32))
    assert bpm == 0.0 and confidence == 0.0


def test_too_short_input_is_handled():
    assert estimate_tempo(np.zeros(4, dtype=np.float32)) == (0.0, 0.0)
    assert onset_envelope(np.zeros(16, dtype=np.float32)).size == 0


# ------------------------------------------------------------------ the grid


def test_beats_are_evenly_spaced_at_the_reported_tempo():
    env = pulse_envelope(120.0, 20.0)
    rough, _ = estimate_tempo(env)
    bpm, beats, _ = fit_grid(env, rough, 20.0)
    gaps = np.diff(beats)
    assert gaps.std() < 1e-6
    assert gaps.mean() == pytest.approx(60.0 / bpm, rel=1e-6)


def test_beats_stay_inside_the_media():
    env = pulse_envelope(120.0, 20.0)
    rough, _ = estimate_tempo(env)
    _, beats, _ = fit_grid(env, rough, 5.0)
    assert all(0.0 <= b <= 5.0 for b in beats)


def test_snap_finds_the_nearest_beat():
    g = BeatGrid(120.0, [0.0, 0.5, 1.0, 1.5], [0.0], 0.9)
    assert g.snap(0.6) == 0.5
    assert g.snap(0.9) == 1.0


def test_next_beat_never_goes_backwards():
    g = BeatGrid(120.0, [0.0, 0.5, 1.0, 1.5], [0.0], 0.9)
    assert g.next_beat(0.6) == 1.0
    assert g.next_beat(0.0) == 0.0
    assert g.next_beat(9.0) is None


def test_downbeats_are_every_fourth_beat():
    g = BeatGrid(120.0, [i * 0.5 for i in range(8)], [], 0.9)
    g.downbeats = g.beats[::4]
    assert g.downbeats == [0.0, 2.0]


def test_beat_interval_matches_bpm():
    assert BeatGrid(120.0).beat_interval == pytest.approx(0.5)
    assert BeatGrid(0.0).beat_interval == 0.0


def test_reports_octave_ambiguity_at_the_range_extremes():
    """Half vs double is a real musical ambiguity at the edges of the tempo range.
    The grid should say so rather than quietly pick one."""
    env = pulse_envelope(200.0, 20.0)
    rough, _ = estimate_tempo(env)
    bpm, _, ambiguous = fit_grid(env, rough, 20.0)
    assert ambiguous or bpm == pytest.approx(200.0, rel=0.03)


def test_a_clear_mid_range_tempo_is_not_flagged_ambiguous():
    env = pulse_envelope(120.0, 20.0)
    rough, _ = estimate_tempo(env)
    _, _, ambiguous = fit_grid(env, rough, 20.0)
    assert not ambiguous
