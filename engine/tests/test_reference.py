"""Cutting to a reference video.

The measurements that set the numbers in `reference.py` are recorded there. What
is tested here is the behaviour those numbers are supposed to produce.
"""

import numpy as np
import pytest

from autoedit.reference import (
    MIN_REFERENCE_SHOT, REFERENCE_MATCH_FLOOR, Reference, ReferenceShot,
    aspect_of, beats_from, match_shots, reference_report,
)


def ref(*durations) -> Reference:
    shots, at = [], 0.0
    for d in durations:
        shots.append(ReferenceShot(start=at, end=at + d))
        at += d
    return Reference(path=None, duration=at, shots=shots)


# ------------------------------------------------------------------- shape

def test_a_shape_is_named_by_ratio_not_by_size():
    """A 720p phone video and a 1080p one are the same editorial shape."""
    assert aspect_of(1080, 1920) == "vertical"
    assert aspect_of(720, 1280) == "vertical"
    assert aspect_of(1920, 1080) == "landscape"
    assert aspect_of(1080, 1080) == "square"
    assert aspect_of(1080, 1350) == "portrait45"


def test_an_unusual_shape_is_not_forced_into_a_name():
    # Better to leave the aspect alone than to claim a reference is 16:9 when it
    # is 10:7 and quietly crop the editor's footage to match.
    assert aspect_of(1000, 700) is None
    assert aspect_of(0, 0) is None


# ------------------------------------------------------------------- beats

def test_a_beat_is_weighted_by_how_long_its_shot_runs():
    """The whole trick. build_story_plans divides runtime by weight, so weighting
    by the reference's own shot lengths reproduces its pacing."""
    beats = beats_from(ref(0.5, 3.0, 0.5, 2.0))
    assert [b.weight for b in beats] == [0.5, 3.0, 0.5, 2.0]
    assert [b.id for b in beats] == ["r1", "r2", "r3", "r4"]


def test_a_beat_is_labelled_with_a_timecode():
    # It is what an editor reads in a warning about a shot the footage could not
    # serve, and it has to locate that shot in the reference they are looking at.
    beats = beats_from(ref(2.0, 3.0))
    assert "0:00.0-0:02.0" in beats[0].text
    assert "0:02.0-0:05.0" in beats[1].text


def test_a_zero_length_shot_cannot_take_the_whole_runtime():
    # weight 0 across every beat would make the share denominator zero.
    beats = beats_from(ref(0.0, 1.0))
    assert all(b.weight > 0 for b in beats)


# ----------------------------------------------------------------- matching

def unit(rows):
    a = np.array(rows, dtype=np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


def test_a_shot_below_the_floor_is_left_unmatched():
    """Measured: a reference from the same shoot scores 0.842-0.918 against the
    footage, and unrelated video scores 0.547-0.649. Nothing in that lower band
    should be presented as a match."""
    reference = unit([[1, 0, 0]])
    footage = unit([[0.6, 0.8, 0]])          # cosine 0.6, below the floor
    assert not match_shots(reference, footage)[0].matched


def test_a_shot_above_the_floor_is_matched():
    reference = unit([[1, 0, 0]])
    footage = unit([[0.95, 0.31, 0]])        # cosine ~0.95
    hit = match_shots(reference, footage)[0]
    assert hit.matched and hit.footage_index == 0


def test_an_unused_span_is_preferred_over_reusing_one():
    # Two reference shots that both like span 0 best; the second should take the
    # next-best rather than repeat, so a reference does not fill an edit with one
    # clip while others go unused.
    reference = unit([[1, 0], [1, 0.05]])
    footage = unit([[1, 0], [0.98, 0.2]])
    picks = match_shots(reference, footage)
    assert [p.footage_index for p in picks] == [0, 1]
    assert not any(p.reused for p in picks)


def test_reuse_is_allowed_once_the_footage_runs_out_and_is_marked():
    """A thirty-cut reference against six clips has no choice but to repeat.

    story.assign_beats refuses to share a shot, deliberately. Refusing here would
    report failures for a job working exactly as asked.
    """
    reference = unit([[1, 0], [1, 0], [1, 0]])
    footage = unit([[1, 0]])
    picks = match_shots(reference, footage)
    assert all(p.matched for p in picks)
    assert [p.reused for p in picks] == [False, True, True]


def test_no_footage_at_all_matches_nothing_rather_than_crashing():
    picks = match_shots(unit([[1, 0]]), np.zeros((0, 2), np.float32))
    assert len(picks) == 1 and not picks[0].matched


def test_the_report_carries_the_band_that_was_seen():
    """The floor is absolute, and absolute floors do not transfer between
    libraries. The observed numbers ride on every job so a wrong floor is visible
    rather than silently ignoring the reference."""
    reference = unit([[1, 0], [0, 1]])
    footage = unit([[1, 0]])
    r = reference_report(match_shots(reference, footage))
    assert r["shots"] == 2 and r["matched"] == 1
    assert r["best"] == pytest.approx(1.0, abs=0.01)
    assert r["worst"] == pytest.approx(0.0, abs=0.01)
    assert r["floor"] == REFERENCE_MATCH_FLOOR


# ------------------------------------- the shape of a reference, end to end

def test_a_real_reference_is_read_exactly(tmp_path):
    """Four patterns of 0.5 / 3.0 / 0.5 / 2.0, built here and measured.

    Visually distinct patterns rather than flat colours: an earlier version of
    this fixture used red and green cards and lost a cut, because a luma-based
    scene detector barely registers red-to-green. That was the fixture, not the
    tool, and it cost an hour to establish.
    """
    import subprocess
    from autoedit.reference import analyse_reference

    plan = [("smptebars", 0.5), ("mandelbrot", 3.0), ("rgbtestsrc", 0.5), ("testsrc2", 2.0)]
    lines = []
    for src, dur in plan:
        out = tmp_path / f"{src}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s=320x568:r=30",
             "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", str(out)], check=True)
        lines.append(f"file '{out.name}'")
    (tmp_path / "list.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "15",
         "ref.mp4"], cwd=tmp_path, check=True)

    got = analyse_reference(tmp_path / "ref.mp4", tmp_path / "cache")
    assert got.cut_count == 4
    assert [round(s.duration, 2) for s in got.shots] == [0.5, 3.0, 0.5, 2.0]
    assert got.aspect == "vertical"
    # And the weights that carry that rhythm into the allocator.
    assert [b.weight for b in beats_from(got)] == [0.5, 3.0, 0.5, 2.0]


