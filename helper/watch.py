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
import re
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
from autoedit import fingerprint as fingerprints        # noqa: E402
from autoedit.options import ASPECT_LABELS, CUT_RATES, PACING  # noqa: E402
from autoedit.progress import parse as parse_progress   # noqa: E402
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


# Characters a filename cannot hold on macOS or Windows, plus control codes.
# Everything else -- including every script on earth -- is allowed through.
# Kept in step with UNSAFE_IN_FILENAME in panel/src/request.js and with the
# jobId pattern in schema/job-request.schema.json.
UNSAFE_IN_FILENAME = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_job_id(job_id: str) -> bool:
    """True when this id can become a filename inside the jobs folder, and only there.

    The schema enforces the same rule, but a request that FAILS the schema still
    has to be reported -- and it is reported by writing `<jobId>.status.json`.
    So the id reaches a path before it has been validated, and the guard cannot
    live in the schema alone.
    """
    return bool(job_id) and not UNSAFE_IN_FILENAME.search(job_id) \
        and re.match(r"[.\s]", job_id) is None


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


# How often a running job may rewrite its status file. The engine narrates a
# line per file and a progress line per pass, which on a big job is a few dozen
# writes -- but a step change is written immediately regardless, because the
# whole point is that the editor sees it move.
STATUS_INTERVAL = 0.5


class _EngineOutput(io.StringIO):
    """The engine's stderr, read as it is written instead of after it finishes.

    This is the link that was missing. `cmd_plan` has always narrated itself --
    which file it is on, which pass it is in -- and all of it was captured into
    a buffer that nobody looked at until the engine returned. On a 28-minute
    reference that meant every word of the commentary arrived at once, minutes
    late, to an editor who had spent those minutes unable to tell a working tool
    from a dead one.

    Still a StringIO, so `getvalue()` keeps working: the failure message and the
    "ready" summary are still built from the whole output at the end.
    """

    def __init__(self, on_line):
        super().__init__()
        self._pending = ""
        self._on_line = on_line

    def write(self, text: str) -> int:
        written = super().write(text)
        # print() sends the text and the newline as separate writes, so lines
        # have to be reassembled here rather than assumed.
        self._pending += text
        while "\n" in self._pending:
            line, _, self._pending = self._pending.partition("\n")
            line = line.strip()
            if not line:
                continue
            try:
                self._on_line(line)
            except Exception:
                # Reporting progress must never be able to fail a job. This
                # writes to a folder the editor chose and can unmount, rename or
                # fill; losing the bar is a disappointment, losing the edit
                # because the bar could not be drawn is not.
                pass
        return written


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
# Raised from 400 once footage could come from several folders. A single folder
# of rushes rarely approaches it; a photo library added as a second root has 414
# videos in it on its own, and at 400 it took 395 of the budget and left five
# for the folder with the actual work in it.
#
# The cost is index size, which is metadata: a few hundred bytes a file, so 2000
# files is under half a megabyte and the panel reads it once.
MAX_INDEX_FILES = 2000

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
def root_id_for(index: int) -> str:
    """The name the engine gives this root, so the shot list agrees with it."""
    return "media" if index == 0 else f"media{index + 1}"


_shots_lock = threading.Lock()
_shots_started: set = set()


def ensure_library_shots(media_root: Path, work_dir: Path, jobs: Path,
                         root_name: str = "media") -> None:
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
                {**sp, "relPath": rel, "root": root_name,
                 "durationSeconds": round(info.duration, 4)}
                for sp in spans
            )
            done += 1
            # Written as it goes. A library of 200 clips takes a long time, and
            # a panel that shows nothing until the last one has finished is
            # indistinguishable from one that is broken.
            _write_shots(jobs, shots, complete=False, root=media_root)
        _write_shots(jobs, shots, complete=True, root=media_root)
        print(f"  shots: {len(shots)} spans from {done} file(s)", file=sys.stderr)

    with _shots_lock:
        key = str(media_root)
        if key in _shots_started:
            return
        _shots_started.add(key)
    threading.Thread(target=worker, name="library-shots", daemon=True).start()


_fingerprint_lock = threading.Lock()
_fingerprint_started: set = set()

# Music libraries to index once the machine is free. A job that asked for a
# reference puts one here; `run_once` drains it after the last job it handled.
_index_when_idle: list = []

# Set while a job is being built. Indexing is decode-bound and so is a job, so
# two of them on the same cores make the edit an editor is waiting on slower --
# and starting after the job is not enough on its own, because the index takes
# minutes and the next job can arrive inside them. The worker stands aside.
_job_running = threading.Event()


def index_music_after_job(music_root: Path | None) -> None:
    """Note that this library is worth listening to, once there is time."""
    if music_root and music_root not in _index_when_idle:
        _index_when_idle.append(music_root)


