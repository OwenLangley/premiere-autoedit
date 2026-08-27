"""Reading an editor's sentence.

The parser is rules, so the tests are the specification: every sentence shape
that should work is written down here, and anything not here is a shape nobody
has claimed works.
"""

from autoedit.story import (
    MIN_BEAT_CHARS, find_duration, find_platform, parse_prompt, split_beats,
)

# The sentence this feature was asked for, verbatim.
BRIEF = (
    "a 15 sec long tiktok video which opens with a shot of the front of the "
    "store then cuts to the inside with the chef cooking, a b-roll shot of "
    "cooking, then the food being served, then customer eating food, then "
    "happy customer face"
)


def test_the_brief_parses_into_six_beats_in_order():
    p = parse_prompt(BRIEF)
    assert p.seconds == 15
    assert p.platform == "tiktok"
    assert p.aspect == "vertical"
    assert [b.text for b in p.beats] == [
        "a shot of the front of the store",
        "the inside with the chef cooking",
        "a b-roll shot of cooking",
        "the food being served",
        "customer eating food",
        "happy customer face",
    ]


def test_beat_ids_are_readable_and_ordered():
    # These become sectionIds and appear in warnings. "b4" would tell an editor
    # nothing about which beat could not be filled.
    ids = [b.id for b in parse_prompt(BRIEF).beats]
    assert ids[0].startswith("b1-") and "store" in ids[0]
    assert ids[5].startswith("b6-") and "happy" in ids[5]
    assert len(set(ids)) == 6


def test_the_preamble_is_not_a_shot():
    # "a 15 sec long tiktok video which" describes the video, not a shot in it.
    assert not any("tiktok" in b.text for b in parse_prompt(BRIEF).beats)


# --- duration ---------------------------------------------------------------

def test_durations_people_actually_write():
    assert find_duration("a 15 sec tiktok") == 15
    assert find_duration("30 seconds") == 30
    assert find_duration("a 15s short") == 15
    assert find_duration("90 second promo") == 90
    assert find_duration("1 min 30 sec") == 90
    assert find_duration("2 minutes") == 120
    assert find_duration("1:30 of b-roll") == 90


def test_a_bare_number_is_not_a_duration():
    # "3 shots of the kitchen" is not a three-second video. Guessing here would
    # set a length nobody asked for, which is the exact shape of the bug that
    # produced a forty-second edit against a fifteen-second intention.
    assert find_duration("3 shots of the kitchen") is None
    assert find_duration("the chef and 2 waiters") is None


def test_no_duration_is_not_an_error():
    p = parse_prompt("storefront, then the chef cooking")
    assert p.seconds is None
    assert len(p.beats) == 2


# --- platform ---------------------------------------------------------------

def test_platform_words_map_to_shapes():
    assert find_platform("a tiktok")[1] == "vertical"
    assert find_platform("for reels")[1] == "vertical"
    assert find_platform("a youtube video")[1] == "landscape"
    assert find_platform("a square post")[1] == "square"


def test_platform_matches_whole_words_only():
    assert find_platform("a shortstop swinging")[0] is None


def test_no_platform_leaves_the_shape_open():
    p = parse_prompt("opens with the storefront, then the chef")
    assert p.aspect is None


# --- splitting --------------------------------------------------------------

def test_a_bare_list_needs_no_opener():
    # Perfectly ordinary way to ask, and it has no preamble at all.
    assert split_beats("storefront, chef cooking, happy customer") == [
        "storefront", "chef cooking", "happy customer",
    ]


def test_the_connectives_people_use():
    got = split_beats(
        "opens with the sign, then the kitchen, followed by the chef, "
        "and then the food, ending with the customer"
    )
    assert got == ["the sign", "the kitchen", "the chef", "the food", "the customer"]


def test_cuts_to_is_consumed_whole():
    # Splitting on "then" alone would leave "cuts to" glued to the next beat and
    # every one of those beats would be described to the model as a camera move.
    assert split_beats("opens with the sign then cuts to the kitchen") == [
        "the sign", "the kitchen",
    ]


def test_trailing_punctuation_does_not_become_a_beat():
    assert split_beats("the sign, the kitchen,") == ["the sign", "the kitchen"]
    assert all(len(b) >= MIN_BEAT_CHARS for b in split_beats("the sign, , the kitchen"))


def test_one_beat_is_a_story():
    assert split_beats("just the storefront") == ["just the storefront"]


# --- nothing to do ----------------------------------------------------------

def test_an_empty_prompt_asks_for_nothing():
    # No beats means no story, and the caller falls through to the ordinary
    # form rather than building something the editor did not describe.
    for text in ("", "   ", None):
        assert parse_prompt(text).beats == []


def test_a_prompt_that_is_only_a_length_has_no_beats():
    p = parse_prompt("15 seconds")
    assert p.seconds == 15
    assert p.beats == []


def test_parsing_never_raises_on_odd_input():
    for text in (",,,", "...", "then then then", "opens with", "15s tiktok"):
        parse_prompt(text)   # no assertion: not raising is the whole claim


def test_japanese_text_survives_intact():
    # Not split on -- the connectives are English -- but it must not be mangled
    # or dropped, because a Japanese editor typing one beat should get one beat.
    p = parse_prompt("店の外観")
    assert [b.text for b in p.beats] == ["店の外観"]
