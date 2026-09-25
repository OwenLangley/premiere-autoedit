"""Identifying the library track a reference video is playing.

Synthetic audio, not the real library: a test that needs someone's music folder
is a test that only runs on one machine. The numbers that justify the thresholds
came from the real 103-track library and are recorded in fingerprint.py; what is
checked here is that the mechanism behaves as that measurement assumed.
"""

import numpy as np
import pytest

from autoedit import fingerprint as fp


def synth(seed: int, seconds: float = 30.0) -> np.ndarray:
    """Something with the spectral structure music has.

    Chords with harmonics, changing four times a second, over a percussive
    pulse. The first version of this fixture was sparse sine bursts, and it
    produced 55 peaks where this produces 796 -- so which peaks were "strongest"
    changed completely under a whisper of noise, and a track failed to recognise
    ITSELF. That was the fixture being unlike music, not the matcher being
    fragile, and the difference is the point: this technique leans on music
    having dense, stable spectral lines.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * fp.SAMPLE_RATE)
    out = np.zeros(n, np.float32)
    t = np.arange(n) / fp.SAMPLE_RATE
    scale = 220.0 * 2 ** (np.array([0, 2, 3, 5, 7, 8, 10, 12]) / 12)
    step = int(0.25 * fp.SAMPLE_RATE)
    for i in range(0, n - step, step):
        for freq in rng.choice(scale, 4, replace=False):
            seg = t[i:i + step * 2][:n - i]
            for harmonic in (1, 2, 3, 4, 5, 6):
                out[i:i + len(seg)] += (
                    np.sin(2 * np.pi * freq * harmonic * seg) / harmonic
                    * np.hanning(len(seg))).astype(np.float32)
        out[i:i + 300] += rng.normal(0, 0.4, min(300, n - i)).astype(np.float32)
    return out / (np.abs(out).max() or 1.0)


def print_of(samples: np.ndarray) -> fp.Fingerprint:
    """The analysis pipeline without ffmpeg, so the tests need no audio files."""
    spec = fp.spectrogram(samples)
    cap = int(fp.PEAKS_PER_SECOND * spec.shape[1] * fp.HOP / fp.SAMPLE_RATE) or None
    h, t = fp.landmarks(fp.peaks(spec, cap))
    return fp.Fingerprint(h, t, spec.shape[1])


@pytest.fixture(scope="module")
def library():
    return {f"track{i}.mp3": print_of(synth(i)) for i in range(8)}


# --- the peak finder --------------------------------------------------------

def test_the_separable_max_filter_is_the_two_dimensional_one():
    """It replaced a 2-D neighbourhood max that was 82% of the entire indexing
    cost. The speedup is only allowed because the answer is the same, so that
    is what is checked -- not that it is fast, but that it is identical."""
    rng = np.random.default_rng(0)
    spec = rng.normal(size=(64, 96)).astype(np.float32)
    for rf, rt in ((1, 1), (3, 5), (12, 12)):
        padded = np.pad(spec, ((rf, rf), (rt, rt)), constant_values=-np.inf)
        both_at_once = np.lib.stride_tricks.sliding_window_view(
            padded, (2 * rf + 1, 2 * rt + 1)).max(axis=(2, 3))
        assert np.array_equal(fp._max_filter(spec, rf, rt), both_at_once), (rf, rt)


# --- the mechanism ----------------------------------------------------------

def test_a_track_recognises_itself(library):
    match = fp.identify(print_of(synth(3)), library)
    assert match.name == "track3.mp3"
    assert match.decisive


def test_a_track_that_is_not_there_is_not_invented(library):
    """None is an ordinary answer. The reference may be playing music nobody
    here owns, and saying so is right -- scoring the cut against the wrong
    track would be worse than leaving the choice alone."""
    match = fp.identify(print_of(synth(999)), library)
    assert not match.decisive
    assert match.score < fp.MATCH_FLOOR


def test_an_excerpt_finds_its_place_in_the_track(library):
    """A reference uses a few seconds from somewhere in the middle."""
    whole = synth(4)
    start = 8.0
    excerpt = whole[int(start * fp.SAMPLE_RATE):int((start + 6.0) * fp.SAMPLE_RATE)]
    match = fp.identify(print_of(excerpt), library)
    assert match.name == "track4.mp3"
    assert match.decisive
    assert match.offset_seconds == pytest.approx(start, abs=0.3)


# --- the reason this technique was chosen -----------------------------------

def test_noise_over_the_music_does_not_hide_it(library):
    """The offset histogram is the whole trick: noise produces spurious hashes
    too, but they vote for offsets scattered across the track and never pile up,
    while a real match puts everything on one offset.

    The noise here is a quarter of the signal's own RMS, about +12dB. That is
    where THIS fixture still holds, and it is a far weaker claim than the
    feature actually makes: on the real 103-track library, speech mixed over the
    music at four times its level still scored 2566, and heavy pink noise 2643,
    against a floor of 100. Synthetic audio is thinner than music and gives out
    sooner, so the measured table in fingerprint.py is the evidence for
    robustness -- this is the evidence that the mechanism is the reason for it.
    """
    base = synth(5)
    rms = float(np.sqrt((base ** 2).mean()))
    rng = np.random.default_rng(0)
    dirty = base + rng.normal(0, rms * 0.25, len(base)).astype(np.float32)

    match = fp.identify(print_of(dirty), library)
    assert match.name == "track5.mp3"
    assert match.decisive


def test_another_track_playing_over_it_picks_one_of_the_two(library):
    """Two pieces of music at once -- the case a plain correlation gets wrong,
    because it has no way to say WHICH of the two it is looking at."""
    mixed = synth(2) + 0.7 * synth(6)
    match = fp.identify(print_of(mixed), library)
    assert match.name in {"track2.mp3", "track6.mp3"}


# --- what it refuses to do --------------------------------------------------

def test_a_score_alone_is_not_enough_when_two_tracks_tie():
    """A library holding two copies of one song is exactly the case where a
    strong score says nothing about WHICH file the editor means."""
    same = print_of(synth(1))
    match = fp.identify(print_of(synth(1)), {"a.mp3": same, "b.mp3": same})
    assert match.score >= fp.MATCH_FLOOR
    assert not match.decisive, "a tie must not be resolved by filename order"


def test_the_floor_and_the_margin_are_both_required():
    assert not fp.Match("x", fp.MATCH_FLOOR - 1, 0, 0.0).decisive
    assert fp.Match("x", fp.MATCH_FLOOR, 0, 0.0).decisive
    assert not fp.Match("x", 1000, 500, 0.0).decisive      # no clear leader
    assert fp.Match("x", 1000, 100, 0.0).decisive


def test_an_empty_library_is_not_a_crash():
    assert fp.identify(print_of(synth(1)), {}) is None


def test_audio_too_short_to_have_landmarks_scores_nothing(library):
    tiny = np.zeros(fp.WINDOW // 2, np.float32)
    assert len(print_of(tiny)) == 0
    assert fp.identify(print_of(tiny), library).score == 0


def test_silence_matches_nothing(library):
    assert fp.identify(print_of(np.zeros(fp.SAMPLE_RATE * 5, np.float32)),
                       library).score < fp.MATCH_FLOOR


# --- the cache --------------------------------------------------------------

def test_a_fingerprint_survives_the_round_trip(tmp_path, library):
    out = tmp_path / "a.npz"
    fp.save(out, library["track1.mp3"])
    back = fp.load(out)
    assert np.array_equal(back.hashes, library["track1.mp3"].hashes)
    assert np.array_equal(back.times, library["track1.mp3"].times)
    assert back.frames == library["track1.mp3"].frames


def test_a_cache_from_an_older_analysis_is_ignored_not_mixed_in(tmp_path, library, monkeypatch):
    out = tmp_path / "a.npz"
    fp.save(out, library["track1.mp3"])
    monkeypatch.setattr(fp, "FINGERPRINT_VERSION", fp.FINGERPRINT_VERSION + 1)
    assert fp.load(out) is None


def test_a_damaged_cache_is_rebuilt_rather_than_fatal(tmp_path):
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not an npz")
    assert fp.load(bad) is None
    assert fp.load(tmp_path / "missing.npz") is None


def test_the_cache_name_changes_when_the_analysis_does(tmp_path, monkeypatch):
    """Otherwise a change to the hashing silently reuses what the old rule made.
    The same reasoning as reference_cache_path's |v1| -> |v2| in the helper."""
    first = fp.cache_path(tmp_path, "sha256:abc")
    monkeypatch.setattr(fp, "FINGERPRINT_VERSION", fp.FINGERPRINT_VERSION + 1)
    assert fp.cache_path(tmp_path, "sha256:abc") != first
    assert fp.cache_path(tmp_path, "sha256:def") != first


