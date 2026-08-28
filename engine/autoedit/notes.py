"""Warnings an editor reads, in a form that can be translated.

Warnings used to be English prose built at the point they were raised, which made
them untranslatable: by the time one reached the panel it was a sentence, not a
fact. An editor who cannot read "the music stops 0.1s before the picture does" is
worse off than one who sees no warning at all, because they will assume it is fine.

So a warning now carries the identity of what happened plus the numbers involved,
and the English is rendered from the same pair:

    note("cut.trimmedToTarget", running=20, seconds=20, dropped=23, order="...")

The panel renders `messageKey` + `params` in the editor's language when it has a
translation, and falls back to `message` when it does not -- so a warning added
here without a translation degrades to English rather than disappearing.

Keeping every user-facing English string in one file is worth it on its own: it
is the only way to see them together and notice that two of them contradict.
"""

from __future__ import annotations



# The English rendering of every warning. Keys are `area.thing` and are a
# contract with the panel's catalogue -- renaming one silently drops the
# translation back to English, so don't.
CATALOGUE: dict[str, str] = {
    # --- cutting from speech ---------------------------------------------
    "cut.lowConfidenceSkipped":
        "{count} cut(s) skipped over low-confidence speech (below {threshold:g}); "
        "review those sections by hand",
    "cut.veryAggressive":
        "cut removed {percent:.0f}% of the source -- unusually aggressive, check "
        "min_silence and filler_mode before trusting this assembly",

    # --- cutting from pictures -------------------------------------------
    "visual.noSamples":
        "frame analysis produced no samples; quality gates skipped",
    "visual.allShotsFailed":
        "every shot failed a quality gate -- thresholds are probably wrong for this "
        "footage; loosen min_sharpness / min_brightness in the recipe",
    "visual.shotsRejected":
        "{rejected} of {total} shots rejected on quality",
    "visual.noBeats":
        "beat detection found no beats; falling back to fixed-length takes",
    "visual.lowBeatConfidence":
        "beat confidence {confidence:.2f} is below {threshold:.2f}; cutting to a wrong "
        "grid is worse than not cutting to one, so fixed-length takes are used instead",
    "visual.targetReachedEarly":
        "reached the {seconds:.0f}s target with {remaining} usable shot(s) unused",
    "visual.nothingSurvived":
        "no clips survived visual cut planning",

    # --- length ------------------------------------------------------------
    "length.shortOfAbout":
        "came out at {total:.0f}s against a target of about {seconds:.0f}s -- there was "
        "not enough usable material to reach it",
    "length.shortOfTarget":
        "came out at {total:.0f}s against a {seconds:.0f}s target -- there was not "
        "enough usable material to fill it",
    # Two keys rather than one with the ordering as a parameter: a sentence
    # assembled from a translated frame and an untranslated fragment is exactly
    # the half-English result this whole mechanism exists to avoid.
    "length.trimmedWorst":
        "trimmed to {running}s for the {seconds}s target "
        "({dropped} clip(s) dropped, lowest quality first)",
    "length.trimmedTail":
        "trimmed to {running}s for the {seconds}s target "
        "({dropped} clip(s) dropped, from the end)",
    "length.nothingFits":
        "nothing fits inside {seconds:.0f}s -- the shortest available clip is longer "
        "than the target",

    # --- reframing ---------------------------------------------------------
    "reframe.fitted":
        "{count} clip(s) resized to {width}x{height}; the shape already matched, so "
        "nothing is cropped",
    "reframe.cropRisk":
        "{count} clip(s) flagged: detail sits outside the centre crop, so check those "
        "before delivering",

    # --- music -------------------------------------------------------------
    "music.cappedToTrack":
        "the edit is capped at {seconds:.0f}s, the length of the music -- set a length "
        "if you want it to run on past the track",
    "music.startPastEnd":
        "start {start:.2f}s is past the end of the {duration:.1f}s track; starting from "
        "the beginning instead",
    "music.snappedToBeat":
        "start moved {moved:.2f}s to the nearest beat, at {start:.2f}s",
    "music.chunkClamped":
        "asked for {wanted:.1f}s from {start:.2f}s but the track only has {available:.1f}s "
        "left, so the bed is {actual:.1f}s",
    "music.runsPastPicture":
        "the music runs {overhang:.1f}s past the last frame of picture -- extend the edit "
        "or shorten the chunk",
    "music.stopsEarly":
        "the music stops {shortfall:.1f}s before the picture does",

    # --- cutting to the beat -----------------------------------------------
    "beat.snapped":
        "{snapped} cut(s) placed on the beat at {bpm} BPM"
        "{held_clause}{dropped_clause}",
    "beat.gridUnavailable":
        "beat confidence {confidence:.2f} is below {threshold:.2f}, so the cuts follow the "
        "speech instead of the music -- cutting to a wrong grid is worse than not cutting to one",

    # --- playback -----------------------------------------------------------
    "media.proxyAttached":
        "{count} clip(s) are too heavy to play back at full resolution, so proxies "
        "are used -- turn on Toggle Proxies in the program monitor to see them",
    "media.proxyBuilding":
        "{count} clip(s) will not play back smoothly and their proxies are still "
        "building; the cut is correct either way, and playback catches up once they finish",

    # --- timebase ----------------------------------------------------------
    "timebase.followedFootage":
        "sequence set to {chosen} to match the footage; the recipe asks for {recipe}, "
        "which no whole number of source frames lands on exactly",
    "timebase.mixedRates":
        "{count} clip(s) are not an exact fit for the {chosen} sequence, so those cuts "
        "can be a frame out; check the joins on {files}",

    # --- alternates the editor can swap in ---------------------------------
    "swap.candidatesTruncated":
        "{file} has {found} usable spans; only the best {kept} are offered as "
        "alternates, so a shot you remember may not be in the list",

    "visual.rateBelowMinimum":
        "your cut rate asks for {take:.2f}s shots, shorter than this recipe's "
        "{minimum:.2f}s minimum -- the rate was used, since a shot cut to the "
        "beat is not a fragment",

    "cut.rateNeedsPictures":
        "a cut rate only applies when cutting from pictures; this edit follows "
        "the words, so the rate was not used -- pick a montage format or turn on "
        "\"Cut from pictures\"",

    # --- describing the video in words -------------------------------------
    "story.settingsApplied":
        "taken from your description: {settings}",
    "story.noRunningOrder":
        "your description does not list shots in order, so it was used for the "
        "settings only -- write \"opens with X, then Y\" to set a running order",
    "story.beatUnfilled":
        "nothing in the footage matched \"{beat}\", so that shot is not in the "
        "edit and its time went to the others",
    "story.assembled":
        "{matched} of {total} described shots were found in the footage",
    "story.nothingMatched":
        "none of the described shots were found in the footage, so the edit was "
        "assembled the ordinary way instead",
    "story.noModel":
        "the vision model is not available, so the description could not be used "
        "-- the edit was assembled the ordinary way",
    "story.noVisualSpans":
        "a description needs pictures to match against; add --visual, or pick "
        "footage the analyser can read",

    # --- subtitles ---------------------------------------------------------
    "subtitles.written":
        "{count} subtitle(s) written to {file} -- File > Import in Premiere puts "
        "them on a caption track",
    "subtitles.failed":
        "{file}: could not be transcribed, so it has no subtitles ({detail}) -- "
        "the edit itself is unaffected",
    "subtitles.none":
        "no subtitles were written: nothing in the finished edit has a transcript",

    # --- language ----------------------------------------------------------
    "language.uncertain":
        "{file}: only {confidence:.0%} sure this is {language} -- set the language in the "
        "panel if that is wrong",
}




