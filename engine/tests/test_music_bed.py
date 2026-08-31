"""Music bed discovery.

The rule under test: pick a bed only when there is exactly one candidate, never
guess between several, and never trust the file extension.
"""

import shutil
from pathlib import Path

import pytest

from autoedit.cli import _find_music_bed, _relative_to_root
from autoedit.recipe import load_recipe

FIXTURES = Path(__file__).parent / "fixtures"
AUDIO = FIXTURES / "sample_audio_only.m4a"
VIDEO = FIXTURES / "sample_25fps_1080p.mp4"

needs_fixtures = pytest.mark.skipif(
    not (AUDIO.exists() and VIDEO.exists()),
    reason="run engine/tests/fixtures/generate.sh first",
)


@needs_fixtures
def test_finds_a_single_audio_only_file(tmp_path):
    shutil.copy(AUDIO, tmp_path / "track.m4a")
    shutil.copy(VIDEO, tmp_path / "footage.mp4")
    bed, candidates = _find_music_bed(tmp_path, set())
    assert bed is not None and bed.name == "track.m4a"
    assert len(candidates) == 1


@needs_fixtures
def test_refuses_to_guess_between_several(tmp_path):
    """Scoring a promo with the wrong track is worse than asking which one."""
    shutil.copy(AUDIO, tmp_path / "track_a.m4a")
    shutil.copy(AUDIO, tmp_path / "track_b.m4a")
    bed, candidates = _find_music_bed(tmp_path, set())
    assert bed is None
    assert len(candidates) == 2


@needs_fixtures
def test_video_with_audio_is_not_a_music_bed(tmp_path):
    shutil.copy(VIDEO, tmp_path / "footage.mp4")
    bed, candidates = _find_music_bed(tmp_path, set())
    assert bed is None and candidates == []


@needs_fixtures
def test_extension_is_not_trusted(tmp_path):
    """A music file exported as .mp4 with no video stream is common -- it is
    exactly what turned up in real use."""
    shutil.copy(AUDIO, tmp_path / "song.mp4")
    bed, _ = _find_music_bed(tmp_path, set())
    assert bed is not None and bed.name == "song.mp4"


@needs_fixtures
def test_source_footage_is_excluded_from_candidates(tmp_path):
    audio_source = tmp_path / "voiceover.m4a"
    shutil.copy(AUDIO, audio_source)
    bed, _ = _find_music_bed(tmp_path, {audio_source.resolve()})
    assert bed is None


@needs_fixtures
def test_ignores_dotfiles(tmp_path):
    shutil.copy(AUDIO, tmp_path / "track.m4a")
    shutil.copy(AUDIO, tmp_path / "._sidecar.m4a")
    bed, candidates = _find_music_bed(tmp_path, set())
    assert bed is not None and len(candidates) == 1


def test_missing_directory_is_not_an_error(tmp_path):
    bed, candidates = _find_music_bed(tmp_path / "nope", set())
    assert bed is None and candidates == []


def test_only_promo_recipes_opt_into_auto_music():
    """Podcast recipes declare a `music` role for a bed the editor adds by hand.
    Auto-detection must not be inferred from that."""
    assert load_recipe("promo-silent").auto_music is True
    for name in ("podcast-2cam", "social-short", "client-promo"):
        assert load_recipe(name).auto_music is False, f"{name} must not auto-add music"


# --- Which root a track hangs off -------------------------------------------


def test_a_track_in_the_music_library_is_recorded_against_it():
    rel, root = _relative_to_root(
        Path("/Library/Upbeat/drive.mp3"), [Path("/Footage")], Path("/Library")
    )
    assert (rel, root) == ("Upbeat/drive.mp3", "music")


def test_a_track_beside_the_footage_stays_on_the_media_root():
    rel, root = _relative_to_root(
        Path("/Footage/theme.wav"), [Path("/Footage")], Path("/Library")
    )
    assert (rel, root) == ("theme.wav", "media")