def ensure_music_fingerprints(music_root: Path | None, work_dir: Path) -> None:
    """Learn what every track in the library sounds like, in the background.

    This is what lets a reference video name its own music. It is here rather
    than in the engine for the same reason proxies are: measured on the real
    103-track library, the first pass is about three minutes, and an editor who
    pressed Create should get a plan now. Matching against whatever has been
    indexed so far costs well under a second.

    Cached by content hash, so this is three minutes once and nothing
    afterwards -- a track already indexed is skipped without being opened.

    **Only ever called because a job asked for a reference.** It used to run at
    startup, which spent three minutes of somebody's machine on a feature they
    may never use: matching only happens when there is a reference to match
    against, so indexing for anyone else is pure cost. And it is queued until
    AFTER the job finishes rather than started alongside it -- this is
    decode-bound and so is the job, and two ffmpeg passes competing for the same
    cores make the edit an editor is waiting on slower. The helper is idle
    between jobs; that is when this should have the machine.
    """
    if not music_root or not music_root.is_dir():
        return

    def worker() -> None:
        done = skipped = failed = 0
        for path in sorted(music_root.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            # The same set the index uses. The library really does hold .mp4s:
            # a track exported as video-less mp4 is common, and five of them are
            # in the library on this machine.
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                out = fingerprints.cache_path(work_dir, content_hash(path))
            except OSError:
                failed += 1
                continue
            if out.exists():
                skipped += 1
                continue
            # Wait for the machine rather than compete for it. A track is a
            # second or two, so this gives way promptly once a job starts.
            while _job_running.is_set():
                time.sleep(1.0)
            try:
                fingerprints.save(out, fingerprints.fingerprint(
                    path, seconds=fingerprints.INDEX_SECONDS))
                done += 1
            except Exception as exc:       # one unreadable track is not fatal
                print(f"  music: could not index {path.name}: {exc}", file=sys.stderr)
                failed += 1
        if done or failed:
            print(f"  music: indexed {done} track(s), {skipped} already known"
                  f"{f', {failed} failed' if failed else ''}", file=sys.stderr)

    with _fingerprint_lock:
        key = str(music_root)
        if key in _fingerprint_started:
            return
        _fingerprint_started.add(key)
    threading.Thread(target=worker, name="music-fingerprints", daemon=True).start()


def forget_music_fingerprints() -> None:
    """Let the next call re-scan, after the library moves."""
    with _fingerprint_lock:
        _fingerprint_started.clear()
    _index_when_idle.clear()


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


# root path -> the shots found under it, and whether that scan has finished.
# The file is the UNION of these. Writing it from one root's worker alone made
# two roots clobber each other -- whichever finished last won, and half the
# library's shots simply vanished from the swap list.
_shots_by_root: dict = {}


def _write_shots(jobs: Path, shots: list[dict], complete: bool,
                 root: Path | None = None) -> None:
    if root is not None:
        _shots_by_root[str(root)] = (shots, complete)
    merged: list[dict] = []
    for found, _ in _shots_by_root.values():
        merged.extend(found)
    if root is None:
        merged = shots
    (jobs / "library-shots.json").write_text(json.dumps({
        "generatedAt": _now(),
        # Complete only when every root has finished. A partial file is usable
        # and says so; claiming complete while a drive is still being read
        # would tell the panel there is nothing more coming.
        "complete": all(done for _, done in _shots_by_root.values()) if root is not None else complete,
        "files": merged,
    }, indent=2) + "\n")


def forget_shots(keep: "list[Path] | None" = None) -> None:
    """Drop shots for roots that are no longer configured.

    Replacing a folder used to leave its clips in the swap list until the new
    scan happened to overwrite them -- so an editor saw shots from footage that
    was no longer part of the job, which is worse than seeing none.
    """
    if keep is None:
        _shots_by_root.clear()
        return
    wanted = {str(p) for p in keep}
    for key in [k for k in _shots_by_root if k not in wanted]:
        _shots_by_root.pop(key, None)


# `.C1367.MP4.icloud` -- what iCloud leaves behind when it evicts a file to
# save space. The original name is inside it.
_EVICTED = re.compile(r"^\.(?P<name>.+)\.icloud$")


def iter_media(media_root: Path, max_depth: int = MAX_INDEX_DEPTH,
               evicted: list | None = None):
    """Media files under the root, breadth-first so top-level rushes come first.

    `evicted` collects the names of files iCloud has moved to the cloud, which
    are not readable and not visible to any of the tests below.
    """
    evicted = [] if evicted is None else evicted
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
                # A file iCloud has evicted is not there: it is a hidden
                # placeholder named `.C1367.MP4.icloud`, so it fails BOTH the
                # dotfile skip and the extension test. The footage is visible in
                # Finder with a cloud badge and invisible here, and the panel
                # said "no video files in the media root", which is true and
                # useless.
                gone = _EVICTED.match(path.name)
                if gone and Path(gone.group("name")).suffix.lower() in MEDIA_EXTENSIONS:
                    evicted.append(gone.group("name"))
                continue
            if path.is_dir():
                if depth < max_depth:
                    folders.append((path, depth + 1))
            elif path.suffix.lower() in MEDIA_EXTENSIONS:
                yield path
        queue.extend(folders)


def build_media_index_across(roots: list[Path], jobs: Path | None = None,
                             max_files: int = MAX_INDEX_FILES) -> dict:
    """One index over several roots, each file tagged with the root it came from.

    `relPath` is per-root, so two drives may both hold `C0001.MP4` without
    colliding -- they differ by `root`, and the panel resolves each through the
    folder it was granted for that root. A single flat list keyed by filename
    would have made the second drive's clips shadow the first's.
    """
    merged: dict = {
        "schemaVersion": "1.0",
        "mediaRoot": str(roots[0]) if roots else "",
        "mediaRoots": [str(r) for r in roots],
        "roots": {},
        "updatedAt": _now(),
        "scanDepth": MAX_INDEX_DEPTH,
        "truncated": False,
        "evicted": [],
        "evictedCount": 0,
        "unreachable": unreachable_media_roots(jobs) if jobs else [],
        "files": [],
    }
    # An equal share each, and whatever a folder does not use passes to the ones
    # AFTER it. First-come let one large folder take the whole budget and leave
    # the next with nothing -- which is precisely what happened: a photo library
    # took 395 of 400 and the folder holding the actual rushes got five.
    #
    # A big folder listed first therefore leaves its share unused rather than
    # reclaiming what a later small one did not need. That is the trade for one
    # pass: no folder can be starved, which is the property that matters, and
    # the alternative is scanning twice to find out.
    share = max(1, max_files // max(1, len(roots)))
    spare = max_files - share * len(roots)
    truncated_roots: list[str] = []
    for i, root in enumerate(roots):
        name = "media" if i == 0 else f"media{i + 1}"
        merged["roots"][name] = str(root)
        one = build_media_index(root, max_files=share + spare)
        for entry in one["files"]:
            merged["files"].append({**entry, "root": name})
        spare = max(0, share + spare - len(one["files"]))
        if one["truncated"]:
            merged["truncated"] = True
            truncated_roots.append(str(root))
        merged["evicted"].extend(one["evicted"])
        merged["evictedCount"] += one["evictedCount"]
    merged["truncatedRoots"] = truncated_roots
    merged["evicted"] = sorted(merged["evicted"])[:20]
    return merged


def build_media_index(media_root: Path, max_files: int = MAX_INDEX_FILES) -> dict:
    """Index the media root so the panel can tell footage from a music bed.

    The panel cannot probe -- UXP has no ffprobe -- so extension alone would have
    to decide, and that is not enough: the music track that turned up in real use
    was a .mp4 with no video stream, which showed up in the clip picker as if it
    were footage.
    """
    entries: list[dict] = []
    evicted: list[str] = []
    truncated = False
    for path in iter_media(media_root, evicted=evicted):
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
        # Named, not just counted: "6 files are in iCloud" is actionable in a
        # way that an empty picker is not.
        "evicted": sorted(evicted)[:20],
        "evictedCount": len(evicted),
        "files": entries,
    }


CONFIG_FILE = "config.json"
LIBRARY_PREFIX = "library:"

# `media2:day2/C0001.MP4` -- which root a requested clip came from. The same
# explicit-prefix idiom as `library:` above, and for the same reason: two drives
# may both hold C0001.MP4, and probing the roots in turn would pick whichever
# came first and be silently wrong about which shoot the editor selected.
_ROOTED = re.compile(r"^(?P<root>media[0-9]*):(?P<rel>.+)$")


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


# Roots already reported as missing. The watch loop re-resolves every poll, so
# without this an unplugged drive writes a warning every 2 seconds: one report
# arrived 6063 lines long, 6049 of them the same sentence about the same LaCie.
# The log is what gets sent back when something goes wrong, so burying the four
# interesting lines is not a cosmetic problem.
_WARNED_UNREACHABLE: set[str] = set()


def resolve_media_roots(jobs: Path, fallback: Path) -> list[Path]:
    """Every folder the footage lives in, in order.

    One media root was always a simplification. An editor keeping this shoot on
    the desktop and last month's on an external drive is the ordinary case, and
    until now the second folder simply did not exist as far as this tool was
    concerned.

    A root that is not currently a folder is DROPPED, not fatal: an unplugged
    drive is a Tuesday, and the rest of the library must keep working. Which
    ones are missing is reported in the index so the panel can say so rather
    than quietly showing fewer clips.
    """
    config = read_config(jobs)
    raw = config.get("mediaRoots")
    if not raw:
        single = config.get("mediaRoot")
        raw = [single] if single else []
    roots: list[Path] = []
    for item in raw:
        candidate = Path(str(item)).expanduser()
        if candidate.is_dir():
            resolved = candidate.resolve()
            if resolved not in roots:
                roots.append(resolved)
        else:
            # Warn on the change, not on the condition. Reachable again clears
            # the flag, so unplugging the same drive tomorrow is reported again.
            if str(item) not in _WARNED_UNREACHABLE:
                _WARNED_UNREACHABLE.add(str(item))
                print(f"warning: media root is not reachable: {item}", file=sys.stderr)
            continue
        _WARNED_UNREACHABLE.discard(str(item))
    return roots or [fallback]


def unreachable_media_roots(jobs: Path) -> list[str]:
    """Configured roots that are not folders right now -- usually an unplugged
    drive. Named so the panel can say which, rather than showing a short list of
    clips and no reason for it."""
    config = read_config(jobs)
    raw = config.get("mediaRoots") or ([config["mediaRoot"]] if config.get("mediaRoot") else [])
    return [str(x) for x in raw if not Path(str(x)).expanduser().is_dir()]


def resolve_media_root(jobs: Path, fallback: Path) -> Path:
    """Where the footage is NOW, not where it was when setup ran.

    The media root was fixed at install time in the launchd plist, and moving
    the rushes anywhere else silently pointed the whole tool at an empty folder
    -- the panel said "no video files" and there was no way to correct it
    without re-running setup.sh in a Terminal, which is precisely what an editor
    cannot be asked to do.

    The music root has worked this way from the start. This is the same thing
    for the folder that matters more.
    """
    raw = read_config(jobs).get("mediaRoot")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        print(f"warning: mediaRoot in config.json is not a folder: {raw}", file=sys.stderr)
    return fallback


def resolve_reference_root(jobs: Path) -> Path | None:
    """Where reference videos live, if the editor has nominated a folder.

    A folder rather than a file picker: UXP grants folder access and has no file
    picker this panel has ever used, so a reference is chosen from a folder the
    editor granted once -- the same shape as the music library.
    """
    raw = read_config(jobs).get("referenceRoot")
    if not raw:
        return None
    candidate = Path(str(raw)).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    print(f"warning: referenceRoot in config.json is not a folder: {raw}", file=sys.stderr)
    return None


# Anything with a scheme is a link to fetch; anything else is a path.
_URLISH = re.compile(r"^https?://", re.I)


# Caption tracks worth asking for. The video's own language comes back under
# whatever code the platform uses, so these are the ones this tool can read
# cues in; anything else still yields timings, which is most of the value.
SUBTITLE_LANGUAGES = ["ja", "en"]
SUBTITLE_SUFFIXES = frozenset({".vtt", ".srt", ".ass", ".ssa", ".json3", ".srv1",
                               ".srv2", ".srv3", ".ttml"})
# `.captions-tried` marks a reference asked twice with nothing to show for it.
# Not a subtitle suffix, so `_downloaded_video` would otherwise return it.
SUBTITLE_SUFFIXES = SUBTITLE_SUFFIXES | {".captions-tried"}


def reference_cache_path(url: str, work_dir: Path) -> Path:
    """Where a downloaded reference lives.

    Keyed on the URL and a version tag, following the convention in `proxy.py`:
    the recipe of the transform belongs in the key, so changing how references
    are fetched does not silently reuse what the old rule produced.

    No mtime and no content hash, because neither exists before the download.
    """
    key = hashlib.sha256(f"{url.strip()}|v3".encode()).hexdigest()[:20]
    return work_dir / "reference" / key


def _fetch_captions(yt_dlp, url: str, stem: Path) -> None:
    """The platform's own captions, beside the video. Best effort, always.

    A SECOND pass rather than options on the first, because the two have
    different stakes. The video is the job; captions are a bonus that says where
    someone is speaking -- seconds of work against the minutes Whisper would
    spend on the same file.

    Asking for both in one call is not the same thing. Measured: a 429 on the
    second language aborted the item and left NO video at all, and yt-dlp's own
    `ignoreerrors: "only_download"` reported the failure without preventing it.
    Rate limits on captions are ordinary; losing the reference over one is not.
    """
    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": SUBTITLE_LANGUAGES,
        "subtitlesformat": "vtt",
        "outtmpl": f"{stem}.%(ext)s",
        "quiet": True, "no_warnings": True, "noprogress": True,
    }
    tried = stem.parent / f"{stem.name}.captions-tried"
    if tried.exists():
        return          # asked twice already; this video simply has none
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        # Reported, not raised: the reference still works without captions, and
        # the engine says so when it falls back to reading shot lengths.
        #
        # Checked afterwards rather than assumed, because a failure here is
        # usually PARTIAL. A real 429 on the English track raised while the
        # Japanese one had already landed -- announcing "no captions available"
        # would have been flatly untrue about the track the tool then used.
        if not _has_captions(stem):
            print(f"  reference: no captions available ({exc})", file=sys.stderr)
        else:
            print(f"  reference: some caption tracks unavailable ({exc})",
                  file=sys.stderr)
    if not _has_captions(stem):
        # Marked only when the attempt produced nothing, so a video with no
        # captions is asked twice and then left alone rather than fetched at
        # every job for the rest of its life.
        try:
            tried.write_text("")
        except OSError:
            pass


