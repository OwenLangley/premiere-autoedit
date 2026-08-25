"""Recipes: per-content-type configuration.

A recipe is the difference between a script only its author can change and a
system a producer can tune. Everything editorially opinionated lives here as
data, not in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detect import DetectionSettings
from .timebase import Timebase
from .visual import VisualSettings

RECIPE_DIR = Path(__file__).resolve().parents[1] / "recipes"

_DETECTION_FIELDS = {
    "min_silence", "trim_head_tail", "min_removed", "lead_in", "tail",
    "filler_mode", "filler_pad", "remove_stutters", "stutter_max_gap",
    "min_confidence", "protect_margin", "min_clip_length",
}
_SET_FIELDS = {"extra_fillers", "keep_fillers"}
_VISUAL_FIELDS = {
    "scene_threshold", "min_shot", "max_shot", "sample_fps",
    "drop_black", "min_brightness", "max_brightness", "min_sharpness",
    "min_motion", "max_motion", "lead_trim", "tail_trim",
    "shot_duration", "min_clip_length", "target_duration",
    "snap_to_beats", "beats_per_shot", "min_beat_confidence",
}
_VALID_FILLER_MODES = {"off", "conservative", "aggressive"}


class RecipeError(RuntimeError):
    pass


@dataclass
class SequenceConfig:
    timebase: Timebase = field(default_factory=lambda: Timebase(25))
    preset_path: str | None = None
    video_tracks: int = 2
    audio_tracks: int = 2
    name_template: str = "{job}_rough_v1"
    crossfade_frames: int = 2      # ~2 frames kills the click at every join

    @property
    def crossfade_seconds(self) -> float:
        return self.crossfade_frames * self.timebase.frame_duration()


@dataclass
class Recipe:
    name: str
    description: str = ""
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    visual: VisualSettings = field(default_factory=VisualSettings)
    transcription: dict[str, Any] = field(default_factory=dict)
    brand: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, Any] = field(default_factory=dict)

    def sequence_name(self, job: str) -> str:
        return self.sequence.name_template.format(job=job, recipe=self.name)


def _coerce_timebase(value: Any) -> Timebase:
    if isinstance(value, dict):
        return Timebase(int(value["fpsNum"]), int(value.get("fpsDen", 1)),
                        bool(value.get("dropFrame", False)))
    return Timebase.parse(str(value))


def load_recipe(name_or_path: str | Path) -> Recipe:
    """Load a recipe by name (from engine/recipes) or by explicit path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = RECIPE_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in RECIPE_DIR.glob("*.yaml"))) or "none found"
        raise RecipeError(f"no recipe at {path}. Available: {available}")

    try:
        import yaml
    except ImportError as exc:
        raise RecipeError("PyYAML is required to load recipes") from exc

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise RecipeError(f"{path.name}: expected a mapping at the top level")

    unknown = set(raw) - {"name", "description", "sequence", "detection",
                          "visual", "transcription", "brand", "roles"}
    if unknown:
        raise RecipeError(f"{path.name}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    seq_raw = raw.get("sequence") or {}
    seq = SequenceConfig(
        timebase=_coerce_timebase(seq_raw.get("timebase", "25")),
        preset_path=seq_raw.get("preset_path"),
        video_tracks=int(seq_raw.get("video_tracks", 2)),
        audio_tracks=int(seq_raw.get("audio_tracks", 2)),
        name_template=seq_raw.get("name_template", "{job}_rough_v1"),
        crossfade_frames=int(seq_raw.get("crossfade_frames", 2)),
    )

    det_raw = raw.get("detection") or {}
    bad = set(det_raw) - _DETECTION_FIELDS - _SET_FIELDS
    if bad:
        raise RecipeError(
            f"{path.name}: unknown detection setting(s): {', '.join(sorted(bad))}. "
            "A typo here silently keeps the default, so it is rejected instead."
        )

    kwargs: dict[str, Any] = {k: v for k, v in det_raw.items() if k in _DETECTION_FIELDS}
    for k in _SET_FIELDS:
        if k in det_raw:
            kwargs[k] = frozenset(str(x).lower() for x in det_raw[k])
    kwargs["crossfade"] = seq.crossfade_seconds

    mode = kwargs.get("filler_mode", "conservative")
    if mode not in _VALID_FILLER_MODES:
        raise RecipeError(
            f"{path.name}: filler_mode must be one of {sorted(_VALID_FILLER_MODES)}, got {mode!r}"
        )

    detection = DetectionSettings(**kwargs)
    _sanity_check(path.name, detection)

    vis_raw = raw.get("visual") or {}
    bad_visual = set(vis_raw) - _VISUAL_FIELDS
    if bad_visual:
        raise RecipeError(
            f"{path.name}: unknown visual setting(s): {', '.join(sorted(bad_visual))}"
        )
    visual = VisualSettings(**vis_raw)
    if visual.min_shot <= 0 or visual.max_shot <= visual.min_shot:
        raise RecipeError(f"{path.name}: max_shot must exceed min_shot, both positive")
    if not 0.0 < visual.scene_threshold < 1.0:
        raise RecipeError(f"{path.name}: scene_threshold must be between 0 and 1")

    return Recipe(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        sequence=seq,
        detection=detection,
        visual=visual,
        transcription=raw.get("transcription") or {},
        brand=raw.get("brand") or {},
        roles=raw.get("roles") or {},
    )


def _sanity_check(source: str, d: DetectionSettings) -> None:
    """Catch settings that are individually legal but jointly nonsense."""
    if d.lead_in < 0 or d.tail < 0:
        raise RecipeError(f"{source}: handles cannot be negative")
    if d.min_silence <= d.lead_in + d.tail:
        raise RecipeError(
            f"{source}: min_silence ({d.min_silence}s) must exceed lead_in + tail "
            f"({d.lead_in + d.tail}s), otherwise every detected pause is cancelled "
            "by its own handles and nothing is ever cut."
        )
    if not 0.0 <= d.min_confidence <= 1.0:
        raise RecipeError(f"{source}: min_confidence must be between 0 and 1")
    if d.min_clip_length <= 0:
        raise RecipeError(f"{source}: min_clip_length must be positive")


def list_recipes() -> list[str]:
    return sorted(p.stem for p in RECIPE_DIR.glob("*.yaml"))
