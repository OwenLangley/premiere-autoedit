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
    build_media_index, iter_media, process, read_config, request_to_argv,
    resolve_media_roots, resolve_music, resolve_music_root, safe_job_id,
    validate_request, write_capabilities, write_music_index,
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


NAME_VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "job-name-vectors.json").read_text()
)


@pytest.mark.parametrize("case", NAME_VECTORS["accept"], ids=lambda c: c["name"] or "empty")
def test_a_job_name_the_panel_accepts_is_accepted_here(case):
    """The panel and this schema must agree, and for months they did not.

    `normaliseJobId` was widened for Japanese; the jobId pattern was left as
    `^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$`. So the panel wrote a request it
    considered perfectly valid and the helper refused it -- showing a video
    editor a regex. panel/test/conformance.test.js reads the same vectors.
    """
    assert validate_request({**request(), "jobId": case["name"]}) == [], case["why"]


@pytest.mark.parametrize("case", NAME_VECTORS["reject"], ids=lambda c: c["name"] or "empty")
def test_a_job_name_that_is_unsafe_as_a_filename_is_refused(case):
    """Widening the rule by script must not widen it by structure."""
    assert validate_request({**request(), "jobId": case["name"]}), case["why"]


def test_the_name_rule_is_explained_without_showing_a_regex():
    problems = validate_request({**request(), "jobId": "a/b"})
    assert problems and "does not match" not in problems[0], problems
    assert "any language" in problems[0], problems


def test_an_unplugged_drive_is_reported_once_not_every_poll(tmp_path, capsys):
    """The watch loop re-resolves every 2 seconds. One report came back 6063
    lines long with 6049 of them naming the same missing drive, which buried the
    four lines that actually said what went wrong."""
    import watch
    watch._WARNED_UNREACHABLE.clear()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    gone = tmp_path / "unplugged"
    (jobs / "config.json").write_text(json.dumps({"mediaRoots": [str(gone)]}))

    for _ in range(10):
        resolve_media_roots(jobs, tmp_path)
    warnings = [l for l in capsys.readouterr().err.splitlines() if "not reachable" in l]
    assert len(warnings) == 1, warnings

    # Plugged back in, then pulled again: that is news, and is reported again.
    gone.mkdir()
    resolve_media_roots(jobs, tmp_path)
    gone.rmdir()
    resolve_media_roots(jobs, tmp_path)
    assert len([l for l in capsys.readouterr().err.splitlines()
                if "not reachable" in l]) == 1