def _has_captions(stem: Path) -> bool:
    return any(stem.parent.glob(f"{stem.name}*{suffix}")
               for suffix in SUBTITLE_SUFFIXES) if stem.parent.exists() else False


def _downloaded_video(stem: Path) -> Path | None:
    """The video beside a cache stem, ignoring the caption tracks next to it.

    The caption files share the stem -- `<key>.mp4` and `<key>.ja.vtt` -- and
    `.ja.vtt` sorts BEFORE `.mp4`, so taking the first match of `<key>.*` would
    hand back a subtitle file and call it the reference.
    """
    if not stem.parent.exists():
        return None
    found = [f for f in sorted(stem.parent.glob(f"{stem.name}.*"))
             if f.suffix.lower() not in SUBTITLE_SUFFIXES]
    return found[0] if found else None


def download_reference(url: str, work_dir: Path, on_progress=None) -> Path:
    """Fetch a reference video once and keep it. Returns a local path.

    `on_progress` is called with a percentage, 0-100, of the DOWNLOAD -- not of
    the job. A 28-minute video is minutes of waiting before the analysis it
    feeds has even started, and that wait had nothing to say for itself.

    Imported here rather than at module scope: a helper that never sees a URL
    should not fail to start because a package is missing, and the message when
    it IS missing has to name the fix.

    As a MODULE, never as the `yt-dlp` binary -- launchd hands this process a
    bare PATH with no .venv/bin on it, so the console script is not there. The
    same trap already caught ffmpeg once.
    """
    stem = reference_cache_path(url, work_dir)
    existing = _downloaded_video(stem)
    if existing:
        # The video is cached, but the captions beside it may not be: a 429 on
        # the caption fetch is ordinary and does not stop the video arriving.
        # Without this the whole feature is disabled permanently by one
        # transient failure, silently, because captions are otherwise only ever
        # attempted while downloading.
        if not _has_captions(stem):
            import yt_dlp                                    # noqa: PLC0415
            _fetch_captions(yt_dlp, url, stem)
        return existing

    try:
        import yt_dlp
    except ImportError as exc:
        raise ValueError(
            "downloading a reference video needs yt-dlp -- press Update in the "
            f"panel, or re-run ./setup.sh ({exc})"
        ) from exc

    stem.parent.mkdir(parents=True, exist_ok=True)
    options = {
        # 720p is plenty: this is measured for its cutting pattern and then
        # thrown away, and a 4K download costs minutes for nothing.
        #
        # H.264 by preference, falling back to whatever exists. YouTube serves
        # AV1 at this size by default, and the engine decodes in software --
        # measured, AV1 costs 15.3s to decode 120s where the same content in
        # H.264 costs 4.1s. A slightly larger download buys back several minutes
        # of scanning on a long reference.
        "format": (
            "bv*[height<=720][vcodec^=avc1]+ba/b[height<=720][vcodec^=avc1]/"
            "bv*[height<=720]+ba/b[height<=720]/b"
        ),
        "outtmpl": f"{stem}.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        # yt-dlp's own progress goes to a terminal nobody is looking at: this
        # process is a launchd agent. The hook below goes where it can be seen.
        "noprogress": True,
    }
    if on_progress:
        last = [0.0]

        def hook(d: dict) -> None:
            # Called per chunk, which is many times a second. The status file is
            # read by a panel polling every two seconds; writing it faster than
            # that is work nobody sees.
            if d.get("status") != "downloading":
                return
            now = time.monotonic()
            if now - last[0] < 1.0:
                return
            last[0] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total > 0:
                on_progress(100.0 * done / total)

        options["progress_hooks"] = [hook]
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise ValueError(
            f"could not download the reference video: {exc}"
        ) from exc

    _fetch_captions(yt_dlp, url, stem)

    found = _downloaded_video(stem)
    if not found:
        raise ValueError("the reference video downloaded but no file appeared")
    return found


