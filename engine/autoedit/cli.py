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
from pathlib import Path
from typing import Any

from .detect import plan_cuts
from .plan import EditPlanBuilder, MediaEntry, validate_plan
from .probe import ProbeError, content_hash, probe
from .recipe import RecipeError, list_recipes, load_recipe
from .transcribe import TranscriptionError, extract_audio, get_provider
from .transcript import Transcript
from .options import JobOptions, OptionError, apply_pacing, fit_duration_across, ASPECT_LABELS
from .preset import write_preset
from .music import MusicError, detect_beats
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
    provider_name = args.provider or recipe.transcription.get("provider", "sidecar")
    try:
        provider = get_provider(provider_name)
    except TranscriptionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    tb = recipe.sequence.timebase
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

    for i, raw in enumerate(args.media):
        path = Path(raw).resolve()
        try:
            info = probe(path)
        except ProbeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

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

        options = dict(recipe.transcription)
        options["duration"] = info.duration
        options["media_path"] = str(path)
        if args.model:
            options["model"] = args.model
        if args.language:
            options["language"] = args.language
        if args.transcript:
            options["path"] = args.transcript

        # Cache transcripts on content hash + provider settings. Transcription is
        # by far the slowest step and recipe tuning is an iterative loop -- without
        # this, nudging min_silence by 0.05s means re-transcribing hours of rushes.
        cache_key = _transcript_cache_key(content_hash(path), provider_name, options)
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
                transcript = provider.transcribe(wav, mid, options)
            except TranscriptionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(transcript.to_dict(), indent=2))

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
            builder.add_warning(
                "reframe",
                f"{flagged} clip(s) flagged: detail sits outside the centre crop, so "
                "check those before delivering",
            )

    for message in builder_warnings:
        builder.add_warning("music", message)

    if music_path:
        try:
            music_info = probe(music_path)
            rel = (
                str(music_path.relative_to(media_root))
                if media_root and media_root in music_path.parents
                else music_path.name
            )
            builder.add_media(MediaEntry(
                id="MUSIC", rel_path=rel, duration=music_info.duration,
                hash=content_hash(music_path), role="music",
                has_video=False, has_audio=True,
            ))
            music_frames = min(
                builder.duration_frames,
                tb.to_frames(music_info.duration),
            )
            if music_frames > 0:
                music_track = (recipe.roles.get("music") or {}).get("audio_track")
                builder.add_full_clip(
                    "MUSIC", 0, music_frames,
                    video_track=-1,
                    audio_track=music_track if music_track is not None else recipe.sequence.audio_tracks - 1,
                    reason=f"music bed at {beats.bpm:.0f} BPM" if beats else "music bed",
                )
        except ProbeError as exc:
            print(f"warning: could not add the music bed: {exc}", file=sys.stderr)

    out = Path(args.out) if args.out else Path(f"{args.job}.editplan.json")

    if options.frame_size:
        width, height = options.frame_size
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

    total = plan["timeline"][-1]["atFrame"] + plan["timeline"][-1]["durationFrames"] if plan["timeline"] else 0
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
    pl.add_argument("--provider", help="override the recipe transcription provider")
    pl.add_argument("--model", help="override the transcription model, e.g. small, medium, large-v3")
    pl.add_argument("--language", help="override the spoken language, e.g. en, ja")
    pl.add_argument("--transcript", help="explicit sidecar transcript path")
    pl.add_argument("--work-dir", help="cache directory for extracted audio and transcripts")
    pl.add_argument("--no-cache", action="store_true", help="re-transcribe even if a cached transcript exists")
    pl.add_argument("--music", help="music bed; cuts snap to its beats and it is laid on the audio track. Auto-detected when the recipe defines a music role and exactly one audio-only file sits alongside the footage.")
    pl.add_argument("--no-music", action="store_true", help="ignore any music bed, including an auto-detected one")
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
