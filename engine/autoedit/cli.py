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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .detect import plan_cuts
from .plan import EditPlanBuilder, MediaEntry, validate_plan
from .probe import ProbeError, content_hash, probe
from .recipe import RecipeError, list_recipes, load_recipe
from .transcribe import TranscriptionError, extract_audio, get_provider
from .transcript import Transcript
from .options import (JobOptions, OptionError, apply_pacing, fit_duration_across,
                      working_frame_size, ASPECT_LABELS)
from .preset import write_preset
from .music import BeatGrid, MusicError, detect_beats
from .notes import Note, note
from .timebase import choose_timebase, holds_exactly
from .visual import (
    Measurements, VisualError, analyse as analyse_visual, measure as measure_visual,
    measurement_key, plan_visual_cuts,
)


# Above this share of detail falling outside the centre crop, the shot is worth
# a human look. Tuned to flag a minority of shots -- a marker on everything is
# the same as no markers at all.
CROP_RISK_THRESHOLD = 0.35

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


def _relative_to_root(
    path: Path, media_root: Path | None, music_root: Path | None
) -> tuple[str, str]:
    """Express `path` against whichever configured root contains it.

    A music library sits outside the footage tree, so without its own root the
    plan could only record a bare filename -- and the panel, resolving everything
    against the media root, would then fail to find it. Falling back to the name
    is kept for the un-rooted case, but it is the lossy branch, not the norm.
    """
    for root, label in ((music_root, "music"), (media_root, "media")):
        if root and (root == path.parent or root in path.parents):
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
        look=getattr(args, "look", None),
        music="none" if getattr(args, "no_music", False) else "auto",
        visual=bool(getattr(args, "visual", False)),
        model=getattr(args, "model", None),
        language=getattr(args, "language", None),
    )


def _transcript_cache_key(media_hash: str, provider: str, options: dict) -> str:
    """Key a cached transcript on the media plus anything that would change it.

    Deliberately ignores recipe cut settings: those are applied after transcription,
    so tuning them must not invalidate the cache -- that is the entire point.
    """
    relevant = {k: options.get(k) for k in ("model", "language", "vad_filter", "diarize")}
    stamp = json.dumps([media_hash, provider, relevant], sort_keys=True)
    return hashlib.sha256(stamp.encode()).hexdigest()[:24]


