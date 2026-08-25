"""Japanese content.

The token shapes here are not invented -- they are what faster-whisper large-v3
actually returned for Kyoko reading
「えーと、今日はですね、あの、ダンスの撮影をしました…」.

That matters, because the thing this file exists to guard is precisely that
Whisper does NOT tokenise Japanese into words: `えーと` arrives as `えー` + `と`,
`うーん` as `う` + `ーん`. Matching one token at a time cannot find the commonest
hesitation sounds in the language.
"""

import pytest

from autoedit.detect import (
    DetectionSettings, JA_AGGRESSIVE, JA_CONSERVATIVE, _filler_indices, plan_cuts,
)
from autoedit.transcript import Transcript, Word, normalize

# (text, start, end) exactly as measured.
MEASURED = [
    ("えー", 0.00, 0.16), ("と、", 0.16, 0.40), ("今日は", 0.44, 1.02),
    ("ですね、", 1.02, 1.54), ("あの、", 1.86, 2.18), ("ダ", 2.30, 2.54),
    ("ン", 2.54, 2.74), ("ス", 2.74, 2.76), ("の", 2.76, 2.94),
    ("撮", 2.94, 3.28), ("影", 3.28, 3.52), ("を", 3.52, 3.66),
    ("しました。", 3.66, 3.92), ("なんか、", 4.24, 4.72), ("すご", 4.90, 5.30),
    ("く", 5.30, 5.52), ("楽", 5.52, 5.64), ("し", 5.64, 5.94),
    ("かった", 5.94, 6.22), ("です。", 6.22, 6.62), ("う", 6.84, 7.04),
    ("ーん、", 7.04, 7.24), ("そうですね、", 7.34, 7.82), ("まあ、", 8.28, 8.60),
    ("また", 8.72, 9.08), ("行", 9.08, 9.24), ("き", 9.24, 9.40),
    ("たい", 9.40, 9.64), ("と思います。", 9.64, 10.04), ("え", 10.62, 10.80),
    ("っと、", 10.80, 11.02), ("以上", 11.18, 11.54), ("です。", 11.54, 11.80),
]


@pytest.fixture
def words():
    return [Word(t, s, e, 0.95) for t, s, e in MEASURED]


def kept_text(words, found):
    return "".join(w.text for i, w in enumerate(words) if i not in found)


# --- the reason this file exists -------------------------------------------


def test_whisper_splits_japanese_hesitations_across_tokens(words):
    # Guarding the assumption itself. If a future model returns えーと whole, the
    # run matcher still works -- but this test should be updated knowingly.
    texts = [w.text for w in words]
    assert "えーと" not in texts
    assert texts[:2] == ["えー", "と、"]


def test_a_single_token_match_cannot_find_them(words):
    # What the old matcher did: membership per token.
    hits = [w.text for w in words if normalize(w.text) in JA_CONSERVATIVE]
    assert "えー" in hits          # the first half matches on its own...
    assert not any(normalize(w.text) == "えーと" for w in words)   # ...the sound never does


# --- what the run matcher does ---------------------------------------------


def test_conservative_removes_hesitation_sounds_only(words):
    found = _filler_indices(words, DetectionSettings(filler_mode="conservative"), "ja")
    assert set(found.values()) == {"filler 'えーと'", "filler 'うーん'", "filler 'えっと'"}
    # The real words survive: あの is also "that", まあ is also "well".
    kept = kept_text(words, found)
    for word in ("あの", "なんか", "まあ", "ダンス", "撮影"):
        assert word in kept


def test_aggressive_also_removes_words_used_as_filler(words):
    found = _filler_indices(words, DetectionSettings(filler_mode="aggressive"), "ja")
    assert kept_text(words, found) == (
        "今日はですね、ダンスの撮影をしました。すごく楽しかったです。"
        "そうですね、また行きたいと思います。以上です。"
    )


def test_the_longest_run_wins(words):
    # えー is a filler and so is えーと. Taking the short one strands と.
    found = _filler_indices(words, DetectionSettings(filler_mode="conservative"), "ja")
    assert found.get(0) == "filler 'えーと'" and found.get(1) == "filler 'えーと'"


def test_a_run_split_by_a_pause_is_not_one_sound():
    # Two tokens that would join into a filler, but with half a second between
    # them, are two different pieces of speech.
    words = [Word("えー", 0.0, 0.2, 0.9), Word("と", 1.0, 1.2, 0.9)]
    found = _filler_indices(words, DetectionSettings(filler_mode="conservative"), "ja")
    assert 1 not in found      # `と` is a particle, not the tail of a hesitation


def test_yes_is_never_treated_as_filler():
    # うん and ええ are agreement. Cutting them out of an interview is a
    # different kind of mistake from tightening a hesitation.
    assert "うん" not in JA_AGGRESSIVE
    assert "ええ" not in JA_AGGRESSIVE


# --- language selection -----------------------------------------------------


def test_the_lexicon_follows_the_transcript_not_the_recipe():
    settings = DetectionSettings(filler_mode="conservative")
    assert settings.filler_lexicon("ja") == JA_CONSERVATIVE
    assert "um" in settings.filler_lexicon("en")
    assert "um" not in settings.filler_lexicon("ja")


def test_a_regional_tag_still_selects_japanese():
    assert DetectionSettings(filler_mode="conservative").filler_lexicon("ja-JP") == JA_CONSERVATIVE


def test_english_is_unaffected_by_the_shared_matcher():
    words = [
        Word("so", 0.0, 0.2), Word("um", 0.25, 0.4), Word("you", 0.42, 0.55),
        Word("know", 0.55, 0.7), Word("it", 0.75, 0.9), Word("works", 0.9, 1.2),
    ]
    found = _filler_indices(words, DetectionSettings(filler_mode="aggressive"), "en")
    assert kept_text(words, found).strip() == "itworks"      # so / um / you know all go


# --- end to end through the cut planner -------------------------------------


def test_a_japanese_transcript_cuts_without_landing_mid_word(words):
    plan = plan_cuts(
        Transcript("ダンス", words, "ja"), 12.1,
        DetectionSettings(filler_mode="aggressive", min_silence=0.30),
    )
    assert plan.keeps, "a Japanese transcript should produce a usable cut"
    boundaries = {round(w.start, 3) for w in words} | {round(w.end, 3) for w in words}
    for keep in plan.keeps:
        assert round(keep.start, 3) in boundaries or keep.start == 0.0