def resolve_reference(option: dict, jobs: Path, work_dir: Path,
                      media_roots: list[Path], on_progress=None) -> Path:
    """A request's `reference` option as a local path. Raises ValueError."""
    source = option.get("source")
    value = str(option.get("value") or "").strip()
    if not value:
        raise ValueError("a reference video was asked for but none was named")

    if source == "url" or _URLISH.match(value):
        return download_reference(value, work_dir, on_progress)

    candidate = Path(value).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return candidate
    roots = [r for r in [resolve_reference_root(jobs), *media_roots] if r]
    for root in roots:
        here = root / value
        if here.exists():
            return here
    raise ValueError(
        f"could not find the reference video {value!r} -- pick the reference "
        f"folder in panel settings, or choose it again"
    )


def resolve_music_root(jobs: Path, fallback: Path | None) -> Path | None:
    raw = read_config(jobs).get("musicRoot")
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        print(f"warning: musicRoot in config.json is not a folder: {raw}", file=sys.stderr)
    return fallback


def _media_path(value: str, media_roots: list[Path]) -> str:
    """A requested clip as an absolute path.

    Plain `C0001.MP4` means the first root, which is what every request written
    before there was more than one root says. `media2:C0001.MP4` names its root
    explicitly.
    """
    match = _ROOTED.match(str(value))
    if not match:
        return str(media_roots[0] / str(value))
    index = 0 if match.group("root") == "media" else int(match.group("root")[5:]) - 1
    root = media_roots[index] if 0 <= index < len(media_roots) else media_roots[0]
    return str(root / match.group("rel"))


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