@pytest.mark.parametrize("job_id", ["../../../oops", "a/b", ".hidden", ""])
def test_an_unsafe_id_never_names_a_status_file(tmp_path, job_id):
    """A request that fails the schema is still reported -- by writing
    `<jobId>.status.json`. So the id becomes a path BEFORE it is validated, and
    the schema cannot be the only guard."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "safe-name.request.json").write_text(json.dumps({**request(), "jobId": job_id}))

    process(jobs / "safe-name.request.json", jobs, tmp_path, tmp_path)

    written = sorted(f.name for f in jobs.rglob("*.status.json"))
    assert written == ["safe-name.status.json"], written
    assert not list(tmp_path.glob("*.status.json"))


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


# --- Choosing part of a track -----------------------------------------------


def test_a_chunk_becomes_start_and_length_flags():
    argv = request_to_argv(
        request(music="library:drive.mp3",
                musicChunk={"startSeconds": 42.4, "lengthSeconds": 20}),
        Path("/jobs"), Path("/media"), Path("/cache"), Path("/Library"),
    )
    assert "--music-start" in argv and "42.4" in argv
    assert "--music-length" in argv and "20" in argv
    assert "--no-music-snap" not in argv


def test_snapping_off_is_passed_through():
    argv = request_to_argv(
        request(music="drive.mp3", musicChunk={"startSeconds": 10, "snapToBeat": False}),
        Path("/jobs"), Path("/media"), Path("/cache"),
    )
    assert "--no-music-snap" in argv


def test_a_chunk_without_a_chosen_track_is_a_readable_failure():
    # Against "automatic" a start would silently apply to whatever the engine
    # happened to find beside the footage.
    with pytest.raises(ValueError, match="without choosing a track"):
        request_to_argv(
            request(music="auto", musicChunk={"startSeconds": 10}),
            Path("/jobs"), Path("/media"), Path("/cache"),
        )


def test_no_chunk_adds_no_flags():
    argv = request_to_argv(
        request(music="drive.mp3"), Path("/jobs"), Path("/media"), Path("/cache"),
    )
    assert not any(a.startswith("--music-start") or a.startswith("--music-length") for a in argv)


# --- the speech path actually runs ------------------------------------------


def test_the_speech_path_survives_duration_fitting(tmp_path):
    """A CLI run through the transcript path, end to end.

    This exists because of a real outage: the per-clip loop rebound `options`,
    which already held the job's JobOptions, so every speech job died on
    `options.duration_mode`. Nothing caught it because the work at the time was
    all going through `--visual`, which never enters that loop. A unit test of
    either half would still have passed.
    """
    import sys
    from autoedit.cli import main as engine_main

    fixtures = Path(__file__).resolve().parent / "fixtures"
    media = fixtures / "sample_25fps_1080p.mp4"
    if not media.exists():
        pytest.skip("sample fixture not generated")

    out = tmp_path / "SPEECH.editplan.json"
    code = engine_main([
        "plan", "--job", "SPEECH", "--recipe", "podcast-2cam",
        "--media", str(media), "--media-root", str(fixtures),
        "--transcript", str(fixtures / "sample_25fps_1080p.transcript.json"),
        "--duration", "5", "--duration-mode", "upTo",
        "--work-dir", str(tmp_path / "cache"), "--out", str(out),
    ])
    assert code == 0, "the speech path must reach the end without an AttributeError"
    assert out.exists()


# --- the sequence rate follows the footage ----------------------------------


def test_a_recipe_rate_the_footage_cannot_land_on_is_overridden(tmp_path):
    """A CLI run whose recipe and footage disagree about the frame rate.

    The outage this guards: social-short pins the sequence to 30.000 and the
    camera shot 59.94. Nothing reconciled them, so every clip's duration became a
    fractional number of sequence frames, the plan rounded one way and Premiere
    rounded the other, and the assembly came back with one-frame gaps -- black
    flashes between shots. Both halves were individually correct; only the whole
    run shows it.
    """
    import json as _json
    from autoedit.cli import main as engine_main

    fixtures = Path(__file__).resolve().parent / "fixtures"
    media = fixtures / "sample_25fps_1080p.mp4"
    if not media.exists():
        pytest.skip("sample fixture not generated")

    out = tmp_path / "RATE.editplan.json"
    code = engine_main([
        "plan", "--job", "RATE", "--recipe", "social-short",
        "--media", str(media), "--media-root", str(fixtures),
        "--transcript", str(fixtures / "sample_25fps_1080p.transcript.json"),
        "--work-dir", str(tmp_path / "cache"), "--out", str(out),
    ])
    assert code == 0
    plan = _json.loads(out.read_text())

    assert plan["timebase"]["fpsNum"] == 25, "the footage decides, not the recipe"
    assert plan["timebase"]["fpsDen"] == 1
    assert any(w.get("messageKey") == "timebase.followedFootage" for w in plan["warnings"]), \
        "changing the delivery rate has to be said out loud, not done quietly"


def test_every_clip_duration_is_a_whole_number_of_source_frames(tmp_path):
    """The property that makes the assembly gap-free, asserted directly."""
    import json as _json
    from autoedit.cli import main as engine_main
    from autoedit.timebase import Timebase

    fixtures = Path(__file__).resolve().parent / "fixtures"
    media = fixtures / "sample_25fps_1080p.mp4"
    if not media.exists():
        pytest.skip("sample fixture not generated")

    out = tmp_path / "GRID.editplan.json"
    code = engine_main([
        "plan", "--job", "GRID", "--recipe", "social-short",
        "--media", str(media), "--media-root", str(fixtures),
        "--transcript", str(fixtures / "sample_25fps_1080p.transcript.json"),
        "--work-dir", str(tmp_path / "cache"), "--out", str(out),
    ])
    assert code == 0
    plan = _json.loads(out.read_text())
    seq = Timebase.from_dict(plan["timebase"])
    sources = {m["id"]: m.get("timebase") for m in plan["media"]}

    for clip in plan["timeline"]:
        src = sources.get(clip["mediaId"])
        if not src:
            continue
        src_tb = Timebase.from_dict(src)
        span = src_tb.to_frames(clip["outSeconds"]) - src_tb.to_frames(clip["inSeconds"])
        # The source span, expressed in sequence frames, must be a whole number --
        # and must be exactly what the plan says it is. Anything else means
        # somebody downstream has to round, and Premiere rounds differently.
        assert span * seq.fps_num * src_tb.fps_den % (seq.fps_den * src_tb.fps_num) == 0, \
            f"{clip['mediaId']} spans {span} source frames, which is not whole in the sequence"
        assert clip["durationFrames"] == seq.to_frames(src_tb.to_seconds(span))


def test_match_source_still_pins_the_sequence_rate(tmp_path):
    """A preset has to be written even when the aspect is "Match source".

    Without one the panel calls `createSequence(name)` and Premiere supplies its
    own defaults, so the rate the plan carefully chose never reaches the
    sequence and the clips are a frame out all over again.
    """
    import json as _json
    from autoedit.cli import main as engine_main

    fixtures = Path(__file__).resolve().parent / "fixtures"
    media = fixtures / "sample_25fps_1080p.mp4"
    if not media.exists():
        pytest.skip("sample fixture not generated")

    out = tmp_path / "SRC.editplan.json"
    code = engine_main([
        "plan", "--job", "SRC", "--recipe", "social-short", "--aspect", "source",
        "--media", str(media), "--media-root", str(fixtures),
        "--transcript", str(fixtures / "sample_25fps_1080p.transcript.json"),
        "--work-dir", str(tmp_path / "cache"), "--out", str(out),
    ])
    assert code == 0
    plan = _json.loads(out.read_text())
    preset = plan["sequence"].get("presetPath")
    assert preset, "match-source jobs need a preset too, or the rate is Premiere's guess"

    xml = Path(preset).read_text()
    from autoedit.preset import ticks_per_frame
    from autoedit.timebase import Timebase
    expected = ticks_per_frame(Timebase.from_dict(plan["timebase"]))
    assert f"<VideoFrameRate>{expected}</VideoFrameRate>" in xml, \
        "the preset's rate must be the plan's rate"


# --- footage that will not play back ----------------------------------------
#
# The report was "the videos are being put in, however when they don't have
# enough length for the slot designated for them it's just showing a freeze
# frame of the final frame". The edit was correct; the decoder was not keeping
# up, and Premiere was holding the last frame it managed to decode -- which at a
# cut is the outgoing clip's final frame.


def _media(**kw):
    from autoedit.probe import MediaInfo
    base = dict(path=Path("/tmp/x.mp4"), duration=10.0, has_video=True,
                width=3840, height=2160, codec="hevc", pix_fmt="yuv422p10le",
                keyframe_interval=1.001)
    base.update(kw)
    return MediaInfo(**base)


def test_the_reported_footage_is_recognised_as_unplayable():
    from autoedit.probe import needs_proxy
    # 4K 59.94p 10-bit 4:2:2 HEVC, keyframes once per second. Measured at 1.19x
    # real time for decode alone with hardware acceleration.
    assert needs_proxy(_media())


def test_ordinary_hd_h264_is_left_alone():
    from autoedit.probe import needs_proxy
    assert not needs_proxy(_media(width=1920, height=1080, codec="h264",
                                  pix_fmt="yuvj420p", keyframe_interval=0.5))


def test_all_intra_is_left_alone_however_large():
    from autoedit.probe import needs_proxy
    # ProRes is every-frame-a-keyframe, so seeking is cheap at any size.
    assert not needs_proxy(_media(codec="prores", pix_fmt="yuv422p10le"))


def test_4k_8bit_420_is_left_alone():
    from autoedit.probe import needs_proxy
    # Heavy frame, but the chroma format every hardware decoder is built for.
    assert not needs_proxy(_media(pix_fmt="yuv420p"))


def test_audio_only_never_needs_a_proxy():
    from autoedit.probe import needs_proxy
    assert not needs_proxy(_media(has_video=False, width=0, height=0))


def test_a_proxy_path_changes_when_the_file_does(tmp_path):
    """Replacing a file with a different take must not reuse the old proxy."""
    from autoedit.proxy import proxy_path
    source = tmp_path / "C1367.MP4"
    source.write_bytes(b"take one")
    first = proxy_path(tmp_path / "cache", source)
    source.write_bytes(b"take two, which is longer")
    second = proxy_path(tmp_path / "cache", source)
    assert first != second, "a changed source must not keep the old proxy"
    assert first.parent == second.parent
    assert first.suffix == ".mov"


def test_a_proxy_path_is_stable_for_an_unchanged_file(tmp_path):
    from autoedit.proxy import proxy_path
    source = tmp_path / "C1367.MP4"
    source.write_bytes(b"unchanged")
    assert proxy_path(tmp_path / "c", source) == proxy_path(tmp_path / "c", source)


# --- footage that is stored one way round and shown another ------------------
#
# Cameras write a rotation flag rather than rotating pixels, so a vertical clip
# arrives as 3840x2160 with rotation=90. Reading the stored dimensions gave a
# landscape sequence for vertical rushes, proxies at 608x1080 instead of
# 1080x1920, and a scale-to-fill that zoomed and cropped a picture that was
# already exactly the right shape.


def _rotated(**kw):
    from autoedit.probe import MediaInfo
    base = dict(path=Path("/tmp/x.mp4"), duration=10.0, has_video=True,
                width=3840, height=2160, rotation=90)
    base.update(kw)
    return MediaInfo(**base)


def test_a_rotated_clip_is_seen_the_way_it_is_shot():
    m = _rotated()
    assert (m.display_width, m.display_height) == (2160, 3840)
    assert m.is_vertical


def test_an_unrotated_clip_is_left_alone():
    m = _rotated(rotation=0)
    assert (m.display_width, m.display_height) == (3840, 2160)
    assert not m.is_vertical


def test_270_also_turns_the_picture():
    assert _rotated(rotation=270).display_width == 2160


def test_180_does_not_swap_the_axes():
    m = _rotated(rotation=180)
    assert (m.display_width, m.display_height) == (3840, 2160)


def test_match_source_follows_the_shape_the_camera_shows(tmp_path):
    """A vertical clip must not produce a landscape sequence."""
    from autoedit.options import working_frame_size
    m = _rotated()
    assert working_frame_size(m.display_width, m.display_height) == (1080, 1920)
    # ...which is exactly what reading the raster would have got wrong:
    assert working_frame_size(m.width, m.height) == (1920, 1080)


def test_rotation_is_read_from_the_display_matrix():
    from autoedit.probe import _rotation_of
    assert _rotation_of({"side_data_list": [
        {"side_data_type": "Display Matrix", "rotation": 90}]}) == 90


def test_rotation_is_read_from_the_older_tag_too():
    from autoedit.probe import _rotation_of
    assert _rotation_of({"tags": {"rotate": "270"}}) == 270


def test_no_rotation_information_means_none():
    from autoedit.probe import _rotation_of
    assert _rotation_of({}) == 0
    assert _rotation_of({"side_data_list": [{"side_data_type": "Other"}]}) == 0


# --- an edit cut to music should not outlive the music -----------------------
#
# The reported job came out 134s of picture against 60s of track, so 55% of it
# had no beat to cut to. The cuts under the music were landing within a tenth of
# a beat; there simply was not any music for the second half.


def _music_job(tmp_path, job, recipe, extra=()):
    import json as _json
    from autoedit.cli import main as engine_main
    fixtures = Path(__file__).resolve().parent / "fixtures"
    media = fixtures / "sample_25fps_1080p.mp4"
    music = fixtures / "sample_music.wav"
    if not media.exists() or not music.exists():
        pytest.skip("fixtures not generated")
    out = tmp_path / f"{job}.editplan.json"
    code = engine_main([
        "plan", "--job", job, "--recipe", recipe,
        "--media", str(media), "--media-root", str(fixtures),
        "--transcript", str(fixtures / "sample_25fps_1080p.transcript.json"),
        "--music", str(music),
        "--work-dir", str(tmp_path / "cache"), "--out", str(out), *extra,
    ])
    assert code == 0
    return _json.loads(out.read_text())


def test_a_music_led_edit_is_capped_at_the_track(tmp_path):
    plan = _music_job(tmp_path, "CAP", "social-short")
    keys = [w.get("messageKey") for w in plan["warnings"]]
    assert "music.cappedToTrack" in keys, "the cap has to be said out loud"


def test_an_explicit_length_still_wins(tmp_path):
    plan = _music_job(tmp_path, "LEN", "social-short", ("--duration", "3", "--duration-mode", "upTo"))
    keys = [w.get("messageKey") for w in plan["warnings"]]
    assert "music.cappedToTrack" not in keys, "the editor's own length must not be overridden"


def test_a_speech_led_recipe_is_not_capped(tmp_path):
    # podcast-2cam prioritises speech; a bed the editor added by hand must not
    # start deciding how long their interview is.
    plan = _music_job(tmp_path, "TALK", "podcast-2cam")
    keys = [w.get("messageKey") for w in plan["warnings"]]
    assert "music.cappedToTrack" not in keys


# --- describing the video ---------------------------------------------------

def test_a_story_reaches_the_engine_and_turns_on_visual():
    """A description is matched against pictures, so it implies visual cutting.

    Requiring the editor to tick a box as well would be a trap: the prompt is
    accepted, silently ignored, and the edit comes back cut to speech with
    nothing explaining why.
    """
    req = {
        "schemaVersion": "1.0", "jobId": "S", "recipe": "social-short",
        "media": ["a.mp4"],
        "options": {"story": "opens with the storefront, then the chef"},
    }
    argv = request_to_argv(req, Path("/tmp/j"), Path("/tmp/m"), Path("/tmp/w"))
    assert "--visual" in argv
    assert argv[argv.index("--story") + 1] == "opens with the storefront, then the chef"


def test_visual_is_not_turned_on_by_an_empty_story():
    req = {
        "schemaVersion": "1.0", "jobId": "S", "recipe": "social-short",
        "media": ["a.mp4"], "options": {"story": "   "},
    }
    argv = request_to_argv(req, Path("/tmp/j"), Path("/tmp/m"), Path("/tmp/w"))
    assert "--story" not in argv
    assert "--visual" not in argv


def test_a_request_without_a_story_is_unchanged():
    req = {
        "schemaVersion": "1.0", "jobId": "S", "recipe": "social-short",
        "media": ["a.mp4"], "options": {"aspect": "vertical"},
    }
    argv = request_to_argv(req, Path("/tmp/j"), Path("/tmp/m"), Path("/tmp/w"))
    assert "--story" not in argv and "--visual" not in argv


def test_icloud_placeholders_are_counted_not_silently_skipped(tmp_path):
    """Footage iCloud has evicted is invisible to every test in the walk.

    An evicted file is a hidden placeholder named `.C1367.MP4.icloud`, so it
    fails the dotfile skip AND the extension test. The footage shows in Finder
    with a cloud badge and the panel said "no video files in the media root",
    which is true and tells an editor nothing they can act on.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import build_media_index

    (tmp_path / "sub").mkdir()
    for name in (".C1367.MP4.icloud", ".C1371.MOV.icloud", ".Notes.txt.icloud"):
        (tmp_path / name).touch()
    (tmp_path / "sub" / ".C1380.MP4.icloud").touch()

    index = build_media_index(tmp_path)
    assert index["files"] == []
    # The text file is not footage and must not be reported as missing footage.
    assert index["evicted"] == ["C1367.MP4", "C1371.MOV", "C1380.MP4"]
    assert index["evictedCount"] == 3


