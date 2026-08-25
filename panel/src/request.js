"use strict";
/**
 * Building job requests from the panel form.
 *
 * Pure functions, no `premierepro` or `uxp` imports, so this runs under
 * `node --test`. The panel is the only place an editor states what they want, so
 * a malformed request here becomes a confusing failure minutes later in the
 * helper -- validate at the point of entry instead.
 */

const SCHEMA_VERSION = "1.0";

const VIDEO_EXTENSIONS = [
  ".mp4", ".mov", ".mxf", ".avi", ".m4v", ".mkv", ".mts", ".m2ts", ".braw", ".r3d",
];

const MUSIC_AUTO = "auto";
const MUSIC_NONE = "none";

const DEFAULTS = {
  aspect: "source",
  pacing: "standard",
  durationMode: "none",
  music: "auto",
};

/** @param {string} name */
function isVideoFile(name) {
  const lower = String(name || "").toLowerCase();
  if (lower.startsWith(".")) return false;   // ._ sidecars and dotfiles
  return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** mm:ss, because "1:04" reads as a track length and "64.2" does not. */
function formatDuration(seconds) {
  const total = Math.round(Number(seconds) || 0);
  const mins = Math.floor(total / 60);
  return `${mins}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * Display name for a track: the path without its extension, so two files called
 * `theme.wav` in different folders stay tellable apart.
 * @param {string} relPath
 */
function trackName(relPath) {
  return String(relPath || "").replace(/\.[^./\\]+$/, "");
}

/**
 * Options for the Music dropdown, built from the media index.
 *
 * Audio-only is decided by the index's probe rather than by extension: the track
 * that turned up in real use was a `.mp4` with no video stream. Automatic is left
 * unlabelled rather than naming the file it would find -- the engine's search is
 * top-level only while this list is recursive, and a label that predicted the
 * wrong track would be worse than one that predicts nothing.
 *
 * @param {{name?: string, relPath?: string, hasVideo?: boolean, hasAudio?: boolean, durationSeconds?: number}[]} files
 */
function musicChoices(files) {
  const tracks = (files || [])
    .filter((f) => f && f.hasAudio && !f.hasVideo)
    .map((f) => {
      const rel = f.relPath || f.name || "";
      return {
        value: rel,
        label: f.durationSeconds
          ? `${trackName(rel)} · ${formatDuration(f.durationSeconds)}`
          : trackName(rel),
      };
    });
  return [
    { value: MUSIC_AUTO, label: "Automatic" },
    { value: MUSIC_NONE, label: "No music" },
    ...tracks,
  ];
}

/**
 * A job id that is safe as a filename and as a Premiere sequence name.
 * @param {string} raw
 */
function normaliseJobId(raw) {
  const cleaned = String(raw || "")
    .trim()
    .replace(/[^A-Za-z0-9 _-]/g, "")
    .replace(/\s+/g, " ")
    .slice(0, 64);
  return cleaned;
}

/**
 * @param {{
 *   jobId: string, recipe: string, media: string[],
 *   aspect?: string, pacing?: string, look?: string|null,
 *   durationMode?: string, durationSeconds?: number|null,
 *   music?: string, visual?: boolean,
 * }} form
 */
function buildRequest(form) {
  const options = {
    aspect: form.aspect || DEFAULTS.aspect,
    pacing: form.pacing || DEFAULTS.pacing,
    music: form.music || DEFAULTS.music,
  };

  const mode = form.durationMode || DEFAULTS.durationMode;
  if (mode !== "none" && form.durationSeconds) {
    options.duration = { mode, seconds: Number(form.durationSeconds) };
  }
  if (form.look) options.look = form.look;
  if (form.visual) options.visual = true;

  return {
    schemaVersion: SCHEMA_VERSION,
    jobId: normaliseJobId(form.jobId),
    recipe: form.recipe,
    createdAt: new Date().toISOString(),
    media: [...(form.media || [])],
    options,
  };
}

/**
 * Problems an editor can act on, phrased for them rather than for a log.
 * @param {any} request
 * @returns {string[]}
 */
function validateRequest(request) {
  /** @type {string[]} */
  const errors = [];
  if (!request || typeof request !== "object") return ["request is empty"];

  if (!request.jobId) {
    errors.push("Give the job a name");
  } else if (!/^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$/.test(request.jobId)) {
    errors.push("Job name must start with a letter or number, and avoid punctuation");
  }

  if (!request.recipe) errors.push("Choose a recipe");
  if (!Array.isArray(request.media) || request.media.length === 0) {
    errors.push("Select at least one clip");
  }

  const o = request.options || {};
  if (o.music !== undefined && typeof o.music !== "string") {
    errors.push("Music must be a track name, 'auto' or 'none'");
  }
  if (o.duration) {
    if (!(o.duration.seconds > 0)) errors.push("Length must be more than zero seconds");
    if (o.duration.seconds > 7200) errors.push("Length must be under two hours");
  }
  return errors;
}

/** File name the helper watches for. */
function requestFileName(jobId) {
  return `${jobId}.request.json`;
}

/**
 * Plain-language summary of what will happen, shown before the editor commits.
 * Worth the code: "Vertical 9:16, up to 30s, punchy" is checkable at a glance in
 * a way that a JSON blob never is.
 */
function describeRequest(request, capabilities) {
  const o = request.options || {};
  const label = (list, value, fallback) => {
    const hit = (list || []).find((x) => x.value === value);
    return hit ? hit.label : fallback || value;
  };
  const caps = capabilities || {};

  const bits = [`${request.media.length} clip${request.media.length === 1 ? "" : "s"}`];
  if (o.aspect && o.aspect !== "source") bits.push(label(caps.aspects, o.aspect));
  if (o.duration) {
    const verb = { upTo: "up to", exactly: "exactly", about: "about" }[o.duration.mode] || "";
    bits.push(`${verb} ${o.duration.seconds}s`);
  }
  if (o.pacing && o.pacing !== "standard") bits.push(label(caps.pacing, o.pacing).toLowerCase());
  if (o.look) bits.push(`look: ${o.look}`);
  if (o.visual) bits.push("from pictures");
  if (o.music === MUSIC_NONE) bits.push("no music");
  else if (o.music && o.music !== MUSIC_AUTO) bits.push(`music: ${trackName(o.music)}`);
  return bits.join(" · ");
}

module.exports = {
  SCHEMA_VERSION,
  VIDEO_EXTENSIONS,
  MUSIC_AUTO,
  MUSIC_NONE,
  isVideoFile,
  formatDuration,
  trackName,
  musicChoices,
  normaliseJobId,
  buildRequest,
  validateRequest,
  requestFileName,
  describeRequest,
};
