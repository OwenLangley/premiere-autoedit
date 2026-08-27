"""Turning a sentence into a running order.

An editor describes a video the way they would to a colleague:

    a 15 sec long tiktok video which opens with a shot of the front of the
    store then cuts to the inside with the chef cooking, a b-roll shot of
    cooking, then the food being served, then customer eating food, then
    happy customer face

Three things are in there: how long it runs, what shape it is, and six shots in
order. This module pulls those apart. It does not decide which footage serves
which beat -- that needs to know what is in a shot, which lives in describe.py.

Rules rather than a model, deliberately. The grammar people use for this is
small and repetitive ("opens with", "then", "cuts to", commas), a model would
need downloading and would still be wrong sometimes, and a wrong split here is
invisible in a way a wrong shot is not. Rules are also inspectable: the editor
sees the beats the parser found and fixes a bad split by typing, which is
cheaper than rephrasing a whole sentence at a black box.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- what the editor is making ---------------------------------------------

# Platform words an editor actually types, and the shape each one implies. The
# aspect names match ASPECT_LABELS in options.py; a recipe `format:` block with
# the same aspect supplies everything else.
PLATFORM_ASPECTS: dict[str, str] = {
    "tiktok": "vertical",
    "reel": "vertical",
    "reels": "vertical",
    "short": "vertical",
    "shorts": "vertical",
    "story": "vertical",
    "stories": "vertical",
    "vertical": "vertical",
    "square": "square",
    "instagram post": "square",
    "youtube": "landscape",
    "landscape": "landscape",
    "widescreen": "landscape",
}

# "15 sec", "15 seconds", "15s", "1 minute", "1:30".
_DURATION = re.compile(
    r"(?:(?P<mins>\d+)\s*(?:m|min|mins|minute|minutes)\b\s*)?"
    r"(?:(?P<secs>\d+)\s*(?:s|sec|secs|second|seconds)\b)",
    re.I,
)
_CLOCK = re.compile(r"\b(?P<m>\d{1,2}):(?P<s>\d{2})\b")

# Where the description of the video stops and the running order starts. Only
# the FIRST of these matters -- everything after it is shots.
_OPENERS = re.compile(
    r"\b(?:which\s+)?(?:opens?|starts?|begins?)\s+(?:with|on)\b|"
    r"\bopening\s+(?:with|on)\b",
    re.I,
)

# How one shot is separated from the next. Longest first, so "then cuts to" is
# consumed whole rather than leaving "cuts to" glued to the next beat.
_SPLIT = re.compile(
    r"\s*(?:"
    r"and\s+then\s+cuts?\s+to|then\s+cuts?\s+to|and\s+then|then\s+to|"
    r"followed\s+by|ending\s+(?:with|on)|finishing\s+(?:with|on)|"
    r"cuts?\s+to|then|next|after\s+that|"
    r"[,;]|\.\s+|\band\s+finally\b|\bfinally\b"
    r")\s*",
    re.I,
)

# Noise left at the head of a fragment once the connective is gone.
_LEADING_NOISE = re.compile(r"^(?:we\s+see\s+|there\s+is\s+|it\s+)", re.I)

# Words that describe the deliverable rather than anything to point a camera at.
# A fragment made only of these is the preamble, not a shot -- "15 seconds" is
# the clearest case, and looking for footage of it would be absurd.
_DESCRIPTION_WORDS = re.compile(
    r"\b(?:long|short|quick|video|clip|edit|cut|film|piece|promo|advert|ad|"
    r"vertical|square|landscape|widescreen|make|create|want|need|please|"
    r"a|an|the|for|of|to|in|is|it|and|about|around|roughly|approx|approximately)\b",
    re.I,
)

# A beat has to say something. One or two characters is punctuation debris.
MIN_BEAT_CHARS = 3


@dataclass
class Beat:
    """One shot in the running order, as the editor described it."""

    id: str
    text: str
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "weight": self.weight}


@dataclass
class StoryPrompt:
    """What a sentence asked for."""

    prompt: str
    beats: list[Beat] = field(default_factory=list)
    seconds: float | None = None
    aspect: str | None = None
    platform: str | None = None

    def to_dict(self) -> dict:
        out: dict = {"prompt": self.prompt, "beats": [b.to_dict() for b in self.beats]}
        if self.seconds is not None:
            out["seconds"] = self.seconds
        if self.aspect:
            out["aspect"] = self.aspect
        if self.platform:
            out["platform"] = self.platform
        return out


def find_duration(text: str) -> float | None:
    """Seconds asked for, or None.

    Handles "15 sec", "90 seconds", "1 min 30 sec" and "1:30". A bare number is
    deliberately NOT a duration: "3 shots of the kitchen" is not three seconds,
    and guessing there would set a length the editor never asked for -- which is
    exactly the failure that produced a forty-second edit against a fifteen-
    second intention.
    """
    clock = _CLOCK.search(text)
    if clock:
        return int(clock.group("m")) * 60 + int(clock.group("s"))
    m = _DURATION.search(text)
    if not m:
        # "2 minutes" on its own, with no seconds part.
        only_mins = re.search(r"\b(\d+)\s*(?:m|min|mins|minute|minutes)\b", text, re.I)
        return float(only_mins.group(1)) * 60 if only_mins else None
    total = float(m.group("secs") or 0)
    if m.group("mins"):
        total += float(m.group("mins")) * 60
    return total or None


def find_platform(text: str) -> tuple[str | None, str | None]:
    """The platform word and the shape it implies, or (None, None).

    Longest key first so "instagram post" wins over a bare "post" would-be
    match, and word boundaries so "shorts" does not fire inside "shortstop".
    """
    low = text.lower()
    for word in sorted(PLATFORM_ASPECTS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", low):
            return word, PLATFORM_ASPECTS[word]
    return None, None


def _clean(fragment: str) -> str:
    """Tidy one beat without rewriting it.

    The text goes to a vision model, so the editor's own words are worth more
    than anything normalisation could impose. Only genuine debris comes off.
    """
    s = unicodedata.normalize("NFC", fragment).strip()
    s = _LEADING_NOISE.sub("", s)
    s = s.strip(" \t\r\n.,;:-–—")
    return re.sub(r"\s+", " ", s)


def _is_description(fragment: str) -> bool:
    """Is this fragment about the deliverable rather than a shot in it?

    Decided by removing everything that describes the video -- a duration, a
    platform, words like "long" and "video" -- and seeing whether anything is
    left to film. "a 15 sec long tiktok video" leaves nothing; "the chef
    cooking" leaves "chef cooking".
    """
    rest = _CLOCK.sub(" ", fragment)
    rest = _DURATION.sub(" ", rest)
    rest = re.sub(r"\b\d+\b", " ", rest)
    for word in PLATFORM_ASPECTS:
        rest = re.sub(rf"\b{re.escape(word)}\b", " ", rest, flags=re.I)
    rest = _DESCRIPTION_WORDS.sub(" ", rest)
    return len(re.sub(r"[^0-9A-Za-z\u3000-\u9fff]+", "", rest)) < MIN_BEAT_CHARS


def split_beats(text: str) -> list[str]:
    """The running order, in order.

    Everything before the first opener is a description of the video rather than
    a shot in it ("a 15 sec long tiktok video which..."), so it is dropped. With
    no opener the whole sentence is treated as the running order, because
    "storefront, chef cooking, happy customer" is a perfectly ordinary way to
    ask for this and has no preamble at all.
    """
    opener = _OPENERS.search(text)
    body = text[opener.end():] if opener else text
    return [
        c for c in (_clean(p) for p in _SPLIT.split(body))
        if len(c) >= MIN_BEAT_CHARS and not _is_description(c)
    ]


# Words that carry no meaning in a four-word slug. Dropped so an id reads
# `b1-shot-front-store` rather than `b1-a-shot-of-the`, which names nothing.
_SLUG_STOPWORDS = {
    "a", "an", "the", "of", "with", "to", "in", "on", "at", "and", "then",
    "is", "are", "being", "some", "his", "her", "their", "it", "its",
}


def _beat_id(index: int, text: str) -> str:
    """A stable, readable id: `b1-shot-front-store`.

    Readable because it becomes a sectionId, which surfaces in the plan, in
    warnings and in the panel -- `b4` in a warning tells an editor nothing about
    which beat went wrong. Numbered because two beats can describe the same
    thing and still be different beats, and the number keeps them apart and in
    order.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]
    kept = [w for w in words if w not in _SLUG_STOPWORDS] or words
    slug = "-".join(kept[:4])[:32].strip("-")
    return f"b{index}-{slug}" if slug else f"b{index}"


def parse_prompt(text: str) -> StoryPrompt:
    """Read an editor's sentence.

    Never raises on odd input. An empty prompt, a prompt with no shots in it, or
    one that is only a duration all return a StoryPrompt with no beats -- which
    the caller treats as "no story was asked for" and falls through to the
    ordinary form.
    """
    text = unicodedata.normalize("NFC", text or "").strip()
    if not text:
        return StoryPrompt(prompt="")

    platform, aspect = find_platform(text)
    beats = [Beat(id=_beat_id(i + 1, t), text=t)
             for i, t in enumerate(split_beats(text))]
    return StoryPrompt(
        prompt=text,
        beats=beats,
        seconds=find_duration(text),
        aspect=aspect,
        platform=platform,
    )
