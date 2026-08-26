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
    fitted, _, _, _ = fit_grid(env, rough, 20.0)
    assert fitted == pytest.approx(bpm, rel=0.02)
    assert confidence > 0.0


def test_grid_fitting_beats_raw_autocorrelation_for_precision():
    """Autocorrelation is limited by integer lag spacing; the fit is not.

    This matters because tempo error accumulates: 1% over a 60s promo walks the
    grid more than half a second away from the music.
    """
    env = pulse_envelope(120.0, 30.0)
    rough, _ = estimate_tempo(env)
    fitted, _, _, _ = fit_grid(env, rough, 30.0)
    assert abs(fitted - 120.0) <= abs(rough - 120.0) + 1e-9


def test_tempo_survives_a_little_timing_jitter():
    env = pulse_envelope(120.0, 20.0, jitter=0.8)
    rough, _ = estimate_tempo(env)
    fitted, _, _, _ = fit_grid(env, rough, 20.0)
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
    bpm, beats, _, _ = fit_grid(env, rough, 20.0)
    gaps = np.diff(beats)
    assert gaps.std() < 1e-6
    assert gaps.mean() == pytest.approx(60.0 / bpm, rel=1e-6)


def test_beats_stay_inside_the_media():
    env = pulse_envelope(120.0, 20.0)
    rough, _ = estimate_tempo(env)
    _, beats, _, _ = fit_grid(env, rough, 5.0)
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
    bpm, _, ambiguous, _ = fit_grid(env, rough, 20.0)
    assert ambiguous or bpm == pytest.approx(200.0, rel=0.03)


def test_a_clear_mid_range_tempo_is_not_flagged_ambiguous():
    env = pulse_envelope(120.0, 20.0)
    rough, _ = estimate_tempo(env)
    _, _, ambiguous, _ = fit_grid(env, rough, 20.0)
    assert not ambiguous


# --- what "confidence" actually measures ------------------------------------
#
# It used to be `estimate_tempo`'s autocorrelation peakiness -- measured BEFORE
# the grid was refined and never updated afterwards. That answers "is there a
# clear periodicity in this signal", not "is the grid we settled on right", and
# the two come apart badly: a track with a dead-even 126 BPM grid scored 0.14,
# fell under the 0.25 floor, and its edit ignored the music entirely while the
# editor could hear the beat perfectly well.


def test_confidence_describes_the_grid_that_was_fitted():
    from autoedit.music import fit_grid, onset_envelope
    import numpy as np
    # A clean pulse train: the grid should explain nearly every onset.
    sr, bpm = 22050, 120.0
    n = int(sr * 20)
    x = np.zeros(n, dtype=np.float32)
    for i in range(int(20 * bpm / 60)):
        at = int(i * sr * 60 / bpm)
        if at < n:
            x[at:at + 200] = 1.0
    env = onset_envelope(x)
    _, _, _, fit = fit_grid(env, bpm, 20.0)
    assert fit > 0.5, f"a clean click track should fit well, got {fit}"


def test_noise_does_not_fit_a_grid():
    from autoedit.music import fit_grid, onset_envelope
    import numpy as np
    rng = np.random.default_rng(7)
    env = onset_envelope(rng.normal(0, 0.2, 22050 * 10).astype(np.float32))
    _, _, _, fit = fit_grid(env, 120.0, 10.0)
    assert fit < 0.5, f"noise should not fit a grid, got {fit}"


def test_the_floor_passes_every_measured_real_track():
    """The threshold is calibrated against measurements, not guessed.

    On the peak-based fit, four real tracks scored 0.41 / 0.60 / 0.64 / 0.65.
    The floor has to pass all of them: the detector recovered the correct tempo
    for every one, including the 0.41, so refusing it would throw away a grid
    that was right.
    """
    from autoedit.recipe import MusicSettings
    floor = MusicSettings().min_beat_confidence
    assert all(m > floor for m in [0.414, 0.601, 0.640, 0.648])


