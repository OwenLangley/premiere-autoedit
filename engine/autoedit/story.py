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

import numpy as np

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
    # Japanese. Compound forms only, deliberately: a bare 縦 or 横 would fire
    # inside 横浜 and turn a shop in Yokohama into a landscape edit. These say
    # the shape and nothing else.
    "縦型": "vertical",
    "縦動画": "vertical",
    "縦長": "vertical",
    "横型": "landscape",
    "横動画": "landscape",
    "横長": "landscape",
    "正方形": "square",
    "スクエア": "square",
}

# CJK characters are word characters, so \b never fires between two of them and
# a boundary search finds none of the Japanese keys above. Substring for those,
# boundary for the rest.
_CJK_KEY = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# "15 sec", "15 seconds", "15s", "1 minute", "1:30".
_DURATION = re.compile(
    r"(?:(?P<mins>\d+)\s*(?:m|min|mins|minute|minutes)\b\s*)?"
    r"(?:(?P<secs>\d+)\s*(?:s|sec|secs|second|seconds)\b)",
    re.I,
)
_CLOCK = re.compile(r"\b(?P<m>\d{1,2}):(?P<s>\d{2})\b")

# "15秒", "1分30秒", "2分". No word boundaries, because Japanese has none: 秒 is
# a word character and \b never fires between 15 and 秒. The units are single
# characters that mean nothing else in a number's company, so a boundary is not
# needed to keep them honest.
_DURATION_JA = re.compile(r"(?:(?P<mins>\d+)\s*分)?\s*(?P<secs>\d+)\s*秒")
_MINUTES_JA = re.compile(r"(?P<mins>\d+)\s*分")

# Where the description of the video stops and the running order starts. Only
# the FIRST of these matters -- everything after it is shots.
_OPENERS = re.compile(
    r"\b(?:which\s+)?(?:opens?|starts?|begins?)\s+(?:with|on)\b|"
    r"\bopening\s+(?:with|on)\b",
    re.I,
)

# The same thing in Japanese, where the opener TRAILS the shot it introduces:
# "店の外観から始まり" is "opens with the shop front", and the shot is the part
# before the marker, not after it.
#
# This is why it needs its own pattern. Treating it like the English one --
# dropping everything up to the marker as preamble -- threw away the opening
# shot every time, silently: "店の外観から始まり、次にシェフが料理をしている"
# parsed to one beat, and the shop front the editor had asked for by name was
# simply not in the edit.
_OPENERS_TRAILING = re.compile(r"から始ま[りるっ]て?|で始ま[りるっ]て?", re.I)