def test_an_ordinary_dotfile_is_not_reported_as_evicted(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import build_media_index

    (tmp_path / ".DS_Store").touch()
    (tmp_path / ".hidden.mp4").touch()
    assert build_media_index(tmp_path)["evictedCount"] == 0


def test_the_media_root_can_move_after_setup(tmp_path):
    """Moving the footage used to point the whole tool at an empty folder.

    The root was fixed in the launchd plist at install time. The panel's own
    picker only stored a UXP token, which the helper cannot use, so changing it
    there did nothing an editor could see -- the clip list comes from the
    helper's index. Config is the one channel both sides share.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_media_root

    installed = tmp_path / "old"
    moved = tmp_path / "new"
    jobs = tmp_path / "jobs"
    for d in (installed, moved, jobs):
        d.mkdir()

    # Nothing in config: the plist value stands.
    assert resolve_media_root(jobs, installed) == installed

    (jobs / "config.json").write_text(json.dumps({"mediaRoot": str(moved)}))
    assert resolve_media_root(jobs, installed) == moved.resolve()


def test_a_media_root_that_is_not_a_folder_falls_back(tmp_path):
    # A stale or mistyped path must not leave the tool pointing at nothing.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_media_root

    installed = tmp_path / "old"
    jobs = tmp_path / "jobs"
    installed.mkdir(); jobs.mkdir()
    (jobs / "config.json").write_text(json.dumps({"mediaRoot": str(tmp_path / "gone")}))
    assert resolve_media_root(jobs, installed) == installed


def test_a_chosen_frame_rate_reaches_the_engine(tmp_path):
    """The editor's choice has to survive the trip from panel to argv."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import request_to_argv

    base = {"schemaVersion": "1.0", "jobId": "j", "recipe": "client-promo",
            "media": ["a.mp4"]}
    argv = request_to_argv({**base, "options": {"frameRate": "25"}},
                           tmp_path, tmp_path, tmp_path)
    assert "--fps" in argv and argv[argv.index("--fps") + 1] == "25"

    # "auto" is the absence of a choice and must not be sent: the engine would
    # treat it as a delivery spec and stop following the footage.
    for options in ({"frameRate": "auto"}, {}):
        assert "--fps" not in request_to_argv({**base, "options": options},
                                              tmp_path, tmp_path, tmp_path)


def test_a_rooted_media_reference_resolves_to_its_own_drive(tmp_path):
    """`media2:C0001.MP4` is the second root's file, not the first's.

    Two cards both holding C0001.MP4 is ordinary. Probing the roots in turn
    would pick whichever came first and be silently wrong about which shoot the
    editor selected, which is why the reference names its root.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import _media_path

    roots = [Path("/A"), Path("/B")]
    assert _media_path("C1.MP4", roots) == "/A/C1.MP4"          # no prefix: the first
    assert _media_path("media:C1.MP4", roots) == "/A/C1.MP4"
    assert _media_path("media2:day2/C1.MP4", roots) == "/B/day2/C1.MP4"
    # A root that is not configured any more falls back rather than crashing.
    assert _media_path("media9:C1.MP4", roots) == "/A/C1.MP4"


def test_every_root_reaches_the_engine(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import request_to_argv

    argv = request_to_argv(
        {"jobId": "j", "recipe": "client-promo", "media": ["a.mp4", "media2:b.mp4"],
         "options": {}},
        tmp_path, Path("/A"), tmp_path, None, [Path("/A"), Path("/B")])
    roots = [argv[i + 1] for i, a in enumerate(argv) if a == "--media-root"]
    assert roots == ["/A", "/B"], "order is the contract: it names them by position"
    assert "/A/a.mp4" in argv and "/B/b.mp4" in argv


def test_an_unplugged_drive_is_dropped_not_fatal(tmp_path):
    """An unplugged drive is a Tuesday. The rest of the library keeps working."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_media_roots, unreachable_media_roots

    here = tmp_path / "here"
    here.mkdir()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "config.json").write_text(json.dumps(
        {"mediaRoots": [str(here), str(tmp_path / "unplugged")]}))

    assert resolve_media_roots(jobs, here) == [here.resolve()]
    assert unreachable_media_roots(jobs) == [str(tmp_path / "unplugged")]


