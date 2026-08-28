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
import hashlib
import io
import json
import shutil
import subprocess
import threading
import unicodedata
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from autoedit.cli import main as engine_main            # noqa: E402
from autoedit.options import ASPECT_LABELS, CUT_RATES, PACING  # noqa: E402
from autoedit.probe import ProbeError, content_hash, needs_proxy, probe  # noqa: E402
from autoedit.proxy import build_proxy, proxy_path        # noqa: E402
from autoedit.thumbs import build_thumb, sample_point, thumb_path  # noqa: E402
from autoedit.visual import (                             # noqa: E402
    Measurements, VisualError, VisualSettings, analyse as analyse_visual,
    measure as measure_visual, measurement_key,
)
from autoedit.recipe import list_recipes, load_recipe   # noqa: E402
from autoedit.story import (                            # noqa: E402
    MONTAGE_WORDS, PACE_WORDS, PLATFORM_ASPECTS, SUBTITLE_WORDS,
    SUBTITLE_WORDS_CJK,
)

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
    formats = []
    for name in list_recipes():
        try:
            r = load_recipe(name)
            recipes.append({
                "name": name,
                "description": (r.description or "").strip().split("\n")[0],
                "visual": r.auto_music,
            })
            # The deliverable a recipe makes, for the panel's first question.
            # A recipe without a format block simply has no card; it is still
            # reachable through the full form.
            if r.format:
                formats.append({"recipe": name, **r.format})
        except Exception:
            recipes.append({"name": name, "description": "", "visual": False})
    formats.sort(key=lambda f: (f.get("order", 99), f["label"]))

    (jobs / "capabilities.json").write_text(json.dumps({
        "schemaVersion": "1.0",
        "updatedAt": _now(),
        "recipes": recipes,
        "formats": formats,
        # The words the engine reads out of a description, so the panel can
        # reflect the same prompt in its controls without a second vocabulary
        # that drifts from this one. The panel owns the regex shapes; the words
        # come from here.
        "promptWords": {
            "platforms": PLATFORM_ASPECTS,
            "pace": PACE_WORDS,
            "montage": list(MONTAGE_WORDS),
            "subtitles": list(SUBTITLE_WORDS) + list(SUBTITLE_WORDS_CJK),
        },
        "aspects": [{"value": k, "label": v} for k, v in ASPECT_LABELS.items()],
        "pacing": [{"value": k, "label": v.label} for k, v in PACING.items()],
        "cutRates": [{"value": _num(n), "label": lbl} for n, lbl in CUT_RATES],
        "durationModes": [
            {"value": "none", "label": "No limit"},
            {"value": "upTo", "label": "Up to"},
            {"value": "exactly", "label": "Exactly"},
            {"value": "about", "label": "About"},
        ],
        "looks": [{"value": k, "label": v} for k, v in looks.items()],
    }, indent=2) + "\n")


MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mxf", ".avi", ".m4v", ".mkv", ".mts", ".m2ts",
    ".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac", ".ogg",
}

# Music does not live loose among the rushes -- it lives in a Music folder beside
# them -- so the index has to descend. Both limits are surfaced in the index and
# on stderr rather than silently truncating: an editor whose track is missing
# needs to know the scan stopped, not wonder why the dropdown is short.
MAX_INDEX_DEPTH = 3
MAX_INDEX_FILES = 400

# Probing is an ffprobe spawn each. Flat, that cost was invisible; recursive and
# repeated every 30s it would not be, so remember results until the file changes.
_PROBE_CACHE: dict[tuple[str, int, int], object] = {}