def test_the_library_wins_when_it_sits_inside_the_media_root():
    # Nesting the library under the footage is a reasonable thing to do, and the
    # more specific root is the one that describes the file.
    rel, root = _relative_to_root(
        Path("/Footage/Music/drive.mp3"), [Path("/Footage")], Path("/Footage/Music")
    )
    assert (rel, root) == ("drive.mp3", "music")


def test_a_track_under_no_root_falls_back_to_its_name():
    rel, root = _relative_to_root(Path("/tmp/loose.wav"), [Path("/Footage")], None)
    assert (rel, root) == ("loose.wav", "media")


# --- The bed's source range must agree with its timeline length -------------


def test_a_bed_is_trimmed_to_the_length_it_is_placed_at():
    from autoedit.plan import EditPlanBuilder, MediaEntry
    from autoedit.timebase import Timebase

    tb = Timebase(25, 1)
    b = EditPlanBuilder(job_id="EP001", recipe="client-promo", timebase=tb,
                        sequence_name="EP001_promo_v1")
    b.add_media(MediaEntry(id="MUSIC", rel_path="track.wav", duration=60.0,
                           has_video=False, has_audio=True, role="music"))
    b.add_full_clip("MUSIC", 0, 498, video_track=-1, audio_track=2)

    clip = b.build()["timeline"][0]
    # 498 frames at 25fps is 19.92s. The apply side cuts from in/out, so leaving
    # outSeconds at the full 60s laid a bed that ran 40s past the picture.
    assert clip["durationFrames"] == 498
    assert clip["outSeconds"] == 19.92


def test_a_bed_shorter_than_the_edit_is_not_stretched():
    from autoedit.plan import EditPlanBuilder, MediaEntry
    from autoedit.timebase import Timebase

    tb = Timebase(25, 1)
    b = EditPlanBuilder(job_id="EP001", recipe="client-promo", timebase=tb,
                        sequence_name="EP001_promo_v1")
    b.add_media(MediaEntry(id="MUSIC", rel_path="short.wav", duration=5.0,
                           has_video=False, has_audio=True, role="music"))
    b.add_full_clip("MUSIC", 0, 498, video_track=-1, audio_track=2)
    assert b.build()["timeline"][0]["outSeconds"] == 5.0


# --- Which part of the track ------------------------------------------------
#
# A trend is a moment in a song, not its opening. The two rules that must stay
# apart: no explicit length means the bed follows the picture (the 126434f fix);
# an explicit length wins even past the last frame.

from autoedit.cli import resolve_music_chunk  # noqa: E402
from autoedit.music import BeatGrid  # noqa: E402

GRID = BeatGrid(bpm=120.0, beats=[i * 0.5 for i in range(240)], confidence=0.8)


def test_without_a_length_the_bed_follows_the_picture():
    chunk = resolve_music_chunk(track_duration=60.0, picture_seconds=20.0)
    assert (chunk.start, chunk.length) == (0.0, 20.0)
    assert chunk.warnings == ()


def test_an_explicit_length_wins_even_past_the_picture():
    chunk = resolve_music_chunk(track_duration=60.0, picture_seconds=12.0, length=20.0)
    assert chunk.length == 20.0
    assert "8.0s past the last frame" in " ".join(chunk.warnings)


def test_a_short_chunk_leaves_silence_and_says_so():
    chunk = resolve_music_chunk(track_duration=60.0, picture_seconds=20.0, length=10.0)
    assert chunk.length == 10.0
    assert "stops 10.0s before the picture" in " ".join(chunk.warnings)


def test_the_start_moves_to_the_nearest_beat():
    chunk = resolve_music_chunk(60.0, 20.0, start=30.2, beats=GRID)
    assert chunk.start == 30.0
    assert "nearest beat" in " ".join(chunk.warnings)


