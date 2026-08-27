import json

import pytest

from autoedit.detect import CutPlan, Keep, DetectionSettings, plan_cuts
from autoedit.notes import note
from autoedit.plan import EditPlanBuilder, MediaEntry, PlanError, validate_plan
from autoedit.timebase import Timebase


def builder(tb=None, **kw) -> EditPlanBuilder:
    b = EditPlanBuilder(
        job_id="job-1", recipe="podcast-2cam",
        timebase=tb or Timebase(25), sequence_name="EP001_rough_v1", **kw,
    )
    b.add_media(MediaEntry(id="A001", rel_path="cam-a/A001.mov", duration=60.0,
                           hash="sha256:deadbeef", role="cam-a", timebase=tb or Timebase(25)))
    return b


def test_built_plan_validates_against_the_schema():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0.0, 2.0, "opening", 0.98, 5),
                                         Keep(4.0, 7.0, "1.5s pause", 0.95, 9)]))
    plan = b.build()
    assert validate_plan(plan) == [], validate_plan(plan)


def test_timeline_is_gapless():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0.0, 2.0), Keep(5.0, 6.5), Keep(9.0, 12.0)]))
    clips = b.build()["timeline"]
    for prev, nxt in zip(clips, clips[1:]):
        assert nxt["atFrame"] == prev["atFrame"] + prev["durationFrames"], (
            "a gap here shows up as black frames between every cut"
        )


def test_no_frame_drift_across_a_long_assembly():
    """The failure mode this exists for: float accumulation landing clip 180 a
    frame off, producing scattered one-frame gaps nobody finds until QC."""
    tb = Timebase(24000, 1001)          # 23.976 -- the least forgiving common rate
    b = builder(tb=tb)
    keeps = [Keep(i * 0.25, i * 0.25 + 0.2) for i in range(200)]
    b.append_cuts("A001", CutPlan(keeps=keeps))
    clips = b.build()["timeline"]
    assert len(clips) == 200
    expected = 0
    for c in clips:
        assert c["atFrame"] == expected
        expected += c["durationFrames"]
    assert b.duration_frames == expected


def test_source_points_snap_to_the_source_frame_grid():
    tb = Timebase(25)
    b = builder(tb=tb)
    b.append_cuts("A001", CutPlan(keeps=[Keep(1.017, 2.033)]))
    clip = b.build()["timeline"][0]
    for value in (clip["inSeconds"], clip["outSeconds"]):
        assert abs(value / 0.04 - round(value / 0.04)) < 1e-6, "in/out must land on a frame"


def test_crossfades_apply_to_internal_joins_only():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2), Keep(4, 6), Keep(8, 10)]),
                  crossfade_seconds=0.08)
    clips = b.build()["timeline"]
    assert clips[0]["fadeInFrames"] == 0
    assert clips[-1]["fadeOutFrames"] == 0
    assert clips[1]["fadeInFrames"] >= 1 and clips[1]["fadeOutFrames"] >= 1


def test_crossfade_is_at_least_one_frame_when_enabled():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2), Keep(4, 6)]), crossfade_seconds=0.001)
    assert b.build()["timeline"][1]["fadeInFrames"] == 1


def test_track_count_grows_to_fit_what_was_placed():
    b = builder(video_tracks=1, audio_tracks=1)
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2)]), video_track=3, audio_track=5)
    seq = b.build()["sequence"]
    assert seq["videoTracks"] >= 4 and seq["audioTracks"] >= 6


def test_rejects_duplicate_media_ids():
    b = builder()
    with pytest.raises(PlanError, match="duplicate"):
        b.add_media(MediaEntry(id="A001", rel_path="other.mov", duration=1.0))


def test_rejects_cuts_against_unknown_media():
    b = builder()
    with pytest.raises(PlanError, match="unknown media"):
        b.append_cuts("NOPE", CutPlan(keeps=[Keep(0, 1)]))


def test_cut_warnings_are_carried_into_the_plan():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2)], warnings=["something looked odd"]))
    assert any(w["message"] == "something looked odd" for w in b.build()["warnings"])


# ------------------------------------------------------------------ validator


def test_validator_catches_out_point_past_end_of_media():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2)]))
    plan = b.build()
    plan["timeline"][0]["outSeconds"] = 999.0
    assert any("exceeds source duration" in e for e in validate_plan(plan))


