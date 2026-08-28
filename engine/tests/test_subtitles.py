"""Subtitles for the edit that was made, not for the rushes.

The remapping is the whole feature: a transcript is in source time, the edit
cuts and reorders, and a file written straight from the transcript describes a
video nobody will watch.
"""

import pytest

from autoedit.subtitles import (
    GAP_BREAK, MAX_CHARS_CJK, MAX_CHARS_LATIN, build_cues, group_cues, is_cjk,
    to_srt, _placed_words, _timecode,
)

TB = {"fpsNum": 25, "fpsDen": 1, "dropFrame": False}


def words(*specs, speaker=None):
    return [{"text": t, "start": s, "end": e, "confidence": 1.0,
             **({"speaker": speaker} if speaker else {})}
            for t, s, e in specs]


def plan(timeline, transcripts, timebase=None):
    return {"timebase": timebase or TB, "timeline": timeline,
            "transcripts": transcripts}


def clip(media, in_s, out_s, at_frame, frames):
    return {"mediaId": media, "inSeconds": in_s, "outSeconds": out_s,
            "atFrame": at_frame, "durationFrames": frames,
            "videoTrack": 0, "audioTrack": 0, "linkedAudio": True}


# --------------------------------------------------------------- remapping

def test_words_are_carried_to_where_the_edit_put_them():
    """A clip taken from 10s in and laid at 0s moves its words back by 10s.

    This is the entire point of the module. Written from the transcript
    directly, every one of these would be ten seconds late.
    """
    p = plan(
        [clip("A", 10.0, 12.0, 0, 50)],
        [{"mediaId": "A", "language": "en",
          "words": words(("hello", 10.2, 10.6), ("there", 10.7, 11.2))}],
    )
    placed = [w for _, w in _placed_words(p)]
    assert [w.text for w in placed] == ["hello", "there"]
    assert placed[0].start == pytest.approx(0.2) and placed[0].end == pytest.approx(0.6)
    assert placed[1].start == pytest.approx(0.7) and placed[1].end == pytest.approx(1.2)


def test_words_in_dropped_material_are_dropped():
    # The edit kept 10-12s. Nothing said outside it is in the film, so nothing
    # said outside it is in the subtitles.
    p = plan(
        [clip("A", 10.0, 12.0, 0, 50)],
        [{"mediaId": "A", "language": "en",
          "words": words(("before", 5.0, 5.5), ("kept", 10.5, 11.0),
                         ("after", 30.0, 30.5))}],
    )
    assert [w.text for _, w in _placed_words(p)] == ["kept"]


def test_reordered_clips_produce_subtitles_in_screen_order():
    # A story edit puts the later half first. The subtitles follow the picture,
    # not the recording.
    p = plan(
        [clip("A", 20.0, 21.0, 0, 25), clip("A", 5.0, 6.0, 25, 25)],
        [{"mediaId": "A", "language": "en",
          "words": words(("second", 5.2, 5.8), ("first", 20.2, 20.8))}],
    )
    assert [w.text for _, w in _placed_words(p)] == ["first", "second"]


def test_music_and_undescribed_clips_are_ignored():
    # A music bed has no transcript and must not crash the lookup.
    p = plan(
        [clip("A", 0.0, 1.0, 0, 25), clip("MUSIC", 0.0, 60.0, 0, 1500)],
        [{"mediaId": "A", "language": "en", "words": words(("one", 0.1, 0.5))}],
    )
    assert [w.text for _, w in _placed_words(p)] == ["one"]


def test_a_word_straddling_a_cut_belongs_to_one_side_only():
    """Midpoint, the rule the cutting already uses.

    Overlap instead would put the same word in both clips, so it would be
    subtitled twice a fraction of a second apart.
    """
    p = plan(
        [clip("A", 0.0, 1.0, 0, 25), clip("A", 1.0, 2.0, 25, 25)],
        [{"mediaId": "A", "language": "en", "words": words(("split", 0.9, 1.3))}],
    )
    placed = _placed_words(p)
    assert len(placed) == 1, [w.text for _, w in placed]


# ------------------------------------------------------------------- cues

def test_a_cue_never_crosses_a_cut():
    """Subtitles running over an edit point read as the wrong speaker.

    The words here are continuous in time and would group into one cue on
    every other rule; only the cut separates them.
    """
    p = plan(
        [clip("A", 0.0, 1.0, 0, 25), clip("B", 0.0, 1.0, 25, 25)],
        [{"mediaId": "A", "language": "en", "words": words(("one", 0.1, 0.4))},
         {"mediaId": "B", "language": "en", "words": words(("two", 0.1, 0.4))}],
    )
    cues = build_cues(p)
    assert len(cues) == 2, [c.text for c in cues]
    assert [c.text for c in cues] == ["one", "two"]


def test_a_pause_starts_a_new_cue():
    p = plan(
        [clip("A", 0.0, 20.0, 0, 500)],
        [{"mediaId": "A", "language": "en",
          "words": words(("one", 0.1, 0.4),
                         ("two", 0.4 + GAP_BREAK + 0.3, 1.6 + GAP_BREAK))}],
    )
    assert len(build_cues(p)) == 2


def test_a_speaker_change_starts_a_new_cue():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en",
          "words": (words(("hello", 0.1, 0.4), speaker="S1")
                    + words(("goodbye", 0.5, 0.9), speaker="S2"))}],
    )
    cues = build_cues(p)
    assert [c.speaker for c in cues] == ["S1", "S2"]


