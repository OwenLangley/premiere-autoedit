"""Music bed discovery.

The rule under test: pick a bed only when there is exactly one candidate, never
guess between several, and never trust the file extension.
"""

import shutil
from pathlib import Path

import pytest

from autoedit.cli import _find_music_bed
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