def test_snapping_can_be_turned_off():
    chunk = resolve_music_chunk(60.0, 20.0, start=30.2, beats=GRID, snap=False)
    assert chunk.start == 30.2
    assert chunk.warnings == ()


def test_a_start_already_on_a_beat_is_not_reported_as_moved():
    chunk = resolve_music_chunk(60.0, 20.0, start=30.0, beats=GRID)
    assert (chunk.start, chunk.warnings) == (30.0, ())


def test_an_inaudible_snap_is_applied_but_not_announced():
    # Parking by ear usually lands within milliseconds of a beat. Reporting that
    # produced "start moved 0.00s", which is noise in a list an editor must read.
    grid = BeatGrid(bpm=92.0, beats=[0.1969 + i * (60 / 92) for i in range(90)],
                    confidence=0.8)
    chunk = resolve_music_chunk(60.0, 20.0, start=30.2, beats=grid)
    assert abs(chunk.start - 30.2) < 0.02
    assert chunk.start != 30.2          # still snapped
    assert chunk.warnings == ()         # just not talked about


def test_a_chunk_running_off_the_end_is_clamped_and_reported():
    chunk = resolve_music_chunk(60.0, 20.0, start=55.0, length=20.0)
    assert chunk.length == 5.0
    assert "only has 5.0s left" in " ".join(chunk.warnings)


def test_a_start_past_the_track_falls_back_to_the_beginning():
    chunk = resolve_music_chunk(60.0, 20.0, start=90.0)
    assert chunk.start == 0.0
    assert "past the end" in " ".join(chunk.warnings)


def test_the_bed_carries_the_chosen_start_into_the_plan():
    from autoedit.plan import EditPlanBuilder, MediaEntry
    from autoedit.timebase import Timebase

    b = EditPlanBuilder(job_id="EP001", recipe="client-promo",
                        timebase=Timebase(25, 1), sequence_name="EP001_promo_v1")
    b.add_media(MediaEntry(id="MUSIC", rel_path="drive.mp3", duration=60.0,
                           has_video=False, has_audio=True, role="music"))
    b.add_full_clip("MUSIC", 0, 500, video_track=-1, audio_track=2, in_seconds=30.0)

    clip = b.build()["timeline"][0]
    assert clip["inSeconds"] == 30.0
    assert clip["outSeconds"] == 50.0      # 500 frames at 25fps, from 30s
    assert clip["atFrame"] == 0            # it still starts the sequence


def test_a_start_near_the_end_cannot_produce_an_out_past_the_track():
    from autoedit.plan import EditPlanBuilder, MediaEntry
    from autoedit.timebase import Timebase

    b = EditPlanBuilder(job_id="EP001", recipe="client-promo",
                        timebase=Timebase(25, 1), sequence_name="EP001_promo_v1")
    b.add_media(MediaEntry(id="MUSIC", rel_path="drive.mp3", duration=60.0,
                           has_video=False, has_audio=True, role="music"))
    b.add_full_clip("MUSIC", 0, 500, video_track=-1, audio_track=2, in_seconds=55.0)
    assert b.build()["timeline"][0]["outSeconds"] == 60.0


def test_a_track_too_short_for_the_picture_says_so_without_being_asked():
    # The bed follows the picture, so no length was set -- and the warning used
    # to be gated on one, which meant a 48s song under a 138s cut produced 48s of
    # music and complete silence about it.
    chunk = resolve_music_chunk(track_duration=48.0, picture_seconds=138.0)
    assert chunk.length == 48.0
    assert "stops 90.0s before the picture" in " ".join(chunk.warnings)


def test_a_bed_that_covers_the_picture_still_says_nothing():
    chunk = resolve_music_chunk(track_duration=200.0, picture_seconds=138.0)
    assert chunk.warnings == ()


# --- Cutting to the beat ----------------------------------------------------
#
# The bug: an editor picked a track, the panel reported its BPM, snapped the
# bed's start to a beat -- and then cut the picture to speech pauses. At 104 BPM
# (one beat = 0.577s) the cuts landed at 9.48, 1.33, 6.30 and 1.62 beats. The
# grid reached the picture planner and the music bed and nothing else.

