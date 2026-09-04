"""Reading a reference's captions, and learning what its ends sound like.

The numbers that set this design were measured on a real 27-minute Japanese
YouTube reference; they are recorded in `cues.py` and `reference.py`. What is
tested here is the behaviour they are supposed to produce.
"""

import pytest

from autoedit.cues import CueStore, phrases
from autoedit.transcribe import parse_vtt


# --------------------------------------------------------------- the parser

# A verbatim slice of yt-dlp's automatic captions, roll-up and all.
YOUTUBE_VTT = (
    "WEBVTT\n"
    "Kind: captions\n"
    "Language: ja\n"
    "\n"
    "00:00:07.205 --> 00:00:08.509 align:start position:0%\n"
    " \n"
    "[音楽]\n"
    "\n"
    "00:00:08.509 --> 00:00:08.519 align:start position:0%\n"
    "[音楽]\n"
    " \n"
    "\n"
    "00:00:08.519 --> 00:00:08.990 align:start position:0%\n"
    "[音楽]\n"
    "すごいすね。\n"
    "\n"
    "00:00:08.990 --> 00:00:09.000 align:start position:0%\n"
    "すごいすね。\n"
    " \n"
    "\n"
    "00:00:09.000 --> 00:00:11.910 align:start position:0%\n"
    "すごいすね。\n"
    "あ、いや、すごいっすね。\n"
)


def test_youtube_roll_up_captions_are_not_counted_twice():
    """YouTube's automatic captions repeat the previous line before adding the
    new one. Left alone, the real 27-minute file parsed to 1035 cues covering
    far more than its own runtime; de-duplicated it is 467, covering 74%."""
    got = parse_vtt(YOUTUBE_VTT, "reference", "ja")
    said = [w.text for w in got.words]
    assert said == ["すごいすね。", "あ、いや、すごいっすね。"], said


def test_caption_markers_are_not_speech():
    """`[音楽]` arrives both as a whole line and glued to real speech. Missing
    the second kind also breaks the de-duplication, because the next cue then
    no longer starts with what this one said."""
    got = parse_vtt(YOUTUBE_VTT, "reference", "ja")
    assert not any("音楽" in w.text for w in got.words)


def test_cue_times_survive():
    got = parse_vtt(YOUTUBE_VTT, "reference", "ja")
    assert got.words[0].start == pytest.approx(8.519)
    assert got.words[-1].end == pytest.approx(11.910)
    assert got.language == "ja"


def test_srt_commas_and_hourless_stamps_parse():
    srt = ("1\n00:00:01,500 --> 00:00:03,000\nhello there\n\n"
           "2\n00:00:04,000 --> 00:00:05,250\nsecond line\n")
    got = parse_vtt(srt, "reference")
    assert [w.text for w in got.words] == ["hello there", "second line"]
    assert got.words[0].start == pytest.approx(1.5)


def test_a_caption_file_with_nothing_in_it_is_empty_not_an_error():
    assert parse_vtt("WEBVTT\n\n", "reference").words == []
    assert parse_vtt("", "reference").words == []


# -------------------------------------------------------------- the learning


def test_one_reference_teaches_nothing():
    """Otherwise the store memorises a single video's subject matter. On the
    real reference the most 'distinctive' opening phrase was ポップアップイベント,
    which is simply what that video was about."""
    store = CueStore()
    store.observe("ja", head="みなさんこんにちは今日は", tail="チャンネル登録お願いします",
                  middle="", reference_id="one")
    assert store.learned("ja", "head") == set()
    assert store.role_of("みなさんこんにちは今日は", "ja") is None


def test_a_phrase_at_the_same_end_of_two_references_is_learned():
    store = CueStore()
    for i, ref in enumerate(("one", "two")):
        store.observe("ja", head="みなさんこんにちは", tail="チャンネル登録お願いします",
                      middle="", reference_id=ref)
    assert store.role_of("みなさんこんにちは", "ja") == "opening"
    assert store.role_of("チャンネル登録お願いします", "ja") == "ending"


def test_a_phrase_that_also_appears_mid_video_is_not_a_cue():
    """The measured false positive. On the real reference `こんにちは` appears
    three times, all at eighteen minutes, greeting customers -- read as an
    opening cue it would have moved the opening into the middle of the film."""
    store = CueStore()
    for ref in ("one", "two"):
        store.observe("ja", head="みなさんこんにちは", tail="またね",
                      middle="こんにちはいらっしゃいませ", reference_id=ref)
    learned = store.learned("ja", "head")
    assert "こんにち" not in learned, learned
    assert "んにちは" not in learned, learned
    # What is left is the part that only ever appears at the opening.
    assert "みなさん" in learned
    assert store.role_of("こんにちはいらっしゃいませ", "ja") is None


def test_the_same_reference_twice_is_still_one_reference():
    """Analysis is cached; this is not. A re-run must not promote a phrase by
    counting one video as two."""
    store = CueStore()
    for _ in range(5):
        store.observe("ja", head="みなさんこんにちは", tail="またね",
                      middle="", reference_id="same")
    assert store.learned("ja", "head") == set()


def test_languages_do_not_share_a_lexicon():
    store = CueStore()
    for ref in ("one", "two"):
        store.observe("en", head="welcome back everyone", tail="thanks for watching",
                      middle="", reference_id=ref)
    assert store.role_of("welcome back everyone", "en") == "opening"
    assert store.role_of("welcome back everyone", "ja") is None


def test_a_regional_tag_still_finds_the_lexicon():
    store = CueStore()
    for ref in ("one", "two"):
        store.observe("ja-JP", head="みなさんこんにちは", tail="またね",
                      middle="", reference_id=ref)
    assert store.role_of("みなさんこんにちは", "ja") == "opening"


def test_the_store_survives_a_round_trip_and_a_damaged_file(tmp_path):
    store = CueStore()
    for ref in ("one", "two"):
        store.observe("ja", head="みなさんこんにちは", tail="またね",
                      middle="", reference_id=ref)
    store.save(tmp_path)
    assert CueStore.load(tmp_path).role_of("みなさんこんにちは", "ja") == "opening"

    (tmp_path / "cues.json").write_text("{ truncated")
    assert CueStore.load(tmp_path).learned("ja", "head") == set()

    assert CueStore.load(tmp_path / "nothing here").learned("ja", "head") == set()


def test_punctuation_does_not_split_a_phrase():
    assert phrases("こんに、ちは") == phrases("こんにちは")