def test_a_long_run_is_broken_before_it_overflows_two_lines():
    spoken = [(f"word{i}", i * 0.3, i * 0.3 + 0.25) for i in range(60)]
    p = plan(
        [clip("A", 0.0, 30.0, 0, 750)],
        [{"mediaId": "A", "language": "en", "words": words(*spoken)}],
    )
    cues = build_cues(p)
    assert len(cues) > 1
    for c in cues:
        assert len(c.lines) <= 2, c.lines
        assert all(len(line) <= MAX_CHARS_LATIN for line in c.lines), c.lines


def test_cues_are_frame_snapped_and_never_inverted():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en",
          "words": words(("tick", 0.137, 0.291), ("tock", 2.004, 2.337))}],
    )
    for c in build_cues(p):
        assert abs(c.start * 25 - round(c.start * 25)) < 1e-6, c.start
        assert abs(c.end * 25 - round(c.end * 25)) < 1e-6, c.end
        assert c.end > c.start


def test_a_short_cue_is_held_long_enough_to_read():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en", "words": words(("hi", 0.0, 0.1))}],
    )
    assert build_cues(p)[0].duration >= 0.9


def test_padding_never_runs_a_cue_into_the_next_one():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en",
          "words": words(("a", 0.0, 0.1),
                         ("b", 0.1 + GAP_BREAK + 0.1, 0.4 + GAP_BREAK))}],
    )
    cues = build_cues(p)
    assert len(cues) == 2
    assert cues[0].end <= cues[1].start


def test_padding_never_runs_past_the_end_of_the_sequence():
    p = plan(
        [clip("A", 0.0, 1.0, 0, 25)],
        [{"mediaId": "A", "language": "en", "words": words(("hi", 0.8, 0.9))}],
    )
    assert build_cues(p)[0].end <= 1.0 + 1e-9


# --------------------------------------------------------------- Japanese

def test_japanese_is_joined_without_spaces():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "ja",
          "words": words(("店", 0.1, 0.3), ("の", 0.3, 0.4), ("外観", 0.4, 0.8))}],
    )
    assert build_cues(p)[0].text == "店の外観"


def test_japanese_lines_are_shorter_than_latin_ones():
    long_ja = [(c, i * 0.3, i * 0.3 + 0.25)
               for i, c in enumerate("あいうえおかきくけこさしすせそたちつてとなにぬねの")]
    p = plan(
        [clip("A", 0.0, 30.0, 0, 750)],
        [{"mediaId": "A", "language": "ja", "words": words(*long_ja)}],
    )
    for c in build_cues(p):
        assert all(len(line) <= MAX_CHARS_CJK for line in c.lines), c.lines


def test_is_cjk_distinguishes_the_scripts():
    assert is_cjk("サッカーをしている") and is_cjk("店の外観")
    assert not is_cjk("children playing football")


# ------------------------------------------------------------------- SRT

def test_timecodes_are_srt_shaped():
    assert _timecode(0.0) == "00:00:00,000"
    assert _timecode(3661.5) == "01:01:01,500"
    assert _timecode(-1.0) == "00:00:00,000"


def test_srt_is_numbered_from_one_and_blank_line_separated():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en",
          "words": words(("one", 0.1, 0.4),
                         ("two", 0.4 + GAP_BREAK + 0.3, 2.0 + GAP_BREAK))}],
    )
    text = to_srt(build_cues(p))
    assert text.startswith("1\n")
    assert "\n\n2\n" in text
    assert " --> " in text


def test_no_speech_produces_no_file_worth_writing():
    assert build_cues(plan([clip("A", 0.0, 1.0, 0, 25)], [])) == []
    assert to_srt([]) == ""


def test_a_sentence_ending_closes_the_cue():
    # Two sentences in one cue reads as a wall; every subtitle house breaks here.
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "en",
          "words": words(("trust.", 0.1, 0.5), ("Nothing", 0.6, 1.0))}],
    )
    assert [c.text for c in build_cues(p)] == ["trust.", "Nothing"]


def test_a_japanese_full_stop_closes_the_cue():
    p = plan(
        [clip("A", 0.0, 10.0, 0, 250)],
        [{"mediaId": "A", "language": "ja",
          "words": words(("信頼です。", 0.1, 0.5), ("他は", 0.6, 1.0))}],
    )
    assert [c.text for c in build_cues(p)] == ["信頼です。", "他は"]


def test_a_clean_up_cut_does_not_break_a_sentence():
    """Removing an "um" leaves a cut the audience never hears.

    Breaking there turned one sentence into "So" / "the thing" / "that really
    matters is trust." on a real clean-up -- a stutter of one-word cues
    describing continuous speech.
    """
    p = plan(
        [clip("A", 0.0, 0.5, 0, 12), clip("A", 0.9, 2.0, 12, 27)],
        [{"mediaId": "A", "language": "en",
          "words": words(("So", 0.1, 0.4), ("the", 1.0, 1.2), ("thing", 1.3, 1.8))}],
    )
    assert [c.text for c in build_cues(p)] == ["So the thing"]


def test_a_real_jump_in_the_source_still_breaks():
    # Same file, but forty seconds apart: a different sentence entirely.
    p = plan(
        [clip("A", 0.0, 0.5, 0, 12), clip("A", 40.0, 41.0, 12, 25)],
        [{"mediaId": "A", "language": "en",
          "words": words(("here", 0.1, 0.4), ("elsewhere", 40.1, 40.6))}],
    )
    assert [c.text for c in build_cues(p)] == ["here", "elsewhere"]
