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


# --- the format block, which is what an editor actually picks ---------------

def test_every_shipped_recipe_offers_a_format():
    # The panel's first question is "what are you making". A recipe with no
    # format has no card, which is allowed -- but all four shipped ones should
    # have one, or the front door is half empty.
    for name in list_recipes():
        fmt = load_recipe(name).format
        assert fmt, f"{name} has no format block"
        assert fmt["label"], f"{name} format has no label"


def test_format_orders_are_unique():
    orders = [load_recipe(n).format.get("order") for n in list_recipes()]
    assert len(set(orders)) == len(orders), f"duplicate format order: {orders}"


def test_a_format_needs_a_label(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("name: r\nformat:\n  aspect: vertical\n")
    with pytest.raises(RecipeError, match="needs a label"):
        load_recipe(p)


def test_format_rejects_an_aspect_the_engine_cannot_build(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("name: r\nformat:\n  label: X\n  aspect: cinemascope\n")
    with pytest.raises(RecipeError, match="unknown aspect"):
        load_recipe(p)


def test_a_length_mode_without_a_length_is_refused(tmp_path):
    # The exact defect this guards: a format promising "exactly 15s" that
    # carries no seconds would produce an edit of whatever length it liked.
    p = tmp_path / "r.yaml"
    p.write_text("name: r\nformat:\n  label: X\n  duration_mode: exactly\n")
    with pytest.raises(RecipeError, match="needs a duration"):
        load_recipe(p)


def test_format_rejects_an_unknown_key(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("name: r\nformat:\n  label: X\n  colour: red\n")
    with pytest.raises(RecipeError, match="unknown format key"):
        load_recipe(p)


def test_every_format_builds_options_the_engine_accepts():
    """A card in the panel must produce a job the engine will run.

    This is the seam where a typo becomes an editor's problem: they pick
    "Reel / Short", the request is assembled from these values, and any one of
    them being unacceptable fails somewhere far from the choice they made.
    """
    from autoedit.options import JobOptions

    for name in list_recipes():
        fmt = load_recipe(name).format
        if not fmt:
            continue
        JobOptions(
            aspect=fmt["aspect"],
            duration_mode=fmt["duration_mode"],
            duration_seconds=fmt.get("duration"),
            cut_rate=fmt.get("cut_rate"),
        )


def test_a_format_promising_a_length_carries_one():
    # "exactly 15s" with no seconds would run to whatever length it liked, which
    # is the defect that produced a 40s edit against a 15s intention.
    for name in list_recipes():
        fmt = load_recipe(name).format
        if fmt and fmt["duration_mode"] != "none":
            assert fmt.get("duration"), f"{name}: {fmt['duration_mode']} with no duration"


def test_a_recipe_that_does_not_transcribe_still_names_a_listening_fallback():
    """Subtitles can be asked for on any recipe, including silent ones.

    promo-silent leaves `transcription.provider` unset, which defaults to
    `sidecar` -- a file reader. Asking it to transcribe produced six "no
    sidecar transcript" warnings and no subtitles at all, on a job that had
    asked for them in plain words.
    """
    from autoedit.recipe import load_recipe
    from autoedit.transcribe import LISTENS

    silent = load_recipe("promo-silent")
    assert silent.transcription.get("provider", "sidecar") not in LISTENS, (
        "the premise: this recipe cannot transcribe on its own"
    )
    assert "whisper-local" in LISTENS


def test_no_two_recipes_claim_the_same_keyword():
    """A word owned by two recipes selects whichever the matcher reaches first,
    which is not a choice anybody made. `vlog` was owned by social-short and
    long-form at once -- a 15-second vertical and a 20-minute documentary."""
    import collections
    from autoedit.recipe import list_recipes, load_recipe

    owner = collections.defaultdict(list)
    for name in list_recipes():
        for word in load_recipe(name).format.get("keywords", []):
            owner[word].append(name)
    clashes = {w: v for w, v in owner.items() if len(v) > 1}
    assert not clashes, clashes