def test_the_old_single_root_setting_still_works(tmp_path):
    # Every machine already configured has `mediaRoot`, not `mediaRoots`.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_media_roots

    here = tmp_path / "here"
    here.mkdir()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "config.json").write_text(json.dumps({"mediaRoot": str(here)}))
    assert resolve_media_roots(jobs, tmp_path) == [here.resolve()]


def test_the_index_keeps_every_root_when_it_is_refreshed(tmp_path):
    """The periodic refresh must not quietly drop back to one root.

    `write_media_index` takes the roots as an optional argument, and two of the
    four call sites did not pass them. So a second folder appeared, worked, and
    thirty seconds later vanished when the refresh rewrote the index from the
    first root alone -- which from outside is indistinguishable from never
    having worked at all.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    a, b, jobs = tmp_path / "a", tmp_path / "b", tmp_path / "jobs"
    for d in (a, b, jobs):
        d.mkdir()
    (a / "one.mp4").write_bytes(b"")
    (b / "two.mp4").write_bytes(b"")

    watch.write_media_index(jobs, a, [a, b])
    index = json.loads((jobs / "media-index.json").read_text())
    assert set(index["roots"]) == {"media", "media2"}
    assert index["mediaRoots"] == [str(a), str(b)]


def test_every_write_media_index_call_passes_its_roots():
    """Guards the defect directly: the argument is optional and was forgotten.

    Reading the source is crude, but the alternative is a live helper and a
    forty-second wait, and what went wrong was a call site rather than a
    behaviour.
    """
    source = (Path(__file__).resolve().parents[2] / "helper" / "watch.py").read_text()
    calls = [line.strip() for line in source.splitlines()
             if "write_media_index(jobs" in line and "def " not in line]
    assert calls, "no call sites found -- this test has gone stale"
    for call in calls:
        assert "roots" in call, f"call site drops the roots: {call}"


def test_one_big_folder_cannot_starve_the_others(tmp_path):
    """A photo library took 395 of a 400-file budget and left five for the
    folder holding the actual rushes.

    The clips that mattered were simply absent from the picker, with a warning
    that named the wrong folder.
    """
    import shutil
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    src = Path(__file__).resolve().parent / "fixtures" / "sample_25fps_1080p.mp4"
    big, small = tmp_path / "big", tmp_path / "small"
    big.mkdir(); small.mkdir()
    for i in range(30):
        shutil.copy(src, big / f"b{i}.mp4")
    for i in range(5):
        shutil.copy(src, small / f"s{i}.mp4")

    from collections import Counter
    index = watch.build_media_index_across([big, small], tmp_path, max_files=20)
    counts = Counter(f["root"] for f in index["files"])
    assert counts["media2"] == 5, "the small folder keeps every file it has"
    assert counts["media"] == 10, "the big one takes its share and no more"
    # And the warning names the folder that actually overflowed.
    assert index["truncatedRoots"] == [str(big)]


def test_unused_share_passes_to_later_folders(tmp_path):
    import shutil
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    src = Path(__file__).resolve().parent / "fixtures" / "sample_25fps_1080p.mp4"
    small, big = tmp_path / "small", tmp_path / "big"
    small.mkdir(); big.mkdir()
    for i in range(5):
        shutil.copy(src, small / f"s{i}.mp4")
    for i in range(30):
        shutil.copy(src, big / f"b{i}.mp4")

    from collections import Counter
    index = watch.build_media_index_across([small, big], tmp_path, max_files=20)
    counts = Counter(f["root"] for f in index["files"])
    assert counts["media"] == 5 and counts["media2"] == 15


def test_shots_from_several_roots_are_merged_not_clobbered(tmp_path):
    """Each root's scan wrote the whole file with only its own shots.

    With two folders, whichever finished last won and half the library's shots
    vanished from the swap list -- silently, because a shorter list looks like a
    smaller library.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    watch.forget_shots()
    jobs = tmp_path
    a, b = tmp_path / "a", tmp_path / "b"
    watch._write_shots(jobs, [{"relPath": "one.mp4", "root": "media"}],
                       complete=True, root=a)
    watch._write_shots(jobs, [{"relPath": "two.mp4", "root": "media2"}],
                       complete=True, root=b)

    written = json.loads((jobs / "library-shots.json").read_text())
    assert {f["relPath"] for f in written["files"]} == {"one.mp4", "two.mp4"}
    assert written["complete"] is True