# --- what the engine sees while the helper is still listening ---------------

def test_only_indexed_tracks_are_offered_and_the_rest_are_counted(tmp_path, library):
    """The first pass over a library is about three minutes, so an editor's
    first job runs against a part-indexed one. That must be able to find
    nothing, and must never find something wrong."""
    from autoedit.cli import _library_fingerprints
    from autoedit.probe import content_hash

    music, cache = tmp_path / "music", tmp_path / "cache"
    music.mkdir()
    for i in range(4):
        (music / f"track{i}.mp3").write_bytes(b"ID3" + bytes(i) + b"\0" * 64)
    (music / "notes.txt").write_bytes(b"not music")      # ignored, not counted
    (music / ".hidden.mp3").write_bytes(b"\0" * 8)       # ignored, not counted

    # Only two of the four have been listened to so far.
    for i in (0, 2):
        fp.save(fp.cache_path(cache, content_hash(music / f"track{i}.mp3")),
                library[f"track{i}.mp3"])

    found, total = _library_fingerprints(music, cache)
    assert total == 4
    assert sorted(found) == ["track0.mp3", "track2.mp3"]


def test_a_library_nothing_has_been_indexed_from_yields_nothing(tmp_path):
    from autoedit.cli import _library_fingerprints
    music = tmp_path / "music"
    music.mkdir()
    (music / "a.mp3").write_bytes(b"\0" * 64)
    found, total = _library_fingerprints(music, tmp_path / "cache")
    assert found == {} and total == 1