def _media_id(index: int, path: Path) -> str:
    stem = "".join(c for c in path.stem if c.isalnum() or c in "_-")[:24]
    return stem or f"M{index:03d}"


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
        print(f"    {info.duration:.2f}s  {info.width}x{info.height}  {info.timebase or 'audio only'}")
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

    media_root = Path(args.media_root).resolve() if args.media_root else None
    music_root = Path(args.music_root).resolve() if getattr(args, "music_root", None) else None
    provider_name = args.provider or recipe.transcription.get("provider", "sidecar")
    try:
        provider = get_provider(provider_name)
    except TranscriptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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

    tb, displaced = choose_timebase(
        recipe.sequence.timebase,
        [p.timebase for p in probes if p.has_video and p.timebase],
    )
    timebase_notes: list[tuple[Note, str | None]] = []
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

    silent: list[str] = []
    visual_used = False
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
                    f"to {max(1, visual.beats_per_shot // 2)} or "
                    f"{visual.beats_per_shot * 2} in the recipe",
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
        except (MusicError, ProbeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    work_dir = cache_root / args.job
    work_dir.mkdir(parents=True, exist_ok=True)

    for w, mid_for in timebase_notes:
        builder.add_warning("timebase", w, mid_for)

    for i, raw in enumerate(args.media):
        path = Path(raw).resolve()
        info = probes[i]

        mid = _media_id(i, path)
        rel = str(path.relative_to(media_root)) if media_root and media_root in path.parents else path.name
        role = args.role[i] if args.role and i < len(args.role) else None

        builder.add_media(MediaEntry(
            id=mid, rel_path=rel, duration=info.duration,
            hash=content_hash(path), role=role,
            timebase=info.timebase, has_video=info.has_video, has_audio=info.has_audio,
            width=info.width, height=info.height,
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
            if options.frame_size and info.width and info.height:
                target = options.frame_size[0] / options.frame_size[1]
                source = info.width / info.height
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
            cuts = plan_visual_cuts(analysis, info.duration, visual, beats)
            kept = len(analysis.usable)
            print(
                f"  {path.name}: {len(analysis.shots)} shots, {kept} usable, "
                f"{cuts.summary(info.duration)}",
                file=sys.stderr,
            )
            risky.extend(
                (mid, s.shot.start, s.shot.end, s.crop_risk)
                for s in analysis.usable if s.crop_risk >= CROP_RISK_THRESHOLD
            )
            collected.append((mid, cuts, v_track, a_track))
            continue

        # NOT `options`: that name already holds the job's JobOptions, and
        # rebinding it here quietly destroyed them. Every speech job then died on
        # `options.duration_mode` a few lines later -- unnoticed only because the
        # work since has all gone through the --visual path, which never enters
        # this loop.
        tx_options = dict(recipe.transcription)
        tx_options["duration"] = info.duration
        tx_options["media_path"] = str(path)
        if args.model:
            tx_options["model"] = args.model
        if args.language:
            tx_options["language"] = args.language
        if args.transcript:
            tx_options["path"] = args.transcript

        # Cache transcripts on content hash + provider settings. Transcription is
        # by far the slowest step and recipe tuning is an iterative loop -- without
        # this, nudging min_silence by 0.05s means re-transcribing hours of rushes.
        cache_key = _transcript_cache_key(content_hash(path), provider_name, tx_options)
        cache_file = cache_root / "transcripts" / f"{cache_key}.json"
        transcript: Transcript

        if cache_file.exists() and not args.no_cache:
            print(f"  {path.name}: transcript from cache", file=sys.stderr)
            transcript = Transcript.from_dict(json.loads(cache_file.read_text()))
            transcript.media_id = mid
        else:
            print(f"  {path.name}: extracting audio", file=sys.stderr)
            try:
                wav = extract_audio(path, work_dir / f"{mid}.wav")
                print(f"  {path.name}: transcribing via {provider_name}", file=sys.stderr)
                transcript = provider.transcribe(wav, mid, tx_options)
            except TranscriptionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(transcript.to_dict(), indent=2))

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

    # Length applies to the finished sequence, so it is fitted across every source
    # at once. Interchangeable b-roll drops its weakest shots; a speech edit drops
    # from the end, because the opening of a narrative is not negotiable.
    if options.duration_mode != "none":
        strategy = "worst" if visual_used else "tail"
        fitted = fit_duration_across(
            [(mid, cuts) for mid, cuts, _, _ in collected],
            options.duration_mode, options.duration_seconds,
            options.duration_tolerance, detection.min_clip_length, strategy,
        )
        by_id = dict(fitted)
        collected = [(mid, by_id.get(mid, cuts), v, a) for mid, cuts, v, a in collected]

    for mid, cuts, v_track, a_track in collected:
        builder.append_cuts(
            mid, cuts, video_track=v_track, audio_track=a_track,
            crossfade_seconds=recipe.sequence.crossfade_seconds,
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
            rel, root = _relative_to_root(music_path, media_root, music_root)
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
            frame_size = working_frame_size(first.width, first.height)

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

    plan = builder.build()

    # An empty timeline is not success. Exiting 0 with a zero-clip plan means the
    # editor builds an empty sequence and has to work out why themselves.
    if not plan["timeline"]:
        print("\nerror: no clips were produced, so there is nothing to build.", file=sys.stderr)
        if visual_used:
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
    pl.add_argument("--media-root", help="paths in the plan are recorded relative to this")
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
    pl.add_argument("--aspect", choices=list(ASPECT_LABELS),
                    help="output shape; generates a matching sequence preset (default: source)")
    pl.add_argument("--duration", type=float, help="target length in seconds")
    pl.add_argument("--duration-mode", choices=["upTo", "exactly", "about"],
                    help="how strictly to honour --duration (default: upTo)")
    pl.add_argument("--pacing", choices=["relaxed", "standard", "punchy"],
                    help="scales the recipe's timing (default: standard)")
    pl.add_argument("--look", help="brand kit LUT key to apply")
    pl.add_argument("--out", help="output path (default <job>.editplan.json)")
    pl.set_defaults(func=cmd_plan)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
