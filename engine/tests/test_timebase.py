import pytest

from autoedit.timebase import Timebase


@pytest.mark.parametrize(
    "fps,expected",
    [
        (23.976023976023978, (24000, 1001)),
        (29.97002997002997, (30000, 1001)),
        (59.94005994005994, (60000, 1001)),
        (25.0, (25, 1)),
        (30.0, (30, 1)),
    ],
)
def test_recovers_exact_rational_from_ffprobe_float(fps, expected):
    tb = Timebase.from_float(fps)
    assert (tb.fps_num, tb.fps_den) == expected


def test_parse_accepts_both_notations():
    assert Timebase.parse("30000/1001").fps == pytest.approx(29.97, abs=1e-3)
    assert Timebase.parse("23.976").fps_num == 24000
    assert Timebase.parse("25").fps_num == 25


def test_frame_roundtrip_is_stable_over_a_long_timeline():
    """The failure this guards: float drift landing clips a frame off deep in a
    long assembly, producing scattered one-frame gaps."""
    tb = Timebase(24000, 1001)
    for frame in range(0, 200_000, 997):
        assert tb.to_frames(tb.to_seconds(frame)) == frame


def test_to_frames_rounds_rather_than_truncates():
    tb = Timebase(25)
    assert tb.to_frames(0.999 * 0.04) == 1     # would truncate to 0
    assert tb.to_frames(0.0) == 0


def test_dropframe_timecode_matches_smpte():
    df = Timebase(30000, 1001, drop_frame=True)
    assert df.timecode(0) == "00:00:00;00"
    assert df.timecode(107892) == "01:00:00;00"      # exactly one hour of 29.97 DF
    assert df.timecode(1800) == "00:01:00;02"        # the two dropped frame numbers


def test_non_dropframe_timecode_uses_colon():
    assert Timebase(25).timecode(1501) == "00:01:00:01"


def test_rejects_nonsense_rates():
    with pytest.raises(ValueError):
        Timebase(0, 1)
    with pytest.raises(ValueError):
        Timebase(25, 0)


# --- choosing the sequence rate from the footage ---------------------------
#
# The bug these guard: a social-short recipe pins the sequence to 30.000 while
# the camera shot 59.94. Nothing reconciled the two, so every clip's duration
# converted to a fractional number of sequence frames, the plan rounded one way,
# Premiere rounded the other, and the assembly came back with one-frame gaps --
# black flashes between shots -- scattered through it.

from autoedit.timebase import choose_timebase, holds_exactly

NTSC60 = Timebase(60000, 1001)
NTSC30 = Timebase(30000, 1001)
FLAT30 = Timebase(30)
PAL = Timebase(25)


def test_a_rate_holds_its_own_footage():
    assert holds_exactly(NTSC60, NTSC60)


def test_a_faster_rate_holds_a_slower_one_at_a_whole_ratio():
    assert holds_exactly(NTSC60, NTSC30)


def test_a_slower_rate_does_not_hold_a_faster_one():
    # 1 source frame at 59.94 is half a frame at 29.97, and half frames do not
    # exist -- so an odd-length clip has to be rounded by somebody.
    assert not holds_exactly(NTSC30, NTSC60)


def test_flat_30_does_not_hold_ntsc_footage():
    # The shipped defect, stated as a fact: 30.000 and 59.94 share almost no
    # frame boundaries at all (the ratio is 1001/2000).
    assert not holds_exactly(FLAT30, NTSC60)
    assert not holds_exactly(FLAT30, NTSC30)


def test_the_recipe_rate_survives_when_the_footage_fits_it():
    chosen, displaced = choose_timebase(PAL, [PAL, PAL])
    assert chosen == PAL
    assert displaced is None


def test_no_footage_leaves_the_recipe_alone():
    chosen, displaced = choose_timebase(PAL, [])
    assert (chosen, displaced) == (PAL, None)


def test_the_footage_displaces_a_rate_it_cannot_land_on():
    chosen, displaced = choose_timebase(FLAT30, [NTSC60] * 5 + [NTSC30])
    assert chosen == NTSC60
    assert displaced == FLAT30


def test_the_faster_rate_wins_a_tie_so_no_frames_are_thrown_away():
    # Both 59.94 and 29.97 could hold this pair; 29.97 would discard every other
    # frame of the 59.94 clip.
    chosen, _ = choose_timebase(FLAT30, [NTSC60, NTSC30])
    assert chosen == NTSC60


def test_audio_only_sources_do_not_get_a_vote():
    chosen, displaced = choose_timebase(PAL, [PAL, None])
    assert (chosen, displaced) == (PAL, None)


def test_genuinely_mixed_footage_keeps_the_recipe_rate():
    # 25 and 29.97 cannot both be exact in any one sequence. Nothing is gained by
    # abandoning the delivery spec as well, so the recipe holds and the caller
    # warns about the clips that do not fit.
    chosen, displaced = choose_timebase(PAL, [PAL, NTSC30])
    assert chosen == PAL
    assert displaced is None
    assert not holds_exactly(chosen, NTSC30)


def test_a_sequence_is_never_faster_than_premiere_can_build():
    """120fps footage produced a 119.880 sequence and no timeline at all.

    Every one of Adobe's 392 shipped presets tops out at 60. A generated preset
    above that is declined, so `createSequenceWithPresetPath` fails, so the
    build stops before it starts -- reported by a colleague as "it wasn't going
    to the timeline", which is exactly what it looks like from outside.
    """
    from autoedit.timebase import MAX_SEQUENCE_FPS, Timebase, choose_timebase

    fast = Timebase(120000, 1001)
    # The reported job: nine clips at 119.88, three at 59.94, recipe asking 25.
    got, _ = choose_timebase(Timebase(25, 1), [fast] * 9 + [Timebase(60000, 1001)] * 3)
    assert got.fps <= MAX_SEQUENCE_FPS
    assert got == Timebase(60000, 1001)


def test_the_cap_survives_a_recipe_that_matches_the_fast_footage():
    """The shortcut for "everything already fits" must not skip the cap.

    A 120fps recipe on 120fps footage holds every frame exactly, which is true
    and useless if the sequence cannot be created.
    """
    from autoedit.timebase import MAX_SEQUENCE_FPS, Timebase, choose_timebase

    fast = Timebase(120000, 1001)
    got, _ = choose_timebase(fast, [fast] * 4)
    assert got.fps <= MAX_SEQUENCE_FPS
    # Halved, so the relationship to the source frames stays exact.
    assert got == Timebase(120000, 2002)


def test_capping_leaves_ordinary_jobs_alone():
    from autoedit.timebase import Timebase, choose_timebase

    for rate in (Timebase(25, 1), Timebase(30000, 1001), Timebase(60000, 1001)):
        got, displaced = choose_timebase(rate, [rate] * 3)
        assert got == rate and displaced is None
