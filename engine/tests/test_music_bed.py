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
        Path("/Library/Upbeat/drive.mp3"), Path("/Footage"), Path("/Library")
    )
    assert (rel, root) == ("Upbeat/drive.mp3", "music")


def test_a_track_beside_the_footage_stays_on_the_media_root():
    rel, root = _relative_to_root(
        Path("/Footage/theme.wav"), Path("/Footage"), Path("/Library")
    )
    assert (rel, root) == ("theme.wav", "media")


def test_the_library_wins_when_it_sits_inside_the_media_root():
    # Nesting the library under the footage is a reasonable thing to do, and the
    # more specific root is the one that describes the file.
    rel, root = _relative_to_root(
        Path("/Footage/Music/drive.mp3"), Path("/Footage"), Path("/Footage/Music")
    )
    assert (rel, root) == ("drive.mp3", "music")


def test_a_track_under_no_root_falls_back_to_its_name():
    rel, root = _relative_to_root(Path("/tmp/loose.wav"), Path("/Footage"), None)
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
