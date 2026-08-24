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
