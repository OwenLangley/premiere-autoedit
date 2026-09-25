"""Command line entry point for the analysis engine.

    autoedit recipes
    autoedit probe   <media>...
    autoedit plan    --job EP042 --recipe podcast-2cam --media a.mov b.mov
    autoedit validate plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import progress
from .detect import plan_cuts
from .plan import EditPlanBuilder, MediaEntry, validate_plan
from .probe import ProbeError, content_hash, needs_proxy, probe
from .recipe import RecipeError, list_recipes, load_recipe
from .transcribe import (
    LISTENS, TranscriptionError, extract_audio, get_provider,
)
from .transcript import Transcript
from .options import (PACING, VALID_CUT_RATES, JobOptions, OptionError, apply_pacing,
                      fit_duration_across,
                      working_frame_size, ASPECT_LABELS)
from .preset import write_preset
from .music import BeatGrid, MusicError, detect_beats
from .notes import Note, note
from .proxy import proxy_path
from .story import DISTRACTORS, assign_beats, build_story_plans, parse_prompt
from .thumbs import build_thumb, sample_point, thumb_path
from .timebase import MAX_SEQUENCE_FPS, Timebase, choose_timebase, holds_exactly
from .visual import (
    Measurements, VisualError, analyse as analyse_visual, measure as measure_visual,
    measurement_key, plan_visual_cuts,
)


# Above this share of detail falling outside the centre crop, the shot is worth
# a human look. Tuned to flag a minority of shots -- a marker on everything is
# the same as no markers at all.
CROP_RISK_THRESHOLD = 0.35
# How many alternates one source may contribute. A long clip can yield hundreds
# of usable spans, and nobody scans hundreds -- but a plan carrying them all is
# megabytes the panel parses on every refresh. Best-scoring survive, and the
# editor is told when the list was cut short rather than left to wonder why a
# shot they remember is missing.
MAX_CANDIDATES_PER_MEDIA = 40

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac", ".ogg", ".mp4", ".mov"}


def _find_music_bed(search_dir: Path, exclude: set[Path]) -> tuple[Path | None, list[Path]]:
    """Look for a single audio-only file to use as the music bed.

    Only ever returns a file when there is exactly ONE candidate. Guessing between
    several would silently score the promo with the wrong track, which is worse
    than asking. Extensions are not trusted -- a music file exported as .mp4 with
    no video stream is common, and is exactly what turned up in testing.

    @returns (the bed if unambiguous, all candidates found)
    """
    candidates: list[Path] = []
    if not search_dir.is_dir():
        return None, []

    for entry in sorted(search_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.resolve() in exclude or entry.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            info = probe(entry)
        except ProbeError:
            continue
        if info.has_audio and not info.has_video:
            candidates.append(entry)

    return (candidates[0] if len(candidates) == 1 else None), candidates


def _library_fingerprints(music_root: Path, cache_root: Path):
    """Whatever the helper has already listened to. Returns (by name, total seen).

    The values are cache PATHS, not loaded fingerprints. A hundred-track library
    is about 45MB of landmarks and the match needs only the best two scores, so
    they are read one at a time and dropped again rather than all held at once.

    Only what is CACHED. Fingerprinting the library belongs in the helper's
    background pass, not in front of an editor who pressed Create. A
    part-indexed library still answers -- it can only fail to find a match,
    never find a wrong one.
    """
    from . import fingerprint as fp

    found, total = {}, 0
    for entry in sorted(music_root.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        total += 1
        try:
            cached = fp.cache_path(cache_root, content_hash(entry))
        except OSError:
            continue
        if cached.exists():
            found[entry.name] = cached
    return found, total


def _read_fingerprints(paths: "dict[str, Path]"):
    """Load them one at a time, skipping any the cache has lost."""
    from . import fingerprint as fp

    for name, path in paths.items():
        cached = fp.load(path)
        if cached is not None:
            yield name, cached


def _music_from_reference(reference: Path, music_root: Path, cache_root: Path,
                          builder) -> Path | None:
    """The library track the reference video is playing, if it is one of them.

    An editor cutting to a reference usually wants the reference's own music,
    and they already have the file. Measured on the real 103-track library: the
    right track scored 1470-4452 through speech and heavy noise, and the best
    WRONG answer across ten held-out tracks scored 8. `fingerprint.py` has the
    table.

    Returns None whenever it is not certain, which is an ordinary outcome: the
    reference may use music nobody here owns.
    """
    from . import fingerprint as fp

    library, total = _library_fingerprints(music_root, cache_root)
    if not library:
        if total:
            builder.add_warning("music", note("music.referenceNotIndexed",
                                              done=0, total=total))
        return None

    try:
        query = fp.fingerprint(reference, seconds=fp.QUERY_SECONDS)
    except fp.FingerprintError as exc:
        print(f"  music: could not listen to the reference ({exc})", file=sys.stderr)
        return None

    match = fp.identify(query, _read_fingerprints(library))
    if match is None or match.score < fp.MATCH_FLOOR:
        if len(library) < total:
            builder.add_warning("music", note("music.referenceNotIndexed",
                                              done=len(library), total=total))
        return None
    if not match.decisive:
        # Two tracks that both look like the reference, which in practice means
        # two copies of one song. Refusing to choose is what `_find_music_bed`
        # does with two candidate beds, and for the same reason.
        builder.add_warning("music", note("music.referenceAmbiguous",
                                          name=match.name, other="another track"))
        print(f"  music: {match.name} scored {match.score} but so did another "
              f"({match.runner_up}) -- not choosing", file=sys.stderr)
        return None

    picked = music_root / match.name
    print(f"  music: the reference is playing {match.name} "
          f"(score {match.score} against {match.runner_up})", file=sys.stderr)
    builder.add_warning("music", note("music.fromReference", name=match.name))
    return picked if picked.is_file() else None


def root_id(index: int) -> str:
    """The name a root is known by in the plan.

    `media` for the first, `media2`, `media3`... after it. The first keeps its
    old name so plans written before there was more than one root still read,
    and so the common case -- one folder of rushes -- says `media` rather than
    something with a number in it.
    """
    return "media" if index == 0 else f"media{index + 1}"


def _relative_to_root(
    path: Path, media_roots: "list[Path]", music_root: Path | None
) -> tuple[str, str]:
    """Express `path` against whichever configured root contains it.

    A music library sits outside the footage tree, so without its own root the
    plan could only record a bare filename -- and the panel, resolving everything
    against the media root, would then fail to find it. The same is now true of
    footage on a second drive, which is why there is a list here rather than one
    root: an editor keeping this shoot on the desktop and last month's on an
    external is the ordinary case, not an exotic one.

    LONGEST root first, so a root nested inside another does not lose its files
    to the outer one. Falling back to the bare name is kept for the un-rooted
    case, but it is the lossy branch, not the norm.
    """
    candidates = [(music_root, "music")] if music_root else []
    candidates += [(r, root_id(i)) for i, r in enumerate(media_roots) if r]
    for root, label in sorted(candidates, key=lambda c: -len(str(c[0]))):
        if root == path.parent or root in path.parents:
            return str(path.relative_to(root)), label
    return path.name, "media"


# Below this, a shift is not something anyone hears -- roughly a frame at 50fps.
AUDIBLE_SHIFT = 0.02

# Whisper is usually near-certain about the language within a few seconds of
# speech. Anything much below this is worth an editor's attention.
MIN_LANGUAGE_CONFIDENCE = 0.75


@dataclass(frozen=True)
class MusicChunk:
    """Which part of the track ends up under the picture."""
    start: float
    length: float
    warnings: tuple[Note, ...] = ()


def resolve_music_chunk(
    track_duration: float,
    picture_seconds: float,
    start: float = 0.0,
    length: float | None = None,
    beats: "BeatGrid | None" = None,
    snap: bool = True,
) -> MusicChunk:
    """Work out the bed's source range, and say out loud where it disagrees.

    Two rules, and the difference between them matters:
      * no explicit length -> the bed follows the picture, so it never hangs past
        the last frame.
      * an explicit length -> that length wins, even past the picture. Choosing a
        chunk is choosing a span of music, and silently shortening it would
        defeat the point of picking one.
    """
    notes: list[Note] = []

    start = max(0.0, start)
    if start >= track_duration:
        notes.append(note("music.startPastEnd", start=start, duration=track_duration))
        start = 0.0
    elif snap and beats and beats.beats:
        # Shot lengths are already whole multiples of the beat interval, so a bed
        # that begins exactly on a beat phase-aligns the entire cut grid to what
        # is audible. Parking by ear lands within a fraction of a beat.
        snapped = beats.snap(start)
        if 0 <= snapped < track_duration:
            moved = abs(snapped - start)
            start = snapped
            # Only say so when the move is audible. Parking the playhead already
            # lands within milliseconds of a beat most of the time, and "start
            # moved 0.00s" is noise in a warning list an editor has to read.
            if moved >= AUDIBLE_SHIFT:
                notes.append(note("music.snappedToBeat", moved=moved, start=snapped))

    available = track_duration - start
    wanted = length if length else picture_seconds
    chunk = min(wanted, available)

    if length and chunk < length - 1e-4:
        notes.append(note(
            "music.chunkClamped",
            wanted=length, start=start, available=available, actual=chunk,
        ))
    if length and chunk > picture_seconds + 1e-4:
        notes.append(note("music.runsPastPicture", overhang=chunk - picture_seconds))
    elif chunk < picture_seconds - 1e-4:
        # Deliberately NOT gated on an explicit length. A bed that follows the
        # picture can still fall short, because the track simply is not long
        # enough -- a 48s song under a 138s cut -- and that is the case where the
        # editor most needs telling, since they never asked for a short bed.
        notes.append(note("music.stopsEarly", shortfall=picture_seconds - chunk))

    return MusicChunk(start=start, length=max(0.0, chunk), warnings=tuple(notes))


def _brandkit_lut(key: str) -> dict | None:
    """Look up a LUT in the brand kit. Returns None when absent, so a missing look
    degrades to 'no grade applied' with a warning rather than failing the job."""
    kit = Path(__file__).resolve().parents[2] / "brandkit" / "brandkit.json"
    if not kit.exists():
        return None
    try:
        return (json.loads(kit.read_text()).get("luts") or {}).get(key)
    except (json.JSONDecodeError, OSError):
        return None


def _options_from_args(args) -> JobOptions:
    """CLI flags -> the same JobOptions a panel request produces.

    Both entry points funnel through one object so the terminal and the panel
    cannot drift into behaving differently.
    """
    return JobOptions(
        aspect=getattr(args, "aspect", None) or "source",
        duration_mode=getattr(args, "duration_mode", None)
        or ("upTo" if getattr(args, "duration", None) else "none"),
        duration_seconds=getattr(args, "duration", None),
        pacing=getattr(args, "pacing", None) or "standard",
        cut_rate=getattr(args, "cut_rate", None),
        look=getattr(args, "look", None),
        music="none" if getattr(args, "no_music", False) else "auto",
        visual=bool(getattr(args, "visual", False)),
        model=getattr(args, "model", None),
        language=getattr(args, "language", None),
    )


def _transcribe_once(
    path, mid, info, args, recipe, provider, provider_name, cache_root, work_dir,
):
    """One clip's transcript, from cache when it can be.

    Pulled out of the speech branch so the picture branch can use it too. A
    montage of footage with people talking in it can be subtitled -- the words
    just do not decide where the cuts go. Before this, asking for subtitles on
    a montage produced nothing at all and said nothing about why.

    Raises TranscriptionError; the caller decides whether that is fatal.
    """
    tx_options = dict(recipe.transcription)
    tx_options["duration"] = info.duration
    tx_options["media_path"] = str(path)
    if args.model:
        tx_options["model"] = args.model
    if args.language:
        tx_options["language"] = args.language
    if getattr(args, "transcript", None):
        tx_options["path"] = args.transcript

    # Cache transcripts on content hash + provider settings. Transcription is
    # by far the slowest step and recipe tuning is an iterative loop -- without
    # this, nudging min_silence by 0.05s means re-transcribing hours of rushes.
    cache_key = _transcript_cache_key(content_hash(path), provider_name, tx_options)
    cache_file = cache_root / "transcripts" / f"{cache_key}.json"

    if cache_file.exists() and not args.no_cache:
        print(f"  {path.name}: transcript from cache", file=sys.stderr)
        transcript = Transcript.from_dict(json.loads(cache_file.read_text()))
        transcript.media_id = mid
        return transcript

    print(f"  {path.name}: extracting audio", file=sys.stderr)
    wav = extract_audio(path, work_dir / f"{mid}.wav")
    print(f"  {path.name}: transcribing via {provider_name}", file=sys.stderr)
    transcript = provider.transcribe(wav, mid, tx_options)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(transcript.to_dict(), indent=2))
    return transcript


def _transcript_cache_key(media_hash: str, provider: str, options: dict) -> str:
    """Key a cached transcript on the media plus anything that would change it.

    Deliberately ignores recipe cut settings: those are applied after transcription,
    so tuning them must not invalidate the cache -- that is the entire point.
    """
    relevant = {k: options.get(k) for k in ("model", "language", "vad_filter", "diarize")}
    stamp = json.dumps([media_hash, provider, relevant], sort_keys=True)
    return hashlib.sha256(stamp.encode()).hexdigest()[:24]


def _assemble_story(story, story_spans, collected, builder, options, detection, cache_root):
    """Rebuild the running order from the editor's sentence.

    Returns the plans to append, in beat order, and a map from each CutPlan's
    identity to the beat that produced it -- identity rather than media id,
    because one file can serve several beats and each occurrence needs its own
    section.

    Falls back to `collected` untouched whenever the story cannot be honoured:
    no model, no analysed spans, or nothing matched. A prompt that cannot be
    served should produce the ordinary edit and a warning, not an empty
    sequence.
    """
    from . import describe

    if not story_spans:
        builder.add_warning("story", note("story.noVisualSpans"))
        return collected, {}

    if not describe.prompt_available(cache_root):
        builder.add_warning("story", note("story.noModel"))
        return collected, {}

    try:
        vectors, kept = describe.embed_stills_cached(
            [sp[4] for sp in story_spans], cache_root)
        # The multilingual tower, because these are the editor's own words and
        # they are not always English. The distractors go through the same tower
        # so the competition is like for like -- a beat scored by one encoder
        # against distractors scored by another compares nothing meaningful.
        beat_vectors = describe.embed_prompt([b.text for b in story.beats], cache_root)
        distractors = describe.embed_prompt(list(DISTRACTORS), cache_root)
    except describe.DescribeError as exc:
        builder.add_warning("story", note("story.noModel"))
        print(f"  story: {exc}", file=sys.stderr)
        return collected, {}

    usable = [story_spans[i] for i in kept]
    matches = assign_beats(beat_vectors, vectors, distractors, story.beats)

    # Every beat nothing matched is named. Not filled with the best-scoring
    # leftover: an edit that confidently tells the wrong story is worse than a
    # short one that admits what it could not find.
    plans, unmatched = build_story_plans(
        matches,
        [(mid, start, end, score) for mid, start, end, score, _ in usable],
        options.duration_seconds if options.duration_mode != "none" else None,
        min_clip_length=detection.min_clip_length,
    )
    for m in unmatched:
        builder.add_warning("story", note("story.beatUnfilled", beat=m.beat.text))

    if not plans:
        builder.add_warning("story", note("story.nothingMatched"))
        return collected, {}

    tracks = {mid: (v, a) for mid, _, v, a in collected}
    rebuilt, sections = [], {}
    for media_id, plan, beat_id in plans:
        v, a = tracks.get(media_id, (0, 0))
        rebuilt.append((media_id, plan, v, a))
        # The plan already carries its own section (build_story_plans sets it).
        # This map is kept only for the caller's signature; the id() keying it
        # used to rely on did not survive duration fitting or speech protection.
        sections[id(plan)] = beat_id

    # Record every described shot, in order, including the ones nothing served.
    # A panel that lists only what worked cannot show an editor what is missing.
    shots_per_beat = {m.beat.id: len(m.shots) for m in matches}
    for beat in story.beats:
        builder.add_section(beat.id, beat.text, beat.weight,
                            shots_per_beat.get(beat.id, 0))

    matched = len(story.beats) - len(unmatched)
    print(f"  story: {matched}/{len(story.beats)} beats matched, "
          f"{len(rebuilt)} clip group(s)", file=sys.stderr)
    builder.add_warning("story", note(
        "story.assembled", matched=matched, total=len(story.beats)))
    return rebuilt, sections


def _assemble_reference(
    reference, story_spans, collected, builder, options, detection, cache_root,
    match_content: bool = True, heard_by_media: dict | None = None,
):
    """Lay the editor's footage out in the reference video's shape.

    Mirrors `_assemble_story`: same shot pool, same allocator, same reporting.
    Two things differ, and both come from the reference being a video rather
    than a sentence.

    **The beats are weighted by the reference's own shot lengths**, so
    `build_story_plans` reproduces its pacing -- a half-second cut stays a
    half-second cut. `Beat.weight` has been threaded end to end since the story
    feature shipped and has been 1.0 every time; this is what it was for.

    **Matching is image to image**, which needs its own floor. The text
    distractors the story path competes against sit at 0.23 against pictures
    while image-to-image sits at 0.55 and up, so they lose every comparison and
    the "not in this footage" answer disappears. See `reference.py` for the
    measurement.

    Falls back to `collected` untouched whenever the reference cannot be
    honoured, exactly as the story path does.
    """
    from . import describe
    from .reference import SPOKEN_ROLES, beats_from, match_shots, reference_report
    from .story import MAX_SHOTS_PER_BEAT, BeatMatch

    if not story_spans:
        builder.add_warning("reference", note("reference.noVisualSpans"))
        return collected, {}

    # The IMAGE tower only. `prompt_available` also demands the multilingual text
    # model, which this path never uses -- requiring it would refuse the job over
    # a model it does not need.
    if not describe.available(cache_root):
        builder.add_warning("reference", note("reference.noModel"))
        return collected, {}

    beats = beats_from(reference)
    if not beats:
        builder.add_warning("reference", note("reference.noShots"))
        return collected, {}

    try:
        footage, kept = describe.embed_stills_cached(
            [sp[4] for sp in story_spans], cache_root)
        ref_vectors, ref_kept = describe.embed_stills_cached(
            reference.stills, cache_root) if reference.stills else (None, [])
    except describe.DescribeError as exc:
        builder.add_warning("reference", note("reference.noModel"))
        print(f"  reference: {exc}", file=sys.stderr)
        return collected, {}

    usable = [story_spans[i] for i in kept]
    spans = [(mid, start, end, score) for mid, start, end, score, _ in usable]
    if not spans:
        builder.add_warning("reference", note("reference.noVisualSpans"))
        return collected, {}

    # Which reference shot each vector belongs to: a shot whose still would not
    # build has no vector, and zipping against the wrong list is how the caption
    # feature once matched 28 candidates and 0 clips.
    with_stills = [i for i, sh in enumerate(reference.shots) if sh.still]
    vector_of = {with_stills[row]: row for row in ref_kept} if ref_kept else {}

    # Which reference shots hold on someone talking, and which footage spans
    # are someone talking. The first is free -- it is read off the reference's
    # own cutting. The second needs a transcript, which exists only when the job
    # was already going to make one (subtitles, protect-speech). No transcript
    # means no bias, an ordinary visual match, and nothing said about it:
    # transcribing 62 minutes of footage is not something to start behind
    # somebody's back.
    # `with_stills` and `ref_kept` are the same indirection `vector_of` uses
    # above: ref_vectors[k] is the still of reference.shots[with_stills[ref_kept[k]]].
    # Getting this wrong is how the caption feature once matched 28 candidates
    # and 0 clips, so it is spelled out rather than re-derived.
    wants_speech = [reference.shots[with_stills[row]].role in SPOKEN_ROLES
                    for row in ref_kept] if ref_kept else []

    spoken_spans: list[bool] | None = None
    if heard_by_media:
        from .speech import is_spoken
        spoken_spans = [
            bool(heard_by_media.get(mid) and is_spoken(heard_by_media[mid], st, en))
            for mid, st, en, _ in spans
        ]
        talking = sum(spoken_spans)
        builder.add_warning("reference", note(
            "reference.roles",
            holds=sum(1 for x in wants_speech if x), talking=talking))

    matched_by_beat: dict[int, int] = {}
    alternates_by_beat: dict[int, list[int]] = {}
    report = None
    if match_content and ref_vectors is not None and len(ref_vectors):
        found = match_shots(ref_vectors, footage,
                            wants_speech=wants_speech or None,
                            is_spoken=spoken_spans)
        report = reference_report(found)
        for beat_index, row in vector_of.items():
            hit = found[row]
            if hit.matched:
                matched_by_beat[beat_index] = hit.footage_index
                alternates_by_beat[beat_index] = list(hit.alternates)

    # Nothing matched on content: the reference is simply of something else. Its
    # RHYTHM is still worth copying, and that is the reliable half of this
    # feature -- so the shots are filled by quality, in order, and the editor is
    # told that is what happened rather than being handed a short edit.
    rhythm_only = not matched_by_beat
    if rhythm_only:
        by_quality = sorted(range(len(spans)), key=lambda i: -spans[i][3])
        for n, beat_index in enumerate(range(len(beats))):
            if by_quality:
                matched_by_beat[beat_index] = by_quality[n % len(by_quality)]

    # How many spans each section needs to hold for as long as its reference
    # shot did. One span per shot was a hard ceiling on the whole edit at
    # (shots) x (longest span): with the footage cut into ~5s spans, a
    # 28-minute reference could not produce more than a few minutes of picture,
    # and reported "not enough usable material" while sitting on 62 minutes of
    # it. The extra spans are the ones that also resemble that reference shot;
    # `fit_duration_across` trims back to the share, so over-providing is safe
    # and under-providing is not.
    # The length the EDITOR asked for, not the reference's own. They are the
    # same number whenever no length was chosen -- the reference block above
    # adopts its duration as the default -- but when someone asks for 28
    # minutes against a 4-minute reference, this path used to lay out four
    # minutes and the reference silently overrode the form.
    target = (options.duration_seconds
              if options.duration_mode != "none" and options.duration_seconds
              else reference.duration)

    total_weight = sum(b.weight for b in beats) or 1.0
    mean_span = (sum(e - st for _, st, e, _ in spans) / len(spans)) if spans else 1.0
    per_beat_cap = MAX_SHOTS_PER_BEAT
    matches = []
    for i, beat in enumerate(beats):
        if i not in matched_by_beat:
            matches.append(BeatMatch(beat=beat, shots=[]))
            continue
        share = target * beat.weight / total_weight
        need = max(1, math.ceil(share / max(0.5, mean_span)))
        chosen = [matched_by_beat[i]] + alternates_by_beat.get(i, [])[:need - 1]
        per_beat_cap = max(per_beat_cap, len(chosen))
        matches.append(BeatMatch(beat=beat, shots=chosen))

    # The reference's own length is the target, so its shot shares come out as
    # its shot lengths. A tight tolerance because the lengths ARE the thing being
    # copied -- the story path's loose 0.35 exists for a described running order,
    # where trimming every beat to the millisecond would cut mid-gesture.
    plans, unmatched = build_story_plans(
        matches, spans, target,
        min_clip_length=detection.min_clip_length, tolerance=0.08,
        max_shots_per_beat=per_beat_cap,
    )
    for m in unmatched:
        builder.add_warning("reference", note("reference.shotUnfilled", shot=m.beat.text))

    if not plans:
        builder.add_warning("reference", note("reference.nothingMatched"))
        return collected, {}

    tracks = {mid: (v, a) for mid, _, v, a in collected}
    rebuilt, sections = [], {}
    for media_id, plan, beat_id in plans:
        v, a = tracks.get(media_id, (0, 0))
        rebuilt.append((media_id, plan, v, a))
        sections[id(plan)] = beat_id

    shots_per_beat = {m.beat.id: len(m.shots) for m in matches}
    for beat in beats:
        builder.add_section(beat.id, beat.text, beat.weight, shots_per_beat.get(beat.id, 0))

    for w in reference.warnings:
        builder.add_warning("reference", w)
    if rhythm_only:
        builder.add_warning("reference", note("reference.rhythmOnly"))
    elif report:
        # The band, on every job. The floor is absolute and absolute floors do
        # not transfer between libraries; printing what was actually seen is how
        # a wrong one becomes visible instead of becoming a quietly ignored
        # reference.
        builder.add_warning("reference", note(
            "reference.matched", matched=report["matched"], shots=report["shots"],
            best=report["best"], worst=report["worst"], floor=report["floor"]))
        if report["reused"]:
            builder.add_warning("reference", note(
                "reference.reused", count=report["reused"]))

    print(f"  reference: {len(plans)} clip group(s) from {len(beats)} shot(s)"
          f"{' (rhythm only)' if rhythm_only else ''}", file=sys.stderr)
    return rebuilt, sections


def _media_id(index: int, path: Path, taken: "dict[str, Path] | None" = None) -> str:
    """A short, safe id for a clip, unique within the plan.

    Derived from the filename because an id shows up in subclip names, warnings
    and the panel, and `C0001` is recognisable where `M007` is not.

    Uniqueness is not optional once footage can come from several drives. Two
    cards both holding `C0001.MP4` are ordinary, and without this the second
    silently replaced the first in the plan -- one media entry, one clip, and
    the other shoot simply absent with nothing said.
    """
    stem = "".join(c for c in path.stem if c.isalnum() or c in "_-")[:24]
    base = stem or f"M{index:03d}"
    if taken is None or taken.get(base) in (None, path):
        return base
    for n in range(2, 100):
        candidate = f"{base}_{n}"
        if taken.get(candidate) in (None, path):
            return candidate
    return f"M{index:03d}"


def cmd_recipes(args) -> int:
    for name in list_recipes():
        r = load_recipe(name)
        print(f"{name}")
        print(f"    {r.description.strip().splitlines()[0] if r.description else ''}")
        print(f"    {r.sequence.timebase}, V{r.sequence.video_tracks}/A{r.sequence.audio_tracks}, "
              f"min_silence={r.detection.min_silence}s, filler={r.detection.filler_mode}")
    return 0


def cmd_probe(args) -> int:
    for f in args.media:
        try:
            info = probe(f)
        except ProbeError as exc:
            print(f"{f}: {exc}", file=sys.stderr)
            return 1
        print(f"{Path(f).name}")
        turned = f"  (stored {info.width}x{info.height}, rotated {info.rotation})" if info.turned else ""
        print(
            f"    {info.duration:.2f}s  {info.display_width}x{info.display_height}"
            f"  {info.timebase or 'audio only'}{turned}"
        )
        print(f"    video={info.has_video} audio={info.has_audio} "
              f"ch={info.audio_channels}@{info.audio_rate} codec={info.codec or '-'}")
        if info.start_timecode:
            print(f"    start TC: {info.start_timecode}")
        for w in info.warnings:
            print(f"    WARNING: {w}")
    return 0


def cmd_validate(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    errors = validate_plan(plan)
    if errors:
        print(f"{args.plan}: {len(errors)} problem(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"{args.plan}: valid")
    return 0


def cmd_plan(args) -> int:
    try:
        recipe = load_recipe(args.recipe)
    except RecipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        options = _options_from_args(args)
    except OptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Pacing scales the recipe rather than replacing it, so these are what the
    # rest of the run uses -- never recipe.detection / recipe.visual directly.
    detection, visual = apply_pacing(recipe.detection, recipe.visual, options.pacing)

    if options.pacing != "standard":
        print(
            f"  pacing: {options.pacing} "
            f"(min_silence {detection.min_silence:.2f}s, shot {visual.shot_duration:.2f}s, "
            f"fillers {detection.filler_mode})",
            file=sys.stderr,
        )

    media_roots = [Path(r).resolve() for r in (args.media_root or [])]
    # The first is still "the" media root wherever one path is wanted -- proxy
    # search, the fallback for un-rooted files -- and the rest are additions.
    media_root = media_roots[0] if media_roots else None
    music_root = Path(args.music_root).resolve() if getattr(args, "music_root", None) else None
    provider_name = args.provider or recipe.transcription.get("provider", "sidecar")
    try:
        provider = get_provider(provider_name)
    except TranscriptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Subtitling needs something that can listen. A recipe written for footage
    # nobody expected to transcribe leaves the provider unset, which defaults to
    # sidecar -- so asking for subtitles on a silent promo asked a file reader to
    # transcribe audio and got six "no sidecar transcript" warnings and no
    # subtitles. An explicit --provider is still obeyed: choosing sidecar and
    # not having one is a different mistake, and the editor's to make.
    speech_provider_name = provider_name
    speech_provider = provider
    if not args.provider and provider_name not in LISTENS:
        speech_provider_name = "whisper-local"
        speech_provider = get_provider(speech_provider_name)

    # Probe before building. The sequence rate used to come straight from the
    # recipe, which meant 59.94 footage was assembled into a 30.000 sequence --
    # two grids that share almost no frame boundaries, so every clip landed a
    # frame out and the joins showed as black flashes. The footage is the fact;
    # the recipe's rate is a preference that has to give way when it cannot hold
    # the material exactly.
    probes: list[Any] = []
    for raw in args.media:
        try:
            probes.append(probe(Path(raw).resolve()))
        except ProbeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # An editor's own choice is a delivery spec and is not negotiable: when they
    # say 25 for broadcast, footage that does not land on 25 is a fact to report,
    # not a reason to hand them 59.94. Without --fps the recipe's rate is a
    # preference and the footage may still displace it.
    chosen_fps = getattr(args, "fps", None)
    timebase_notes: list[tuple[Note, str | None]] = []
    sources = [p.timebase for p in probes if p.has_video and p.timebase]
    if chosen_fps and chosen_fps != "auto":
        try:
            tb = Timebase.parse(str(chosen_fps))
        except (ValueError, ZeroDivisionError):
            print(f"error: unusable frame rate {chosen_fps!r}", file=sys.stderr)
            return 2
        if tb.fps > MAX_SEQUENCE_FPS + 1e-6:
            print(f"error: {tb.fps:.3f} fps is above the {MAX_SEQUENCE_FPS:.0f} fps "
                  f"Premiere will create a sequence at", file=sys.stderr)
            return 2
        displaced = None
        timebase_notes.append((note("timebase.chosen", chosen=f"{tb.fps:.3f}"), None))
    else:
        tb, displaced = choose_timebase(recipe.sequence.timebase, sources)
    if displaced is not None:
        print(f"  sequence: {tb} to match the footage (recipe asks {displaced})", file=sys.stderr)
        timebase_notes.append((note(
            "timebase.followedFootage", chosen=f"{tb.fps:.3f}", recipe=f"{displaced.fps:.3f}"
        ), None))

    misfits = [
        Path(raw).name
        for raw, p in zip(args.media, probes)
        if p.has_video and p.timebase and not holds_exactly(tb, p.timebase)
    ]
    if misfits:
        timebase_notes.append((note(
            "timebase.mixedRates", count=len(misfits), chosen=f"{tb.fps:.3f}",
            files=", ".join(misfits[:3]) + (" and others" if len(misfits) > 3 else ""),
        ), None))

    builder = EditPlanBuilder(
        job_id=args.job,
        recipe=recipe.name,
        timebase=tb,
        sequence_name=recipe.sequence_name(args.job),
        video_tracks=recipe.sequence.video_tracks,
        audio_tracks=recipe.sequence.audio_tracks,
    )

    # id -> the path it was given to, so a second drive's C0001.MP4 gets its own
    # id rather than overwriting the first.
    media_ids: dict[str, Path] = {}
    silent: list[str] = []
    visual_used = False
    # Shots that passed the quality gates, across every file. Distinguishes
    # "the footage was rejected" from "the footage was fine and the cuts were
    # not", which are opposite problems with opposite fixes.
    usable_shots = 0
    # What the run is made of, weighted by the seconds of video each part has to
    # decode. See progress.py for why duration is the weight and why the bar
    # moves between passes rather than through them.
    reference_seconds = 0.0
    if getattr(args, "reference", None):
        try:
            reference_seconds = probe(Path(args.reference)).duration
        except ProbeError:
            # Weightless rather than fatal: analyse_reference is about to probe
            # the same file and report the failure in terms an editor can act on.
            reference_seconds = 0.0
    footage_seconds = sum(p.duration for p in probes)
    units: list[tuple[str, float]] = []
    if getattr(args, "reference", None):
        units.append(("reference", reference_seconds))
    units += [(f"media:{i}", p.duration) for i, p in enumerate(probes)]
    # Assembly decodes nothing, but it is not free and most of what it costs is
    # a CONSTANT, so it cannot be a percentage of the material. Measured on a
    # two-file job with a reference: 17.5s in total, of which describe.available
    # -- loading the shot recogniser -- was 10.7s. That 10.7s is the same 10.7s
    # on a three-hour job.
    #
    # So it is expressed in the same unit as everything else here, seconds of
    # video, using the scanning rate the visual.py measurements imply: a
    # 27-minute reference scans in under two minutes, about 13x faster than
    # real time. Ten seconds of fixed cost is therefore worth roughly 130
    # seconds of material, plus a small share for the work that does scale.
    ASSEMBLE_FIXED_SECONDS = 130.0
    units.append(("assemble",
                  ASSEMBLE_FIXED_SECONDS + (reference_seconds + footage_seconds) * 0.05))
    progress.begin(units)

    # The editor's sentence, read once. `beats` here would collide with the
    # musical beat grid that is live throughout this function, so the story's
    # units keep their own name everywhere: sections.
    reference = None
    if getattr(args, "reference", None):
        from .reference import ReferenceError, analyse_reference

        progress.unit("reference")
        progress.step("progress.reference", Path(args.reference).name)
        print(f"  reference: reading {Path(args.reference).name}", file=sys.stderr)
        try:
            reference = analyse_reference(
                args.reference, Path(args.work_dir or ".autoedit-cache"),
                no_cache=getattr(args, "no_cache", False))
        except ReferenceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"  reference: {reference.cut_count} shots in {reference.duration:.1f}s"
              f"{f', {reference.aspect}' if reference.aspect else ''}", file=sys.stderr)
        # Matching a reference means matching its pictures, so the picture path
        # is implied exactly as a description implies it. Requiring the editor to
        # tick a box as well would accept the reference and silently ignore it.
        args.visual = True

    # What the reference implies about the film. Applied BEFORE the description,
    # so a description still wins -- the editor typed that in this session, and
    # the reference is an example they are borrowing from.
    if reference is not None:
        taken = []
        if reference.aspect and options.aspect == "source":
            options = replace(options, aspect=reference.aspect)
            taken.append(reference.aspect)
        if options.duration_mode == "none":
            options = replace(options, duration_mode="exactly",
                              duration_seconds=round(reference.duration, 2))
            taken.append(f"{reference.duration:.0f}s")
        if taken:
            builder.add_warning("reference", note(
                "reference.settingsTaken", settings=", ".join(taken)))

    story = parse_prompt(args.story) if getattr(args, "story", None) else None
    # An edit worth subtitling is one where what is said matters, so cutting
    # through the middle of a sentence is never what was wanted. Checked again
    # after the description is read, because the description can ask for
    # subtitles too and this must hold however the ask arrived.
    if args.subtitles:
        args.protect_speech = True
    story_spans: list[tuple[str, float, float, float, Path]] = []
    # Transcripts made ONLY to subtitle a montage. Deliberately not added to the
    # plan: the panel hands plan transcripts to Premiere's Text-Based Editing,
    # which rejects them, and a montage would collect one "could not import"
    # warning per clip for a feature its editor never asked for.
    subtitle_only: list[Transcript] = []
    speech_nudges = 0
    speech_stuck = 0
    silence_removed: dict[str, float] = {}
    heard_by_media: dict[str, Transcript] = {}
    durations: dict[str, float] = {}
    if story:
        # What the sentence said about the film itself. The editor typed this in
        # the same breath as everything else, so it wins over the form: a prompt
        # asking for twelve seconds and getting fifteen is the form contradicting
        # the person using it.
        applied = []
        if story.seconds and options.duration_seconds != story.seconds:
            options = replace(options, duration_mode="exactly",
                              duration_seconds=story.seconds)
            applied.append(f"{story.seconds:.0f}s")
        if story.aspect and options.aspect != story.aspect:
            options = replace(options, aspect=story.aspect)
            applied.append(story.aspect)
        if story.visual and not args.visual:
            args.visual = True
            applied.append("from pictures")
        if story.subtitles and not getattr(args, "subtitles", False):
            args.subtitles = True
            applied.append("subtitles")
        if (story.protect_speech or args.subtitles) and not args.protect_speech:
            args.protect_speech = True
            applied.append("whole sentences")
        if story.cut_rate and options.cut_rate != story.cut_rate:
            options = replace(options, cut_rate=story.cut_rate)
            applied.append(f"cut every {story.cut_rate:g}")
        if applied:
            print(f"  story: applied {', '.join(applied)}", file=sys.stderr)
            builder.add_warning("story", note(
                "story.settingsApplied", settings=", ".join(applied)))

        if not story.beats:
            # A description rather than a running order. Its settings have been
            # taken; there is no order to impose, so the ordinary assembly runs.
            print("  story: read as a description, not a shot list", file=sys.stderr)
            builder.add_warning("story", note("story.noRunningOrder"))
            story = None

    # The cut rate reaches the PICTURE path here, after both the form and the
    # prompt have had their say -- doing it earlier read a rate the prompt had
    # not set yet.
    #
    # It only ever set the cut grid in append_cuts and never touched
    # beats_per_shot, so a montage kept cutting one shot per bar however fast the
    # editor asked: "every beat" produced 1.735s shots at 139 BPM, which is
    # exactly four beats. The recipe's value is the default; the editor overrules.
    if options.cut_rate:
        visual = replace(visual, beats_per_shot=float(options.cut_rate))
    collected: list[tuple[str, Any, int, int]] = []
    risky: list[tuple[str, float, float, float]] = []
    builder_warnings: list[str] = []
    cache_root = Path(args.work_dir or ".autoedit-cache")

    # Auto-detection is opt-in per recipe via `auto_music`. Inferring it from a
    # `music` role was wrong: podcast recipes declare one for a bed the editor
    # adds by hand, and a rough cut silently gaining a soundtrack is a surprise
    # nobody wants.
    music_path = Path(args.music).resolve() if args.music else None
    if music_path is not None and not music_path.exists() and music_root:
        # A bare track name is resolved against the library, so callers can pass
        # what the panel shows rather than reconstructing an absolute path.
        candidate = (music_root / args.music).resolve()
        if candidate.is_file():
            music_path = candidate
    if music_path is not None and options.music == "none":
        # --no-music is the more emphatic of the two; honouring --music here would
        # score a cut the editor just asked to be silent.
        print("  music: --no-music overrides --music", file=sys.stderr)
        music_path = None
    if music_path is not None and not music_path.is_file():
        print(f"error: music file not found: {music_path}", file=sys.stderr)
        return 2
    # A reference plus music left on automatic is an editor saying "cut it like
    # this one" without having gone to find the track. If that track is in their
    # library, this is a lookup a machine should do.
    #
    # NOT gated on `recipe.auto_music`, unlike the search below it. That gate
    # exists to stop a stray audio file lying among the rushes becoming a
    # soundtrack nobody asked for; a reference is the opposite of stray, it is
    # the thing the editor chose. It is reported either way, never silent.
    if (music_path is None and options.music == "auto" and music_root
            and getattr(args, "reference", None)):
        progress.step("progress.music")
        music_path = _music_from_reference(
            Path(args.reference), music_root, cache_root, builder)

    if music_path is None and options.music != "none" and recipe.auto_music:
        search_dir = media_root or Path(args.media[0]).resolve().parent
        found, candidates = _find_music_bed(
            search_dir, {Path(m).resolve() for m in args.media}
        )
        if found:
            music_path = found
            print(f"  music: found {found.name} in {search_dir}", file=sys.stderr)
        elif len(candidates) > 1:
            print(
                f"  music: {len(candidates)} audio-only files here "
                f"({', '.join(c.name for c in candidates)}) -- pass --music to choose one",
                file=sys.stderr,
            )

    beats = None
    music_available = 0.0        # how much track there is to cut against
    if music_path:
        try:
            music_info = probe(music_path)
            beats = detect_beats(music_path, music_info.duration, cache_root)
            print(
                f"  music: {beats.bpm:.1f} BPM, {len(beats.beats)} beats, "
                f"confidence {beats.confidence:.2f}",
                file=sys.stderr,
            )
            if beats.octave_ambiguous:
                print(
                    f"  music: half/double tempo is ambiguous here -- if the cut "
                    f"feels twice or half as fast as the track, set beats_per_shot "
                    f"to {max(1, visual.beats_per_shot / 2):g} or "
                    f"{visual.beats_per_shot * 2:g} in the recipe",
                    file=sys.stderr,
                )
                builder_warnings.append(
                    f"tempo read as {beats.bpm:.0f} BPM, but half or double fits "
                    "almost as well -- check the cut against the track"
                )
            if beats.confidence < visual.min_beat_confidence:
                print(
                    f"  music: confidence {beats.confidence:.2f} is low; cuts will "
                    "use fixed-length takes instead of the beat grid",
                    file=sys.stderr,
                )
            # The most bed this job could possibly have: an explicit chunk
            # length, or whatever is left of the track after the start point.
            requested = float(args.music_length) if getattr(args, "music_length", None) else None
            from_start = max(0.0, music_info.duration - float(args.music_start or 0.0))
            music_available = min(requested, from_start) if requested else from_start
        except (MusicError, ProbeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    # How often to cut, in beats. The editor's dial wins outright when they have
    # set one; pacing is only the default. Scaling their explicit "every 2 beats"
    # by the pacing profile would make the control lie about what it does.
    cut_every = options.cut_rate or max(
        1, round(recipe.music.max_shot_beats * PACING[options.pacing].beats)
    )

    # Where the bed begins in the track, snapped the same way resolve_music_chunk
    # will snap it. The cut planner needs this BEFORE the timeline exists,
    # because the beat times it works from are track times and the timeline runs
    # from zero: sequence t is track (bed_start + t).
    bed_start = max(0.0, float(getattr(args, "music_start", None) or 0.0))
    if beats and beats.beats and not getattr(args, "no_music_snap", False):
        snapped = beats.snap(bed_start)
        if snapped >= 0:
            bed_start = snapped

    work_dir = cache_root / args.job
    work_dir.mkdir(parents=True, exist_ok=True)

    for w, mid_for in timebase_notes:
        builder.add_warning("timebase", w, mid_for)

    proxied: list[str] = []
    awaiting_proxy: list[str] = []

    for i, raw in enumerate(args.media):
        path = Path(raw).resolve()
        info = probes[i]
        progress.unit(f"media:{i}")
        progress.step("progress.clip", f"{path.name} ({i + 1}/{len(args.media)})")

        mid = _media_id(i, path, media_ids)
        media_ids[mid] = path
        rel, media_root_name = _relative_to_root(path, media_roots, None)
        role = args.role[i] if args.role and i < len(args.role) else None

        # Point at a proxy if one has been built. The helper makes these in the
        # background, so on a first job there may be none yet -- the plan is
        # still correct, it just will not play smoothly until they land.
        proxy = None
        if needs_proxy(info):
            candidate = proxy_path(cache_root, path)
            if candidate.exists():
                proxy = str(candidate)
                proxied.append(path.name)
            else:
                awaiting_proxy.append(path.name)

        builder.add_media(MediaEntry(
            id=mid, rel_path=rel, root=media_root_name, duration=info.duration,
            hash=content_hash(path), role=role,
            timebase=info.timebase, has_video=info.has_video, has_audio=info.has_audio,
            # Display dimensions, not stored ones: the panel's scale-to-fill maths
            # reasons about the picture, and rotated rushes are stored the other
            # way round. Using the raster made it zoom and crop verticals that
            # were already the right shape.
            width=info.display_width, height=info.display_height, proxy_path=proxy,
        ))
        for w in info.warnings:
            builder.add_warning("probe", w, mid)

        roles_cfg = recipe.roles.get(role or "", {}) if role else {}
        v_track = roles_cfg.get("video_track", 0)
        a_track = roles_cfg.get("audio_track", 0)

        if args.visual or not info.has_audio:
            if not info.has_audio:
                silent.append(path.name)
            visual_used = True
            print(f"  {path.name}: cutting from the pictures", file=sys.stderr)
            # Cache the decode, not the decisions: re-scoring with different
            # thresholds is instant, which is what makes tuning pacing usable.
            # Fraction of the width a centre crop keeps. Only meaningful when the
            # output is narrower than the source; that is when detail gets thrown away.
            centre_ratio = None
            if options.frame_size and info.display_width and info.display_height:
                target = options.frame_size[0] / options.frame_size[1]
                source = info.display_width / info.display_height
                if target < source - 1e-6:
                    centre_ratio = round(target / source, 4)

            vkey = hashlib.sha256(
                json.dumps([content_hash(path), measurement_key(visual), centre_ratio],
                           sort_keys=True).encode()
            ).hexdigest()[:24]
            vfile = cache_root / "visual" / f"{vkey}.json"
            try:
                if vfile.exists() and not args.no_cache:
                    print(f"  {path.name}: visual analysis from cache", file=sys.stderr)
                    measured = Measurements.from_dict(json.loads(vfile.read_text()))
                else:
                    measured = measure_visual(str(path), info.duration, visual, centre_ratio)
                    vfile.parent.mkdir(parents=True, exist_ok=True)
                    vfile.write_text(json.dumps(measured.to_dict()))
                analysis = analyse_visual(str(path), info.duration, visual, measured)
            except VisualError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            # The words, before the cuts rather than after them. A montage does
            # not cut TO speech -- that is what makes it a montage -- but it can
            # avoid cutting THROUGH it, and it cannot do that without knowing
            # where the speech is.
            heard: Transcript | None = None
            if (getattr(args, "subtitles", False)
                    or getattr(args, "protect_speech", False)
                    or getattr(args, "remove_silence", False)) and info.has_audio:
                try:
                    heard = _transcribe_once(
                        path, mid, info, args, recipe, speech_provider,
                        speech_provider_name, cache_root, work_dir,
                    )
                    subtitle_only.append(heard)
                except TranscriptionError as exc:
                    # Never fatal here: the edit does not depend on it, and
                    # losing the whole job over a missing subtitle would be a
                    # far worse trade than losing the subtitle.
                    builder.add_warning("subtitles", note(
                        "subtitles.failed", file=path.name, detail=str(exc)), mid)
                    print(f"  {path.name}: no subtitles ({exc})", file=sys.stderr)

            cuts = plan_visual_cuts(analysis, info.duration, visual, beats)

            if heard is not None and heard.words:
                from . import speech

                if getattr(args, "remove_silence", False):
                    trimmed, gone = speech.drop_silence(
                        cuts, heard, allowed=float(args.silence_allowed),
                        min_length=detection.min_clip_length)
                    if trimmed.keeps:
                        cuts = trimmed
                        if gone > 0.05:
                            silence_removed[mid] = gone
                    else:
                        # Every take was silent. Emptying the edit is never the
                        # answer to a slider.
                        builder.add_warning("silence", note("silence.allDropped"), mid)

                # Protection does NOT happen here. Duration fitting and beat
                # snapping both run after this and move boundaries again, which
                # put them straight back inside a word -- measured: 36 cuts
                # nudged here, 10 still mid-word in the finished plan. It is the
                # last word on a boundary or it is nothing.
                heard_by_media[mid] = heard
                durations[mid] = info.duration
            kept = len(analysis.usable)
            usable_shots += kept
            print(
                f"  {path.name}: {len(analysis.shots)} shots, {kept} usable, "
                f"{cuts.summary(info.duration)}",
                file=sys.stderr,
            )
            risky.extend(
                (mid, s.shot.start, s.shot.end, s.crop_risk)
                for s in analysis.usable if s.crop_risk >= CROP_RISK_THRESHOLD
            )

            # Every usable span, with the still that stands for it. Used to match
            # a story against, and to put a word to each shot -- an editor
            # scanning a strip needs to know what a shot IS, and a filename does
            # not tell them.
            # One ffmpeg seek and JPEG per shot, so this is the only phase in
            # the run whose progress is genuinely continuous -- everything else
            # is an ffmpeg pass we cannot see inside. Reported every tenth
            # still, over the half of the unit the two decodes did not use.
            drawn = 0
            expected = len(analysis.usable) + min(len(analysis.usable),
                                                  MAX_CANDIDATES_PER_MEDIA)

            def drew_one() -> None:
                nonlocal drawn
                drawn += 1
                if drawn % 10 == 0:
                    progress.step("progress.thumbs", f"{path.name} ({drawn}/{expected})",
                                  within=0.5 + 0.5 * drawn / max(expected, 1))

            progress.step("progress.thumbs", f"{path.name} (0/{expected})", within=0.5)
            for span in analysis.usable:
                at = sample_point(span.shot.start, span.shot.end)
                tp = thumb_path(cache_root, path, at)
                made = build_thumb(path, tp, at)
                drew_one()
                if made:
                    story_spans.append(
                        (mid, span.shot.start, span.shot.end, span.score, tp))

            # Offer every usable span as an alternate the editor can swap in.
            # The whole pool, not the leftovers: a span already on the timeline
            # is a fine alternate for a DIFFERENT slot, and reaching for another
            # moment of the same shot is the commonest swap there is. Rejected
            # spans stay out -- handing back the shots the scorer just called
            # unusable is not a choice, it is noise.
            ranked = sorted(analysis.usable, key=lambda s: s.score, reverse=True)
            if len(ranked) > MAX_CANDIDATES_PER_MEDIA:
                builder.add_warning("swap", note(
                    "swap.candidatesTruncated", file=path.name,
                    found=len(ranked), kept=MAX_CANDIDATES_PER_MEDIA,
                ), mid)
                ranked = ranked[:MAX_CANDIDATES_PER_MEDIA]
            # Back into shot order once the cut is made: an alternates list that
            # jumps around the source is hard to reason about, and the score is
            # already shown against each one.
            for span in sorted(ranked, key=lambda s: s.shot.start):
                # A still per span. Cheap enough to do inline -- one keyframe
                # seek and a JPEG each -- and without it the alternates list is
                # filenames and timecodes, which is not how anyone picks a shot.
                at = sample_point(span.shot.start, span.shot.end)
                tp = thumb_path(cache_root, path, at)
                thumb = str(tp) if build_thumb(path, tp, at) else None
                drew_one()
                builder.add_candidate(
                    mid, span.shot.start, span.shot.end, span.score,
                    reason=f"quality {span.score:.2f}", thumb_path=thumb,
                )

            collected.append((mid, cuts, v_track, a_track))
            continue

        # NOT `options`: that name already holds the job's JobOptions, and
        # rebinding it here quietly destroyed them. Every speech job then died on
        # `options.duration_mode` a few lines later -- unnoticed only because the
        # work since has all gone through the --visual path, which never enters
        # this loop.
        transcript: Transcript
        try:
            transcript = _transcribe_once(
                path, mid, info, args, recipe, provider, provider_name,
                cache_root, work_dir,
            )
        except TranscriptionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if transcript.language_confidence is not None:
            print(
                f"  {path.name}: language detected as {transcript.language} "
                f"({transcript.language_confidence:.0%})",
                file=sys.stderr,
            )
            # A wrong guess produces a fluent, confident, meaningless transcript
            # and cuts to match, so a shaky one has to be said out loud.
            if transcript.language_confidence < MIN_LANGUAGE_CONFIDENCE:
                builder.add_warning("language", note(
                    "language.uncertain",
                    file=path.name, confidence=transcript.language_confidence,
                    language=transcript.language,
                ), media_id=mid)

        cuts = plan_cuts(transcript, info.duration, detection)
        print(f"  {path.name}: {cuts.summary(info.duration)}", file=sys.stderr)

        collected.append((mid, cuts, v_track, a_track))
        builder.add_transcript(transcript)

    # When the music leads and no length was asked for, the edit runs as long as
    # the speech does -- and the reported job came out 134s against 60s of track,
    # so 55% of it had no beat to cut to at all. An edit that is cut to music
    # should not outlive the music. Said out loud, and overridden the moment the
    # editor sets a length of their own.
    # A cut rate only reaches the cutting on the picture path. Asked for on a
    # speech edit it is accepted, ignored, and the editor is left wondering why
    # "every beat" produced six clips of two and a half seconds -- which is
    # exactly what happened. Say so.
    if options.cut_rate is not None and not visual_used:
        builder.add_warning("cut", note("cut.rateNeedsPictures"))

    capped_to_music = False
    if (recipe.music.music_wins and options.duration_mode == "none"
            and music_available > 0):
        options = replace(options, duration_mode="upTo", duration_seconds=music_available)
        capped_to_music = True

    # Length applies to the finished sequence, so it is fitted across every source
    # at once. Interchangeable b-roll drops its weakest shots; a speech edit drops
    # from the end, because the opening of a narrative is not negotiable.
    if options.duration_mode != "none":
        # A montage should draw on everything the editor picked. "tail" keeps the
        # opening and drops the rest, which is right for an interview and wrong
        # for a social short -- it turned six selected clips into two.
        if visual_used:
            strategy = "worst"
        elif recipe.music.music_wins and len(collected) > 1:
            strategy = "spread"
        else:
            strategy = "tail"
        # In a montage the beat sets how long a shot may run, so the pacing
        # control finally changes the number of cuts rather than only their
        # length. Without a usable grid there is no beat to cap against.
        max_shot = quantum = None
        exact_shot = False
        if strategy == "spread" and beats and beats.beat_interval > 0:
            # The editor's dial wins outright when they have set one; pacing is
            # only the default. Scaling their explicit "every 2 beats" by the
            # pacing profile would make the control lie about what it does.
            shot_beats = cut_every
            max_shot = beats.beat_interval * shot_beats
            # The gap between candidate cut points, which is what the allocator
            # should round to -- not the beat, when the editor is subdividing.
            quantum = beats.beat_interval * shot_beats
            exact_shot = options.cut_rate is not None
        fitted = fit_duration_across(
            [(mid, cuts) for mid, cuts, _, _ in collected],
            options.duration_mode, options.duration_seconds,
            options.duration_tolerance, detection.min_clip_length, strategy,
            max_shot=max_shot, quantum=quantum, exact_shot=exact_shot,
        )
        by_id = dict(fitted)
        collected = [(mid, by_id.get(mid, cuts), v, a) for mid, cuts, v, a in collected]

    # Put a word to every shot. One text embedding for the vocabulary and cached
    # image vectors, so this is a fraction of a second on a warm cache.
    # media id -> [(span start, span end, English text, id)], so a clip can be
    # matched to its span by containment. A keep's start is not its span's start.
    # The id is what the panel translates; the text is its fallback.
    captions: dict[str, list[tuple[float, float, str, str]]] = {}
    described = 0
    # The scanning is done; everything from here is assembly. It used to report
    # nothing at all, which on the run measured above meant the bar sat at 89%
    # for 84% of the wall clock -- the exact complaint this is here to answer.
    progress.unit("assemble")
    if visual_used and story_spans:
        try:
            from . import describe
            # Said BEFORE the call, not after: `available` loads the recogniser
            # and takes about ten seconds the first time, and an editor watching
            # a bar deserves to know what the ten seconds is for.
            progress.step("progress.describe", within=0.0)
            if describe.available(cache_root):
                vecs, kept = describe.embed_stills_cached(
                    [sp[4] for sp in story_spans], cache_root)
                for (text, _score), index in zip(
                        describe.describe_shots(vecs, cache_root), kept):
                    if not text:
                        continue
                    mid_, start, end, *_ = story_spans[index]
                    captions.setdefault(mid_, []).append(
                        (start, end, text, describe.descriptor_id(text)))
                    described += 1
                print(f"  describe: {described} shot(s) described", file=sys.stderr)
        except Exception as exc:            # never fail a job over a nicety
            print(f"  describe: {exc}", file=sys.stderr)

    if captions:
        from .plan import _caption_for
        for cand in builder._candidates:          # noqa: SLF001 - same module's data
            found = _caption_for(captions, cand["mediaId"], cand["inSeconds"])
            if found:
                cand["caption"], cand["captionId"] = found

    # A story replaces the running order outright: the beats decide what appears
    # and in what sequence, so the source-by-source assembly above is set aside.
    story_sections: dict[str, str] = {}
    progress.step("progress.matching", within=0.55)
    if story and story.beats:
        collected, story_sections = _assemble_story(
            story, story_spans, collected, builder, options, detection, cache_root)
    elif reference is not None:
        # A description and a reference are two ways of saying the same thing,
        # so the description wins when both are given rather than the two
        # fighting over the running order.
        collected, story_sections = _assemble_reference(
            reference, story_spans, collected, builder, options, detection,
            cache_root, match_content=not args.reference_rhythm_only,
            heard_by_media=heard_by_media)
    if reference is not None and story and story.beats:
        builder.add_warning("reference", note("reference.describedInstead"))

    # Every boundary is now final except for the beat snapping inside
    # append_cuts, so this is the last place a cut can be moved off a word.
    protected: set[str] = set()
    if getattr(args, "protect_speech", False) and heard_by_media:
        from . import speech

        def run_protection(entries, may_grow):
            out, moved, stuck_here = [], 0, 0
            for mid, cuts, v_track, a_track in entries:
                heard = heard_by_media.get(mid)
                if heard is not None and heard.words:
                    cuts, nudged, stuck = speech.protect(
                        cuts, heard, media_duration=durations.get(mid, 0.0) or 1e9,
                        min_length=detection.min_clip_length, may_grow=may_grow)
                    stuck_here += stuck
                    if nudged:
                        moved += nudged
                        protected.add(mid)
                out.append((mid, cuts, v_track, a_track))
            return out, moved, stuck_here

        # Twice, and the pair is the point. The first pass takes the better cut
        # even when it lengthens a take; the duration fit then reclaims the time
        # the same way it did the first time round. The second pass cleans up
        # the boundaries that fit has just moved, and may only shorten, because
        # nothing runs after it.
        collected, speech_nudges, speech_stuck = run_protection(collected, True)

        if options.duration_mode != "none":
            fitted = fit_duration_across(
                [(mid, cuts) for mid, cuts, _, _ in collected],
                options.duration_mode, options.duration_seconds,
                options.duration_tolerance, detection.min_clip_length, strategy,
                max_shot=max_shot, quantum=quantum, exact_shot=exact_shot,
            )
            by_id = dict(fitted)
            collected = [(mid, by_id.get(mid, cuts), v, a)
                         for mid, cuts, v, a in collected]

        collected, again, speech_stuck = run_protection(collected, False)
        speech_nudges += again

    for mid, cuts, v_track, a_track in collected:
        # The beat grid finally reaches the speech path. It was computed once per
        # job, live in scope here the whole time, and passed only to the picture
        # planner and the music bed -- so an edit with a chosen track cut to the
        # speech and ignored the song entirely.
        builder.append_cuts(
            mid, cuts, video_track=v_track, audio_track=a_track,
            # From the plan itself. Reading it out of a map keyed on id(cuts)
            # lost every section as soon as anything rebuilt the CutPlan.
            section_id=cuts.section_id or story_sections.get(id(cuts)),
            crossfade_seconds=recipe.sequence.crossfade_seconds,
            # Speech wins over the metronome on a clip whose boundaries were
            # just moved to keep a sentence whole. Snapping it back to the grid
            # would undo the thing that was asked for, silently.
            beats=None if mid in protected else beats, music=recipe.music,
            min_clip_seconds=detection.min_clip_length,
            bed_start=bed_start, every=cut_every, captions=captions,
        )

    # Mark the shots a centre crop is likely to spoil. A heuristic, not subject
    # detection -- it points the editor at a handful of clips to check rather than
    # claiming to know where the subject is.
    if risky and options.frame_size:
        flagged = 0
        for entry in builder.timeline_entries():
            if entry["videoTrack"] < 0:
                continue
            for mid, start, end, risk in risky:
                if entry["mediaId"] == mid and start <= entry["inSeconds"] < end:
                    builder.add_marker(
                        entry["atFrame"],
                        "Check framing",
                        f"{risk * 100:.0f}% of the detail in this shot sits outside the "
                        f"centre crop -- the subject may be off to one side.",
                        kind="Comment",
                    )
                    flagged += 1
                    break
        if flagged:
            print(
                f"  reframe: {flagged} clip(s) marked 'Check framing' -- the crop may "
                "lose the subject",
                file=sys.stderr,
            )
            builder.add_warning("reframe", note("reframe.cropRisk", count=flagged))

    for message in builder_warnings:
        builder.add_warning("music", message)

    if music_path:
        try:
            music_info = probe(music_path)
            rel, root = _relative_to_root(music_path, media_roots, music_root)
            builder.add_media(MediaEntry(
                id="MUSIC", rel_path=rel, duration=music_info.duration, root=root,
                hash=content_hash(music_path), role="music",
                has_video=False, has_audio=True,
            ))
            chunk = resolve_music_chunk(
                track_duration=music_info.duration,
                picture_seconds=tb.to_seconds(builder.duration_frames),
                start=float(args.music_start or 0.0),
                length=float(args.music_length) if args.music_length else None,
                beats=beats,
                snap=not getattr(args, "no_music_snap", False),
            )
            # NOT `note`: binding that name anywhere in this function makes it
            # local throughout, shadowing the imported `note()` -- which then
            # fails hundreds of lines earlier, only on the paths that call it.
            for chunk_note in chunk.warnings:
                print(f"  music: {chunk_note}", file=sys.stderr)
                builder.add_warning("music", chunk_note, media_id="MUSIC")

            music_frames = tb.to_frames(chunk.length)
            if music_frames > 0:
                music_track = (recipe.roles.get("music") or {}).get("audio_track")
                where = f" from {chunk.start:.2f}s" if chunk.start else ""
                builder.add_full_clip(
                    "MUSIC", 0, music_frames,
                    video_track=-1,
                    audio_track=music_track if music_track is not None else recipe.sequence.audio_tracks - 1,
                    reason=(f"music bed at {beats.bpm:.0f} BPM" if beats else "music bed") + where,
                    in_seconds=chunk.start,
                )
        except ProbeError as exc:
            print(f"warning: could not add the music bed: {exc}", file=sys.stderr)

    # Check the length that was actually DELIVERED, not the one the fitter aimed
    # at. Beat quantising runs after the fit and can only shorten -- a shot cannot
    # be rounded up into material that is not there -- so an edit can hit its
    # target on paper and come out well under it. Saying so is the difference
    # between a tool that is wrong and a tool that is honest about being limited.
    if options.duration_mode in ("exactly", "about") and options.duration_seconds:
        delivered = tb.to_seconds(builder.duration_frames)
        target = float(options.duration_seconds)
        slack = target * (options.duration_tolerance if options.duration_mode == "about" else 0.05)
        if delivered < target - slack:
            key = ("length.shortOfAbout" if options.duration_mode == "about"
                   else "length.shortOfTarget")
            builder.add_warning("length", note(key, total=delivered, seconds=target))

    if capped_to_music:
        builder.add_warning("music", note("music.cappedToTrack", seconds=music_available))

    if speech_provider_name != provider_name and subtitle_only:
        builder.add_warning("subtitles", note(
            "subtitles.borrowedProvider",
            recipe=args.recipe, provider=speech_provider_name))

    if speech_nudges:
        builder.add_warning("speech", note("speech.protected", count=speech_nudges))
        # Cuts were snapped to beats before this moved them, so some no longer
        # land on one. Better said out loud than discovered by an editor
        # wondering why a music-led promo drifted.
        if protected and visual.snap_to_beats and beats and beats.beats:
            builder.add_warning("speech", note("speech.beatsGaveWay"))
        print(f"  speech: {speech_nudges} cut(s) moved off a word", file=sys.stderr)
    if speech_stuck and speech_stuck > speech_nudges:
        # A count on its own does not tell an editor what to change. When more
        # boundaries are stuck than were saved, the reason is almost always the
        # pace: at 92 BPM cutting on every beat a shot is 0.65s, and 0.65s of
        # continuous speech has no gap in it anywhere.
        builder.add_warning("speech", note("speech.paceTooFast"))
    if speech_stuck:
        # Whisper sometimes reports words with no silence between them at all,
        # and then there is nowhere in that stretch that is not inside a word.
        builder.add_warning("speech", note("speech.couldNotProtect", count=speech_stuck))
        print(f"  speech: {speech_stuck} cut(s) had nowhere to go", file=sys.stderr)
    if silence_removed:
        total = sum(silence_removed.values())
        builder.add_warning("silence", note(
            "silence.removed", seconds=total, allowed=float(args.silence_allowed)))
        print(f"  silence: {total:.1f}s removed", file=sys.stderr)

    if proxied:
        builder.add_warning("media", note("media.proxyAttached", count=len(proxied)))
    if awaiting_proxy:
        builder.add_warning("media", note("media.proxyBuilding", count=len(awaiting_proxy)))

    out = Path(args.out) if args.out else Path(f"{args.job}.editplan.json")

    # A preset is the only way to pin the sequence's frame rate. Without one the
    # panel calls `createSequence(name)` and Premiere supplies its own defaults --
    # so a plan built at 59.94 could still land in a 30fps sequence and every clip
    # would be a frame out again, which is the whole defect this run is meant to
    # avoid. "Match source" therefore means the source's own size and rate, not
    # "let Premiere decide".
    frame_size = options.frame_size
    if frame_size is None:
        first = next((pr for pr in probes if pr.has_video and pr.width and pr.height), None)
        if first:
            # The footage's shape, not its pixel count -- see working_frame_size.
            frame_size = working_frame_size(first.display_width, first.display_height)

    if frame_size:
        width, height = frame_size
        preset_file = out.with_suffix("").with_suffix(".sqpreset")
        write_preset(
            preset_file,
            name=f"AutoEdit {ASPECT_LABELS[options.aspect]} {tb.fps:.0f}fps",
            width=width, height=height, timebase=tb,
            video_tracks=recipe.sequence.video_tracks,
            audio_tracks=recipe.sequence.audio_tracks,
        )
        builder.set_sequence_preset(str(preset_file.resolve()), width, height)
        print(
            f"  aspect: {ASPECT_LABELS[options.aspect]} ({width}x{height}) "
            f"-> {preset_file.name}",
            file=sys.stderr,
        )

    if options.look:
        lut = _brandkit_lut(options.look)
        if lut:
            builder.add_effect(
                "track:v0", "AE.ADBE Lumetri", lut=options.look,
                reason=f"look: {options.look}",
            )
            print(f"  look: {options.look}", file=sys.stderr)
        else:
            builder.add_warning(
                "look",
                f"look {options.look!r} is not in the brand kit, so no grade was applied",
            )
            print(f"  look: {options.look!r} not in the brand kit -- skipped", file=sys.stderr)

    # What each root name in this plan means, so it can be read on a machine
    # that was not the one that wrote it.
    builder.set_roots({
        **{root_id(i): r for i, r in enumerate(media_roots)},
        **({"music": music_root} if music_root else {}),
    })
    plan = builder.build()

    # An empty timeline is not success. Exiting 0 with a zero-clip plan means the
    # editor builds an empty sequence and has to work out why themselves.
    if not plan["timeline"]:
        print("\nerror: no clips were produced, so there is nothing to build.", file=sys.stderr)
        if visual_used:
            # Which of the two very different failures this was. They were
            # reported as one, and the wrong one: a job where every shot PASSED
            # its quality gates and then produced no cuts was told to lower
            # min_sharpness, which would have changed nothing.
            if usable_shots:
                print(
                    f"  {usable_shots} shot(s) passed the quality gates and then produced\n"
                    "  no cuts, so the problem is the cut length rather than the footage.\n"
                    "  A cut rate shorter than the recipe's min_clip_length is the usual\n"
                    "  cause; the per-shot reasons above say which gate dropped them.",
                    file=sys.stderr,
                )
            else:
                print(
                    "  Every shot failed a quality gate. The thresholds in the recipe's\n"
                    "  `visual:` section are probably wrong for this footage -- start by\n"
                    "  lowering min_sharpness and min_brightness, and check the per-shot\n"
                    "  reasons above to see which gate is firing.",
                    file=sys.stderr,
                )
        elif silent:
            print(
                f"  {len(silent)} source(s) have no audio track: {', '.join(silent)}",
                file=sys.stderr,
            )
        else:
            print(
                "  The transcript produced no usable spans. Check the audio actually\n"
                "  contains speech, and try --model large-v3 for a better transcript.",
                file=sys.stderr,
            )
        return 1

    # --- subtitles ----------------------------------------------------------
    # After the plan is final and before it is validated, because the path goes
    # into the plan. Built from the plan rather than from the transcripts
    # directly: the words have to land where the EDIT put them, not where they
    # were spoken.
    speech = list(plan.get("transcripts") or []) + [t.to_dict() for t in subtitle_only]
    if speech:
        from . import subtitles

        cues = subtitles.build_cues(plan, speech)
        if cues:
            srt_file = out.with_suffix("").with_suffix(".srt")
            srt_file.write_text(subtitles.to_srt(cues), encoding="utf-8")
            plan["subtitlePath"] = str(srt_file.resolve())
            builder.add_warning("subtitles", note(
                "subtitles.written", count=len(cues), file=srt_file.name))
            plan["warnings"] = builder.build()["warnings"]
            print(f"  subtitles: {len(cues)} cue(s) -> {srt_file.name}", file=sys.stderr)
        else:
            builder.add_warning("subtitles", note("subtitles.none"))
            plan["warnings"] = builder.build()["warnings"]
            print("  subtitles: no speech survived the edit", file=sys.stderr)
    elif getattr(args, "subtitles", False):
        builder.add_warning("subtitles", note("subtitles.none"))
        plan["warnings"] = builder.build()["warnings"]
        print("  subtitles: asked for, but nothing was transcribed", file=sys.stderr)

    progress.step("progress.writing", within=0.9)

    errors = validate_plan(plan)
    if errors:
        print(f"error: generated plan is invalid ({len(errors)} problem(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    out.write_text(json.dumps(plan, indent=2) + "\n")

    # The furthest point anything reaches, not the last entry written. A music
    # bed is appended last and starts at frame 0, so reading the tail reported a
    # 138s edit as 48s -- the length of the song.
    total = max(
        (c["atFrame"] + c["durationFrames"] for c in plan["timeline"]), default=0
    )
    print(f"\n{out}", file=sys.stderr)
    print(f"  {len(plan['timeline'])} clips, {tb.timecode(total)} ({tb.to_seconds(total):.1f}s)", file=sys.stderr)
    for w in plan.get("warnings", []):
        print(f"  WARNING [{w['code']}] {w['message']}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autoedit", description="Premiere Pro rough-cut analysis engine")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("recipes", help="list available recipes").set_defaults(func=cmd_recipes)

    pr = sub.add_parser("probe", help="show technical metadata for media files")
    pr.add_argument("media", nargs="+")
    pr.set_defaults(func=cmd_probe)

    va = sub.add_parser("validate", help="check an EditPlan against the schema")
    va.add_argument("plan")
    va.set_defaults(func=cmd_validate)

    pl = sub.add_parser("plan", help="analyse media and write an EditPlan")
    pl.add_argument("--job", required=True, help="job id, also used in the sequence name")
    pl.add_argument("--recipe", required=True, help="recipe name or path")
    pl.add_argument("--media", nargs="+", required=True)
    pl.add_argument("--role", nargs="*", help="role per media file, matching recipe roles")
    pl.add_argument("--story", help=(
        "describe the video in plain text and the beats become the running "
        "order, e.g. \"opens with the storefront, then the chef cooking, then a "
        "happy customer\". Needs --visual: beats are matched against pictures."))
    pl.add_argument("--media-root", action="append", default=None, metavar="DIR", help=(
        "paths in the plan are recorded relative to this. Repeatable: give it "
        "once per folder or drive the footage lives on, and each is recorded "
        "under its own name so the plan stays free of absolute paths"))
    pl.add_argument("--music-root", help="a music library outside the footage tree. Tracks under it are recorded relative to it, so the plan stays free of absolute paths.")
    pl.add_argument("--provider", help="override the recipe transcription provider")
    pl.add_argument("--model", help="override the transcription model, e.g. small, medium, large-v3")
    pl.add_argument("--language", help="override the spoken language, e.g. en, ja")
    pl.add_argument("--transcript", help="explicit sidecar transcript path")
    pl.add_argument("--work-dir", help="cache directory for extracted audio and transcripts")
    pl.add_argument("--no-cache", action="store_true", help="re-transcribe even if a cached transcript exists")
    pl.add_argument("--music", help="music bed; cuts snap to its beats and it is laid on the audio track. Auto-detected when the recipe defines a music role and exactly one audio-only file sits alongside the footage.")
    pl.add_argument("--no-music", action="store_true", help="ignore any music bed, including an auto-detected one")
    pl.add_argument("--music-start", type=float, default=0.0, help="seconds into the track to start. A trend is a moment in a song, not its opening.")
    pl.add_argument("--music-length", type=float, default=None, help="seconds of track to use. Omit and the bed follows the picture; set it and that much music is laid even past the last frame.")
    pl.add_argument("--no-music-snap", action="store_true", help="use the start exactly as given instead of moving it to the nearest beat")
    pl.add_argument("--visual", action="store_true", help="cut from the pictures even when the footage has audio")
    pl.add_argument("--reference", metavar="VIDEO", help=(
        "cut the footage to match this video's structure: its shot count, its "
        "shot lengths and their order. The reference is measured and discarded "
        "-- no frame of it reaches the timeline"))
    pl.add_argument("--reference-rhythm-only", action="store_true", help=(
        "use the reference for its cutting rhythm alone, without trying to "
        "match what each shot shows"))
    pl.add_argument("--fps", metavar="RATE", help=(
        "sequence frame rate: 'auto' (default) follows the recipe and the "
        "footage, or give one -- 25, 29.97, 59.94, 30000/1001. Above 60 is "
        "refused because Premiere will not create the sequence"))
    pl.add_argument("--protect-speech", action="store_true", help=(
        "do not cut through the middle of what someone is saying. Implied by "
        "--subtitles: an edit worth subtitling is one where the words matter"))
    pl.add_argument("--remove-silence", action="store_true",
                    help="drop silence beyond --silence-allowed, and shots with no speech")
    pl.add_argument("--silence-allowed", type=float, default=0.5, metavar="SECONDS",
                    help="silence to leave around what is said (default 0.5)")
    pl.add_argument("--subtitles", action="store_true", help=(
        "write an .srt for the finished edit. Implied by a speech edit, which "
        "already has the words; needed for a montage, which does not"))
    pl.add_argument("--aspect", choices=list(ASPECT_LABELS),
                    help="output shape; generates a matching sequence preset (default: source)")
    pl.add_argument("--duration", type=float, help="target length in seconds")
    pl.add_argument("--duration-mode", choices=["upTo", "exactly", "about"],
                    help="how strictly to honour --duration (default: upTo)")
    pl.add_argument("--cut-rate", type=float, choices=sorted(VALID_CUT_RATES), default=None,
                    help="beats per shot when the music leads; 0.5 also cuts halfway "
                         "between beats. Omit to follow --pacing")
    pl.add_argument("--pacing", choices=["relaxed", "standard", "punchy"],
                    help="scales the recipe's timing (default: standard)")
    pl.add_argument("--look", help="brand kit LUT key to apply")
    pl.add_argument("--out", help="output path (default <job>.editplan.json)")
    pl.set_defaults(func=cmd_plan)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    finally:
        # cmd_plan returns from a dozen places and can raise from any of them,
        # and the helper calls this IN-PROCESS, reusing the interpreter for the
        # next job. Clearing here rather than at each exit is what stops one
        # run's weights from being the next run's starting point.
        progress.finish()


if __name__ == "__main__":
    sys.exit(main())
