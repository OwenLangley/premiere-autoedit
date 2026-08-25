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
  if (o.music === "none") bits.push("no music");
  return bits.join(" · ");
}

module.exports = {
  SCHEMA_VERSION,
  VIDEO_EXTENSIONS,
  isVideoFile,
  normaliseJobId,
  buildRequest,
  validateRequest,
  requestFileName,
  describeRequest,
};