# How one shot is separated from the next. Longest first, so "then cuts to" is
# consumed whole rather than leaving "cuts to" glued to the next beat.
_SPLIT = re.compile(
    r"\s*(?:"
    r"and\s+then\s+cuts?\s+to|then\s+cuts?\s+to|and\s+then|then\s+to|"
    r"followed\s+by|ending\s+(?:with|on)|finishing\s+(?:with|on)|"
    r"cuts?\s+to|then|next|after\s+that|"
    r"[,;]|\.\s+|\band\s+finally\b|\bfinally\b|"
    # Japanese. Without these a Japanese prompt has no connective at all and can
    # never form a running order -- it would always be read as a description,
    # which makes the feature unusable in half the languages this panel ships in.
    r"、|。|その後|そのあと|次に|つぎに|そして|それから|最後に"
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

# A beat has to say something. In a Latin script one or two characters is
# punctuation debris; in Japanese two characters is a whole word -- 料理 is
# "cooking". A single count cannot serve both, so CJK is measured separately.
MIN_BEAT_CHARS = 3
MIN_BEAT_CJK = 1

# CJK ideographs, hiragana and katakana. Enough to tell "is this dense script"
# from "is this an alphabet"; not a full Unicode script table.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LETTERS = re.compile(r"[0-9A-Za-z]")


def _says_something(fragment: str) -> bool:
    """Is there enough here to look for?

    Measured per script, because a character means far more in Japanese than in
    English. Getting this wrong silently drops beats: 料理 was cut for being two
    characters long.
    """
    if len(_CJK.findall(fragment)) >= MIN_BEAT_CJK:
        return True
    return len(_LETTERS.findall(fragment)) >= MIN_BEAT_CHARS

# How fast to cut, in words. These map onto the cut rates in options.py: 0.5 is
# twice a beat, 1 every beat, 2 every other, 4 and 8 slower still.
PACE_WORDS: dict[str, float] = {
    "frantic": 0.5, "breakneck": 0.5, "hyper": 0.5, "rapid": 0.5,
    "fast": 1.0, "quick": 1.0, "punchy": 1.0, "snappy": 1.0, "energetic": 1.0,
    "upbeat": 1.0, "dynamic": 1.0, "high energy": 1.0,
    "steady": 2.0, "measured": 2.0,
    "slow": 4.0, "calm": 4.0, "relaxed": 4.0, "gentle": 4.0, "cinematic": 4.0,
}

# Words that say the video is carried by pictures and music rather than by
# someone talking. A promo is a montage; an interview is not.
MONTAGE_WORDS = (
    "promo", "montage", "b-roll", "broll", "trailer", "teaser", "sizzle",
    "highlight", "highlights", "advert", "commercial", "no talking",
    "no dialogue", "music video",
)


# Asking for subtitles in words. A montage never transcribes -- that is what
# makes it a montage -- so without this an editor could write "with subtitles",
# get an edit with none, and be told nothing about why.
#
# Split by script because \b does not work in Japanese: CJK characters are word
# characters, so \b字幕\b never matches inside 字幕付きの動画. The Latin terms
# still need the boundary, or "caption" fires inside "captioning software".
SUBTITLE_WORDS = (
    "subtitle", "subtitles", "caption", "captions", "captioned",
    "subtitled", "closed captions",
)
SUBTITLE_WORDS_CJK = (
    "字幕", "テロップ", "キャプション",
)


# Descriptions where what is being said is the point. Subtitles imply this on
# their own; these catch the editor who says it without using that word.
#
# Compound forms in Japanese: a bare 話 lives inside 電話 and 世話 and would fire
# on a phone and on looking after someone.
SPEECH_WORDS = (
    "interview", "interviews", "talking head", "talking heads", "testimonial",
    "vox pop", "speech", "what they say", "what they are saying",
    "what people say", "dialogue", "podcast", "voiceover", "voice over",
)
SPEECH_WORDS_CJK = (
    "インタビュー", "会話", "トーク", "話している", "話してる", "証言", "対談",
)


def wants_speech_kept(text: str) -> bool:
    """Does this description make the talking the point?"""
    low = text.lower()
    if any(w in low for w in SPEECH_WORDS_CJK):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in SPEECH_WORDS)


def wants_subtitles(text: str) -> bool:
    """Did the editor ask for subtitles in the description?"""
    low = text.lower()
    if any(w in low for w in SUBTITLE_WORDS_CJK):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in SUBTITLE_WORDS)


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
    """What a sentence asked for.

    Two kinds of prompt arrive here and both are legitimate. One lists shots --
    "opens with the storefront, then the chef" -- and produces a running order.
    The other describes the video without naming a single shot -- "a 12 second
    tiktok with a fast beat that acts as a dramatic promo" -- and produces
    settings instead: how long, what shape, how fast, cut from pictures.

    A prompt with no beats is not a failed parse. It is an editor who described
    the film rather than storyboarding it, and the settings are the answer.
    """

    prompt: str
    beats: list[Beat] = field(default_factory=list)
    seconds: float | None = None
    aspect: str | None = None
    platform: str | None = None
    cut_rate: float | None = None
    visual: bool = False
    subtitles: bool = False
    protect_speech: bool = False

    @property
    def has_running_order(self) -> bool:
        return bool(self.beats)

    def to_dict(self) -> dict:
        out: dict = {"prompt": self.prompt, "beats": [b.to_dict() for b in self.beats]}
        for key, value in (("seconds", self.seconds), ("aspect", self.aspect),
                           ("platform", self.platform), ("cutRate", self.cut_rate)):
            if value is not None:
                out[key] = value
        if self.visual:
            out["visual"] = True
        return out


