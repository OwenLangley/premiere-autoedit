"use strict";
/**
 * Building job requests from the panel form.
 *
 * Pure functions, no `premierepro` or `uxp` imports, so this runs under
 * `node --test`. The panel is the only place an editor states what they want, so
 * a malformed request here becomes a confusing failure minutes later in the
 * helper -- validate at the point of entry instead.
 */

const { makeTranslator } = require("./i18n");

const SCHEMA_VERSION = "1.0";

const VIDEO_EXTENSIONS = [
  ".mp4", ".mov", ".mxf", ".avi", ".m4v", ".mkv", ".mts", ".m2ts", ".braw", ".r3d",
];

const MUSIC_AUTO = "auto";
const MUSIC_NONE = "none";
// Marks a track as living in the separate music library rather than beside the
// footage. Explicit, because the alternative -- trying one root then the other --
// resolves an ambiguous name to the wrong track silently, and a promo scored to
// the wrong music is only noticed on playback.
const MUSIC_LIBRARY_PREFIX = "library:";

const DEFAULTS = {
  aspect: "source",
  pacing: "standard",
  cutRate: "",
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
 * @param {any[]} [libraryFiles]
 * @param {any} [translator]
 */
function musicChoices(files, libraryFiles, translator) {
  const t = translator || makeTranslator("en");
  const asChoice = (prefix, suffix) => (f) => {
    const rel = f.relPath || f.name || "";
    const length = f.durationSeconds ? ` · ${formatDuration(f.durationSeconds)}` : "";
    return { value: `${prefix}${rel}`, label: `${trackName(rel)}${length}${suffix}` };
  };
  const isTrack = (f) => f && f.hasAudio && !f.hasVideo;

  // Library first: when one is configured it is the deliberate choice, and the
  // audio-only files that happen to sit among the rushes are the exception.
  const library = (libraryFiles || []).filter(isTrack).map(asChoice(MUSIC_LIBRARY_PREFIX, ""));
  // The "(with the footage)" note only earns its space once a library exists to
  // be distinguished from; with one source of tracks it is just noise.
  const beside = (files || []).filter(isTrack)
    .map(asChoice("", library.length ? ` ${t("music.withFootage")}` : ""));

  return [
    { value: MUSIC_AUTO, label: t("music.automatic") },
    { value: MUSIC_NONE, label: t("music.none") },
    ...library,
    ...beside,
  ];
}

/**
 * Split a Music dropdown value back into a path and the root it hangs off.
 *
 * The inverse of `musicChoices`. The panel needs it to hand SourceMonitor an
 * absolute path for auditioning, and the resolver takes (relPath, root).
 *
 * @param {string} value
 * @returns {{kind: "auto"|"none"|"track", relPath: string, root: string}}
 */
function parseMusicValue(value) {
  const raw = String(value || MUSIC_AUTO);
  if (raw === MUSIC_AUTO) return { kind: "auto", relPath: "", root: "media" };
  if (raw === MUSIC_NONE) return { kind: "none", relPath: "", root: "media" };
  if (raw.startsWith(MUSIC_LIBRARY_PREFIX)) {
    return { kind: "track", relPath: raw.slice(MUSIC_LIBRARY_PREFIX.length), root: "music" };
  }
  return { kind: "track", relPath: raw, root: "media" };
}

/**
 * Seconds from what an editor types: `1:23.5`, `83.5`, `1:02:03`.
 * Returns null for anything unparseable, so the caller can say so rather than
 * silently treating a typo as 0:00 and scoring the promo from the intro.
 *
 * @param {string|number} text
 * @returns {number|null}
 */
function parseTimecode(text) {
  if (typeof text === "number") return Number.isFinite(text) && text >= 0 ? text : null;
  const raw = String(text == null ? "" : text).trim();
  if (!raw) return null;
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(raw)) return null;
  const parts = raw.split(":");
  let seconds = 0;
  for (const part of parts) seconds = seconds * 60 + Number(part);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/**
 * A plain seconds field, tolerant of the empty case.
 *
 * Returns 0 for empty or unparseable, which the caller reads as "no explicit
 * length -- follow the picture". UXP hands back the literal string "nan" for an
 * empty number input, which is why this is not just `Number(x)`.
 *
 * @param {string|number} text
 */
function parseSeconds(text) {
  const value = Number(String(text == null ? "" : text).trim());
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/** `0:42.4` -- tenths, because half a beat at 120bpm is a quarter of a second. */
function formatTimecode(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const mins = Math.floor(total / 60);
  const secs = (total - mins * 60).toFixed(1).padStart(4, "0");
  return `${mins}:${secs}`;
}

// Characters a filename cannot hold on macOS or Windows, plus control codes.
// Everything else -- including every script on earth -- is allowed through.
const UNSAFE_IN_FILENAME = /[/\\:*?"<>|\u0000-\u001f]/g;

/**
 * A job id that is safe as a filename and as a Premiere sequence name.
 *
 * Stripping to `[A-Za-z0-9 _-]` deleted whole alphabets: `エピソード1` arrived as
 * `1` and was accepted without a word, and `ダンス動画` became empty and was
 * reported as "give the job a name" to someone who just had. Only genuinely
 * unsafe characters are removed now, and anything removed is reported rather
 * than quietly applied -- see `validateRequest`.
 *
 * Normalised to NFC because macOS hands back decomposed forms from some APIs,
 * and the id becomes a filename that later has to be found again.
 *
 * @param {string} raw
 */
function normaliseJobId(raw) {
  return String(raw || "")
    .normalize("NFC")
    .trim()
    .replace(UNSAFE_IN_FILENAME, "")
    .replace(/\s+/g, " ")
    .slice(0, 64);
}

/** True when normalising would silently change what the editor typed. */
function jobIdWasChanged(raw) {
  const typed = String(raw || "").normalize("NFC").trim().replace(/\s+/g, " ");
  return typed.length > 0 && typed !== normaliseJobId(raw);
}

/**
 * @param {{
 *   jobId: string, recipe: string, media: string[],
 *   aspect?: string, pacing?: string, cutRate?: string|number, look?: string|null,
 *   durationMode?: string, durationSeconds?: number|null,
 *   music?: string, visual?: boolean,
 *   musicStart?: number, musicLength?: number, musicSnap?: boolean, language?: string,
 *   story?: string,
 * }} form
 */
function buildRequest(form) {
  const options = {
    aspect: form.aspect || DEFAULTS.aspect,
    pacing: form.pacing || DEFAULTS.pacing,
    music: form.music || DEFAULTS.music,
  };

  // Only sent when the editor has actually chosen one. Absent means "follow the
  // pacing setting", which is what the recipe intended.
  // 0.5 is a real setting -- cut halfway between beats as well as on them -- so
  // this must not round to an integer.
  const rate = Number(form.cutRate);
  if (Number.isFinite(rate) && rate > 0) options.cutRate = rate;

  const mode = form.durationMode || DEFAULTS.durationMode;
  if (mode !== "none" && form.durationSeconds) {
    options.duration = { mode, seconds: Number(form.durationSeconds) };
  }
  if (form.look) options.look = form.look;
  if (form.visual) options.visual = true;
  // Sent raw. The engine owns the parsing, so there is one grammar rather than
  // two that drift -- and the beats it found come back on the plan, where the
  // editor reviews them against real shots instead of against a guess.
  const story = String(form.story || "").trim();
  if (story) options.story = story;
  // Only when stated. Omitting it leaves the recipe on `auto`, which asks
  // Whisper to identify the language from the audio.
  if (form.language && form.language !== "auto") options.language = form.language;

  // Only a real track has a chunk; against "automatic" a start would apply to
  // whatever the engine happened to find.
  if (parseMusicValue(options.music).kind === "track") {
    const chunk = {};
    if (form.musicStart) chunk.startSeconds = Number(form.musicStart);
    if (form.musicLength) chunk.lengthSeconds = Number(form.musicLength);
    if (form.musicSnap === false) chunk.snapToBeat = false;
    if (Object.keys(chunk).length) options.musicChunk = chunk;
  }

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
 * Problems an editor can act on, as i18n keys rather than English.
 *
 * Returning prose here would have made the form the one part of the panel that
 * could not be translated -- and the form is where a Japanese editor spends all
 * their time. The panel renders these through `t()`.
 *
 * @param {any} request
 * @returns {string[]} keys into the message catalogue
 */
function validateRequest(request) {
  /** @type {string[]} */
  const errors = [];
  if (!request || typeof request !== "object") return ["err.nameRequired"];

  if (!request.jobId) {
    errors.push("err.nameRequired");
  } else if (/^[.\s]/.test(request.jobId)) {
    // A leading dot hides the file from the helper's glob; leading space is a
    // typo that produces a filename nobody can see is different.
    errors.push("err.nameDotOrSpace");
  } else if (UNSAFE_IN_FILENAME.test(request.jobId)) {
    UNSAFE_IN_FILENAME.lastIndex = 0;   // the g flag makes .test stateful
    errors.push("err.nameUnsafe");
  }
  UNSAFE_IN_FILENAME.lastIndex = 0;

  if (!request.recipe) errors.push("err.recipeRequired");
  if (!Array.isArray(request.media) || request.media.length === 0) {
    errors.push("err.clipsRequired");
  }

  const o = request.options || {};
  if (o.music !== undefined && typeof o.music !== "string") {
    errors.push("err.musicType");
  }
  if (o.musicChunk) {
    const c = o.musicChunk;
    if (c.startSeconds !== undefined && !(c.startSeconds >= 0)) {
      errors.push("err.musicStart");
    }
    if (c.lengthSeconds !== undefined && !(c.lengthSeconds > 0)) {
      errors.push("err.musicLength");
    }
    if (parseMusicValue(o.music).kind !== "track") {
      errors.push("err.musicNeedsTrack");
    }
  }
  if (o.duration) {
    if (!(o.duration.seconds > 0)) errors.push("err.lengthPositive");
    if (o.duration.seconds > 7200) errors.push("err.lengthTooLong");
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
function describeRequest(request, capabilities, translator) {
  const t = translator || makeTranslator("en");
  const o = request.options || {};
  const caps = capabilities || {};
  // The helper labels these in English. Prefer a translated label and fall back
  // to whatever it sent, so a value added there still reads sensibly.
  const label = (list, prefix, value) => {
    const hit = (list || []).find((x) => x.value === value);
    return t(`${prefix}.${value}`, {}, hit ? hit.label : value);
  };

  const count = request.media.length;
  const bits = [t("sum.clips", { count, plural: count === 1 ? "" : "s" })];
  if (o.aspect && o.aspect !== "source") bits.push(label(caps.aspects, "aspect", o.aspect));
  if (o.duration) {
    const key = { upTo: "sum.upTo", exactly: "sum.exactly", about: "sum.about" }[o.duration.mode];
    if (key) bits.push(t(key, { seconds: o.duration.seconds }));
  }
  if (o.pacing && o.pacing !== "standard") bits.push(label(caps.pacing, "pacing", o.pacing));
  if (o.cutRate) bits.push(label(caps.cutRates, "cutRate", String(o.cutRate)));
  if (o.look) bits.push(t("sum.look", { name: o.look }));
  if (o.visual) bits.push(t("sum.fromPictures"));
  if (o.music === MUSIC_NONE) bits.push(t("sum.noMusic"));
  else if (o.music && o.music !== MUSIC_AUTO) {
    const c = o.musicChunk || {};
    const name = trackName(parseMusicValue(o.music).relPath);
    if (c.startSeconds && c.lengthSeconds) {
      bits.push(t("sum.musicFromFor", {
        name, start: formatTimecode(c.startSeconds), length: c.lengthSeconds,
      }));
    } else if (c.startSeconds) {
      bits.push(t("sum.musicFrom", { name, start: formatTimecode(c.startSeconds) }));
    } else {
      bits.push(t("sum.music", { name }));
    }
  }
  return bits.join(" · ");
}

module.exports = {
  SCHEMA_VERSION,
  VIDEO_EXTENSIONS,
  MUSIC_AUTO,
  MUSIC_NONE,
  MUSIC_LIBRARY_PREFIX,
  isVideoFile,
  formatDuration,
  parseMusicValue,
  parseTimecode,
  formatTimecode,
  parseSeconds,
  trackName,
  musicChoices,
  normaliseJobId,
  jobIdWasChanged,
  buildRequest,
  validateRequest,
  requestFileName,
  describeRequest,
};
