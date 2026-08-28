"""Subtitles for the edit that was actually made.

The transcript is in SOURCE time and the edit cuts, drops and reorders, so a
subtitle file written straight from the transcript describes a video nobody
will watch. Every word here is carried onto the finished sequence through the
timeline entry that contains it, and words in material the edit dropped are
dropped with it.

Built from the plan alone -- timeline, transcripts and timebase are all in
there -- so this needs no media, no model and no decode, and can be run again
over a plan that already exists.

Two rules do most of the work:

* **A cue never crosses a cut to different material.** Subtitles that run over
  a real edit read as though the wrong person is speaking. But a dialogue edit
  cuts constantly *within* one continuous take -- removing an "um" leaves a cut
  in the middle of a sentence the audience hears as unbroken -- and breaking
  there fragments a sentence into a stutter of one-word cues. Measured on a
  real clean-up: "So" / "the thing" / "that really matters is trust." where the
  viewer hears one sentence. Only a jump in the source, or a change of speaker
  or camera, ends a cue.
* **A word straddling a cut belongs to one side**, by midpoint -- the rule
  `Transcript.slice` already uses, so the subtitles agree with the cutting
  about which clip a word is in rather than inventing a second answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .timebase import Timebase
from .transcript import Transcript, Word

# CJK ideographs, hiragana and katakana. Japanese subtitles carry far fewer
# characters per line than Latin ones and are joined without spaces, so the
# script has to be known before anything can be laid out.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Per line, then at most two lines. 42 is the broadcast convention for Latin
# subtitles; Japanese runs about 16, which is why one number cannot serve both.
MAX_CHARS_LATIN = 42
MAX_CHARS_CJK = 16
MAX_LINES = 2

# A pause long enough to be a new thought rather than a breath.
GAP_BREAK = 0.7

# A word that ends a sentence ends a cue. Standard subtitling practice, and it
# reads far better than filling two lines and breaking wherever they run out.
#
# An abbreviation ("Mr.", "U.S.") breaks a cue early, which costs a slightly
# short cue and nothing else. Whisper rarely emits them, and the alternative --
# a list of abbreviations per language -- is a much larger thing to be wrong
# about.
_SENTENCE_END = re.compile(r"[.!?。！？]['\"”’]?$")

# How far the source can jump between two clips and still be the same breath.
# A removed "um" or a repaired stumble is a fraction of a second; anything
# longer is a real edit and the sentence either side of it is a different one.
SAME_BREATH = 1.5

# A cue too short to read is worse than one that lingers, so the floor is
# enforced and the ceiling merely stops a cue hanging over a silence.
MIN_CUE_SECONDS = 1.0
MAX_CUE_SECONDS = 6.0

# Characters a viewer gets through in a second. Latin is the usual 17; Japanese
# is denser per character and read slower, so a shorter line needs as long.
CPS_LATIN = 17.0
CPS_CJK = 8.0


def is_cjk(text: str) -> bool:
    """Is this line laid out as Japanese rather than as an alphabet?"""
    return bool(_CJK.search(text))


@dataclass
class Cue:
    """One subtitle, in SEQUENCE seconds -- where it lands in the finished cut."""

    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    speaker: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _timecode(seconds: float) -> str:
    """`HH:MM:SS,mmm`, SRT's own format, rounded rather than truncated.

    Truncating loses up to a millisecond per cue in one direction only, which
    accumulates into a visible drift over a long interview.
    """
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _join(words: list[Word]) -> str:
    """Words back into a line, spaced or not according to the script."""
    if not words:
        return ""
    text = " ".join(w.text for w in words)
    return "".join(w.text for w in words) if is_cjk(text) else text


def _wrap(words: list[Word], limit: int) -> list[str]:
    """At most MAX_LINES lines, broken on the widest fit.

    Greedy rather than balanced. A balanced break reads better on a two-line
    cue and worse on everything else, because it moves the break away from the
    phrase boundary the words already imply.
    """
    text = _join(words)
    if len(text) <= limit:
        return [text]

    if is_cjk(text):
        return [text[i:i + limit] for i in range(0, len(text), limit)][:MAX_LINES]

    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:MAX_LINES]


def _placed_words(plan: dict) -> list[tuple[int, Word]]:
    """Every transcript word that survived the edit, in sequence time.

    Returns ((media id, clip in, clip out), word) so grouping can tell a
    clean-up cut from a real one without looking the clip up again.
    """
    tb = Timebase.from_dict(plan["timebase"])
    by_media: dict[str, Transcript] = {}
    for raw in plan.get("transcripts") or []:
        t = Transcript.from_dict(raw)
        by_media[t.media_id] = t

    out: list[tuple[tuple[str, float, float], Word]] = []
    for clip in plan.get("timeline") or []:
        transcript = by_media.get(clip.get("mediaId"))
        if transcript is None:
            continue                      # a music bed, or a clip cut from pictures
        clip_in = float(clip["inSeconds"])
        clip_out = float(clip["outSeconds"])
        at = tb.to_seconds(int(clip["atFrame"]))
        ends = at + tb.to_seconds(int(clip["durationFrames"]))
        # Source time to sequence time is one shift, because a clip plays at
        # speed: the offset is where the clip landed minus where it was taken
        # from.
        shift = at - clip_in
        where = (clip["mediaId"], clip_in, clip_out)
        for word in transcript.slice(clip_in, clip_out):
            start = min(max(word.start + shift, at), ends)
            end = min(max(word.end + shift, start), ends)
            out.append((where, Word(
                text=word.text, start=start, end=end,
                confidence=word.confidence, speaker=word.speaker,
            )))
    out.sort(key=lambda pair: (pair[1].start, pair[1].end))
    return out


def _limit_for(words: list[Word]) -> int:
    return MAX_CHARS_CJK if is_cjk(_join(words)) else MAX_CHARS_LATIN


def _flush(words: list[Word]) -> Cue | None:
    if not words:
        return None
    limit = _limit_for(words)
    lines = _wrap(words, limit)
    if not any(line.strip() for line in lines):
        return None
    return Cue(start=words[0].start, end=words[-1].end, lines=lines,
               speaker=words[0].speaker)


def _is_hard_cut(previous, current) -> bool:
    """Does the material change here, or is this the same breath?

    `previous` and `current` are (media id, clip in, clip out). A different
    file is always a real cut. Within one file it is the size of the jump that
    decides: a removed filler word leaves a hole of a few hundred
    milliseconds, and the sentence carries straight over it.
    """
    if previous is None or previous == current:
        return False
    if previous[0] != current[0]:
        return True
    return abs(current[1] - previous[2]) > SAME_BREATH


def group_cues(placed) -> list[Cue]:
    """Words into readable cues, breaking on a real cut, a pause or a full line."""
    cues: list[Cue] = []
    run: list[Word] = []
    # The clip the PREVIOUS word came from, not the one the cue started in: a
    # run that carried over one clean-up cut must judge the next jump against
    # where it has got to, or a sentence spanning three clips measures its last
    # hop from the first clip and breaks on a gap nobody can hear.
    last_where = None

    for where, word in placed:
        if run:
            limit = _limit_for(run + [word])
            over_length = len(_join(run + [word])) > limit * MAX_LINES
            too_long = word.end - run[0].start > MAX_CUE_SECONDS
            gap = word.start - run[-1].end
            ended = bool(_SENTENCE_END.search(run[-1].text))
            if (_is_hard_cut(last_where, where) or over_length or too_long
                    or ended or gap > GAP_BREAK
                    or word.speaker != run[0].speaker):
                cue = _flush(run)
                if cue:
                    cues.append(cue)
                run = []
        run.append(word)
        last_where = where

    cue = _flush(run)
    if cue:
        cues.append(cue)
    return cues


def _pad(cues: list[Cue], limit: float) -> list[Cue]:
    """Hold a short cue on screen long enough to read, where there is room.

    Never into the next cue and never past the end of the sequence: a subtitle
    that outlives its own shot is the thing this file exists to avoid.
    """
    for i, cue in enumerate(cues):
        text = "".join(cue.lines)
        cps = CPS_CJK if is_cjk(text) else CPS_LATIN
        wanted = max(MIN_CUE_SECONDS, len(text) / cps)
        if cue.duration >= wanted:
            continue
        room = (cues[i + 1].start if i + 1 < len(cues) else limit) - cue.start
        cue.end = cue.start + min(wanted, max(room, cue.duration))
    return cues


def build_cues(plan: dict) -> list[Cue]:
    """The finished cut's subtitles, frame-snapped, in order."""
    tb = Timebase.from_dict(plan["timebase"])
    placed = _placed_words(plan)
    if not placed:
        return []

    end_of_sequence = max(
        (tb.to_seconds(int(c["atFrame"]) + int(c["durationFrames"]))
         for c in plan.get("timeline") or []),
        default=0.0,
    )
    cues = _pad(group_cues(placed), end_of_sequence)

    # Snapped last, so padding cannot push a cue a fraction of a frame past the
    # cut it belongs to. Everything else in this project lands on a frame and
    # subtitles are not the place to start being approximate.
    for cue in cues:
        cue.start = tb.snap(cue.start)
        cue.end = max(tb.snap(cue.end), cue.start + tb.frame_duration())
    return cues


def to_srt(cues: list[Cue]) -> str:
    """SubRip, which every editor and every platform already reads.

    Chosen over Premiere's own transcript JSON because that format is
    undocumented and `Transcript.importFromJSON` rejects the obvious shape --
    see findings 9. An .srt is also the thing a client asks for.
    """
    blocks = []
    for i, cue in enumerate(cues, 1):
        blocks.append(
            f"{i}\n{_timecode(cue.start)} --> {_timecode(cue.end)}\n{cue.text}\n"
        )
    return "\n".join(blocks)