def _cached_probe(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _PROBE_CACHE:
        try:
            _PROBE_CACHE[key] = probe(path)
        except ProbeError:
            _PROBE_CACHE[key] = None
    return _PROBE_CACHE[key]


# --- proxies ----------------------------------------------------------------
#
# The transcode itself lives in autoedit.proxy so the engine and the helper
# cannot disagree about where a proxy is; what belongs here is only the decision
# to start one and the thread it runs on.
_proxy_lock = threading.Lock()
_proxy_started: set[str] = set()


def ensure_proxies(media_root: Path, work_dir: Path) -> None:
    """Start building proxies for anything that will not play, in the background.

    Deliberately off the job path. Transcoding six 4K files takes minutes, and an
    editor who pressed Create should get a plan now, not after the transcode.
    Whatever is ready when they build gets attached; the rest gets attached next
    time.
    """
    def worker() -> None:
        for path in iter_media(media_root):
            info = _cached_probe(path)
            if info is None or not needs_proxy(info):
                continue
            dest = proxy_path(work_dir, path)
            if dest.exists():
                continue
            print(f"  proxy: building {path.name} -> {dest.name}", file=sys.stderr)
            if build_proxy(path, dest):
                print(f"  proxy: {path.name} ready", file=sys.stderr)

    with _proxy_lock:
        key = str(media_root)
        if key in _proxy_started:
            return
        _proxy_started.add(key)
    threading.Thread(target=worker, name="proxies", daemon=True).start()


# How many spans one library file contributes to the shot library. Lower than
# the per-job cap: this is every file under the media root, not the handful an
# editor picked, so the total is what has to stay sane.
MAX_LIBRARY_SPANS = 12
_shots_lock = threading.Lock()
_shots_started: set = set()


def ensure_library_shots(media_root: Path, work_dir: Path, jobs: Path) -> None:
    """Analyse the whole library in the background, so alternates are not limited
    to the clips the editor happened to pick for this job.

    An editor choosing a replacement shot is not thinking "which of the six files
    I selected" -- they are thinking of their footage. Offering only what is
    already in the edit answers a question nobody asked.

    Off the job path and heavily cached, for the same reason proxies are: the
    first pass over a big library is minutes of decoding, and an editor who
    pressed Create should get a plan now. The file is written after every source
    so a partial library is usable immediately rather than only at the end.
    """
    def worker() -> None:
        settings = VisualSettings()
        shots: list[dict] = []
        done = 0
        for path in iter_media(media_root):
            info = _cached_probe(path)
            if info is None or not info.has_video:
                continue
            try:
                spans = _library_spans(path, info, settings, work_dir)
            except (VisualError, OSError) as exc:
                print(f"  shots: skipped {path.name}: {exc}", file=sys.stderr)
                continue
            if not spans:
                continue
            try:
                rel = str(path.relative_to(media_root))
            except ValueError:
                rel = path.name
            # durationSeconds travels with every span: swapping in a file the
            # plan has never seen means synthesising a media entry for it, and
            # that entry needs a length or the panel cannot keep a swap inside
            # the source.
            shots.extend(
                {**sp, "relPath": rel, "durationSeconds": round(info.duration, 4)}
                for sp in spans
            )
            done += 1
            # Written as it goes. A library of 200 clips takes a long time, and
            # a panel that shows nothing until the last one has finished is
            # indistinguishable from one that is broken.
            _write_shots(jobs, shots, complete=False)
        _write_shots(jobs, shots, complete=True)
        print(f"  shots: {len(shots)} spans from {done} file(s)", file=sys.stderr)

    with _shots_lock:
        key = str(media_root)
        if key in _shots_started:
            return
        _shots_started.add(key)
    threading.Thread(target=worker, name="library-shots", daemon=True).start()


def _library_spans(path: Path, info, settings, work_dir: Path) -> list[dict]:
    """The best spans in one file, with a still for each.

    Shares the engine's measurement cache rather than keeping its own: the same
    decode serves a job that later uses this file, so a library pass makes the
    next real edit faster instead of duplicating its work.
    """
    vkey = hashlib.sha256(
        json.dumps([content_hash(path), measurement_key(settings), None],
                   sort_keys=True).encode()
    ).hexdigest()[:24]
    vfile = work_dir / "visual" / f"{vkey}.json"
    if vfile.exists():
        measured = Measurements.from_dict(json.loads(vfile.read_text()))
    else:
        measured = measure_visual(str(path), info.duration, settings, None)
        vfile.parent.mkdir(parents=True, exist_ok=True)
        vfile.write_text(json.dumps(measured.to_dict()))

    analysis = analyse_visual(str(path), info.duration, settings, measured)
    ranked = sorted(analysis.usable, key=lambda s: s.score, reverse=True)[:MAX_LIBRARY_SPANS]

    out = []
    for span in sorted(ranked, key=lambda s: s.shot.start):
        at = sample_point(span.shot.start, span.shot.end)
        tp = thumb_path(work_dir, path, at)
        out.append({
            "inSeconds": round(span.shot.start, 4),
            "outSeconds": round(span.shot.end, 4),
            "score": round(span.score, 4),
            "reason": f"quality {span.score:.2f}",
            **({"thumbPath": str(tp)} if build_thumb(path, tp, at) else {}),
        })
    return out


def _write_shots(jobs: Path, shots: list[dict], complete: bool) -> None:
    (jobs / "library-shots.json").write_text(json.dumps({
        "generatedAt": _now(),
        "complete": complete,
        "files": shots,
    }, indent=2) + "\n")


def iter_media(media_root: Path, max_depth: int = MAX_INDEX_DEPTH):
    """Media files under the root, breadth-first so top-level rushes come first."""
    queue: list[tuple[Path, int]] = [(media_root, 0)]
    while queue:
        folder, depth = queue.pop(0)
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        folders = []
        for path in entries:
            if path.name.startswith("."):
                continue
            if path.is_dir():
                if depth < max_depth:
                    folders.append((path, depth + 1))
            elif path.suffix.lower() in MEDIA_EXTENSIONS:
                yield path
        queue.extend(folders)


def build_media_index(media_root: Path, max_files: int = MAX_INDEX_FILES) -> dict:
    """Index the media root so the panel can tell footage from a music bed.

    The panel cannot probe -- UXP has no ffprobe -- so extension alone would have
    to decide, and that is not enough: the music track that turned up in real use
    was a .mp4 with no video stream, which showed up in the clip picker as if it
    were footage.
    """
    entries: list[dict] = []
    truncated = False
    for path in iter_media(media_root):
        if len(entries) >= max_files:
            truncated = True
            break
        info = _cached_probe(path)
        if info is None:
            continue
        entries.append({
            # NFC on both: macOS filesystems hand back decomposed forms, and the
            # panel compares these strings against names Premiere reports. With
            # ASCII the two forms are identical; with Japanese they are not, and
            # the clip silently fails to resolve.
            "name": unicodedata.normalize("NFC", path.name),
            "relPath": unicodedata.normalize("NFC", str(path.relative_to(media_root))),
            "hasVideo": info.has_video,
            "hasAudio": info.has_audio,
            "durationSeconds": round(info.duration, 2),
            "width": info.width,
            "height": info.height,
        })
    return {
        "schemaVersion": "1.0",
        "mediaRoot": str(media_root),
        "updatedAt": _now(),
        "scanDepth": MAX_INDEX_DEPTH,
        "truncated": truncated,
        "files": entries,
    }


CONFIG_FILE = "config.json"
LIBRARY_PREFIX = "library:"


def read_config(jobs: Path) -> dict:
    """Panel-owned settings that must not need a helper restart to change.

    The roots are the helper's, but the editor is the one who knows where the
    music lives -- making them relaunch the watcher to point at a folder would
    put the terminal back in the loop this whole design exists to remove.
    """
    try:
        return json.loads((jobs / CONFIG_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_music_root(jobs: Path, fallback: Path | None) -> Path | None:
    raw = read_config(jobs).get("musicRoot")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        print(f"warning: musicRoot in config.json is not a folder: {raw}", file=sys.stderr)
    return fallback


def resolve_music(value: str, media_root: Path, music_root: Path | None) -> Path | None:
    """Turn a request's `music` value into a path.

    `library:` marks the separate music folder. The prefix is explicit rather
    than probing both roots in turn, because the failure mode of guessing is a
    promo scored with the wrong track -- silent, and only noticed on playback.
    """
    if not value or value in ("auto", "none"):
        return None
    if value.startswith(LIBRARY_PREFIX):
        rel = value[len(LIBRARY_PREFIX):]
        if not music_root:
            return None
        return music_root / rel
    path = Path(value)
    return path if path.is_absolute() else media_root / value


def write_music_index(jobs: Path, music_root: Path | None) -> None:
    """Index the music library, when one is configured."""
    if not music_root:
        (jobs / "music-index.json").write_text(json.dumps({
            "schemaVersion": "1.0",
            "musicRoot": None,
            "updatedAt": _now(),
            "truncated": False,
            "files": [],
        }, indent=2) + "\n")
        return
    index = build_media_index(music_root)
    # A library holds tracks, not rushes; anything with a picture is not one.
    index["files"] = [f for f in index["files"] if f["hasAudio"] and not f["hasVideo"]]
    index["musicRoot"] = index.pop("mediaRoot")
    (jobs / "music-index.json").write_text(json.dumps(index, indent=2) + "\n")


def write_media_index(jobs: Path, media_root: Path) -> None:
    index = build_media_index(media_root)
    if index["truncated"]:
        print(
            f"warning: media index stopped at {MAX_INDEX_FILES} files; "
            f"some footage or music under {media_root} is not listed in the panel",
            file=sys.stderr,
        )
    (jobs / "media-index.json").write_text(json.dumps(index, indent=2) + "\n")


def _num(value) -> str:
    """A number the way a person writes it: "2", not "2.0"; "0.5" stays "0.5"."""
    f = float(value)
    return str(int(f)) if f == int(f) else str(f)


def request_to_argv(
    request: dict, jobs: Path, media_root: Path, work_dir: Path,
    music_root: Path | None = None,
) -> list[str]:
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
    if options.get("cutRate"):
        argv += ["--cut-rate", _num(options["cutRate"])]
    if options.get("look"):
        argv += ["--look", options["look"]]
    # A description is matched against pictures, so it implies visual cutting.
    # Requiring the editor to tick a box as well would be a trap: the prompt
    # would be accepted, silently ignored, and the edit would come back cut to
    # speech with no explanation.
    story = (options.get("story") or "").strip()
    if options.get("visual") or story:
        argv += ["--visual"]
    if story:
        argv += ["--story", story]
    # The engine reads the sentence too and will turn this on itself; the flag
    # is for the checkbox, and for a prompt that says it in a way only one side
    # happens to recognise.
    if options.get("subtitles"):
        argv += ["--subtitles"]
    if options.get("protectSpeech"):
        argv += ["--protect-speech"]
    if options.get("removeSilence"):
        argv += ["--remove-silence"]
        argv += ["--silence-allowed", _num(options.get("silenceAllowed", 0.5))]
    if options.get("model"):
        argv += ["--model", options["model"]]
    if options.get("language"):
        argv += ["--language", options["language"]]

    if music_root:
        argv += ["--music-root", str(music_root)]

    music = options.get("music", "auto")
    if music == "none":
        argv += ["--no-music"]
    elif music and music != "auto":
        chosen = resolve_music(music, media_root, music_root)
        if chosen is None:
            raise ValueError(
                f"music {music!r} names the library, but no music folder is set "
                f"-- choose one in the panel"
            )
        argv += ["--music", str(chosen)]

    chunk = options.get("musicChunk") or {}
    if chunk:
        if music in ("none", "auto") or not music:
            # A chunk is a span of a PARTICULAR track. Against "automatic" it
            # would silently apply to whatever the engine happened to find.
            raise ValueError(
                "a music start or length was set without choosing a track -- "
                "pick one in the Music dropdown, or clear the start and length"
            )
        if chunk.get("startSeconds"):
            argv += ["--music-start", str(chunk["startSeconds"])]
        if chunk.get("lengthSeconds"):
            argv += ["--music-length", str(chunk["lengthSeconds"])]
        if chunk.get("snapToBeat") is False:
            argv += ["--no-music-snap"]

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


def process(path: Path, jobs: Path, media_root: Path, work_dir: Path,
            music_root: Path | None = None) -> bool:
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

    try:
        argv = request_to_argv(request, jobs, media_root, work_dir, music_root)
    except ValueError as exc:
        write_status(jobs, job_id, "failed", str(exc))
        return False
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


REPORT_REQUEST = "report.request"


def run_report(jobs: Path, verbose: bool = True) -> None:
    """Collect a diagnostic bundle because the panel asked for one.

    The panel cannot run a shell script -- UXP has no child process -- and an
    editor should not have to open Terminal to report a bug. So the panel drops
    a marker in the jobs folder, which the helper is already watching, and the
    helper runs the collector it was going to run anyway.

    The result path goes back through a file the panel polls, because that is
    the only channel these two have.
    """
    import subprocess

    marker = jobs / REPORT_REQUEST
    out = jobs / "report.result.json"
    script = Path(__file__).resolve().parent.parent / "report.sh"
    if verbose:
        print(f"[{_now()}] {REPORT_REQUEST}", file=sys.stderr)
    try:
        done = subprocess.run(["bash", str(script)], capture_output=True,
                              text=True, timeout=300)
        # The script prints the path it wrote; the last .zip it mentions is it.
        zips = [w for w in done.stdout.split() if w.endswith(".zip")]
        out.write_text(json.dumps({
            "at": _now(),
            "ok": done.returncode == 0 and bool(zips),
            "path": zips[-1] if zips else None,
            "output": (done.stdout + done.stderr)[-4000:],
        }, indent=2))
    except Exception as exc:
        out.write_text(json.dumps({
            "at": _now(), "ok": False, "path": None, "output": str(exc),
        }, indent=2))
    finally:
        marker.unlink(missing_ok=True)


def run_once(jobs: Path, media_root: Path, work_dir: Path, verbose: bool = True,
             music_root: Path | None = None) -> int:
    if (jobs / REPORT_REQUEST).exists():
        run_report(jobs, verbose)
    handled = 0
    for path in sorted(jobs.glob(f"*{REQUEST_SUFFIX}")):
        marker = claimed_marker(jobs, path)
        if marker.exists() and marker.stat().st_mtime >= path.stat().st_mtime:
            continue
        marker.write_text(_now())
        if verbose:
            print(f"[{_now()}] {path.name}", file=sys.stderr)
        process(path, jobs, media_root, work_dir, music_root)
        handled += 1
    return handled


def watch(jobs: Path, media_root: Path, work_dir: Path, interval: float = POLL_SECONDS,
          music_root: Path | None = None) -> None:
    jobs.mkdir(parents=True, exist_ok=True)
    write_capabilities(jobs)
    write_media_index(jobs, media_root)
    ensure_proxies(media_root, work_dir)
    ensure_library_shots(media_root, work_dir, jobs)
    music_root = resolve_music_root(jobs, music_root)
    write_music_index(jobs, music_root)
    print(f"watching {jobs} (media root {media_root})", file=sys.stderr)
    if music_root:
        print(f"  music library: {music_root}", file=sys.stderr)
    ticks = 0
    while True:
        try:
            # The panel can point at a different music folder at any time, so the
            # config is re-read rather than captured at startup.
            current = resolve_music_root(jobs, music_root)
            if current != music_root:
                music_root = current
                print(f"  music library: {music_root}", file=sys.stderr)
                write_music_index(jobs, music_root)
            run_once(jobs, media_root, work_dir, music_root=music_root)
            # Refresh the index periodically so newly ingested footage appears
            # without restarting the helper.
            ticks += 1
            if ticks % 15 == 0:
                write_media_index(jobs, media_root)
                write_music_index(jobs, music_root)
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoEdit job watcher")
    ap.add_argument("--jobs", required=True, help="folder the panel writes requests into")
    ap.add_argument("--media", required=True, help="media root; request paths are relative to it")
    ap.add_argument("--music", default=None, help="a music library folder outside the footage. The panel can also set this without a restart.")
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

    music_root = Path(args.music).expanduser().resolve() if args.music else None
    if music_root and not music_root.is_dir():
        print(f"error: music folder does not exist: {music_root}", file=sys.stderr)
        return 2

    jobs.mkdir(parents=True, exist_ok=True)
    if args.once:
        write_capabilities(jobs)
        write_media_index(jobs, media_root)
        music_root = resolve_music_root(jobs, music_root)
        write_music_index(jobs, music_root)
        print(
            f"processed {run_once(jobs, media_root, work_dir, music_root=music_root)} request(s)",
            file=sys.stderr,
        )
        return 0

    watch(jobs, media_root, work_dir, args.interval, music_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
