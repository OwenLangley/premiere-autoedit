"""Learning what an intro and an outro sound like, from references seen so far.

A hand-written cue list does not survive contact with real videos. Measured on a
27-minute Japanese reference: of six obvious phrases -- `こんにちは`, `どうも`,
`ということで今日は`, `チャンネル登録`, `また次回`, `最後まで見て` -- five never
appeared at all, and the one that did appeared three times at eighteen minutes,
where someone was greeting customers. That channel closes with a recruitment ad,
not a subscribe button. The list encoded what its author assumed a YouTube video
says.

So the phrases are learned instead, and three rules keep the learning honest:

**Only the ends are looked at.** An opening is at the opening. `reference.py`
already restricts roles to the first and last tenth, and the same restriction
here is what stops `こんにちは`-at-eighteen-minutes from ever being seen as an
opening phrase in the first place.

**The middle is the negative set.** A phrase that also turns up mid-video is not
a cue, it is just something this channel says. This is the rule that would have
caught the false positive above without anybody noticing it by hand.

**Nothing fires from one video.** A phrase has to appear at the same end of at
least two DIFFERENT references. Otherwise the store memorises one video's
subject matter -- on that reference the most "distinctive" opening phrase was
`ポップアップイベント`, which is what it happened to be about.

The store is small, plain JSON, and can be read: what the tool has learned is
inspectable, which matters for something that changes its own behaviour.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Phrase length. Four characters is about the shortest run of Japanese that
# carries meaning on its own, and about the shortest English phrase worth
# calling a cue once spaces are gone.
GRAM = 4

# How many DIFFERENT references must show a phrase at the same end before it
# counts. Two is the smallest number that can distinguish a convention from a
# coincidence; one is memorisation.
MIN_REFERENCES = 2

# A phrase seen anywhere in the middle of any reference is disqualified, however
# often it appears at the ends.
MIDDLE_DISQUALIFIES = True

# Cap per language per end, so the file cannot grow without bound. Phrases are
# kept by how many references showed them.
MAX_PHRASES = 400

STORE_NAME = "cues.json"

_STRIP = re.compile(r"[\s。、，,.!?！？「」『』()（）\[\]\-—…・]+")


def _normalise(text: str) -> str:
    return _STRIP.sub("", text)


def phrases(text: str) -> set[str]:
    """Overlapping character n-grams, punctuation and spacing removed.

    Characters rather than words because the languages this has to serve are
    split differently -- Japanese has no spaces, and Whisper does not tokenise
    it into words anyway (`detect.py` records the same finding for fillers).
    Overlapping so a phrase is found wherever it starts.
    """
    flat = _normalise(text)
    if len(flat) < GRAM:
        return set()
    return {flat[i:i + GRAM] for i in range(len(flat) - GRAM + 1)}


class CueStore:
    """What the ends of previously-seen references had in common.

    Counts REFERENCES, not occurrences: a phrase repeated forty times in one
    video counts once, so a single talkative reference cannot teach the tool
    its own catchphrases.
    """

    def __init__(self, data: dict | None = None):
        self.data: dict = data or {}

    # ------------------------------------------------------------- storage

    @classmethod
    def load(cls, work_dir: Path) -> "CueStore":
        try:
            return cls(json.loads((work_dir / STORE_NAME).read_text()))
        except (OSError, ValueError):
            return cls()          # no store yet, or a damaged one: start clean

    def save(self, work_dir: Path) -> None:
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / STORE_NAME).write_text(json.dumps(self.data, indent=2,
                                                          ensure_ascii=False))
        except OSError:
            pass                  # an unwritable store learns nothing, and that is all

    # ------------------------------------------------------------ learning

    def observe(self, language: str, head: str, tail: str, middle: str,
                reference_id: str) -> None:
        """Record one reference's ends, and its middle as the negative set.

        `reference_id` makes this idempotent: analysing the same reference twice
        -- which happens on every re-run, since the measurement is cached but
        this is not -- must not count it as two references.
        """
        lang = (language or "en").split("-")[0].lower()
        bucket = self.data.setdefault(lang, {"seen": [], "head": {}, "tail": {},
                                             "middle": {}})
        if reference_id in bucket["seen"]:
            return
        bucket["seen"].append(reference_id)
        for end, text in (("head", head), ("tail", tail), ("middle", middle)):
            for gram in phrases(text):
                bucket[end][gram] = bucket[end].get(gram, 0) + 1
        self._trim(bucket)

    def _trim(self, bucket: dict) -> None:
        for end in ("head", "tail", "middle"):
            if len(bucket[end]) <= MAX_PHRASES:
                continue
            kept = sorted(bucket[end].items(), key=lambda kv: -kv[1])[:MAX_PHRASES]
            bucket[end] = dict(kept)

    # ------------------------------------------------------------- reading

    def learned(self, language: str, end: str) -> set[str]:
        """Phrases that earned their place at this end. Empty until they have."""
        lang = (language or "en").split("-")[0].lower()
        bucket = self.data.get(lang)
        if not bucket:
            return set()
        middle = bucket.get("middle", {}) if MIDDLE_DISQUALIFIES else {}
        return {
            gram for gram, count in bucket.get(end, {}).items()
            if count >= MIN_REFERENCES and gram not in middle
        }

    def role_of(self, text: str, language: str) -> str | None:
        """"opening", "ending" or None, from what has been learned so far."""
        found = phrases(text)
        if not found:
            return None
        if found & self.learned(language, "head"):
            return "opening"
        if found & self.learned(language, "tail"):
            return "ending"
        return None

    def summary(self, language: str) -> str:
        lang = (language or "en").split("-")[0].lower()
        bucket = self.data.get(lang) or {}
        return (f"{len(bucket.get('seen', []))} reference(s) seen, "
                f"{len(self.learned(language, 'head'))} opening and "
                f"{len(self.learned(language, 'tail'))} ending phrase(s) learned")