def test_validator_catches_dangling_media_reference():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2)]))
    plan = b.build()
    plan["timeline"][0]["mediaId"] = "GHOST"
    assert any("unknown media" in e for e in validate_plan(plan))


def test_validator_catches_overlapping_clips():
    """A silent overwrite is the worst outcome -- the editor just sees a lost clip."""
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2), Keep(4, 6)]))
    plan = b.build()
    plan["timeline"][1]["atFrame"] = 10          # now inside clip 0
    assert any("overlaps" in e for e in validate_plan(plan))


def test_validator_rejects_unknown_fields():
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2)]))
    plan = b.build()
    plan["timeline"][0]["surpriseField"] = True
    assert validate_plan(plan), "schema must be closed to typos in the contract"


def test_plan_survives_a_json_roundtrip(tmp_path):
    b = builder()
    b.append_cuts("A001", CutPlan(keeps=[Keep(0, 2), Keep(4, 6)]))
    b.add_marker(0, "Cold open")
    b.add_graphic("lower-third", 25, 100, 1, {"name": "Jane Doe", "title": "CTO"})
    b.add_effect("track:v0", "AE.ADBE Lumetri", lut="brand")
    out = b.write(tmp_path / "plan.json")
    reloaded = json.loads(out.read_text())
    assert validate_plan(reloaded) == []
    assert reloaded["graphics"][0]["fields"]["name"] == "Jane Doe"


def test_end_to_end_from_transcript_to_validated_plan(mktranscript):
    t = mktranscript([("hello", 0.5), ("um", 1.0), ("world", 1.5),
                      ("goodbye", 5.0), ("now", 5.5)])
    cuts = plan_cuts(t, 8.0, DetectionSettings())
    b = builder()
    b.append_cuts("A001", cuts, crossfade_seconds=0.08)
    b.add_transcript(t)
    plan = b.build()
    assert validate_plan(plan) == [], validate_plan(plan)
    assert plan["timeline"], "a normal transcript must produce clips"
    assert plan["transcripts"][0]["mediaId"] == "A001"


def test_out_point_never_exceeds_the_source_duration():
    """Regression: snapping the final clip's out point rounded to the nearest
    frame, which lands past the end of the media. Premiere rejects such a clip,
    so the whole plan failed validation over 2ms."""
    tb = Timebase(25)
    b = EditPlanBuilder(job_id="j", recipe="r", timebase=tb, sequence_name="S")
    b.add_media(MediaEntry(id="A", rel_path="a.mov", duration=13.838, timebase=tb))
    b.append_cuts("A", CutPlan(keeps=[Keep(0.0, 13.838)]))
    plan = b.build()
    assert plan["timeline"][0]["outSeconds"] <= 13.838
    assert validate_plan(plan) == [], validate_plan(plan)


def test_out_point_floor_is_frame_aligned():
    tb = Timebase(25)
    b = EditPlanBuilder(job_id="j", recipe="r", timebase=tb, sequence_name="S")
    b.add_media(MediaEntry(id="A", rel_path="a.mov", duration=10.037, timebase=tb))
    b.append_cuts("A", CutPlan(keeps=[Keep(0.0, 10.037)]))
    out = b.build()["timeline"][0]["outSeconds"]
    assert abs(out / 0.04 - round(out / 0.04)) < 1e-6
    assert out <= 10.037


def test_identical_warnings_collapse():
    """Six copies of one sentence is how a real warning gets scrolled past."""
    b = builder()
    for _ in range(4):
        b.add_warning("cut", note("visual.rateBelowMinimum", take=0.38, minimum=0.4))
    assert len(b.build().get("warnings", [])) == 1


def test_a_job_level_note_is_not_tagged_to_one_file():
    # The cut rate being below the recipe's minimum is true once, not once per
    # source -- and a media id would make six identical sentences six different
    # warnings that no longer collapse.
    from autoedit.plan import JOB_LEVEL_NOTES
    assert "visual.rateBelowMinimum" in JOB_LEVEL_NOTES


def test_per_file_warnings_still_repeat_per_file():
    # Collapsing must not hide something that is genuinely about two files.
    b = builder()
    b.add_warning("probe", note("language.uncertain", file="a.mov",
                                confidence=0.5, language="en"), "A")
    b.add_warning("probe", note("language.uncertain", file="b.mov",
                                confidence=0.5, language="en"), "B")
    assert len(b.build().get("warnings", [])) == 2