def write_media_index(jobs: Path, media_root, roots: list[Path] | None = None) -> None:
    index = (build_media_index_across(roots, jobs) if roots
             else build_media_index(media_root))
    if index["evictedCount"] and not index["files"]:
        print(
            f"warning: {index['evictedCount']} file(s) under {media_root} are in "
            f"iCloud and not downloaded, so nothing is readable. In Finder, "
            f"select them and File > Download Now, or turn off "
            f"'Optimise Mac Storage'.",
            file=sys.stderr,
        )
    if index["truncated"]:
        # Name the folder that actually overflowed. This used to name the first
        # root regardless, so it pointed at the wrong folder the moment there
        # was more than one.
        where = ", ".join(index.get("truncatedRoots") or [str(media_root)])
        print(
            f"warning: media index stopped at {MAX_INDEX_FILES} files; "
            f"some footage or music under {where} is not listed in the panel",
            file=sys.stderr,
        )
    (jobs / "media-index.json").write_text(json.dumps(index, indent=2) + "\n")


def _num(value) -> str:
    """A number the way a person writes it: "2", not "2.0"; "0.5" stays "0.5"."""
    f = float(value)
    return str(int(f)) if f == int(f) else str(f)


def request_to_argv(
    request: dict, jobs: Path, media_root: Path, work_dir: Path,
    music_root: Path | None = None, media_roots: list[Path] | None = None,
    reference: Path | None = None,
) -> list[str]:
    """Map a job request onto engine arguments."""
    job_id = request["jobId"]
    media_roots = media_roots or [media_root]
    options = request.get("options") or {}
    duration = options.get("duration") or {}

    argv = [
        "plan",
        "--job", job_id,
        "--recipe", request["recipe"],
        # One per root, in order: the engine names them media, media2, media3
        # by position, so the order here is the contract.
        *[arg for r in media_roots for arg in ("--media-root", str(r))],
        "--work-dir", str(work_dir),
        "--out", str(jobs / f"{job_id}.editplan.json"),
        "--media", *[_media_path(m, media_roots) for m in request["media"]],
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
    # Already a local path by the time it reaches here: acquiring it may mean a
    # download, which needs to happen where a status can be written, not inside
    # a pure argv builder.
    if reference is not None:
        argv += ["--reference", str(reference)]
        if (options.get("reference") or {}).get("matchContent") is False:
            argv += ["--reference-rhythm-only"]

    fps = options.get("frameRate")
    if fps and fps != "auto":
        argv += ["--fps", str(fps)]
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
    except ImportError:
        return problems
    schema_file = Path(__file__).resolve().parents[1] / "schema" / "job-request.schema.json"
    try:
        jsonschema.Draft7Validator(json.loads(schema_file.read_text())).validate(request)
    except jsonschema.ValidationError as exc:
        problems.append(_readable_schema_error(exc))
    except Exception as exc:
        problems.append(str(exc).split("\n")[0])
    return problems


def _readable_schema_error(error) -> str:
    """jsonschema's own wording, except where it would show an editor a regex.

    Most of its messages are already fine -- "'widescreen' is not one of
    [...]" says what to do. A failed `pattern` does not: an editor whose
    Japanese job name was rejected was told it "does not match
    '^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$'", which is true and useless.
    """
    field = ".".join(str(p) for p in error.absolute_path) or "request"
    if error.validator != "pattern":
        return str(error.message).split("\n")[0]
    if field == "jobId":
        return ("the name can be in any language, but not contain / \\ : * ? \" < > | "
                "and not start with a dot or a space")
    return f"{field}: {error.instance!r} is not in the expected form"


def process(path: Path, jobs: Path, media_root: Path, work_dir: Path,
            music_root: Path | None = None,
            media_roots: list[Path] | None = None) -> bool:
    """Run one request. Returns True when a plan was produced."""
    job_id = path.stem.replace(".request", "")
    try:
        request = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        write_status(jobs, job_id, "failed", f"request is not valid JSON: {exc}")
        return False

    # The file is already named for the job, so the stem is safe by construction;
    # the id inside the request is not, and it is about to become a path.
    requested = str(request.get("jobId") or "")
    if requested and safe_job_id(requested):
        job_id = requested
    problems = validate_request(request)
    if problems:
        write_status(jobs, job_id, "failed", "; ".join(problems))
        return False

    started = _now()
    write_status(jobs, job_id, "analysing", "starting", startedAt=started, percent=0)

    try:
        reference = None
        option = (request.get("options") or {}).get("reference")
        if option:
            # Said out loud before it starts. A download can take a minute and
            # the panel polls a status file: without this it sits on "starting"
            # and reads as a job that has hung.
            fetching = option.get("source") == "url"
            write_status(jobs, job_id, "analysing",
                         "fetching the reference video" if fetching
                         else "reading the reference video",
                         startedAt=started, percent=0,
                         step="progress.fetching" if fetching else "progress.reference")

            def downloading(pct: float) -> None:
                # The BAR stays at nought and the LABEL carries the percentage.
                # Downloading is not analysis: none of the run's work is done
                # when it finishes, so moving the bar to 60% and then back to
                # nought would be a lie told twice. The number climbing in the
                # label is what proves the tool is alive, which is the thing
                # actually being asked for.
                write_status(jobs, job_id, "analysing",
                             f"fetching the reference video ({pct:.0f}%)",
                             startedAt=started, percent=0,
                             step="progress.fetching", stepDetail=f"{pct:.0f}%")

            reference = resolve_reference(option, jobs, work_dir,
                                          media_roots or [media_root],
                                          downloading if fetching else None)
            # This job wanted a reference, so this machine is one where the
            # reference's music is worth being able to look up. Queued, not
            # started: see ensure_music_fingerprints.
            index_music_after_job(music_root)

        argv = request_to_argv(request, jobs, media_root, work_dir, music_root,
                                media_roots, reference)
    except ValueError as exc:
        write_status(jobs, job_id, "failed", str(exc))
        return False
    # Where the run has got to, kept across lines so each status write says
    # everything currently known rather than only what just changed.
    shown = {"percent": 0, "step": "", "detail": "", "message": "starting"}
    last_write = 0.0

    def on_line(line: str) -> None:
        nonlocal last_write
        reading = parse_progress(line)
        if reading:
            fraction, key, detail = reading
            moved = key != shown["step"]
            shown.update(percent=int(fraction * 100), step=key, detail=detail)
        else:
            # Prose. It is the engine's own words and it is English, which is why
            # the bar is driven by the key above and not by this -- but it is
            # also the detail that makes a log worth reading, so it is kept.
            moved = False
            shown["message"] = line
        now = time.monotonic()
        # A step change is written at once; everything else waits its turn. The
        # editor is watching for movement, and movement is the step changing.
        if not moved and now - last_write < STATUS_INTERVAL:
            return
        last_write = now
        write_status(jobs, job_id, "analysing", shown["message"], startedAt=started,
                     percent=shown["percent"], step=shown["step"],
                     stepDetail=shown["detail"])

    captured = _EngineOutput(on_line)
    try:
        # The engine reports progress on stderr; capture it so the failure message
        # in the status file is the engine's own words rather than a stack trace,
        # and stream it so the panel can draw a bar while the run is still going.
        with redirect_stderr(captured), redirect_stdout(io.StringIO()):
            code = engine_main(argv)
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        write_status(jobs, job_id, "failed", traceback.format_exc(limit=3).strip().split("\n")[-1])
        return False

    output = captured.getvalue().strip()
    # Progress lines are for the bar, not for the editor. Left in, the last one
    # would become the "ready" summary -- so a finished job would report
    # "PROGRESS 0.9990 progress.assemble" where it used to say how many clips it
    # had made.
    lines = [l.strip() for l in output.splitlines()
             if l.strip() and parse_progress(l.strip()) is None]

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
        startedAt=started, percent=100, planFile=plan_file.name,
        warnings=[l for l in lines if l.lower().startswith("warning")],
    )

    # AFTER the new plan exists and the status says ready, never before. The
    # sweep is allowed to remove old plans precisely because a newer one is
    # already on disk; a run that failed above returned long ago and swept
    # nothing.
    # `work_dir` here is the CACHE ROOT -- it is what goes to `--work-dir`, and
    # the engine makes the per-job directory under it as `cache_root / job`.
    # Passing its parent would have aimed the sweep at the jobs folder itself.
    swept = prune_plans(jobs, work_dir)
    if swept:
        print(f"swept {len(swept)} old plan(s): {', '.join(swept[:8])}"
              f"{' ...' if len(swept) > 8 else ''}", file=sys.stderr)
    return True


