"""EditPlan construction and validation.

Turns per-clip cut decisions into the single document the UXP panel consumes.

The one invariant worth stating plainly: timeline positions accumulate as
integer frames. Source in/out points stay in seconds because source media can
run at a different rate than the sequence, but nothing downstream ever adds two
floats together to decide where a clip lands.
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .detect import KIND_SILENCE, CutPlan
from .music import MAX_BEAT_NUDGE, BeatGrid
from .notes import Note, note
from .recipe import MusicSettings
from .timebase import Timebase
from .transcript import Transcript

SCHEMA_VERSION = "1.0"
GENERATOR_NAME = "premiere-autoedit"
GENERATOR_VERSION = "0.1.0"


class PlanError(RuntimeError):
    pass


def _slack_after(cut_plan: CutPlan) -> dict[int, float]:
    """Seconds of removed SILENCE sitting immediately after each keep.

    That silence is the only room a cut has to move without taking a word with
    it, so it is the budget for a beat nudge. Filler and stutter drops are
    deliberately excluded: extending a clip into one of those would put the "eh"
    back, which is the opposite of what the editor asked for.
    """
    drops = [d for d in (getattr(cut_plan, "drops", None) or []) if d.kind == KIND_SILENCE]
    room: dict[int, float] = {}
    for i, keep in enumerate(cut_plan.keeps):
        for d in drops:
            if abs(d.start - keep.end) < 1e-6:
                room[i] = max(0.0, d.end - d.start)
                break
    return room


@dataclass
class MediaEntry:
    id: str
    rel_path: str
    duration: float
    # Which configured root rel_path hangs off. A music library lives outside the
    # footage tree, so it needs its own root rather than an absolute path -- the
    # plan stays portable, and the NAS move stays a settings change.
    root: str = "media"
    hash: str | None = None
    role: str | None = None
    timebase: Timebase | None = None
    has_video: bool = True
    has_audio: bool = True
    speaker: str | None = None
    width: int = 0
    height: int = 0
    # Absolute, unlike rel_path: a proxy lives in the work cache, not under any
    # configured root, and it is a local build artefact rather than something
    # that should survive a move to a NAS.
    proxy_path: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "relPath": self.rel_path,
            "durationSeconds": round(self.duration, 4),
            "hasVideo": self.has_video,
            "hasAudio": self.has_audio,
        }
        if self.root and self.root != "media":
            d["root"] = self.root
        if self.hash:
            d["hash"] = self.hash
        if self.role:
            d["role"] = self.role
        if self.timebase:
            d["timebase"] = self.timebase.to_dict()
        if self.speaker:
            d["speaker"] = self.speaker
        if self.width and self.height:
            d["width"], d["height"] = self.width, self.height
        if self.proxy_path:
            d["proxyPath"] = self.proxy_path
        return d


@dataclass
class EditPlanBuilder:
    job_id: str
    recipe: str
    timebase: Timebase
    sequence_name: str
    video_tracks: int = 2
    audio_tracks: int = 4

    _media: dict[str, MediaEntry] = field(default_factory=dict)
    _timeline: list[dict] = field(default_factory=list)
    _graphics: list[dict] = field(default_factory=list)
    _effects: list[dict] = field(default_factory=list)
    _markers: list[dict] = field(default_factory=list)
    _transcripts: list[dict] = field(default_factory=list)
    _warnings: list[dict] = field(default_factory=list)
    # Spans the analyser judged usable, whether or not one was placed. The panel
    # offers these as alternates for a slot, so this is deliberately the WHOLE
    # usable pool rather than the leftovers: a shot already on the timeline is a
    # perfectly good alternate somewhere else, and a different moment of it is
    # often the swap an editor actually wants.
    _candidates: list[dict] = field(default_factory=list)
    _playhead: int = 0          # next free frame on the timeline
    _sequence_preset: str | None = None
    _frame_size: tuple[int, int] | None = None
    # How beat fitting went, reported once at build time rather than per clip.
    _beats_snapped: int = 0
    _beats_held: int = 0
    _beats_dropped: int = 0
    _beat_grid_refused: bool = False
    _beat_bpm: float = 0.0
    # Cut positions in sequence frames, from the beats found in the waveform.
    _cut_frames: list = field(default_factory=list)

    # ------------------------------------------------------------- inputs

    def add_media(self, entry: MediaEntry) -> "EditPlanBuilder":
        if entry.id in self._media:
            raise PlanError(f"duplicate media id {entry.id!r}")
        self._media[entry.id] = entry
        return self

    def add_candidate(
        self, media_id: str, in_seconds: float, out_seconds: float,
        score: float = 0.0, reason: str = "", section_id: str | None = None,
        thumb_path: str | None = None,
    ) -> "EditPlanBuilder":
        """Offer a usable span as an alternate the editor can swap in.

        Stored in SECONDS, like every other source in/out in the plan, because a
        candidate is a span of a source file and has no position on the
        timeline. The panel trims it to whatever the slot's length happens to be
        -- which is why the span is recorded whole rather than pre-cut.
        """
        if media_id not in self._media:
            raise PlanError(f"unknown media id {media_id!r} -- add_media first")
        if out_seconds <= in_seconds:
            raise PlanError(
                f"candidate for {media_id!r} ends at or before it starts "
                f"({in_seconds} -> {out_seconds})"
            )
        entry: dict[str, Any] = {
            "mediaId": media_id,
            "inSeconds": round(float(in_seconds), 4),
            "outSeconds": round(float(out_seconds), 4),
            "score": round(float(score), 4),
        }
        if reason:
            entry["reason"] = reason
        if section_id:
            entry["sectionId"] = section_id
        if thumb_path:
            entry["thumbPath"] = thumb_path
        self._candidates.append(entry)
        return self

    def add_warning(
        self, code: str, message: "str | Note", media_id: str | None = None
    ) -> "EditPlanBuilder":
        """Record a warning. A Note carries a key and params so the panel can
        translate it; a plain string is kept for callers that have not been
        converted, and renders as English."""
        if isinstance(message, Note):
            self._warnings.append(message.to_dict(code, media_id))
            return self
        w: dict[str, Any] = {"code": code, "message": message}
        if media_id:
            w["mediaId"] = media_id
        self._warnings.append(w)
        return self

    def add_transcript(self, transcript: Transcript) -> "EditPlanBuilder":
        self._transcripts.append(transcript.to_dict())
        return self

    # ------------------------------------------------------------- timeline

    def append_cuts(
        self,
        media_id: str,
        cut_plan: CutPlan,
        video_track: int = 0,
        audio_track: int = 0,
        section_id: str | None = None,
        crossfade_seconds: float = 0.0,
        beats: "BeatGrid | None" = None,
        music: "MusicSettings | None" = None,
        min_clip_seconds: float = 0.0,
        bed_start: float = 0.0,
        every: float = 1.0,
    ) -> "EditPlanBuilder":
        """Lay a clip's surviving spans end-to-end from the current playhead.

        Beat alignment happens here rather than in the cut planner, and the
        reason is worth stating: the beat grid belongs to the MUSIC, which plays
        in sequence time. Moving a cut in *source* time does not put it on a beat
        -- it changes the clip's length, which shifts every cut after it. Only
        this function knows the running sequence playhead, so only this function
        can put a cut where the editor will actually hear it land.
        """
        if media_id not in self._media:
            raise PlanError(f"unknown media id {media_id!r} -- add_media first")

        media = self._media[media_id]
        src_tb = media.timebase or self.timebase
        fade = max(1, self.timebase.to_frames(crossfade_seconds)) if crossfade_seconds > 0 else 0
        last = len(cut_plan.keeps) - 1

        grid = self._usable_grid(beats, music)
        # The bed's own times are TRACK times; the timeline runs from zero. The
        # bed is laid at sequence frame 0 starting `bed_start` into the track, so
        # sequence t is track (bed_start + t) and the candidates shift by that.
        self._cut_frames = (
            sorted({
                self.timebase.to_frames(t - bed_start)
                for t in grid.cut_points(every) if t >= bed_start
            })
            if grid else []
        )
        # The silence that follows each keep, which is the slack a cut may be
        # nudged into without eating a word.
        slack_after = _slack_after(cut_plan)

        for i, keep in enumerate(cut_plan.keeps):
            # Snap source points to the source grid so Premiere is not left to round.
            # The out point floors against the media end: snapping to nearest can
            # land a frame past the last frame that exists, and Premiere rejects it.
            in_s = src_tb.snap(max(0.0, keep.start))
            out_s = min(src_tb.snap(keep.end), src_tb.floor(media.duration))
            frames = self.timebase.to_frames(out_s - in_s)
            if frames < 1:
                continue

            if grid:
                frames = self._fit_to_beats(
                    frames, music, slack_after.get(i, 0.0), min_clip_seconds
                )
                if frames < 1:
                    self._beats_dropped += 1
                    continue
                # Re-derive the source out point from the length we settled on,
                # then re-snap: a 29.97 source in a 59.94 sequence has no frame at
                # every odd sequence frame, and leaving Premiere to round there is
                # exactly the drift this codebase already fixed once.
                out_s = min(
                    src_tb.snap(in_s + self.timebase.to_seconds(frames)),
                    src_tb.floor(media.duration),
                )
                frames = self.timebase.to_frames(out_s - in_s)
                if frames < 1:
                    self._beats_dropped += 1
                    continue

            self._timeline.append({
                "mediaId": media_id,
                "inSeconds": round(in_s, 4),
                "outSeconds": round(out_s, 4),
                "atFrame": self._playhead,
                "durationFrames": frames,
                "videoTrack": video_track,
                "audioTrack": audio_track,
                "linkedAudio": media.has_video and media.has_audio,
                "reason": keep.reason,
                "confidence": keep.confidence,
                **({"sectionId": section_id} if section_id else {}),
                # Every internal join gets a fade; without one each cut clicks.
                "fadeInFrames": fade if i > 0 else 0,
                "fadeOutFrames": fade if i < last else 0,
            })
            self._playhead += frames

        for w in cut_plan.warnings:
            self.add_warning("cut", w, media_id)
        return self

    def _report_beat_fitting(self) -> None:
        """Say how the grid went, once, in numbers.

        An editor who is told "12 cuts on the beat, 3 left for the words" can
        judge whether the setting is right for this job. "Cutting to music: on"
        tells them nothing they did not already know.
        """
        if not (self._beats_snapped or self._beats_held or self._beats_dropped):
            return
        held = self._beats_held
        dropped = self._beats_dropped
        self.add_warning("beat", note(
            "beat.snapped",
            snapped=self._beats_snapped,
            # Rounded here, not in the format string: the engine renders with
            # {bpm:.0f} but the panel's catalogue interpolates the raw value, so
            # an unrounded param showed "104 BPM" in English and "103.94 BPM" in
            # Japanese from the same warning.
            bpm=round(self._beat_bpm or 0.0),
            held_clause=f"; {held} left where the words put them" if held else "",
            dropped_clause=f"; {dropped} dropped as too short for a beat" if dropped else "",
        ))

    # ------------------------------------------------------------ beat fitting

    def _usable_grid(self, beats, music):
        """The grid, or None when it should not be trusted or is not wanted.

        A music-led recipe running on a bad grid produces confidently wrong cuts,
        which is worse than not cutting to the music at all -- so a grid below the
        confidence floor is refused and said out loud rather than used quietly.
        """
        if not beats or not music or not getattr(beats, "beats", None):
            return None
        if beats.beat_interval <= 0:
            return None
        self._beat_bpm = beats.bpm
        if beats.confidence < music.min_beat_confidence:
            if not self._beat_grid_refused:
                self._beat_grid_refused = True
                self.add_warning("beat", note(
                    "beat.gridUnavailable",
                    confidence=beats.confidence, threshold=music.min_beat_confidence,
                ))
            return None
        return beats

    def _fit_to_beats(self, frames, music, slack_seconds, min_clip_seconds):
        """Choose a clip length so the NEXT cut lands on the music.

        Positions come from a list of times the beat tracker actually found in
        the waveform, not from `k * interval`. That is the whole point: a song's
        spacing drifts, and arithmetic from beat zero drifts with it. Snapping to
        real onsets means an error never compounds -- each cut is placed against
        where the music is at that moment.
        """
        candidates = self._cut_frames
        if not candidates:
            return frames

        playhead = self._playhead
        natural_end = playhead + frames

        if music.music_wins:
            # The last cut point at or before what the material allows. Going
            # past it would need frames that do not exist, and Premiere pads a
            # short clip by holding its last frame.
            i = bisect_right(candidates, natural_end) - 1
            while i >= 0 and candidates[i] <= playhead:
                i -= 1
            if i < 0:
                return 0
            self._beats_snapped += 1
            return int(candidates[i] - playhead)

        # Speech has priority: move the cut only if it is already close to a beat
        # and the surrounding silence can absorb the move.
        i = bisect_left(candidates, natural_end)
        near = [c for c in candidates[max(0, i - 1):i + 1] if c > playhead]
        if not near:
            self._beats_held += 1
            return frames
        target = min(near, key=lambda c: abs(c - natural_end))
        move = target - natural_end
        budget = MAX_BEAT_NUDGE * self.timebase.fps
        room = slack_seconds * self.timebase.fps
        fits = (
            abs(move) <= budget
            and move <= room                                     # extending eats silence
            and (target - playhead) >= min_clip_seconds * self.timebase.fps
            and (target - playhead) >= 1
        )
        if fits:
            self._beats_snapped += 1
            return int(target - playhead)
        self._beats_held += 1
        return frames

    def add_full_clip(
        self, media_id: str, at_frame: int, duration_frames: int,
        video_track: int, audio_track: int, reason: str = "",
        in_seconds: float = 0.0,
    ) -> "EditPlanBuilder":
        """Place a source uncut -- a music bed, or an intro sting.

        `in_seconds` picks where in the source to begin. A trend is a moment in a
        track, not its opening, so a bed that could only start at 0:00 was the
        wrong unit for the job.
        """
        if media_id not in self._media:
            raise PlanError(f"unknown media id {media_id!r}")
        media = self._media[media_id]
        # The source range has to agree with duration_frames, not just span the
        # whole file: the apply side cuts from in/out, so a bed asked to run 20s
        # under a 20s edit was laid at its full 60s and ran 40s past the picture.
        in_seconds = max(0.0, min(in_seconds, media.duration))
        out_seconds = min(media.duration, in_seconds + self.timebase.to_seconds(duration_frames))
        self._timeline.append({
            "mediaId": media_id,
            "inSeconds": round(in_seconds, 4),
            "outSeconds": round(out_seconds, 4),
            "atFrame": at_frame,
            "durationFrames": duration_frames,
            "videoTrack": video_track,
            "audioTrack": audio_track,
            "linkedAudio": False,
            "reason": reason or "placed whole",
            "confidence": 1.0,
            "fadeInFrames": 0,
            "fadeOutFrames": 0,
        })
        return self

    def set_sequence_preset(self, preset_path: str, width: int, height: int) -> "EditPlanBuilder":
        """Point the sequence at a generated preset, and record the frame size.

        The apply side needs the size to work out the crop scale, and it is not
        recoverable from the preset path alone.
        """
        self._sequence_preset = preset_path
        self._frame_size = (width, height)
        return self

    def add_marker(
        self, at_frame: int, name: str, comment: str = "",
        kind: str = "Comment", duration_frames: int = 0,
    ) -> "EditPlanBuilder":
        m: dict[str, Any] = {"atFrame": at_frame, "name": name, "type": kind}
        if comment:
            m["comment"] = comment
        if duration_frames:
            m["durationFrames"] = duration_frames
        self._markers.append(m)
        return self

    def add_graphic(
        self, mogrt: str, at_frame: int, duration_frames: int,
        video_track: int, fields: dict | None = None,
        reason: str = "", section_id: str | None = None,
    ) -> "EditPlanBuilder":
        g: dict[str, Any] = {
            "mogrt": mogrt, "atFrame": at_frame,
            "durationFrames": duration_frames, "videoTrack": video_track,
        }
        if fields:
            g["fields"] = fields
        if reason:
            g["reason"] = reason
        if section_id:
            g["sectionId"] = section_id
        self._graphics.append(g)
        return self

    def add_effect(
        self, target: str, match_name: str, params: dict | None = None,
        lut: str | None = None, reason: str = "", section_id: str | None = None,
    ) -> "EditPlanBuilder":
        e: dict[str, Any] = {"target": target, "matchName": match_name}
        if params:
            e["params"] = params
        if lut:
            e["lut"] = lut
        if reason:
            e["reason"] = reason
        if section_id:
            e["sectionId"] = section_id
        self._effects.append(e)
        return self

    # ------------------------------------------------------------- output

    def timeline_entries(self) -> list[dict]:
        """Read-only view of what has been placed so far."""
        return list(self._timeline)

    @property
    def duration_frames(self) -> int:
        return self._playhead

    def build(self) -> dict:
        if not self._media:
            raise PlanError("plan has no media")

        self._report_beat_fitting()

        needed_v = max((c["videoTrack"] for c in self._timeline), default=0)
        needed_v = max(needed_v, max((g["videoTrack"] for g in self._graphics), default=0))
        needed_a = max((c["audioTrack"] for c in self._timeline), default=0)
        needed_v, needed_a = max(needed_v, 0), max(needed_a, 0)

        plan: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": self.job_id,
            "recipe": self.recipe,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
            "timebase": self.timebase.to_dict(),
            "media": [m.to_dict() for m in self._media.values()],
            "sequence": {
                "name": self.sequence_name,
                "videoTracks": max(self.video_tracks, needed_v + 1),
                "audioTracks": max(self.audio_tracks, needed_a + 1),
                **({"presetPath": self._sequence_preset} if self._sequence_preset else {}),
                **({"frameWidth": self._frame_size[0], "frameHeight": self._frame_size[1]}
                   if self._frame_size else {}),
            },
            "timeline": self._timeline,
        }
        for key, value in (
            ("graphics", self._graphics), ("effects", self._effects),
            ("markers", self._markers), ("transcripts", self._transcripts),
            ("candidates", self._candidates), ("warnings", self._warnings),
        ):
            if value:
                plan[key] = value
        return plan

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.build(), indent=2) + "\n")
        return p


# ------------------------------------------------------------------ validation


def schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "schema" / "edit-plan.schema.json"


def validate_plan(plan: dict) -> list[str]:
    """Schema errors plus the cross-field checks JSON Schema cannot express."""
    errors: list[str] = []
    try:
        import jsonschema
    except ImportError:
        errors.append("jsonschema not installed; structural validation skipped")
    else:
        schema = json.loads(schema_path().read_text())
        validator = jsonschema.Draft7Validator(schema)
        for e in sorted(validator.iter_errors(plan), key=lambda x: list(x.path)):
            where = "/".join(str(p) for p in e.path) or "(root)"
            errors.append(f"{where}: {e.message}")

    errors.extend(_semantic_errors(plan))
    return errors


def _semantic_errors(plan: dict) -> list[str]:
    errors: list[str] = []
    media = {m["id"]: m for m in plan.get("media", [])}

    for i, clip in enumerate(plan.get("timeline", [])):
        ref = clip.get("mediaId")
        if ref not in media:
            errors.append(f"timeline/{i}: references unknown media {ref!r}")
            continue
        if clip["outSeconds"] <= clip["inSeconds"]:
            errors.append(f"timeline/{i}: out point is not after in point")
        limit = media[ref]["durationSeconds"]
        if clip["outSeconds"] > limit + 0.001:
            errors.append(
                f"timeline/{i}: out point {clip['outSeconds']:.3f}s exceeds "
                f"source duration {limit:.3f}s"
            )

    for t in plan.get("transcripts", []):
        if t["mediaId"] not in media:
            errors.append(f"transcripts: unknown media {t['mediaId']!r}")

    # Overlap detection, per track. A silent overwrite is the worst outcome here:
    # the editor sees a timeline that simply lost a clip.
    by_track: dict[int, list[tuple[int, int, int]]] = {}
    for i, c in enumerate(plan.get("timeline", [])):
        if c["videoTrack"] < 0:      # audio-only clip; no video lane to collide on
            continue
        by_track.setdefault(c["videoTrack"], []).append(
            (c["atFrame"], c["atFrame"] + c["durationFrames"], i)
        )
    for track, spans in by_track.items():
        for (a_start, a_end, ai), (b_start, _b_end, bi) in zip(sorted(spans), sorted(spans)[1:]):
            if b_start < a_end:
                errors.append(
                    f"timeline/{bi}: overlaps timeline/{ai} on video track {track} "
                    f"(frame {b_start} starts before {a_end})"
                )
    return errors
