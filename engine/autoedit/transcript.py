"""Word-level transcript model.

Word-level timing is non-negotiable. Segment-level timestamps (the default from
most APIs) are accurate to roughly a sentence, which is useless for removing a
250ms "um" without clipping the syllable either side of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^\w'-]+", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, keep internal apostrophes and hyphens.

    Hyphens survive because a trailing one is how most engines transcribe a cut-off
    word ("th-"), which is the main signal for stutter detection.
    """
    return _PUNCT.sub("", text.strip().lower())


@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float = 1.0
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def norm(self) -> str:
        return normalize(self.text)

    def to_dict(self) -> dict:
        d: dict = {"text": self.text, "start": round(self.start, 4), "end": round(self.end, 4)}
        if self.confidence < 1.0:
            d["confidence"] = round(self.confidence, 4)
        if self.speaker:
            d["speaker"] = self.speaker
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Word":
        return cls(
            text=d["text"],
            start=float(d["start"]),
            end=float(d["end"]),
            confidence=float(d.get("confidence", 1.0)),
            speaker=d.get("speaker"),
        )


@dataclass
class Transcript:
    media_id: str
    words: list[Word] = field(default_factory=list)
    language: str = "en"

    def __len__(self) -> int:
        return len(self.words)

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0

    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def speakers(self) -> list[str]:
        seen: dict[str, None] = {}
        for w in self.words:
            if w.speaker:
                seen.setdefault(w.speaker, None)
        return list(seen)

    def slice(self, start: float, end: float) -> list[Word]:
        """Words whose midpoint falls inside [start, end).

        Midpoint rather than overlap so a word straddling a cut belongs to exactly
        one side -- otherwise the same word appears in two clips' text.
        """
        return [w for w in self.words if start <= (w.start + w.end) / 2 < end]

    def validate(self) -> list[str]:
        """Structural problems worth surfacing rather than silently tolerating."""
        problems: list[str] = []
        for i, w in enumerate(self.words):
            if w.end < w.start:
                problems.append(f"word {i} ({w.text!r}) ends before it starts")
            if i and w.start < self.words[i - 1].start:
                problems.append(f"word {i} ({w.text!r}) is out of chronological order")
        return problems

    def to_dict(self) -> dict:
        return {
            "mediaId": self.media_id,
            "language": self.language,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            media_id=d["mediaId"],
            language=d.get("language", "en"),
            words=[Word.from_dict(w) for w in d["words"]],
        )
