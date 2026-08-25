"""EditPlan construction and validation.

Turns per-clip cut decisions into the single document the UXP panel consumes.

The one invariant worth stating plainly: timeline positions accumulate as
integer frames. Source in/out points stay in seconds because source media can
run at a different rate than the sequence, but nothing downstream ever adds two
floats together to decide where a clip lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .detect import CutPlan
from .timebase import Timebase
from .transcript import Transcript

SCHEMA_VERSION = "1.0"
GENERATOR_NAME = "premiere-autoedit"
GENERATOR_VERSION = "0.1.0"


class PlanError(RuntimeError):
    pass


@dataclass
class MediaEntry:
    id: str
    rel_path: str
    duration: float
    hash: str | None = None
    role: str | None = None
    timebase: Timebase | None = None
    has_video: bool = True
    has_audio: bool = True
    speaker: str | None = None
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "relPath": self.rel_path,
            "durationSeconds": round(self.duration, 4),
            "hasVideo": self.has_video,
            "hasAudio": self.has_audio,
        }
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
    _playhead: int = 0          # next free frame on the timeline
    _sequence_preset: str | None = None
    _frame_size: tuple[int, int] | None = None

    # ------------------------------------------------------------- inputs

    def add_media(self, entry: MediaEntry) -> "EditPlanBuilder":
        if entry.id in self._media:
            raise PlanError(f"duplicate media id {entry.id!r}")
        self._media[entry.id] = entry
        return self

    def add_warning(self, code: str, message: str, media_id: str | None = None) -> "EditPlanBuilder":
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
    ) -> "EditPlanBuilder":
        """Lay a clip's surviving spans end-to-end from the current playhead."""
        if media_id not in self._media:
            raise PlanError(f"unknown media id {media_id!r} -- add_media first")

        media = self._media[media_id]
        src_tb = media.timebase or self.timebase
        fade = max(1, self.timebase.to_frames(crossfade_seconds)) if crossfade_seconds > 0 else 0
        last = len(cut_plan.keeps) - 1

        for i, keep in enumerate(cut_plan.keeps):
            # Snap source points to the source grid so Premiere is not left to round.
            # The out point floors against the media end: snapping to nearest can
            # land a frame past the last frame that exists, and Premiere rejects it.
            in_s = src_tb.snap(max(0.0, keep.start))
            out_s = min(src_tb.snap(keep.end), src_tb.floor(media.duration))
            frames = self.timebase.to_frames(out_s - in_s)
            if frames < 1:
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

    def add_full_clip(
        self, media_id: str, at_frame: int, duration_frames: int,
        video_track: int, audio_track: int, reason: str = "",
    ) -> "EditPlanBuilder":
        """Place a whole source without cutting it -- a music bed, or an intro sting."""
        if media_id not in self._media:
            raise PlanError(f"unknown media id {media_id!r}")
        media = self._media[media_id]
        self._timeline.append({
            "mediaId": media_id,
            "inSeconds": 0.0,
            "outSeconds": round(media.duration, 4),
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
            ("warnings", self._warnings),
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
