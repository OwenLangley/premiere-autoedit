"""The job watcher.

Exercises the request -> argv mapping and the failure paths, which are what an
editor actually sees when something goes wrong.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))

from watch import (  # noqa: E402
    build_media_index, iter_media, read_config, request_to_argv, resolve_music,
    resolve_music_root, validate_request, write_capabilities, write_music_index,
)


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


# --- Media index -----------------------------------------------------------
#
# The index is what fills the panel's clip and music dropdowns. Music does not
# live loose among the rushes, so a flat scan left the music picker empty for
# anyone with an organised library.


def _tree(root: Path, *rel: str) -> None:
    for name in rel:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0")


def test_iter_media_descends_into_subfolders(tmp_path):
    _tree(tmp_path, "A.mp4", "Music/theme.wav", "Music/Cues/sting.mp3")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_media(tmp_path)}
    assert found == {"A.mp4", "Music/theme.wav", "Music/Cues/sting.mp3"}


def test_iter_media_stops_at_the_depth_limit(tmp_path):
    _tree(tmp_path, "deep/one/two/three/buried.wav")
    assert list(iter_media(tmp_path, max_depth=2)) == []
    assert len(list(iter_media(tmp_path, max_depth=4))) == 1


def test_iter_media_is_breadth_first_so_rushes_come_before_a_music_folder(tmp_path):
    _tree(tmp_path, "Music/theme.wav", "B.mp4", "A.mp4")
    order = [p.name for p in iter_media(tmp_path)]
    assert order == ["A.mp4", "B.mp4", "theme.wav"]


def test_iter_media_skips_dotfiles_and_unknown_extensions(tmp_path):
    _tree(tmp_path, "A.mp4", "._A.mp4", "notes.txt", ".hidden/B.mp4")
    assert [p.name for p in iter_media(tmp_path)] == ["A.mp4"]


def test_index_entries_carry_a_relative_path(tmp_path, monkeypatch):
    _tree(tmp_path, "A.mp4", "Music/theme.wav")
    _stub_probe(monkeypatch)
    index = build_media_index(tmp_path)
    assert {f["relPath"] for f in index["files"]} == {"A.mp4", "Music/theme.wav"}
    # `name` stays the basename so the panel has something short to show.
    assert {f["name"] for f in index["files"]} == {"A.mp4", "theme.wav"}


def test_index_reports_truncation_rather_than_silently_stopping(tmp_path, monkeypatch):
    _tree(tmp_path, *[f"C{i:03d}.mp4" for i in range(10)])
    _stub_probe(monkeypatch)
    assert build_media_index(tmp_path, max_files=100)["truncated"] is False
    capped = build_media_index(tmp_path, max_files=4)
    assert capped["truncated"] is True
    assert len(capped["files"]) == 4


def test_index_probes_each_file_once_across_refreshes(tmp_path, monkeypatch):
    _tree(tmp_path, "A.mp4", "B.mp4")
    calls = _stub_probe(monkeypatch)
    build_media_index(tmp_path)
    build_media_index(tmp_path)
    # Two files, two probes -- the second pass is served from cache. Without this
    # the recursive scan would re-spawn ffprobe for the whole tree every 30s.
    assert len(calls) == 2


def _stub_probe(monkeypatch):
    """Replace ffprobe with something deterministic, and record what it saw."""
    import watch

    calls = []

    class Info:
        has_video = True
        has_audio = True
        duration = 1.0
        width = 1920
        height = 1080

    def fake(path):
        calls.append(Path(path))
        return Info()

    watch._PROBE_CACHE.clear()
    monkeypatch.setattr(watch, "probe", fake)
    return calls


# --- A music library outside the footage tree -------------------------------
#
# Editors keep music in a library, not among the rushes. The `library:` marker
# says which root a track hangs off; guessing between roots would score a promo
# with the wrong track and nothing would say so until playback.


def test_library_tracks_resolve_against_the_music_root():
    got = resolve_music("library:Upbeat/drive.mp3", Path("/media"), Path("/Library"))
    assert got == Path("/Library/Upbeat/drive.mp3")


def test_unmarked_tracks_resolve_against_the_media_root():
    got = resolve_music("theme.wav", Path("/media"), Path("/Library"))
    assert got == Path("/media/theme.wav")


def test_an_absolute_track_is_left_alone():
    got = resolve_music("/elsewhere/theme.wav", Path("/media"), None)
    assert got == Path("/elsewhere/theme.wav")


def test_auto_and_none_resolve_to_nothing():
    assert resolve_music("auto", Path("/media"), None) is None
    assert resolve_music("none", Path("/media"), None) is None


def test_a_library_track_without_a_library_is_a_readable_failure():
    request = {
        "schemaVersion": "1.0", "jobId": "EP001", "recipe": "promo-silent",
        "media": ["a.mp4"], "options": {"music": "library:drive.mp3"},
    }
    with pytest.raises(ValueError, match="no music folder is set"):
        request_to_argv(request, Path("/jobs"), Path("/media"), Path("/cache"), None)


def test_the_music_root_is_passed_to_the_engine():
    argv = request_to_argv(
        request(music="library:drive.mp3"),
        Path("/jobs"), Path("/media"), Path("/cache"), Path("/Library"),
    )
    assert "--music-root" in argv and "/Library" in argv
    assert "/Library/drive.mp3" in argv


def test_config_sets_the_music_root_without_a_restart(tmp_path):
    jobs = tmp_path / "jobs"
    library = tmp_path / "Library"
    jobs.mkdir()
    library.mkdir()
    assert resolve_music_root(jobs, None) is None

    (jobs / "config.json").write_text(json.dumps({"musicRoot": str(library)}))
    assert resolve_music_root(jobs, None) == library.resolve()


def test_a_bad_music_root_falls_back_rather_than_crashing(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "config.json").write_text(json.dumps({"musicRoot": "/nope/not/here"}))
    assert resolve_music_root(jobs, None) is None


def test_unreadable_config_is_treated_as_absent(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "config.json").write_text("{ not json")
    assert read_config(jobs) == {}


def test_the_music_index_holds_only_tracks(tmp_path, monkeypatch):
    library = tmp_path / "Library"
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    _tree(library, "drive.mp3", "Upbeat/lift.wav", "promo.mp4")

    import watch
    watch._PROBE_CACHE.clear()

    class Track:
        has_video = False
        has_audio = True
        duration = 120.0
        width = 0
        height = 0

    class Video:
        has_video = True
        has_audio = True
        duration = 30.0
        width = 1920
        height = 1080

    monkeypatch.setattr(
        watch, "probe",
        lambda path: Video() if Path(path).suffix == ".mp4" else Track(),
    )
    write_music_index(jobs, library)
    index = json.loads((jobs / "music-index.json").read_text())
    assert index["musicRoot"] == str(library)
    assert {f["relPath"] for f in index["files"]} == {"drive.mp3", "Upbeat/lift.wav"}


def test_no_library_still_writes_an_empty_index(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    write_music_index(jobs, None)
    index = json.loads((jobs / "music-index.json").read_text())
    assert index["musicRoot"] is None and index["files"] == []