from autoedit.plan import EditPlanBuilder, MediaEntry  # noqa: E402,F811
from autoedit.recipe import MusicSettings  # noqa: E402
from autoedit.detect import CutPlan, Drop, Keep, KIND_SILENCE  # noqa: E402
from autoedit.timebase import Timebase  # noqa: E402

# 104 BPM, the tempo of the track in the report. One beat is 0.5769s, which at
# 59.94fps is 34.58 frames -- deliberately NOT a whole number of frames, because
# that is what makes naive accumulation drift.
BPM104 = BeatGrid(bpm=104.0, beats=[i * (60.0 / 104.0) for i in range(400)], confidence=0.8)
SEQ = Timebase(60000, 1001)


def _builder():
    b = EditPlanBuilder(job_id="BEAT", recipe="social-short", timebase=SEQ,
                        sequence_name="BEAT_short_v1")
    b.add_media(MediaEntry(id="A", rel_path="a.mp4", duration=600.0,
                           timebase=SEQ, has_video=True, has_audio=True))
    return b


def _cuts(spans, silence_after=0.0):
    keeps = [Keep(s, e, "test", 1.0, 1) for s, e in spans]
    drops = []
    if silence_after:
        for _, e in spans:
            drops.append(Drop(e, e + silence_after, KIND_SILENCE, "pause"))
    return CutPlan(keeps=keeps, drops=drops, warnings=[])


def _beats_at(plan, bpm=104.0):
    fps = SEQ.fps
    interval = 60.0 / bpm
    return [
        round(c["atFrame"] / fps / interval, 3)
        for c in plan["timeline"] if c["videoTrack"] >= 0
    ]


def test_music_priority_puts_every_cut_on_a_beat():
    b = _builder()
    # The exact segment lengths from the report, which landed at 9.48 / 1.33 /
    # 6.30 / 1.62 beats before this existed.
    b.append_cuts("A", _cuts([(0, 5.472), (10, 10.767), (20, 23.637), (30, 30.934)]),
                  beats=BPM104, music=MusicSettings(beat_priority="music", beats_per_cut=1))
    for position in _beats_at(b.build()):
        assert abs(position - round(position)) < 0.03, f"{position} beats is not on the grid"


def test_beat_positions_do_not_drift_over_a_long_edit():
    """The property that makes this work at all.

    One beat is 34.58 frames. Rounding each duration to 35 and accumulating
    would drift 0.42 of a frame per cut -- over 60 cuts that is 25 frames, most
    of half a second, and the edit would visibly slide off the track. Absolute
    positions cannot compound.
    """
    b = _builder()
    spans = [(i * 4.0, i * 4.0 + 3.1) for i in range(60)]
    b.append_cuts("A", _cuts(spans),
                  beats=BPM104, music=MusicSettings(beat_priority="music", beats_per_cut=1))
    positions = _beats_at(b.build())
    assert len(positions) >= 50
    worst = max(abs(p - round(p)) for p in positions)
    assert worst < 0.05, f"drifted to {worst} beats off the grid by the end"
    # And specifically: the error at the end is no worse than at the start.
    assert abs(positions[-1] - round(positions[-1])) <= worst


def test_speech_priority_moves_a_cut_that_is_already_close():
    b = _builder()
    # 8 beats is 4.6154s. A segment ending at 4.66s is 45ms past it -- inside the
    # 120ms budget -- and there is a second of silence after it to absorb the move.
    b.append_cuts("A", _cuts([(0, 4.66), (10, 14.0)], silence_after=1.0),
                  beats=BPM104, music=MusicSettings(beat_priority="speech"),
                  min_clip_seconds=0.35)
    positions = _beats_at(b.build())
    assert abs(positions[1] - 8.0) < 0.03, f"expected the cut on beat 8, got {positions[1]}"


