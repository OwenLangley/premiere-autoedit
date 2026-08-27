"""The vision model, when there is one.

These skip rather than fail without the model. A colleague running the suite on
a fresh machine has not downloaded 154MB of weights yet, and a red suite would
tell them something is broken when nothing is.

The claim that CLIP separates real footage is NOT made here -- it cannot be made
by a unit test, and it was answered by measuring the actual library: three beats
describing subjects the footage does not contain matched zero shots, and three
describing subjects it does contain matched 11, 16 and 1 with p=0.98, 0.91 and
0.64. That measurement is in the commit and in docs/premiere-uxp-findings.md.
"""

import numpy as np
import pytest

from autoedit import describe
from autoedit.story import DISTRACTORS

pytestmark = pytest.mark.skipif(
    not describe.available(describe.Path.home() / "Desktop/AutoEdit-jobs/.cache"),
    reason="CLIP weights not downloaded on this machine",
)
WORK = describe.Path.home() / "Desktop/AutoEdit-jobs/.cache"


def test_text_embeddings_are_unit_vectors():
    v = describe.embed_texts(["a storefront", "a chef cooking"], WORK)
    assert v.shape[0] == 2
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-4)


def test_more_than_one_phrase_at_a_time_works():
    # The exact call that failed under CoreML: a batch of phrases through the
    # text tower. It is why this runs on CPU.
    v = describe.embed_texts(list(DISTRACTORS), WORK)
    assert v.shape[0] == len(DISTRACTORS)


def test_related_phrases_sit_closer_than_unrelated_ones():
    v = describe.embed_texts(
        ["a chef cooking in a kitchen", "a cook preparing food", "a snowy mountain"], WORK)
    near = float(v[0] @ v[1])
    far = float(v[0] @ v[2])
    assert near > far, f"related {near:.3f} should beat unrelated {far:.3f}"


def test_empty_input_is_not_an_error():
    assert describe.embed_texts([], WORK).shape[0] == 0
    vecs, kept = describe.embed_images([], WORK)
    assert vecs.shape[0] == 0 and kept == []


def test_an_unreadable_still_is_skipped_not_fatal(tmp_path):
    # One corrupt JPEG must not cost an editor their whole library.
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not a jpeg")
    vecs, kept = describe.embed_images([bad], WORK)
    assert vecs.shape[0] == 0 and kept == []