# --- keeping the jobs folder from silting up ---------------------------------
#
# Nothing ever deleted a job's files, so every run left five behind for good.
# Measured on this machine after a few months: 85 plans, 357 files, and a
# dropdown of `eadaeda`, `klklklkl`, `dial4` that an editor has to read past to
# find the one they made a minute ago.
#
# On disk it is small -- 6.9MB of job files against 2.3GB of proxies -- so this
# is a tidiness problem, not a space one, and it is fixed conservatively.

# How many un-kept plans survive. Marked plans are exempt and are not counted
# against this, because that is what marking one means.
PLANS_RETAINED = 5

KEEP_SUFFIX = ".keep"

# What a sweep removes. Everything here is derived from the request and can be
# produced again by running the job again.
SWEPT_SUFFIXES = (".editplan.json", ".status.json", ".request.json", ".sqpreset")

# What a sweep must never remove, and why:
#
#   .srt          the panel imports subtitles into the Premiere project BY PATH
#                 (`project.importFiles([srtPath], ...)`), so the project holds
#                 a reference to this exact file. Deleting one turns it into
#                 missing media inside an editor's project.
#
#   .receipt.json the only record anywhere of the swaps and exclusions an editor
#                 made by hand -- apply.js says so in as many words, because
#                 swaps are never written back into the plan.
#
#   .keep         the marker itself.
#
# The line is: if something outside this folder points at it, or it is the only
# copy of a decision a person made, it stays.