def test_a_partial_scan_does_not_claim_to_be_complete(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    watch.forget_shots()
    watch._write_shots(tmp_path, [{"relPath": "one.mp4"}], complete=True,
                       root=tmp_path / "a")
    watch._write_shots(tmp_path, [{"relPath": "two.mp4"}], complete=False,
                       root=tmp_path / "b")
    written = json.loads((tmp_path / "library-shots.json").read_text())
    assert written["complete"] is False, "one root still scanning means not complete"


def test_removing_a_folder_takes_its_shots_with_it(tmp_path):
    """Replacing a folder left its clips in the swap list.

    An editor then saw shots from footage that was no longer part of the job,
    which is worse than seeing none.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    watch.forget_shots()
    a, b = tmp_path / "a", tmp_path / "b"
    watch._write_shots(tmp_path, [{"relPath": "old.mp4"}], complete=True, root=a)
    watch._write_shots(tmp_path, [{"relPath": "new.mp4"}], complete=True, root=b)

    watch.forget_shots(keep=[b])
    watch._write_shots(tmp_path, [{"relPath": "new.mp4"}], complete=True, root=b)
    written = json.loads((tmp_path / "library-shots.json").read_text())
    assert {f["relPath"] for f in written["files"]} == {"new.mp4"}


def test_a_reference_reaches_the_engine_as_a_path(tmp_path):
    """The helper resolves it; the engine only ever sees a local file.

    Acquisition may mean a download, which needs somewhere a status can be
    written. request_to_argv is pure and synchronous, so the path arrives
    already resolved.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import request_to_argv

    base = {"jobId": "j", "recipe": "promo-silent", "media": ["a.mp4"]}
    argv = request_to_argv(
        {**base, "options": {"reference": {"source": "file", "value": "r.mp4"}}},
        tmp_path, Path("/A"), tmp_path, None, [Path("/A")], Path("/refs/r.mp4"))
    assert argv[argv.index("--reference") + 1] == "/refs/r.mp4"
    assert "--reference-rhythm-only" not in argv

    rhythm = request_to_argv(
        {**base, "options": {"reference": {"source": "file", "value": "r.mp4",
                                           "matchContent": False}}},
        tmp_path, Path("/A"), tmp_path, None, [Path("/A")], Path("/refs/r.mp4"))
    assert "--reference-rhythm-only" in rhythm

    # Not asked for, not passed. A default that travels stops meaning "unchanged".
    assert "--reference" not in request_to_argv(
        {**base, "options": {}}, tmp_path, Path("/A"), tmp_path, None, [Path("/A")])


def test_a_reference_file_is_found_in_the_reference_folder(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_reference

    jobs, refs = tmp_path / "jobs", tmp_path / "refs"
    jobs.mkdir(); refs.mkdir()
    (refs / "look.mp4").write_bytes(b"")
    (jobs / "config.json").write_text(json.dumps({"referenceRoot": str(refs)}))

    got = resolve_reference({"source": "file", "value": "look.mp4"}, jobs, tmp_path, [])
    assert got == refs / "look.mp4"


def test_a_missing_reference_says_what_to_do(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import resolve_reference

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    with pytest.raises(ValueError, match="reference folder"):
        resolve_reference({"source": "file", "value": "gone.mp4"}, jobs, tmp_path, [])


def test_a_downloaded_reference_is_cached_by_its_url(tmp_path):
    """Keyed on the URL and a version tag: there is no mtime or content hash to
    key on before the file exists."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    from watch import reference_cache_path

    a = reference_cache_path("https://x/y", tmp_path)
    b = reference_cache_path("  https://x/y  ", tmp_path)
    c = reference_cache_path("https://x/z", tmp_path)
    assert a == b, "surrounding whitespace is not a different video"
    assert a != c
    assert a.parent.name == "reference"


def test_a_bare_url_is_treated_as_one_even_when_labelled_a_file(tmp_path):
    # The panel labels it, but a link pasted into the file field is still a link
    # and joining it to a folder path would produce nonsense.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helper"))
    import watch

    calls = []
    original = watch.download_reference
    watch.download_reference = (lambda url, work, on_progress=None:
                                calls.append(url) or Path("/tmp/x.mp4"))
    try:
        watch.resolve_reference({"source": "file", "value": "https://x/y"},
                                tmp_path, tmp_path, [])
    finally:
        watch.download_reference = original
    assert calls == ["https://x/y"]


def test_a_caption_file_is_never_mistaken_for_the_reference_video(tmp_path):
    """The caption tracks share the video's stem -- `<key>.mp4` and
    `<key>.ja.vtt` -- and `.en.vtt` sorts BEFORE `.mp4`. Taking the first match
    of `<key>.*` handed back a subtitle file and called it the reference."""
    from watch import _downloaded_video

    stem = tmp_path / "abc123"
    for name in ("abc123.en.vtt", "abc123.ja.vtt", "abc123.mp4"):
        (tmp_path / name).write_text("x")
    assert _downloaded_video(stem).name == "abc123.mp4"


def test_captions_alone_do_not_count_as_a_downloaded_reference(tmp_path):
    """Or a re-run would skip the download and hand the engine a .vtt."""
    from watch import _downloaded_video

    stem = tmp_path / "abc123"
    (tmp_path / "abc123.ja.vtt").write_text("x")
    assert _downloaded_video(stem) is None
    assert _downloaded_video(tmp_path / "nothing" / "abc123") is None


# --- progress reaches the panel while the job is still running ---------------

def _run_with_fake_engine(tmp_path, monkeypatch, emit):
    """Drive `process` with a stand-in engine. Returns (status seen mid-run, final).

    `emit` is called with a recorder; whatever it prints to stderr goes through
    the same path the real engine's output does.
    """
    import watch

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "EP001.request.json").write_text(json.dumps(request()))
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / name).write_bytes(b"")

    seen = []

    def fake_engine(argv):
        def look():
            # Read the status file from INSIDE the run. This is the whole point:
            # before this change the engine's output was buffered and nothing
            # was written until it returned, so there was nothing to read here.
            try:
                seen.append(json.loads((jobs / "EP001.status.json").read_text()))
            except (OSError, ValueError):
                pass
        emit(look)
        (jobs / "EP001.editplan.json").write_text("{}")
        return 0

    monkeypatch.setattr(watch, "engine_main", fake_engine)
    process(jobs / "EP001.request.json", jobs, tmp_path, tmp_path)
    return seen, json.loads((jobs / "EP001.status.json").read_text())


def test_the_panel_can_see_the_bar_move_before_the_job_finishes(tmp_path, monkeypatch):
    def emit(look):
        print("PROGRESS 0.2500 progress.scanShots C0001.MP4", file=sys.stderr)
        look()
        print("PROGRESS 0.7500 progress.thumbs C0001.MP4", file=sys.stderr)
        look()

    seen, _ = _run_with_fake_engine(tmp_path, monkeypatch, emit)
    assert [s["percent"] for s in seen] == [25, 75]
    assert [s["step"] for s in seen] == ["progress.scanShots", "progress.thumbs"]
    assert seen[0]["stepDetail"] == "C0001.MP4"


def test_a_finished_job_still_reports_what_it_made_not_a_progress_line(tmp_path, monkeypatch):
    """The summary is the last thing an editor reads. Left unfiltered, the last
    progress line would be it -- "PROGRESS 0.9990 progress.writing" where the
    clip count used to be."""
    def emit(look):
        print("  12 clips, 00:00:28:00 (28.0s)", file=sys.stderr)
        print("PROGRESS 0.9990 progress.writing", file=sys.stderr)

    _, final = _run_with_fake_engine(tmp_path, monkeypatch, emit)
    assert final["state"] == "ready"
    assert final["message"] == "12 clips, 00:00:28:00 (28.0s)"
    assert final["percent"] == 100


def test_a_failing_job_reports_the_engines_words_not_its_progress(tmp_path, monkeypatch):
    import watch

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "EP001.request.json").write_text(json.dumps(request()))

    def fake_engine(argv):
        print("PROGRESS 0.1000 progress.scanShots C0001.MP4", file=sys.stderr)
        print("error: ref.mp4: no cuts were found in it", file=sys.stderr)
        return 2

    monkeypatch.setattr(watch, "engine_main", fake_engine)
    process(jobs / "EP001.request.json", jobs, tmp_path, tmp_path)

    final = json.loads((jobs / "EP001.status.json").read_text())
    assert final["state"] == "failed"
    assert final["message"] == "error: ref.mp4: no cuts were found in it"


def test_the_elapsed_clock_survives_every_update(tmp_path, monkeypatch):
    """startedAt is set once, at the start, and every later write has to carry
    it forward -- the panel's clock is `now - startedAt`, so dropping it would
    reset the elapsed time to nothing on the first progress line."""
    def emit(look):
        print("PROGRESS 0.5000 progress.thumbs C0001.MP4", file=sys.stderr)
        look()

    seen, final = _run_with_fake_engine(tmp_path, monkeypatch, emit)
    assert seen[0].get("startedAt")
    assert final.get("startedAt") == seen[0]["startedAt"]
