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