def plan_job_ids(jobs: Path) -> list[tuple[str, float]]:
    """Every plan in the folder as (job id, modified time), newest first."""
    found = []
    for f in jobs.glob("*.editplan.json"):
        job_id = f.name[: -len(".editplan.json")]
        if not job_id or not safe_job_id(job_id):
            continue
        try:
            found.append((job_id, f.stat().st_mtime))
        except OSError:
            continue
    return sorted(found, key=lambda pair: -pair[1])


def is_kept(jobs: Path, job_id: str) -> bool:
    return (jobs / f"{job_id}{KEEP_SUFFIX}").exists()


def prune_plans(jobs: Path, cache_root: Path | None = None,
                retain: int = PLANS_RETAINED) -> list[str]:
    """Sweep un-kept plans past the newest few. Returns the job ids removed.

    Called only after a new plan has been written successfully. That ordering is
    the safety property: a run that fails never sweeps, so the last plan that
    worked cannot be destroyed by a run that did not.
    """
    doomed = [job_id for job_id, _ in plan_job_ids(jobs)
              if not is_kept(jobs, job_id)][retain:]

    removed = []
    for job_id in doomed:
        gone = False
        for suffix in SWEPT_SUFFIXES:
            try:
                (jobs / f"{job_id}{suffix}").unlink(missing_ok=True)
                gone = True
            except OSError:
                pass
        if cache_root:
            _remove_job_cache(cache_root, job_id)
        if gone:
            removed.append(job_id)
    return removed


# Directories under the cache root that belong to everyone rather than to one
# job. They sit at the same level as the per-job folders, so a job that happened
# to be NAMED one of these would aim the sweep at a shared cache -- and
# `proxies` is 2.3GB of work on this machine, rebuilt only by re-encoding every
# clip in the library. Nothing stops an editor calling a job "models".
SHARED_CACHE_DIRS = frozenset({
    "proxies", "models", "thumbs", "visual", "transcripts", "reference",
})


