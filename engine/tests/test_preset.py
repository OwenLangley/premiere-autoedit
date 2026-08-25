"""Generated sequence presets.

Premiere's own Social presets are 30fps only, so these are authored. Correctness
is checked against Adobe's shipped values -- if the tick maths is wrong the
sequence runs at the wrong speed and every clip lands on the wrong frame.
"""

import xml.etree.ElementTree as ET

import pytest

from autoedit.preset import (
    TICKS_PER_SECOND,
    preset_xml,
    ticks_per_frame,
    time_display_code,
    write_preset,
)
from autoedit.timebase import Timebase

# Read directly off Adobe's shipped .sqpreset files.
ADOBE_TICKS = {
    "23.976": 10594584000,
    "24": 10584000000,
    "25": 10160640000,
    "29.97": 8475667200,
    "30": 8467200000,
    "50": 5080320000,
    "59.94": 4237833600,
    "60": 4233600000,
}


@pytest.mark.parametrize("rate,expected", ADOBE_TICKS.items())
def test_tick_maths_matches_adobes_own_presets(rate, expected):
    assert ticks_per_frame(Timebase.parse(rate)) == expected


@pytest.mark.parametrize("rate", ADOBE_TICKS)
def test_ticks_are_exact_integers(rate):
    """A fractional tick count would mean an inexact frame rate."""
    tb = Timebase.parse(rate)
    assert TICKS_PER_SECOND * tb.fps_den % tb.fps_num == 0


def test_generated_preset_is_well_formed_xml():
    root = ET.fromstring(preset_xml("Test", 1080, 1920, Timebase(25)))
    assert root.tag == "PremiereData"
    assert root.find('SequencePreset[@ObjectID="1"]') is not None


@pytest.mark.parametrize("w,h", [(1920, 1080), (1080, 1920), (1080, 1080), (1080, 1350)])
def test_frame_size_round_trips(w, h):
    root = ET.fromstring(preset_xml("T", w, h, Timebase(25)))
    sp = root.find('SequencePreset[@ObjectID="1"]')
    assert sp.find("VideoFrameSize").text == f"0,0,{w},{h}"
    assert sp.find("PreviewVideoFrameSize").text == f"0,0,{w},{h}"


def test_track_counts_are_honoured():
    root = ET.fromstring(preset_xml("T", 1080, 1920, Timebase(25), video_tracks=4, audio_tracks=3))
    sp = root.find('SequencePreset[@ObjectID="1"]')
    assert sp.find("InitialNumberOfVideoTracks").text == "4"
    assert sp.find("AudioTracks").text.count('"mTrackID"') == 3


def test_name_is_xml_escaped():
    """A recipe or job name with an ampersand would otherwise produce broken XML."""
    xml = preset_xml("Ben & Jerry's <promo>", 1080, 1920, Timebase(25))
    ET.fromstring(xml)
    assert "&amp;" in xml and "<promo>" not in xml


def test_time_display_is_known_for_every_common_rate():
    for rate in ADOBE_TICKS:
        assert time_display_code(Timebase.parse(rate)) > 0


def test_unknown_rate_still_produces_a_usable_preset():
    """Time display is cosmetic, so an odd rate must not fail the job."""
    root = ET.fromstring(preset_xml("Odd", 1920, 1080, Timebase(37, 1)))
    assert root.find('SequencePreset[@ObjectID="1"]/VideoFrameRate') is not None


def test_rejects_a_nonsense_frame_size():
    with pytest.raises(ValueError):
        preset_xml("T", 0, 1080, Timebase(25))


def test_write_preset_creates_missing_directories(tmp_path):
    out = write_preset(tmp_path / "nested" / "a.sqpreset", "T", 1080, 1920, Timebase(25))
    assert out.exists()
    ET.parse(out)