def find_duration(text: str) -> float | None:
    """Seconds asked for, or None.

    Handles "15 sec", "90 seconds", "1 min 30 sec", "1:30", and the same in
    Japanese: "15秒", "1分30秒", "2分". A bare number is
    deliberately NOT a duration: "3 shots of the kitchen" is not three seconds,
    and guessing there would set a length the editor never asked for -- which is
    exactly the failure that produced a forty-second edit against a fifteen-
    second intention.
    """
    clock = _CLOCK.search(text)
    if clock:
        return int(clock.group("m")) * 60 + int(clock.group("s"))
    ja = _DURATION_JA.search(text)
    if ja:
        return float(ja.group("secs")) + float(ja.group("mins") or 0) * 60
    ja_mins = _MINUTES_JA.search(text)
    if ja_mins:
        return float(ja_mins.group("mins")) * 60
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
    match, and word boundaries so "shorts" does not fire inside "shortstop" --
    except for the Japanese keys, which have no boundaries to search for.
    """
    low = text.lower()
    for word in sorted(PLATFORM_ASPECTS, key=len, reverse=True):
        found = (word in low if _CJK_KEY.search(word)
                 else re.search(rf"\b{re.escape(word)}\b", low))
        if found:
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
    return not _says_something(rest)


def find_recipe(text: str, formats: list[dict]) -> str | None:
    """Which kind of edit the description asks for, if it says.

    Longest keyword first, so "case study" is not decided by "study" appearing
    in another format's list, and so a two-word phrase beats a one-word one.

    Returns None when nothing matches, which leaves whatever recipe is already
    chosen alone. Guessing a recipe from silence would change the thresholds,
    the tracks and the filler handling of an edit on no evidence at all.
    """
    low = (text or "").lower()
    best: tuple[int, str] | None = None
    for fmt in formats or []:
        for word in fmt.get("keywords") or []:
            if len(word) > (best[0] if best else 0) and re.search(
                    rf"\b{re.escape(word)}\b", low):
                best = (len(word), fmt["recipe"])
    return best[1] if best else None


def find_pace(text: str) -> float | None:
    """How fast to cut, if the prompt says.

    Longest phrase first so "high energy" is not read as "energy".
    """
    low = text.lower()
    for word in sorted(PACE_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", low):
            return PACE_WORDS[word]
    return None


def wants_montage(text: str) -> bool:
    """Is this carried by pictures and music rather than by someone talking?"""
    low = text.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in MONTAGE_WORDS)


def split_beats(text: str) -> list[str]:
    """The running order, in order -- or nothing, when there is not one.

    Everything before the first opener describes the video rather than appearing
    in it ("a 15 sec long tiktok video which..."), so it is dropped.

    **A running order needs an opener or a connective.** Without either, the
    editor has described the film rather than storyboarding it -- "a 12 second
    tiktok with a fast beat that acts as a dramatic promo" is one fragment, and
    treating it as a shot sends the matcher looking for footage of the sentence.
    That case returns no beats, and the caller uses the settings instead.

    The cost is that a genuine one-shot video has to be written with an opener:
    "opens with the storefront" rather than "the storefront". That is a smaller
    price than turning every description into a phantom shot.
    """
    opener = _OPENERS.search(text)
    trailing = _OPENERS_TRAILING.search(text)
    if opener:
        body = text[opener.end():]
    elif trailing:
        # Keep the clause the marker trails -- that is the opening shot. Only
        # what precedes THAT is preamble, and the last connective before the
        # marker is where it ends: in "15秒の動画、店の外観から始まり" the
        # preamble is "15秒の動画" and the first beat is "店の外観".
        head = text[:trailing.start()]
        cut = max((m.end() for m in _SPLIT.finditer(head)), default=0)
        body = head[cut:] + "、" + text[trailing.end():]
    else:
        body = text
    if not opener and not trailing and not _SPLIT.search(body):
        return []
    return [
        c for c in (_clean(p) for p in _SPLIT.split(body))
        if _says_something(c) and not _is_description(c)
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
    pace = find_pace(text)
    # Cutting to a rate means cutting from pictures. A montage word says so
    # outright; naming a pace implies it, since a rate has nothing to act on
    # otherwise -- the engine would accept it and quietly ignore it.
    visual = wants_montage(text) or pace is not None
    return StoryPrompt(
        prompt=text,
        beats=beats,
        seconds=find_duration(text),
        aspect=aspect,
        platform=platform,
        cut_rate=pace,
        visual=visual,
        subtitles=wants_subtitles(text),
        # Subtitling an edit is itself a statement that the words matter.
        protect_speech=wants_subtitles(text) or wants_speech_kept(text),
    )


# --- matching beats to shots ------------------------------------------------

# Phrases that describe almost any frame. A beat whose best shot prefers one of
# these is a beat the footage does not contain.
#
# This exists because an absolute similarity floor does not work. Measured on
# the real library: phrases that fit the footage scored 0.268-0.298 and ones
# that did not scored 0.195-0.215, and both bands move with the footage and the
# wording. There is no number to put in a constant. Competition is self-
# calibrating instead -- a beat has to beat "a photograph" on its own best shot,
# which no absent subject manages.
DISTRACTORS: tuple[str, ...] = (
    "a photograph",
    "an indoor scene",
    "an outdoor scene",
    "people in a room",
    "a wide shot of a place",
    "an ordinary video frame",
)

# CLIP's own learned temperature. Its logits are cosine similarity times this,
# and the softmax is meaningless at any other scale.
LOGIT_SCALE = 100.0


@dataclass
class BeatMatch:
    """Which shots a beat won, and how convincingly."""

    beat: Beat
    shots: list[int] = field(default_factory=list)   # indices into the shot list
    confidence: float = 0.0                          # softmax share of its best shot

    @property
    def matched(self) -> bool:
        return bool(self.shots)


def assign_beats(
    beat_vectors: "np.ndarray",
    shot_vectors: "np.ndarray",
    distractor_vectors: "np.ndarray",
    beats: list[Beat],
) -> list[BeatMatch]:
    """Give every shot to the beat that wants it most, or to nobody.

    All vectors must be L2-normalised and share an embedding space.

    Each shot is assigned by softmax over the beats AND the distractors: the
    winner takes it, and if a distractor wins, no beat gets that shot. A beat
    that ends with no shots is unmatched, which the caller reports rather than
    papering over -- an edit that confidently tells the wrong story is worse
    than a short one that admits what it could not find.

    Shots are NOT shared, and no beat gets a second chance at one another beat
    already took. Sharing was specified once and this docstring described it for
    a while before anyone noticed it had never been written -- so it is written
    down here as absent, deliberately: the decision on record is that an empty
    beat is skipped and named, not filled with a shot that fitted something else
    better.

    It costs most where beats are close together in meaning, or where one beat
    is much the strongest and sweeps the pool. That happens more in Japanese,
    because the multilingual encoder compresses the gaps between phrases: on one
    real library three English beats split 4/17/7 while their Japanese
    equivalents went 0/28/0 against the same pictures.
    """
    if not beats or shot_vectors.size == 0:
        return [BeatMatch(beat=b) for b in beats]

    everything = np.vstack([beat_vectors, distractor_vectors])
    logits = (everything @ shot_vectors.T) * LOGIT_SCALE
    logits -= logits.max(axis=0, keepdims=True)          # stable softmax
    prob = np.exp(logits)
    prob /= prob.sum(axis=0, keepdims=True)

    n = len(beats)
    matches = [BeatMatch(beat=b) for b in beats]
    for shot in range(shot_vectors.shape[0]):
        winner = int(np.argmax(prob[:, shot]))
        if winner >= n:
            continue                                      # a distractor took it
        matches[winner].shots.append(shot)
        matches[winner].confidence = max(
            matches[winner].confidence, float(prob[winner, shot]))
    return matches


def normalise(vectors: "np.ndarray") -> "np.ndarray":
    """L2-normalise rows, leaving a zero row as zeros rather than NaN."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