def _remove_job_cache(cache_root: Path, job_id: str) -> None:
    """The per-job work directory, which holds the extracted audio.

    Checked rather than trusted before an rmtree. `safe_job_id` already makes a
    traversal impossible, but this is the one operation here that deletes a tree
    rather than a file, and the cost of being wrong is not symmetrical with the
    cost of one extra comparison.
    """
    if job_id in SHARED_CACHE_DIRS:
        return
    try:
        root = cache_root.resolve()
        target = (cache_root / job_id).resolve()
    except OSError:
        return
    if target.parent != root or target == root or not target.is_dir():
        return
    shutil.rmtree(target, ignore_errors=True)


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


UPDATE_REQUEST = "update.request"


def run_update(jobs: Path, verbose: bool = True) -> None:
    """Update the tool because the panel asked.

    **Detached, and that is the whole trick.** The last thing update.sh does is
    restart this helper, and a child of the helper dies with it -- launchd stops
    the whole process group. `start_new_session` puts the updater in its own
    session, so it survives the restart it causes and finishes writing its
    answer.

    Nothing is waited on here: this function returns immediately and the panel
    reads the result file when it appears.
    """
    import subprocess

    marker = jobs / UPDATE_REQUEST
    result = jobs / "update.result.json"
    script = Path(__file__).resolve().parent.parent / "update.sh"
    if verbose:
        print(f"[{_now()}] {UPDATE_REQUEST}", file=sys.stderr)
    result.unlink(missing_ok=True)
    try:
        subprocess.Popen(
            ["bash", str(script), "--result", str(result)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        result.write_text(json.dumps(
            {"status": "failed", "detail": str(exc)}, indent=2))
    finally:
        marker.unlink(missing_ok=True)


def run_once(jobs: Path, media_root: Path, work_dir: Path, verbose: bool = True,
             music_root: Path | None = None,
             media_roots: list[Path] | None = None) -> int:
    if (jobs / REPORT_REQUEST).exists():
        run_report(jobs, verbose)
    if (jobs / UPDATE_REQUEST).exists():
        run_update(jobs, verbose)
    handled = 0
    for path in sorted(jobs.glob(f"*{REQUEST_SUFFIX}")):
        marker = claimed_marker(jobs, path)
        if marker.exists() and marker.stat().st_mtime >= path.stat().st_mtime:
            continue
        marker.write_text(_now())
        if verbose:
            print(f"[{_now()}] {path.name}", file=sys.stderr)
        _job_running.set()
        try:
            process(path, jobs, media_root, work_dir, music_root, media_roots)
        finally:
            # In a finally because `process` returns from a dozen places and can
            # raise from any of them, and a flag left set would stall the
            # indexer for the life of the helper.
            _job_running.clear()
        handled += 1

    # Every job in this pass is done, so the machine is ours again.
    while _index_when_idle:
        ensure_music_fingerprints(_index_when_idle.pop(), work_dir)
    return handled


def watch(jobs: Path, media_root: Path, work_dir: Path, interval: float = POLL_SECONDS,
          music_root: Path | None = None) -> None:
    jobs.mkdir(parents=True, exist_ok=True)
    write_capabilities(jobs)
    # The plist value is only a fallback from here on.
    installed_media_root = media_root
    media_roots = resolve_media_roots(jobs, installed_media_root)
    media_root = media_roots[0]
    write_media_index(jobs, media_root, media_roots)
    for i, root in enumerate(media_roots):
        ensure_proxies(root, work_dir)
        ensure_library_shots(root, work_dir, jobs, root_name=root_id_for(i))
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
                # A different folder is a different library, so the guard that
                # stops this running twice has to be released for it. Nothing
                # starts here: the next job that wants a reference will ask.
                forget_music_fingerprints()
            # The footage can move too, and moving it used to break everything
            # quietly until someone re-ran setup.sh.
            moved = resolve_media_roots(jobs, installed_media_root)
            if moved != media_roots:
                media_roots = moved
                media_root = media_roots[0]
                print(f"  media roots: {', '.join(str(r) for r in media_roots)}",
                      file=sys.stderr)
                # Forget the folders that are gone BEFORE rewriting anything, or
                # their clips linger in the swap list looking like part of the
                # job.
                forget_shots(keep=media_roots)
                _write_shots(jobs, [], complete=False)
                write_media_index(jobs, media_root, media_roots)
                for i, root in enumerate(media_roots):
                    ensure_proxies(root, work_dir)
                    ensure_library_shots(root, work_dir, jobs,
                                         root_name=root_id_for(i))
            run_once(jobs, media_root, work_dir, music_root=music_root,
                     media_roots=media_roots)
            # Refresh the index periodically so newly ingested footage appears
            # without restarting the helper.
            ticks += 1
            if ticks % 15 == 0:
                # WITH the roots. Without them this rewrote the index from the
                # first root alone every thirty seconds, so a second folder
                # appeared, worked, and then silently vanished again -- which
                # reads exactly like it never worked.
                write_media_index(jobs, media_root, media_roots)
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
        once_roots = resolve_media_roots(jobs, media_root)
        write_media_index(jobs, media_root, once_roots)
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
