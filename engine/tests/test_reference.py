"""Cutting to a reference video.

The measurements that set the numbers in `reference.py` are recorded there. What
is tested here is the behaviour those numbers are supposed to produce.
"""

import numpy as np
import pytest

from autoedit.reference import (
    MIN_REFERENCE_SHOT, REFERENCE_MATCH_FLOOR, ROLE_CUT, ROLE_ENDING,
    ROLE_INTERVIEW, ROLE_OPENING, Reference, ReferenceShot, aspect_of,
    beats_from, classify_roles, match_shots, reference_report,
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


def test_a_matched_shot_also_reports_what_else_resembled_it():
    """A section longer than one span needs somewhere to draw the rest from."""
    ref = np.array([[1.0, 0.0]])
    footage = np.array([[1.0, 0.0], [0.99, 0.141], [0.98, 0.199], [0.0, 1.0]])
    got = match_shots(ref, footage, floor=0.9)

    assert got[0].footage_index == 0
    assert got[0].alternates == [1, 2], got[0].alternates
    assert 3 not in got[0].alternates, "below the floor is still below the floor"


def test_an_unmatched_shot_offers_no_alternates():
    ref = np.array([[1.0, 0.0]])
    footage = np.array([[0.0, 1.0]])
    got = match_shots(ref, footage, floor=0.75)
    assert not got[0].matched
    assert got[0].alternates == []


def test_truncating_a_long_reference_moves_the_target_with_it(tmp_path):
    """Keeping a prefix of the shots while still targeting the whole video's
    length stretched every section by the ratio between them -- 6.8x on a real
    27-minute reference, silently, because both numbers were right on their own.
    """
    from autoedit.reference import analyse_reference

    src = _two_shot_reference(tmp_path)
    whole = analyse_reference(src, tmp_path / "c1")
    assert whole.cut_count == 2

    clipped = analyse_reference(src, tmp_path / "c2", max_shots=1)
    assert clipped.cut_count == 1
    assert clipped.duration == pytest.approx(clipped.shots[-1].end)
    assert clipped.duration < whole.duration
    assert any("were used" in w for w in clipped.warnings), clipped.warnings


# ------------------------------------------------------------------- roles


def _shots(*durations):
    out, at = [], 0.0
    for d in durations:
        out.append(ReferenceShot(start=at, end=at + d))
        at += d
    return out, at


def test_a_long_hold_early_is_an_opening_and_late_is_an_ending():
    """Read off the reference's own cutting. A reference has audio, but
    transcribing someone else's 27-minute video to learn what its shot lengths
    already say would be minutes of Whisper for a free answer."""
    shots, total = _shots(22.0, 3.0, 2.5, 4.0, 3.0, 45.0, 2.0, 3.5, 2.0, 18.0)
    classify_roles(shots, total)
    assert [sh.role for sh in shots] == [
        ROLE_OPENING, ROLE_CUT, ROLE_CUT, ROLE_CUT, ROLE_CUT,
        ROLE_INTERVIEW, ROLE_CUT, ROLE_CUT, ROLE_CUT, ROLE_ENDING,
    ]


def test_a_fast_cut_reference_has_no_holds_at_all():
    """A TikTok opens on a 0.5s hook, not a piece to camera. The absolute floor
    is what stops the ratio calling the longest of a run of short shots a hold."""
    shots, total = _shots(0.5, 0.6, 0.4, 1.2, 0.5, 0.7, 0.5)
    classify_roles(shots, total)
    assert {sh.role for sh in shots} == {ROLE_CUT}


def test_a_slow_film_does_not_become_all_interview():
    """And the ratio is what stops the floor calling every shot a hold when the
    whole film is unhurried."""
    shots, total = _shots(8.0, 9.0, 7.5, 8.5, 9.5, 8.0)
    classify_roles(shots, total)
    assert {sh.role for sh in shots} == {ROLE_CUT}


def test_footage_of_someone_talking_is_preferred_for_a_shot_that_holds():
    """The bias reorders spans the picture cannot separate; it never overrules
    the picture, and it never touches the floor."""
    ref = np.array([[1.0, 0.0]])
    # Two spans the vision model rates almost identically.
    footage = np.array([[0.99, 0.141], [0.995, 0.0999]])

    quiet_first = match_shots(ref, footage, floor=0.75,
                              wants_speech=[True], is_spoken=[False, True])
    assert quiet_first[0].footage_index == 1, "the talking span should win"

    flipped = match_shots(ref, footage, floor=0.75,
                          wants_speech=[True], is_spoken=[True, False])
    assert flipped[0].footage_index == 0


def test_a_cut_prefers_footage_that_is_not_someone_talking():
    """Set up so the bias has to overturn the picture's own order: the talking
    span scores HIGHER, and a two-second cut should still take the other one."""
    ref = np.array([[1.0, 0.0]])
    footage = np.array([[0.995, 0.0999],     # 0 -- better match, someone talking
                        [0.99, 0.141]])      # 1 -- slightly worse, quiet
    assert float(ref[0] @ footage[0]) > float(ref[0] @ footage[1])

    got = match_shots(ref, footage, floor=0.75,
                      wants_speech=[False], is_spoken=[True, False])
    assert got[0].footage_index == 1, "b-roll should not be served by an interview"

    # And with no roles supplied at all, the picture decides as it always did.
    plain = match_shots(ref, footage, floor=0.75)
    assert plain[0].footage_index == 0


def test_the_role_bias_never_lifts_a_span_over_the_floor():
    """Or "the footage does not contain this" would quietly start depending on
    who happened to be talking."""
    ref = np.array([[1.0, 0.0]])
    below = np.array([[0.73, 0.683]])         # 0.73 similarity, under a 0.75 floor
    got = match_shots(ref, below, floor=0.75,
                      wants_speech=[True], is_spoken=[True])
    assert not got[0].matched, "a role bonus is not evidence the shot is there"


def test_mismatched_lengths_are_ignored_rather_than_guessed():
    ref = np.array([[1.0, 0.0]])
    footage = np.array([[1.0, 0.0], [0.99, 0.141]])
    got = match_shots(ref, footage, floor=0.75,
                      wants_speech=[True], is_spoken=[True])   # 1 flag, 2 spans
    assert got[0].matched