def test_the_floor_no_longer_separates_music_from_speech_and_that_is_fine():
    """Recorded because it USED to, and someone will assume it still does.

    The energy-based fit scored beatless speech at 0.09-0.14, well under any
    real track. The peak-based fit that replaced it scores speech at 0.26-0.40,
    overlapping the weakest real track at 0.41 -- syllables are onsets too, and
    they are quite regular.

    That is an acceptable trade because the input here is always a music file the
    editor chose. The floor is a guard against a grid that is not there at all
    (silence, a corrupt read), not a genre classifier. What earns trust in the
    tempo is that it is now measured correctly, not that the score is high.
    """
    from autoedit.recipe import MusicSettings
    floor = MusicSettings().min_beat_confidence
    speech = [0.261, 0.292, 0.402]
    assert not all(x < floor for x in speech), "if this passes, re-read the docstring"
    assert floor < 0.414, "the floor must not refuse the weakest real track"


# --- following the beat instead of assuming a fixed one ---------------------
#
# "Since the time between beats can change throughout a song..." -- exactly so.
# fit_grid finds ONE tempo and lays a perfectly even grid, which drifts away
# from any song that is not metronomic.


def _drifting(bpm_start, bpm_end, dur=20.0):
    """A click track that accelerates, plus the true beat times."""
    import numpy as np, math
    from autoedit.music import SAMPLE_RATE
    n = int(SAMPLE_RATE * dur)
    x = np.zeros(n, dtype=np.float32)
    t, times = 0.0, []
    while t < dur:
        times.append(t)
        at = int(t * SAMPLE_RATE)
        for k in range(min(900, n - at)):
            x[at + k] += math.sin(2 * math.pi * 1200 * k / SAMPLE_RATE) * math.exp(-k / 300.0)
        t += 60.0 / (bpm_start + (bpm_end - bpm_start) * (t / dur))
    return x, times


def test_the_tracker_follows_a_song_that_speeds_up():
    """The measurement that motivated all of this.

    An even grid over a track accelerating 100 -> 130 BPM sits a median 70ms and
    a worst 546ms from the real beats -- over a beat adrift by the end, which is
    what "it isn't cutting to the beat" sounds like.
    """
    import numpy as np
    from autoedit.music import (onset_envelope, fit_grid, track_beats,
                                SAMPLE_RATE, HOP, WINDOW)
    x, truth = _drifting(100.0, 130.0)
    env = onset_envelope(x)
    bpm, grid, _, _ = fit_grid(env, 115.0, 20.0)
    tracked = track_beats(env, bpm, 20.0, SAMPLE_RATE / HOP,
                          (WINDOW / 2 + HOP / 2) / SAMPLE_RATE)

    def worst(beats):
        beats = np.array(beats)
        return max(float(np.min(np.abs(beats - t))) for t in truth)

    assert worst(tracked) < 0.10, f"tracked beats drifted {worst(tracked)*1000:.0f}ms"
    assert worst(tracked) < worst(grid) / 3, "tracking should beat a fixed grid outright"
    assert abs(len(tracked) - len(truth)) <= 2, "should find about the right number of beats"


def test_beat_interval_is_measured_not_assumed():
    """With tracked beats the spacing varies, so 60/bpm is the wrong number."""
    from autoedit.music import BeatGrid
    g = BeatGrid(bpm=120.0, beats=[0.0, 0.5, 1.1, 1.6, 2.2])
    assert abs(g.beat_interval - 0.5) < 0.11


def test_cut_points_thin_out_the_beats():
    from autoedit.music import BeatGrid
    g = BeatGrid(bpm=120.0, beats=[i * 0.5 for i in range(9)])
    assert g.cut_points(1) == g.beats
    assert g.cut_points(2) == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert g.cut_points(4) == [0.0, 2.0, 4.0]


def test_subdividing_uses_the_midpoint_of_real_beats():
    """Which is why it stays right when the tempo moves.

    Halfway between two ACTUAL beats is still halfway when the gap changes; a
    fixed half-interval offset would not be.
    """
    from autoedit.music import BeatGrid
    g = BeatGrid(bpm=120.0, beats=[0.0, 0.5, 1.4])      # deliberately uneven
    pts = g.cut_points(0.5)
    assert pts == [0.0, 0.25, 0.5, 0.95, 1.4], pts


def test_no_beats_means_no_cut_points():
    from autoedit.music import BeatGrid
    assert BeatGrid(bpm=0.0, beats=[]).cut_points(1) == []
