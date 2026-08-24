"""Command line entry point for the analysis engine.

    autoedit recipes
    autoedit probe   <media>...
    autoedit plan    --job EP042 --recipe podcast-2cam --media a.mov b.mov
    autoedit validate plan.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .detect import plan_cuts
from .plan import EditPlanBuilder, MediaEntry, validate_plan
from .probe import ProbeError, content_hash, probe
from .recipe import RecipeError, list_recipes, load_recipe
from .transcribe import TranscriptionError, extract_audio, get_provider
from .transcript import Transcript


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

    work_dir = Path(args.work_dir or ".autoedit-cache") / args.job
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
        ))
        for w in info.warnings:
            builder.add_warning("probe", w, mid)

        if not info.has_audio:
            print(f"  {path.name}: no audio, cannot transcript-cut -- skipped", file=sys.stderr)
            continue

        print(f"  {path.name}: extracting audio", file=sys.stderr)
        try:
            wav = extract_audio(path, work_dir / f"{mid}.wav")
            options = dict(recipe.transcription)
            options["duration"] = info.duration
            options["media_path"] = str(path)
            if args.transcript:
                options["path"] = args.transcript
            print(f"  {path.name}: transcribing via {provider_name}", file=sys.stderr)
            transcript: Transcript = provider.transcribe(wav, mid, options)
        except TranscriptionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        cuts = plan_cuts(transcript, info.duration, recipe.detection)
        print(f"  {path.name}: {cuts.summary(info.duration)}", file=sys.stderr)

        roles = recipe.roles.get(role or "", {}) if role else {}
        builder.append_cuts(
            mid, cuts,
            video_track=roles.get("video_track", 0),
            audio_track=roles.get("audio_track", 0),
            crossfade_seconds=recipe.sequence.crossfade_seconds,
        )
        builder.add_transcript(transcript)

    plan = builder.build()
    errors = validate_plan(plan)
    if errors:
        print(f"error: generated plan is invalid ({len(errors)} problem(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else Path(f"{args.job}.editplan.json")
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
    pl.add_argument("--transcript", help="explicit sidecar transcript path")
    pl.add_argument("--work-dir", help="cache directory for extracted audio")
    pl.add_argument("--out", help="output path (default <job>.editplan.json)")
    pl.set_defaults(func=cmd_plan)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
