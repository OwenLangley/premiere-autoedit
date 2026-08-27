"""What is in a shot.

Until now a shot was four numbers -- sharpness, motion, exposure, crop risk --
and none of them can tell a shopfront from a chef. This puts pictures and words
in the same space so a sentence can find footage.

CLIP, run locally through onnxruntime, which was already installed. No torch, no
network at edit time, no per-job cost, and nothing leaves the machine. The model
is ~154MB and downloads once.

**Absolute similarity is not a threshold.** Measured on the real library:
phrases that fit the footage scored 0.268-0.298 and phrases that did not scored
0.195-0.215. Both bands move with the footage and with the wording, so there is
no number to put in a constant. `story.assign_beats` decides by competition
against generic distractor phrases instead, which calibrates itself. Six
restaurant beats against twenty-nine shots of a futsal court matched zero, which
is the right answer and the one an absolute floor would have got wrong in either
direction depending on where it was set.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

# ViT-B/32, the quantized ONNX export. One model serves both towers: it takes
# input_ids and pixel_values together and returns text_embeds and image_embeds.
MODEL_REPO = "Xenova/clip-vit-base-patch32"
MODEL_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"

# CLIP's fixed input geometry and normalisation. Not tunable -- these are what
# the weights were trained against.
IMAGE_SIZE = 224
CONTEXT_LENGTH = 77
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


class DescribeError(RuntimeError):
    """The model could not be loaded or run."""


def model_dir(work_dir: Path) -> Path:
    """Where the weights live: beside the proxies and stills, not in the venv.

    A model is a cache artefact like a proxy -- large, re-downloadable, and
    nothing to do with the code. Keeping it in the work directory means clearing
    the cache clears it, and a reinstall does not.
    """
    return Path(work_dir) / "models"


@lru_cache(maxsize=2)
def _load(work_dir_str: str):
    """Session and tokenizer, loaded once per process.

    Deliberately lazy. The engine plans plenty of jobs that never ask a question
    about content, and none of them should pay 154MB of model load to do it.
    """
    try:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
    except ImportError as exc:      # pragma: no cover - install-shaped failure
        raise DescribeError(
            f"the vision model needs {exc.name}; re-run ./setup.sh"
        ) from exc

    cache = model_dir(Path(work_dir_str))
    cache.mkdir(parents=True, exist_ok=True)
    try:
        weights = hf_hub_download(MODEL_REPO, MODEL_FILE, cache_dir=str(cache))
        vocab = hf_hub_download(MODEL_REPO, TOKENIZER_FILE, cache_dir=str(cache))
    except Exception as exc:
        raise DescribeError(
            f"could not fetch {MODEL_REPO}: {exc}. It downloads once and needs "
            f"network; after that this works offline."
        ) from exc

    # CPU, deliberately, on a machine where CoreML is available and looks free.
    #
    # CoreML claims only 1324 of this graph's 2358 nodes, and the partitioned
    # execution that results fails outright on the text tower: "Unable to compute
    # the prediction using a neural network model" the moment more than one
    # phrase is embedded at a time. Images went through it fine, which is the
    # trap -- half the model works and the failure arrives later, on the other
    # half. CPU does the whole thing in 0.3s a still, which is well inside what a
    # background pass can afford.
    session = ort.InferenceSession(weights, providers=["CPUExecutionProvider"])

    tok = Tokenizer.from_file(vocab)
    # CLIP wants a fixed 77-token window, padded and truncated.
    tok.enable_padding(length=CONTEXT_LENGTH, pad_id=tok.token_to_id("<|endoftext|>") or 0)
    tok.enable_truncation(CONTEXT_LENGTH)
    return session, tok


def available(work_dir: Path) -> bool:
    """Can this machine answer questions about pictures?

    False is an ordinary state, not a fault: the model has not been fetched yet,
    or there is no network to fetch it. Callers fall back to the form.
    """
    try:
        _load(str(work_dir))
        return True
    except DescribeError:
        return False


def _pixels(path: Path) -> np.ndarray:
    """One still as CLIP's input tensor.

    ffmpeg rather than Pillow, which is not installed and would be a new
    dependency for a job ffmpeg already does -- it is a hard requirement of this
    project and is already resizing, cropping and transcoding everywhere else.

    Scale-to-fill then centre crop, which is CLIP's own preprocessing. A vertical
    frame loses its top and bottom; that is what the model was trained on.
    """
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"scale={IMAGE_SIZE}:{IMAGE_SIZE}:force_original_aspect_ratio=increase,"
                f"crop={IMAGE_SIZE}:{IMAGE_SIZE}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60).stdout
    expected = IMAGE_SIZE * IMAGE_SIZE * 3
    if len(out) < expected:
        raise DescribeError(f"{Path(path).name}: ffmpeg returned no usable frame")
    a = np.frombuffer(out[:expected], np.uint8).reshape(IMAGE_SIZE, IMAGE_SIZE, 3)
    a = a.astype(np.float32) / 255.0
    return ((a - _MEAN) / _STD).transpose(2, 0, 1)


def embed_texts(texts: list[str], work_dir: Path) -> np.ndarray:
    """Phrases as unit vectors. Empty input gives an empty array, not an error."""
    if not texts:
        return np.zeros((0, 512), np.float32)
    session, tok = _load(str(work_dir))
    enc = tok.encode_batch(list(texts))
    ids = np.array([e.ids for e in enc], np.int64)
    mask = np.array([e.attention_mask for e in enc], np.int64)
    # The graph wants pixels even when only text is asked for; one black frame
    # is the cheapest way to satisfy it and its output is discarded.
    blank = np.zeros((1, 3, IMAGE_SIZE, IMAGE_SIZE), np.float32)
    out = session.run(["text_embeds"],
                      {"input_ids": ids, "attention_mask": mask, "pixel_values": blank})[0]
    return _unit(out)


def embed_images(paths: list[Path], work_dir: Path) -> tuple[np.ndarray, list[int]]:
    """Stills as unit vectors, plus the indices that survived.

    A still that will not decode is skipped rather than fatal -- one unreadable
    JPEG should not cost an editor their whole library -- and the returned
    indices say which inputs the rows correspond to, so a caller can never line
    the wrong vector up against the wrong shot.
    """
    if not paths:
        return np.zeros((0, 512), np.float32), []
    session, tok = _load(str(work_dir))
    enc = tok.encode_batch(["a photograph"])
    ids = np.array([enc[0].ids], np.int64)
    mask = np.array([enc[0].attention_mask], np.int64)

    rows, kept = [], []
    for i, path in enumerate(paths):
        try:
            px = _pixels(Path(path))[None]
        except (subprocess.SubprocessError, DescribeError, OSError, ValueError):
            continue
        out = session.run(["image_embeds"],
                          {"input_ids": ids, "attention_mask": mask, "pixel_values": px})[0]
        rows.append(out[0])
        kept.append(i)
    if not rows:
        return np.zeros((0, 512), np.float32), []
    return _unit(np.array(rows)), kept


# What a shot might be of, for putting a word to it in the panel.
#
# Ranked, not generated. A captioner was tried first -- vit-gpt2, the obvious
# choice -- and on this footage it called futsal "tennis" in half its output:
# "a man is on a tennis court with a racquet". A confidently wrong sentence is
# worse than a filename, because an editor believes it. CLIP is not being asked
# to write anything here, only to say which of these fits best, and ranking is
# what it is good at.
#
# Deliberately general. An editor's own description, when they write one, is
# always better than anything in this list -- these exist for the shots nobody
# described.
DESCRIPTORS: tuple[str, ...] = (
    # people and what they are doing
    "a person talking to camera", "a close-up of a person's face",
    "a person smiling", "two people talking", "a group of people standing together",
    "a crowd of people", "people walking", "people sitting at a table",
    "a person working at a desk", "a handshake", "a person pointing at something",
    "someone giving a thumbs up", "people laughing", "a child", "a family",
    # sport
    "children playing football", "a person kicking a ball", "a ball on the ground",
    "a player running", "a goalkeeper", "a goal net", "people celebrating",
    "a coach giving instructions", "a scoreboard", "a sports hall", "a pitch or court",
    # food and hospitality
    "a chef cooking", "food being prepared", "a plate of food", "a person eating",
    "a drink being poured", "a restaurant interior", "a bar", "a kitchen",
    "a waiter serving a table",
    # places and objects
    "the outside of a building", "a shop front", "a sign or banner",
    "an empty room", "a corridor", "a street", "a car", "a car park",
    "a landscape", "the sky", "trees or plants", "water",
    "a computer screen", "a product on a surface", "machinery or equipment",
    # how it was shot
    "a wide establishing shot", "a close-up of an object", "a hand doing something",
    "a moving camera shot", "a dark or low-light scene", "an empty scene with no people",
)

# Generic enough to win when nothing specific fits. A shot whose best descriptor
# cannot beat these gets no label rather than a wrong one -- the same rule the
# beat matcher uses, and for the same reason.
DESCRIPTOR_FLOOR: tuple[str, ...] = (
    "a photograph", "a video frame", "an indoor scene", "an outdoor scene",
)


def describe_shots(vectors, work_dir: Path) -> list[tuple[str | None, float]]:
    """Put a word to each shot: (descriptor, confidence), or (None, 0) for none.

    `vectors` are unit image embeddings from `embed_images` or
    `embed_stills_cached`.

    Softmax over the descriptors AND the floor phrases, so a shot that is not
    really any of these says nothing. Guessing produces exactly the confident
    wrongness a captioner already demonstrated.
    """
    import numpy as np
    if vectors is None or len(vectors) == 0:
        return []
    words = embed_texts(list(DESCRIPTORS) + list(DESCRIPTOR_FLOOR), work_dir)
    logits = (vectors @ words.T) * 100.0
    logits -= logits.max(axis=1, keepdims=True)
    prob = np.exp(logits)
    prob /= prob.sum(axis=1, keepdims=True)

    out: list[tuple[str | None, float]] = []
    n = len(DESCRIPTORS)
    for row in prob:
        best = int(np.argmax(row))
        out.append((None, 0.0) if best >= n else (DESCRIPTORS[best], round(float(row[best]), 3)))
    return out


def embed_stills_cached(paths: list[Path], work_dir: Path) -> tuple[np.ndarray, list[int]]:
    """Embeddings for stills, computed once and kept.

    A vector is saved beside its still as a `.vec.npy` sibling, which inherits
    the content-derived name `thumbs.py` already generates -- so a re-edit of the
    same footage reuses it, and replacing a file with a different take produces a
    different still and therefore a different vector. No second cache key to keep
    in step with the first.

    Cheap enough to matter: 0.19s a still, so a thirty-shot library is six
    seconds the first time and nothing every time after.
    """
    vectors: dict[int, np.ndarray] = {}
    todo: list[tuple[int, Path]] = []
    for i, path in enumerate(paths):
        cached = Path(str(path) + ".vec.npy")
        if cached.exists():
            try:
                vectors[i] = np.load(cached)
                continue
            except (OSError, ValueError):
                pass          # a corrupt cache file is just a cache miss
        todo.append((i, Path(path)))

    if todo:
        fresh, kept = embed_images([p for _, p in todo], work_dir)
        for row, which in enumerate(kept):
            index, path = todo[which]
            vectors[index] = fresh[row]
            try:
                np.save(Path(str(path) + ".vec.npy"), fresh[row])
            except OSError:
                pass          # an unwritable cache costs speed, not correctness

    order = sorted(vectors)
    if not order:
        return np.zeros((0, 512), np.float32), []
    return np.array([vectors[i] for i in order], np.float32), order


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.where(norms == 0, 1.0, norms)).astype(np.float32)
