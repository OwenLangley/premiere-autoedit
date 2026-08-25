"""Transcription providers.

Deliberately pluggable. Transcription is the one step with a real privacy and
cost profile, so which engine runs it is a per-project decision, not something
baked into the pipeline.

Providers:
  sidecar  -- read a transcript that already exists next to the media (no deps)
  whisper  -- faster-whisper, running locally; nothing leaves the machine
  stub     -- deterministic fake, for tests

Every provider returns word-level timings. Segment-level output is rejected
rather than silently interpolated: guessed word boundaries produce cuts that
clip syllables, and the failure looks like a bug in the cutter.
"""

from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .transcript import Transcript, Word


class TranscriptionError(RuntimeError):
    pass


class Provider(Protocol):
    def transcribe(self, audio_path: Path, media_id: str, options: dict[str, Any]) -> Transcript: ...


# ------------------------------------------------------------------ audio prep


def extract_audio(media_path: str | Path, out_path: str | Path, sample_rate: int = 16000) -> Path:
    """Mono 16kHz WAV -- what every speech model wants, and small enough that
    sending only this off-machine is a defensible privacy position."""
    src, dst = Path(media_path), Path(out_path)
    if not shutil.which("ffmpeg"):
        raise TranscriptionError("ffmpeg not found on PATH (brew install ffmpeg)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        raise TranscriptionError(f"audio extraction failed for {src.name}: {result.stderr.strip()[:300]}")
    return dst


# ------------------------------------------------------------------ providers


@dataclass
class SidecarProvider:
    """Use a transcript that already exists beside the media.

    Accepts our own format, plus the shapes WhisperX and faster-whisper emit,
    so a transcript produced anywhere can drive the cut.
    """

    suffixes: tuple[str, ...] = (".transcript.json", ".words.json", ".json")

    def transcribe(self, audio_path: Path, media_id: str, options: dict) -> Transcript:
        explicit = options.get("path")
        if explicit:
            candidates = [Path(explicit)]
        else:
            # Look beside the original media, not beside the extracted audio --
            # that is where an editor would actually drop the file.
            base = Path(options.get("media_path") or audio_path)
            candidates = [base.with_name(base.stem + s) for s in self.suffixes]
        for c in candidates:
            if c.exists():
                return parse_transcript_json(json.loads(c.read_text()), media_id)
        raise TranscriptionError(
            f"no sidecar transcript for {audio_path.name}. Looked for: "
            + ", ".join(str(c.name) for c in candidates)
        )


def _stated_language(value: str | None) -> str | None:
    """`auto`, empty and None all mean "work it out from the audio"."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return None if text in ("", "auto", "detect") else text


@dataclass
class WhisperProvider:
    """faster-whisper, locally. Audio never leaves the machine."""

    model: str = "large-v3"
    # "auto" (or None) lets Whisper identify the language from the audio. The old
    # default of "en" was applied to every recipe, so Japanese speech was
    # transcribed as though it were English -- confidently, and as nonsense.
    language: str | None = "auto"
    compute_type: str = "auto"

    def transcribe(self, audio_path: Path, media_id: str, options: dict) -> Transcript:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "faster-whisper is not installed. Either `pip install faster-whisper` "
                "or switch the recipe to the 'sidecar' provider."
            ) from exc

        model_name = options.get("model", self.model)
        model = WhisperModel(model_name, compute_type=options.get("compute_type", self.compute_type))
        stated = _stated_language(options.get("language", self.language))
        segments, info = model.transcribe(
            str(audio_path),
            language=stated,               # None asks Whisper to identify it
            word_timestamps=True,          # non-negotiable
            vad_filter=options.get("vad_filter", True),
        )

        words: list[Word] = []
        for seg in segments:
            if not getattr(seg, "words", None):
                raise TranscriptionError(
                    f"{model_name} returned a segment without word timings. "
                    "Cutting on interpolated boundaries clips syllables, so this is refused."
                )
            for w in seg.words:
                words.append(Word(w.word.strip(), float(w.start), float(w.end),
                                  float(getattr(w, "probability", 1.0))))
        detected = getattr(info, "language", None) or stated or "en"
        confidence = getattr(info, "language_probability", None) if stated is None else None
        return Transcript(media_id, words, detected,
                          float(confidence) if confidence is not None else None)


@dataclass
class StubProvider:
    """Deterministic fake so the pipeline can be exercised without a model."""

    words_per_second: float = 2.5

    def transcribe(self, audio_path: Path, media_id: str, options: dict) -> Transcript:
        duration = float(options.get("duration", 10.0))
        vocab = ["this", "is", "um", "a", "stub", "transcript", "uh", "for", "testing", "only"]
        words, t, i = [], 0.0, 0
        step = 1.0 / self.words_per_second
        while t < duration - step:
            words.append(Word(vocab[i % len(vocab)], round(t, 3), round(t + step * 0.7, 3), 0.99))
            t += step
            i += 1
        return Transcript(media_id, words, "en")


# ------------------------------------------------------------------ parsing


def parse_transcript_json(data: Any, media_id: str) -> Transcript:
    """Accept our format, WhisperX, or faster-whisper JSON."""
    if isinstance(data, dict) and "words" in data and isinstance(data["words"], list):
        return Transcript(
            media_id,
            [Word.from_dict(w) if "text" in w else _loose_word(w) for w in data["words"]],
            data.get("language", "en"),
        )

    if isinstance(data, dict) and "segments" in data:
        words: list[Word] = []
        for seg in data["segments"]:
            for w in seg.get("words") or []:
                words.append(_loose_word(w))
        if not words:
            raise TranscriptionError(
                "transcript has segments but no word-level timings. Re-run the "
                "transcription with word timestamps enabled."
            )
        return Transcript(media_id, words, data.get("language", "en"))

    if isinstance(data, list):
        return Transcript(media_id, [_loose_word(w) for w in data])

    raise TranscriptionError("unrecognised transcript JSON shape")


def _loose_word(w: dict) -> Word:
    text = w.get("text") or w.get("word") or ""
    try:
        start, end = float(w["start"]), float(w["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TranscriptionError(f"word entry missing usable start/end: {w!r}") from exc
    conf = w.get("confidence", w.get("probability", w.get("score", 1.0)))
    return Word(text.strip(), start, end, float(conf), w.get("speaker"))


# ------------------------------------------------------------------ registry

_REGISTRY: dict[str, Callable[[], Provider]] = {
    "sidecar": SidecarProvider,
    "whisper-local": WhisperProvider,
    "whisper": WhisperProvider,
    "stub": StubProvider,
}


def get_provider(name: str) -> Provider:
    if name not in _REGISTRY:
        raise TranscriptionError(
            f"unknown transcription provider {name!r}. Available: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[name]()


def available_providers() -> list[str]:
    return sorted(_REGISTRY)
