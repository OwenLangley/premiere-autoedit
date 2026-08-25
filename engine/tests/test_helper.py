"""The job watcher.

Exercises the request -> argv mapping and the failure paths, which are what an
editor actually sees when something goes wrong.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))

from watch import request_to_argv, validate_request, write_capabilities  # noqa: E402


def request(**options):
    return {
        "schemaVersion": "1.0",
        "jobId": "EP001",
        "recipe": "promo-silent",
        "media": ["a.mp4", "b.mp4"],
        "options": options,
    }


def test_maps_a_minimal_request():
    argv = request_to_argv(request(), Path("/jobs"), Path("/media"), Path("/cache"))
    assert argv[0] == "plan"
    assert "--job" in argv and "EP001" in argv
    assert "/media/a.mp4" in argv and "/media/b.mp4" in argv


def test_media_paths_are_resolved_against_the_media_root():
    argv = request_to_argv(request(), Path("/jobs"), Path("/Volumes/NAS"), Path("/cache"))
    assert "/Volumes/NAS/a.mp4" in argv


def test_source_aspect_and_standard_pacing_add_no_flags():
    """Defaults must not be passed explicitly, or 'unchanged' stops meaning unchanged."""
    argv = request_to_argv(
        request(aspect="source", pacing="standard"), Path("/j"), Path("/m"), Path("/c")
    )
    assert "--aspect" not in argv and "--pacing" not in argv


def test_options_become_flags():
    argv = request_to_argv(
        request(aspect="vertical", pacing="punchy", look="brand-punch", visual=True,
                duration={"mode": "exactly", "seconds": 30}),
        Path("/j"), Path("/m"), Path("/c"),
    )
    for expected in ("--aspect", "vertical", "--pacing", "punchy", "--look",
                     "brand-punch", "--visual", "--duration", "30", "--duration-mode", "exactly"):
        assert expected in argv


def test_duration_without_a_mode_is_not_passed():
    argv = request_to_argv(request(duration={"mode": "none"}), Path("/j"), Path("/m"), Path("/c"))
    assert "--duration" not in argv


def test_music_none_and_explicit_path():
    assert "--no-music" in request_to_argv(request(music="none"), Path("/j"), Path("/m"), Path("/c"))
    argv = request_to_argv(request(music="track.wav"), Path("/j"), Path("/m"), Path("/c"))
    assert "--music" in argv and "/m/track.wav" in argv
    assert "--music" not in request_to_argv(request(music="auto"), Path("/j"), Path("/m"), Path("/c"))


# ------------------------------------------------------------------ validation


def test_a_good_request_validates():
    assert validate_request(request()) == []


@pytest.mark.parametrize("mutate,expected", [
    ({"schemaVersion": "2.0"}, "schemaVersion"),
    ({"recipe": ""}, "recipe"),
    ({"media": []}, "media"),
])
def test_bad_requests_are_rejected_with_a_reason(mutate, expected):
    r = request()
    r.update(mutate)
    problems = validate_request(r)
    assert problems and any(expected in p for p in problems)


def test_unknown_option_is_rejected_not_ignored():
    """A typo that silently falls back to a default would produce the wrong edit."""
    r = request()
    r["options"]["pacng"] = "punchy"
    assert validate_request(r)


def test_capabilities_lists_what_the_engine_supports(tmp_path):
    write_capabilities(tmp_path)
    caps = json.loads((tmp_path / "capabilities.json").read_text())
    names = [r["name"] for r in caps["recipes"]]
    assert "promo-silent" in names and "podcast-2cam" in names
    assert {a["value"] for a in caps["aspects"]} >= {"source", "vertical", "landscape"}
    assert {p["value"] for p in caps["pacing"]} == {"relaxed", "standard", "punchy"}