# --- turning matches into a running order -----------------------------------

# How many shots one beat may contribute. A beat that wins sixteen shots of the
# same subject would otherwise fill the film with it; the runtime share limits
# total length but not repetition, and six near-identical shots of a chef is not
# what "then the chef cooking" asked for.
MAX_SHOTS_PER_BEAT = 4


def build_story_plans(
    matches: list[BeatMatch],
    spans: list[tuple[str, float, float, float]],
    target_seconds: float | None,
    min_clip_length: float = 0.4,
    max_shot: float | None = None,
    tolerance: float = 0.35,
) -> tuple[list[tuple[str, "object", str]], list[BeatMatch]]:
    """Lay matched beats out in the order the editor described them.

    `spans` is (media_id, start, end, score), indexed the way `assign_beats`
    indexed them.

    Returns the plans to append -- (media_id, CutPlan, beat_id), already in beat
    order -- and the beats that matched nothing, for the caller to report.

    `tolerance` is how far a beat may run over its share before it is trimmed.
    The default 0.35 is deliberately loose for a described running order, where
    the beats are a rough intention and trimming every one to the millisecond
    would cut mid-gesture for no reason anyone asked for. Matching a REFERENCE
    video is the opposite case -- the shot lengths are the thing being copied --
    and it passes a tight one.

    Runtime is divided by weight across the beats that MATCHED. An unfilled beat
    is excluded from the denominator rather than given a zero share, so the film
    stays the length that was asked for instead of quietly shrinking by however
    many beats the footage could not serve.
    """
    from .detect import CutPlan, Keep      # local: detect must not import story

    matched = [m for m in matches if m.matched]
    unmatched = [m for m in matches if not m.matched]
    if not matched:
        return [], unmatched

    total_weight = sum(m.beat.weight for m in matched) or 1.0
    out: list[tuple[str, object, str]] = []

    for m in matched:
        share = (target_seconds * m.beat.weight / total_weight) if target_seconds else None

        # Strongest first, then capped: a beat is a moment in the story, not a
        # montage of everything that resembled it.
        chosen = sorted(m.shots, key=lambda i: -spans[i][3])[:MAX_SHOTS_PER_BEAT]

        by_media: dict[str, list[tuple[float, float, float]]] = {}
        for i in chosen:
            media_id, start, end, score = spans[i]
            by_media.setdefault(media_id, []).append((start, end, score))

        # Within one beat the shots run in the order they were shot. Ordering
        # them by score instead would cut back and forth in time for no reason
        # an audience could follow.
        per_beat: list[tuple[str, object]] = []
        for media_id, items in by_media.items():
            keeps = [
                Keep(start, end, f"{m.beat.text} ({score:.2f})", round(min(1.0, score + 0.2), 4), 0)
                for start, end, score in sorted(items)
            ]
            per_beat.append((media_id, CutPlan(keeps=keeps, drops=[], warnings=[],
                                              section_id=m.beat.id)))

        if share:
            from .options import fit_duration_across
            per_beat = fit_duration_across(
                per_beat, "about", share, tolerance=tolerance,
                min_clip_length=min_clip_length, strategy="worst", max_shot=max_shot,
            )
        out.extend((media_id, plan, m.beat.id) for media_id, plan in per_beat)

    return out, unmatched