def test_a_single_take_is_refused_with_a_reason(tmp_path):
    """One continuous shot has no cutting pattern to copy, and saying so is more
    use than returning an edit of one clip."""
    import subprocess
    from autoedit.reference import ReferenceError, analyse_reference

    out = tmp_path / "one.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=30",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(out)], check=True)
    with pytest.raises(ReferenceError, match="no cuts were found"):
        analyse_reference(out, tmp_path / "cache")


def test_slivers_are_not_counted_as_shots():
    # A detection artefact is not a beat of anyone's edit.
    tiny = ref(MIN_REFERENCE_SHOT / 2, 2.0)
    assert tiny.shots[0].duration < MIN_REFERENCE_SHOT


def _two_shot_reference(tmp_path):
    """A reference with a real cut in it. Flat colour cards do not work here --
    ffmpeg's scene score barely registers red against green at similar luma, a
    fixture mistake that once cost an hour and a confident wrong conclusion."""
    import subprocess
    lines = []
    for src, dur in [("smptebars", 1.0), ("mandelbrot", 1.0)]:
        out = tmp_path / f"{src}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=s=320x240:r=30",
             "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", str(out)], check=True)
        lines.append(f"file '{out.name}'")
    (tmp_path / "list.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "15",
         "ref.mp4"], cwd=tmp_path, check=True)
    return tmp_path / "ref.mp4"


def test_a_reference_is_measured_once_however_many_times_it_is_used(tmp_path, monkeypatch):
    """Reading a reference is the slowest part of a reference job -- `measure`
    decodes the whole video three times. It ran again on every attempt, so
    changing a length and pressing Create paid the cost twice for a file that
    had not changed. The footage path has cached this since it shipped.
    """
    from autoedit import reference as refmod

    src = _two_shot_reference(tmp_path)
    calls = []
    real = refmod.measure
    monkeypatch.setattr(refmod, "measure",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    cache = tmp_path / "cache"
    first = refmod.analyse_reference(src, cache)
    assert calls == [1], "the first run has to measure"

    second = refmod.analyse_reference(src, cache)
    assert calls == [1], "the second run must come from the cache"
    assert [round(x.duration, 3) for x in second.shots] == \
           [round(x.duration, 3) for x in first.shots]
    assert second.duration == first.duration
    assert second.aspect == first.aspect

    # --no-cache still means what it says.
    refmod.analyse_reference(src, cache, no_cache=True)
    assert calls == [1, 1]


def test_a_damaged_cache_file_is_measured_again_rather_than_failing(tmp_path):
    """An interrupted write must not make a reference permanently unreadable."""
    from autoedit.reference import analyse_reference

    src = _two_shot_reference(tmp_path)
    cache = tmp_path / "cache"
    first = analyse_reference(src, cache)
    for f in (cache / "visual").glob("*.json"):
        f.write_text("{ truncated")

    again = analyse_reference(src, cache)
    assert again.cut_count == first.cut_count
