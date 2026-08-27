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
_MUSIC_FIELDS = {"beat_priority", "beats_per_cut", "min_beat_confidence",
                 "max_shot_beats"}
_VALID_BEAT_PRIORITIES = {"music", "speech"}


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
class MusicSettings:
    """How hard the picture chases the beat.

    One setting serves both cut planners, which is why it lives here rather than
    being duplicated into DetectionSettings and VisualSettings: "does the music
    win" is a property of the format, not of how the cuts happen to be found.

      * `music`  -- every shot is a whole number of beats, so the picture always
        changes on the beat. Words get clipped at the joins. What a trend edit
        needs, and what a talking head must never get.
      * `speech` -- a cut moves to a beat only when the silence around it has
        room. Nothing spoken is ever lost; some cuts simply will not land on the
        grid, which is the right answer for an interview.
    """

    beat_priority: str = "speech"
    beats_per_cut: int = 1          # the quantum in music mode; 4 would be a bar
    # How long any one shot may run, in beats, when the music leads. This is what
    # makes the pacing control mean something in a montage: without it, a source
    # handed a 2.4s share spent it all on one shot, so a six-clip edit had six
    # cuts however punchy the setting. Scaled by pacing in apply_pacing().
    max_shot_beats: int = 4
    # Calibrated against measured values rather than guessed. On the grid-fit
    # metric (see detect_beats), four real tracks scored 0.19, 0.27, 0.38 and
    # 0.47, while speech recordings with no beat at all scored 0.09 and 0.14.
    # The old 0.25 was tuned for a different metric entirely and rejected a track
    # whose beat the editor could hear perfectly well.
    min_beat_confidence: float = 0.15

    @property
    def music_wins(self) -> bool:
        return self.beat_priority == "music"


@dataclass
class Recipe:
    name: str
    description: str = ""
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    visual: VisualSettings = field(default_factory=VisualSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    transcription: dict[str, Any] = field(default_factory=dict)
    # Opt-in, and deliberately not inferred from the presence of a `music` role:
    # podcast recipes define one for a bed the editor adds by hand, and a rough
    # cut that quietly gained a soundtrack would be a nasty surprise.
    auto_music: bool = False
    brand: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, Any] = field(default_factory=dict)
    # How this recipe is offered to an editor: the deliverable it makes, rather
    # than the thresholds it uses to make it. Optional -- a recipe without one
    # still works, it just has no card in the panel.
    format: dict[str, Any] = field(default_factory=dict)

    def sequence_name(self, job: str) -> str:
        return self.sequence.name_template.format(job=job, recipe=self.name)


# Aspect names the engine accepts. Imported lazily inside the validator to keep
# recipe.py free of an options.py import at module scope.
_FORMAT_KEYS = {"label", "aspect", "duration", "duration_mode", "cut_rate", "order"}
_DURATION_MODES = {"none", "upTo", "exactly", "about"}


def _coerce_format(raw: Any, where: str) -> dict[str, Any]:
    """Validate a recipe's `format:` block.

    Checked here rather than trusted, because a typo would otherwise surface as
    a card in the panel that quietly builds the wrong shape -- and an aspect or
    a duration mode the engine does not accept fails much later, after the
    editor has chosen it.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise RecipeError(f"{where}: `format` must be a mapping")
    unknown = set(raw) - _FORMAT_KEYS
    if unknown:
        raise RecipeError(f"{where}: unknown format key(s): {', '.join(sorted(unknown))}")
    if not raw.get("label"):
        raise RecipeError(f"{where}: a format needs a label -- it is what the editor picks")

    from .options import ASPECT_LABELS
    aspect = raw.get("aspect", "source")
    if aspect not in ASPECT_LABELS:
        raise RecipeError(
            f"{where}: unknown aspect {aspect!r}; choose from {', '.join(ASPECT_LABELS)}"
        )
    mode = raw.get("duration_mode", "none")
    if mode not in _DURATION_MODES:
        raise RecipeError(f"{where}: unknown duration_mode {mode!r}")
    seconds = raw.get("duration")
    if mode != "none" and not seconds:
        raise RecipeError(f"{where}: duration_mode {mode!r} needs a duration")
    if seconds is not None and (not isinstance(seconds, (int, float)) or seconds <= 0):
        raise RecipeError(f"{where}: duration must be a positive number of seconds")

    out = {"label": str(raw["label"]), "aspect": aspect, "duration_mode": mode,
           "order": int(raw.get("order", 99))}
    if seconds is not None:
        out["duration"] = float(seconds)
    if raw.get("cut_rate") is not None:
        out["cut_rate"] = float(raw["cut_rate"])
    return out


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
                          "visual", "music", "transcription", "brand", "roles",
                          "auto_music", "format"}
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

    mus_raw = raw.get("music") or {}
    bad_music = set(mus_raw) - _MUSIC_FIELDS
    if bad_music:
        raise RecipeError(
            f"{path.name}: unknown music setting(s): {', '.join(sorted(bad_music))}"
        )
    music = MusicSettings(**mus_raw)
    if music.beat_priority not in _VALID_BEAT_PRIORITIES:
        raise RecipeError(
            f"{path.name}: beat_priority must be one of "
            f"{sorted(_VALID_BEAT_PRIORITIES)}, got {music.beat_priority!r}"
        )
    if music.beats_per_cut < 1:
        raise RecipeError(f"{path.name}: beats_per_cut must be at least 1")
    if music.max_shot_beats < 1:
        raise RecipeError(f"{path.name}: max_shot_beats must be at least 1")

    return Recipe(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        sequence=seq,
        detection=detection,
        visual=visual,
        music=music,
        auto_music=bool(raw.get("auto_music", False)),
        transcription=raw.get("transcription") or {},
        brand=raw.get("brand") or {},
        roles=raw.get("roles") or {},
        format=_coerce_format(raw.get("format"), path.name),
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