def test_speech_priority_leaves_a_cut_that_would_cost_a_word():
    # 5.48s sits almost exactly between beat 9 (5.192s) and beat 10 (5.769s) --
    # 288ms from either, well outside the 120ms budget. Reaching a beat from here
    # would cost a word, so the cut must stay where the speech put it.
    plan = _builder()
    plan.append_cuts("A", _cuts([(0, 5.48), (10, 14.0)], silence_after=1.0),
                     beats=BPM104, music=MusicSettings(beat_priority="speech"),
                     min_clip_seconds=0.35)
    built = plan.build()
    first = next(c for c in built["timeline"] if c["videoTrack"] >= 0)
    assert first["durationFrames"] == SEQ.to_frames(5.48), "the cut moved when it should not have"


def test_speech_priority_will_not_extend_into_speech():
    """No silence after the clip means no room, even for a tiny move."""
    b = _builder()
    b.append_cuts("A", _cuts([(0, 4.55), (10, 14.0)], silence_after=0.0),
                  beats=BPM104, music=MusicSettings(beat_priority="speech"),
                  min_clip_seconds=0.35)
    first = next(c for c in b.build()["timeline"] if c["videoTrack"] >= 0)
    assert first["durationFrames"] == SEQ.to_frames(4.55)


def test_a_grid_below_the_confidence_floor_is_refused_and_said_out_loud():
    shaky = BeatGrid(bpm=104.0, beats=[i * 0.5769 for i in range(100)], confidence=0.1)
    b = _builder()
    b.append_cuts("A", _cuts([(0, 5.472), (10, 10.767)]),
                  beats=shaky, music=MusicSettings(beat_priority="music"))
    plan = b.build()
    keys = [w.get("messageKey") for w in plan["warnings"]]
    assert "beat.gridUnavailable" in keys
    # And the cuts kept their natural length rather than being forced onto a
    # grid nobody trusts.
    first = next(c for c in plan["timeline"] if c["videoTrack"] >= 0)
    assert first["durationFrames"] == SEQ.to_frames(5.472)


def test_no_grid_at_all_changes_nothing():
    b = _builder()
    b.append_cuts("A", _cuts([(0, 5.472), (10, 10.767)]))
    first = next(c for c in b.build()["timeline"] if c["videoTrack"] >= 0)
    assert first["durationFrames"] == SEQ.to_frames(5.472)


def test_footage_on_a_second_drive_gets_its_own_root_name():
    """An editor with this shoot on the desktop and last month's on a drive.

    Without a name of its own, a file outside the first root fell back to its
    bare filename -- and the panel, resolving everything against the one root,
    then could not find it. The bare name is still the last resort; a second
    root is not.
    """
    roots = [Path("/Users/x/Desktop/Footage"), Path("/Volumes/Shoots/June")]
    assert _relative_to_root(Path("/Users/x/Desktop/Footage/a.mp4"), roots, None) \
        == ("a.mp4", "media")
    assert _relative_to_root(Path("/Volumes/Shoots/June/b.mp4"), roots, None) \
        == ("b.mp4", "media2")
    assert _relative_to_root(Path("/Volumes/Shoots/June/day2/c.mp4"), roots, None) \
        == ("day2/c.mp4", "media2")


def test_a_root_nested_inside_another_keeps_its_own_files():
    """Longest root wins, or the outer one swallows the inner one's files.

    Someone adding /Footage and then /Footage/Selects -- which is an ordinary
    thing to do -- would otherwise find every Selects file recorded against the
    outer root, and the distinction they made would silently not exist.
    """
    roots = [Path("/Footage"), Path("/Footage/Selects")]
    assert _relative_to_root(Path("/Footage/Selects/hero.mp4"), roots, None) \
        == ("hero.mp4", "media2")
    assert _relative_to_root(Path("/Footage/other.mp4"), roots, None) \
        == ("other.mp4", "media")
