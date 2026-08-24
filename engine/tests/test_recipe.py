import pytest

from autoedit.recipe import RecipeError, list_recipes, load_recipe


def test_all_shipped_recipes_load():
    names = list_recipes()
    assert {"podcast-2cam", "social-short", "client-promo"} <= set(names)
    for n in names:
        load_recipe(n)


def test_recipes_encode_their_editorial_intent():
    """Short-form should cut harder than a podcast. If this inverts, someone has
    mixed up the tuning."""
    short = load_recipe("social-short").detection
    pod = load_recipe("podcast-2cam").detection
    promo = load_recipe("client-promo").detection

    assert short.min_silence < pod.min_silence < promo.min_silence
    assert short.min_clip_length < pod.min_clip_length < promo.min_clip_length
    assert short.filler_mode == "aggressive"
    assert pod.filler_mode == "conservative"


def test_keep_fillers_survives_into_the_lexicon():
    lex = load_recipe("social-short").detection.filler_lexicon()
    assert "right" not in lex, "keep_fillers must win over the aggressive list"
    assert "like" in lex


def test_crossfade_is_derived_from_the_recipe_timebase():
    r = load_recipe("social-short")
    assert r.sequence.crossfade_seconds == pytest.approx(1 / 30, abs=1e-4)


def test_unknown_detection_key_is_rejected(tmp_path):
    """A typo that silently keeps the default is worse than a crash: the editor
    would never learn their setting did nothing."""
    p = tmp_path / "typo.yaml"
    p.write_text("name: typo\ndetection:\n  min_silense: 0.4\n")
    with pytest.raises(RecipeError, match="unknown detection setting"):
        load_recipe(p)


def test_unknown_top_level_key_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: bad\ndetektion: {}\n")
    with pytest.raises(RecipeError, match="unknown top-level"):
        load_recipe(p)


def test_rejects_handles_that_would_cancel_every_cut(tmp_path):
    """min_silence below lead_in+tail means every pause is eaten by its own
    handles and nothing is ever cut -- a silent no-op that looks like a bug."""
    p = tmp_path / "impossible.yaml"
    p.write_text("name: impossible\ndetection:\n  min_silence: 0.10\n  lead_in: 0.20\n  tail: 0.20\n")
    with pytest.raises(RecipeError, match="min_silence"):
        load_recipe(p)


def test_rejects_invalid_filler_mode(tmp_path):
    p = tmp_path / "mode.yaml"
    p.write_text("name: mode\ndetection:\n  filler_mode: nuclear\n")
    with pytest.raises(RecipeError, match="filler_mode"):
        load_recipe(p)


def test_missing_recipe_lists_what_is_available():
    with pytest.raises(RecipeError, match="Available:"):
        load_recipe("does-not-exist")


def test_sequence_name_templating():
    assert load_recipe("podcast-2cam").sequence_name("EP042") == "EP042_rough_v1"