def render(key: str, params: dict) -> str:
    template = CATALOGUE.get(key)
    if template is None:
        # An unknown key is a programming error, but a warning that fails to
        # render is worse than an ugly one -- the editor still needs to know.
        return f"{key} {params}".strip()
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return f"{key} {params}".strip()


class Note(str):
    """A warning that is its own English text, and also knows what it is.

    Subclassing `str` rather than wrapping one is deliberate: warnings are
    collected, joined, searched and printed as strings all over the engine and
    its tests. Making the translatable form a string means none of that had to
    change, and a caller that has not been converted keeps working.
    """

    key: str
    params: dict

    def __new__(cls, key: str, params: dict | None = None) -> "Note":
        params = dict(params or {})
        obj = super().__new__(cls, render(key, params))
        obj.key = key
        obj.params = params
        return obj

    @property
    def message(self) -> str:
        return str(self)

    def to_dict(self, code: str, media_id: str | None = None) -> dict:
        d: dict = {"code": code, "message": str(self), "messageKey": self.key}
        if self.params:
            d["params"] = dict(self.params)
        if media_id:
            d["mediaId"] = media_id
        return d


def note(key: str, **params) -> Note:
    return Note(key, params)


def trimmed_key(strategy: str) -> str:
    """Which of the two trimmed-to-target sentences applies."""
    return "length.trimmedWorst" if strategy == "worst" else "length.trimmedTail"
