"""How far through a run we say we are."""

import pytest

from autoedit import progress
from autoedit.progress import PROGRESS_PREFIX, Run, parse


@pytest.fixture(autouse=True)
def _no_leftover_run():
    """The helper runs jobs in one process, so a run must not outlive itself."""
    progress.finish()
    yield
    progress.finish()


def emitted(capsys) -> list[tuple[float, str, str]]:
    return [p for p in (parse(l) for l in capsys.readouterr().err.splitlines()) if p]


# --- the line format --------------------------------------------------------

def test_a_progress_line_survives_the_round_trip():
    assert parse(f"{PROGRESS_PREFIX} 0.4231 progress.scanShots C0001.MP4") == (
        0.4231, "progress.scanShots", "C0001.MP4")


def test_a_step_with_no_detail_is_still_a_step():
    assert parse(f"{PROGRESS_PREFIX} 0.5000 progress.describe") == (
        0.5, "progress.describe", "")


def test_the_engines_own_prose_is_not_mistaken_for_progress():
    # The helper builds the failure message and the "ready" summary out of these
    # lines. A progress line getting in there would replace "12 clips, 00:00:28"
    # with machine noise at the moment an editor is reading for a reason.
    for line in ["  reference: reading ref.mp4",
                 "error: could not read the reference video",
                 "  PROGRESS is coming",
                 f"{PROGRESS_PREFIX} soon progress.x",
                 f"{PROGRESS_PREFIX} 1.5 progress.x",
                 ""]:
        assert parse(line) is None, line


# --- the weighting ----------------------------------------------------------

def test_position_is_weighted_by_size_not_by_count(capsys):
    # A 4-second cutaway beside a 28-minute reference. Counting units would put
    # the bar past halfway once the cutaway was done.
    run = Run([("ref", 1680.0), ("clip", 4.0)])
    run.unit("clip")
    run.step("progress.clip")
    fraction, _, _ = emitted(capsys)[-1]
    assert fraction > 0.99


def test_a_unit_fills_as_it_goes(capsys):
    run = Run([("a", 10.0), ("b", 10.0)])
    run.unit("b")
    run.step("progress.scanShots", within=0.0)
    run.step("progress.scanFrames", within=0.5)
    assert [f for f, _, _ in emitted(capsys)] == [0.5, 0.75]


def test_skipped_units_do_not_misattribute_the_ones_after_them(capsys):
    # A run skips units routinely: no reference, a silent file, subtitles off.
    # Stepping one at a time would report every later unit in the wrong place.
    run = Run([("ref", 10.0), ("media:0", 10.0), ("assemble", 20.0)])
    run.unit("assemble")
    run.step("progress.writing")
    assert emitted(capsys)[-1][0] == 0.5


def test_an_unknown_unit_does_not_stop_the_run(capsys):
    run = Run([("a", 10.0)])
    run.unit("nonesuch")
    run.step("progress.writing")
    assert emitted(capsys)[-1][0] == 0.0


def test_a_run_of_stills_does_not_divide_by_zero(capsys):
    run = Run([("a", 0.0), ("b", 0.0)])
    run.unit("b")
    run.step("progress.writing", within=1.0)
    assert emitted(capsys)          # reported something rather than raising


# --- what the bar is never allowed to do ------------------------------------

def test_the_bar_never_goes_backwards(capsys):
    # A bar that retreats reads as a fault even when the work is fine.
    run = Run([("a", 10.0), ("b", 10.0)])
    run.unit("b")
    run.step("progress.scanFrames", within=0.8)
    run.step("progress.thumbs", within=0.1)
    fractions = [f for f, _, _ in emitted(capsys)]
    assert fractions == sorted(fractions)


def test_the_bar_never_reaches_the_end_before_the_job_does(capsys):
    # 100% is the helper's word, written when the plan exists. The engine saying
    # it while still working is the "finished bar, unfinished job" complaint.
    run = Run([("a", 10.0)])
    run.unit("a")
    run.step("progress.writing", within=1.0)
    assert emitted(capsys)[-1][0] < 1.0


def test_within_outside_the_range_is_clamped(capsys):
    run = Run([("a", 10.0), ("b", 10.0)])
    run.unit("a")
    run.step("progress.scanShots", within=5.0)
    assert emitted(capsys)[-1][0] == 0.5


# --- the module-level run ---------------------------------------------------

def test_nothing_is_reported_when_no_run_is_in_progress(capsys):
    # measure() is also called by the library indexer and by the tests, neither
    # of which has a run to report against.
    progress.step("progress.scanShots", "x.mp4")
    assert capsys.readouterr().err == ""


def test_a_finished_run_does_not_become_the_next_jobs_starting_point(capsys):
    # The helper calls engine_main IN-PROCESS and reuses the interpreter.
    progress.begin([("a", 10.0)])
    progress.unit("a")
    progress.step("progress.scanShots", within=1.0)
    progress.finish()
    capsys.readouterr()
    progress.step("progress.scanShots")
    assert capsys.readouterr().err == ""
