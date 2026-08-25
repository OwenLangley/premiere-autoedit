#!/usr/bin/env python3
"""Job watcher: turns editor requests into EditPlans.

The panel cannot run the engine -- UXP has no subprocess access -- so this closes
the loop. It watches the same jobs folder the panel already reads, picks up
requests, runs the engine, and writes the plan back alongside a status file the
panel polls while it works.

Deliberately built on the CLI rather than importing the planning internals: the
terminal and the panel then cannot drift into behaving differently, because there
is only one path through the engine.

    python3 helper/watch.py --jobs ~/Desktop/AutoEdit-jobs --media ~/Footage
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from autoedit.cli import main as engine_main            # noqa: E402
from autoedit.options import ASPECT_LABELS, PACING      # noqa: E402
from autoedit.recipe import list_recipes, load_recipe   # noqa: E402

REQUEST_SUFFIX = ".request.json"
POLL_SECONDS = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_status(jobs: Path, job_id: str, state: str, message: str = "", **extra) -> None:
    payload = {
        "schemaVersion": "1.0",
        "jobId": job_id,
        "state": state,
        "message": message,
        "updatedAt": _now(),
        **extra,
    }
    (jobs / f"{job_id}.status.json").write_text(json.dumps(payload, indent=2) + "\n")


def write_capabilities(jobs: Path) -> None:
    """Tell the panel what this engine actually supports.

    The alternative -- hardcoding the lists in the panel -- guarantees they drift
    the first time a recipe is added.
    """
    looks: dict[str, str] = {}
    kit = Path(__file__).resolve().parents[1] / "brandkit" / "brandkit.json"
    if kit.exists():
        try:
            looks = {k: k for k in (json.loads(kit.read_text()).get("luts") or {})}
        except (json.JSONDecodeError, OSError):
            pass

    recipes = []
    for name in list_recipes():
        try:
            r = load_recipe(name)
            recipes.append({
                "name": name,
                "description": (r.description or "").strip().split("\n")[0],
                "visual": r.auto_music,
            })
        except Exception:
            recipes.append({"name": name, "description": "", "visual": False})

    (jobs / "capabilities.json").write_text(json.dumps({
        "schemaVersion": "1.0",
        "updatedAt": _now(),
        "recipes": recipes,
        "aspects": [{"value": k, "label": v} for k, v in ASPECT_LABELS.items()],
        "pacing": [{"value": k, "label": v.label} for k, v in PACING.items()],
        "durationModes": [
            {"value": "none", "label": "No limit"},
            {"value": "upTo", "label": "Up to"},
            {"value": "exactly", "label": "Exactly"},
            {"value": "about", "label": "About"},
        ],
        "looks": [{"value": k, "label": v} for k, v in looks.items()],
    }, indent=2) + "\n")


def request_to_argv(request: dict, jobs: Path, media_root: Path, work_dir: Path) -> list[str]:
    """Map a job request onto engine arguments."""
    job_id = request["jobId"]
    options = request.get("options") or {}
    duration = options.get("duration") or {}

    argv = [
        "plan",
        "--job", job_id,
        "--recipe", request["recipe"],
        "--media-root", str(media_root),
        "--work-dir", str(work_dir),
        "--out", str(jobs / f"{job_id}.editplan.json"),
        "--media", *[str(media_root / m) for m in request["media"]],
    ]

    aspect = options.get("aspect", "source")
    if aspect and aspect != "source":
        argv += ["--aspect", aspect]

    if duration.get("mode", "none") != "none" and duration.get("seconds"):
        argv += ["--duration", str(duration["seconds"]), "--duration-mode", duration["mode"]]

    if options.get("pacing", "standard") != "standard":
        argv += ["--pacing", options["pacing"]]
    if options.get("look"):
        argv += ["--look", options["look"]]
    if options.get("visual"):
        argv += ["--visual"]
    if options.get("model"):
        argv += ["--model", options["model"]]
    if options.get("language"):
        argv += ["--language", options["language"]]

    music = options.get("music", "auto")
    if music == "none":
        argv += ["--no-music"]
    elif music and music != "auto":
        argv += ["--music", str(media_root / music)]

    return argv


def validate_request(request: dict) -> list[str]:
    problems: list[str] = []
    if request.get("schemaVersion") != "1.0":
        problems.append(f"unsupported schemaVersion {request.get('schemaVersion')!r}")
    for key in ("jobId", "recipe", "media"):
        if not request.get(key):
            problems.append(f"missing required field {key!r}")
    if request.get("media") and not isinstance(request["media"], list):
        problems.append("media must be a list of paths")
    try:
        import jsonschema
        schema_file = Path(__file__).resolve().parents[1] / "schema" / "job-request.schema.json"
        jsonschema.Draft7Validator(json.loads(schema_file.read_text())).validate(request)
    except ImportError:
        pass
    except Exception as exc:
        problems.append(str(exc).split("\n")[0])
    return problems


def process(path: Path, jobs: Path, media_root: Path, work_dir: Path) -> bool:
    """Run one request. Returns True when a plan was produced."""
    job_id = path.stem.replace(".request", "")
    try:
        request = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        write_status(jobs, job_id, "failed", f"request is not valid JSON: {exc}")
        return False

    job_id = request.get("jobId") or job_id
    problems = validate_request(request)
    if problems:
        write_status(jobs, job_id, "failed", "; ".join(problems))
        return False

    write_status(jobs, job_id, "analysing", "starting", startedAt=_now())

    argv = request_to_argv(request, jobs, media_root, work_dir)
    captured = io.StringIO()
    try:
        # The engine reports progress on stderr; capture it so the failure message
        # in the status file is the engine's own words rather than a stack trace.
        with redirect_stderr(captured), redirect_stdout(io.StringIO()):
            code = engine_main(argv)
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        write_status(jobs, job_id, "failed", traceback.format_exc(limit=3).strip().split("\n")[-1])
        return False

    output = captured.getvalue().strip()
    lines = [l.strip() for l in output.splitlines() if l.strip()]

    if code != 0:
        detail = " / ".join(lines[-3:]) if lines else f"engine exited {code}"
        write_status(jobs, job_id, "failed", detail)
        return False

    plan_file = jobs / f"{job_id}.editplan.json"
    if not plan_file.exists():
        write_status(jobs, job_id, "failed", "engine reported success but wrote no plan")
        return False

    write_status(
        jobs, job_id, "ready", lines[-1] if lines else "done",
        planFile=plan_file.name,
        warnings=[l for l in lines if l.lower().startswith("warning")],
    )
    return True


def claimed_marker(jobs: Path, path: Path) -> Path:
    return jobs / f".{path.name}.claimed"


def run_once(jobs: Path, media_root: Path, work_dir: Path, verbose: bool = True) -> int:
    handled = 0
    for path in sorted(jobs.glob(f"*{REQUEST_SUFFIX}")):
        marker = claimed_marker(jobs, path)
        if marker.exists() and marker.stat().st_mtime >= path.stat().st_mtime:
            continue
        marker.write_text(_now())
        if verbose:
            print(f"[{_now()}] {path.name}", file=sys.stderr)
        process(path, jobs, media_root, work_dir)
        handled += 1
    return handled


def watch(jobs: Path, media_root: Path, work_dir: Path, interval: float = POLL_SECONDS) -> None:
    jobs.mkdir(parents=True, exist_ok=True)
    write_capabilities(jobs)
    print(f"watching {jobs} (media root {media_root})", file=sys.stderr)
    while True:
        try:
            run_once(jobs, media_root, work_dir)
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoEdit job watcher")
    ap.add_argument("--jobs", required=True, help="folder the panel writes requests into")
    ap.add_argument("--media", required=True, help="media root; request paths are relative to it")
    ap.add_argument("--work-dir", default=None, help="cache directory (default <jobs>/.cache)")
    ap.add_argument("--once", action="store_true", help="process what is pending and exit")
    ap.add_argument("--interval", type=float, default=POLL_SECONDS)
    args = ap.parse_args(argv)

    jobs = Path(args.jobs).expanduser().resolve()
    media_root = Path(args.media).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else jobs / ".cache"

    if not media_root.is_dir():
        print(f"error: media root does not exist: {media_root}", file=sys.stderr)
        return 2

    jobs.mkdir(parents=True, exist_ok=True)
    if args.once:
        write_capabilities(jobs)
        print(f"processed {run_once(jobs, media_root, work_dir)} request(s)", file=sys.stderr)
        return 0

    watch(jobs, media_root, work_dir, args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
