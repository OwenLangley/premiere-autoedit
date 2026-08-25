"use strict";
/**
 * EditPlan handling on the panel side: validate, summarise, and let the editor
 * drop sections before anything touches the project.
 *
 * Pure functions only -- no `premierepro` import -- so this runs under `node --test`.
 * The panel must not trust a plan file blindly: it is written by another process,
 * possibly an older version of it.
 */

const { toFrames, toSeconds, timecode } = require("./timebase");

const SUPPORTED_SCHEMA = "1.0";

/**
 * Structural check. Deliberately strict: a malformed plan caught here is a
 * dialog, the same plan caught halfway through a transaction is a half-built
 * sequence the editor has to unpick.
 * @param {any} plan
 * @returns {string[]} human-readable problems, empty when the plan is usable
 */
function validatePlan(plan) {
  /** @type {string[]} */
  const errors = [];
  if (!plan || typeof plan !== "object") return ["plan is not an object"];

  if (plan.schemaVersion !== SUPPORTED_SCHEMA) {
    errors.push(
      `plan uses schema ${plan.schemaVersion || "(missing)"}, this panel supports ${SUPPORTED_SCHEMA}. Update the panel or re-run the analysis.`
    );
    return errors; // every later check assumes 1.0 field names
  }

  for (const key of ["jobId", "recipe", "timebase", "media", "sequence", "timeline"]) {
    if (plan[key] === undefined) errors.push(`missing required field: ${key}`);
  }
  if (errors.length) return errors;

  const tb = plan.timebase;
  if (!(tb.fpsNum > 0) || !(tb.fpsDen > 0)) errors.push("timebase has a non-positive frame rate");

  if (!Array.isArray(plan.media) || plan.media.length === 0) errors.push("plan has no media");

  const ids = new Set();
  for (const m of plan.media || []) {
    if (ids.has(m.id)) errors.push(`duplicate media id: ${m.id}`);
    ids.add(m.id);
    if (!m.relPath) errors.push(`media ${m.id} has no relPath`);
    if (!(m.durationSeconds > 0)) errors.push(`media ${m.id} has a non-positive duration`);
  }

  plan.timeline.forEach((c, i) => {
    if (!ids.has(c.mediaId)) errors.push(`timeline[${i}] references unknown media ${c.mediaId}`);
    if (!(c.durationFrames >= 1)) errors.push(`timeline[${i}] has a zero-length duration`);
    if (c.videoTrack < 0 && c.audioTrack < 0) {
      errors.push(`timeline[${i}] is on neither a video nor an audio track`);
    }
    if (!(c.outSeconds > c.inSeconds)) errors.push(`timeline[${i}] out point is not after in point`);
    if (c.atFrame < 0) errors.push(`timeline[${i}] starts before the sequence`);
    const m = (plan.media || []).find((x) => x.id === c.mediaId);
    if (m && c.outSeconds > m.durationSeconds + 0.001) {
      errors.push(`timeline[${i}] reads past the end of ${c.mediaId}`);
    }
  });

  // Overlaps, per video track. Premiere would silently overwrite, and the editor
  // would simply find a clip missing.
  /** @type {Map<number, {start:number,end:number,i:number}[]>} */
  const byTrack = new Map();
  plan.timeline.forEach((c, i) => {
    if (c.videoTrack < 0) return;   // audio-only clip: no video lane to collide on
    const list = byTrack.get(c.videoTrack) || [];
    list.push({ start: c.atFrame, end: c.atFrame + c.durationFrames, i });
    byTrack.set(c.videoTrack, list);
  });
  for (const [track, spans] of byTrack) {
    spans.sort((a, b) => a.start - b.start);
    for (let k = 1; k < spans.length; k++) {
      if (spans[k].start < spans[k - 1].end) {
        errors.push(
          `timeline[${spans[k].i}] overlaps timeline[${spans[k - 1].i}] on V${track + 1}`
        );
      }
    }
  }
  return errors;
}

/**
 * Headline numbers for the panel.
 * @param {any} plan
 */
function summarize(plan) {
  const tb = plan.timebase;
  const clips = plan.timeline || [];
  const frames = clips.reduce((n, c) => Math.max(n, c.atFrame + c.durationFrames), 0);
  const confidences = clips.map((c) => (c.confidence === undefined ? 1 : c.confidence));
  const lowConfidence = confidences.filter((c) => c < 0.75).length;

  return {
    clipCount: clips.length,
    durationFrames: frames,
    durationSeconds: toSeconds(tb, frames),
    timecode: timecode(tb, frames),
    mediaCount: (plan.media || []).length,
    graphicsCount: (plan.graphics || []).length,
    effectsCount: (plan.effects || []).length,
    markerCount: (plan.markers || []).length,
    warnings: plan.warnings || [],
    lowConfidence,
    meanConfidence: confidences.length
      ? confidences.reduce((a, b) => a + b, 0) / confidences.length
      : 1,
  };
}

/**
 * Group entries by sectionId so the editor can review and reject before applying.
 * Entries without a sectionId land in a single implicit section that cannot be
 * toggled off -- the rough cut itself.
 * @param {any} plan
 */
function sections(plan) {
  /** @type {Map<string, {id:string,label:string,clips:number,graphics:number,effects:number,frames:number}>} */
  const out = new Map();
  const touch = (id) => {
    if (!out.has(id)) {
      out.set(id, { id, label: id === "" ? "Rough cut" : id, clips: 0, graphics: 0, effects: 0, frames: 0 });
    }
    return out.get(id);
  };
  for (const c of plan.timeline || []) {
    const s = touch(c.sectionId || "");
    s.clips += 1;
    s.frames += c.durationFrames;
  }
  for (const g of plan.graphics || []) touch(g.sectionId || "").graphics += 1;
  for (const e of plan.effects || []) touch(e.sectionId || "").effects += 1;
  return [...out.values()];
}

/**
 * Remove the named sections and re-close the resulting gaps.
 *
 * Ripple rather than leave holes: an editor who rejects the outro expects the
 * timeline to shorten, not to end with thirty seconds of black.
 * @param {any} plan
 * @param {string[]} disabledIds
 */
function withoutSections(plan, disabledIds) {
  const disabled = new Set(disabledIds);
  if (disabled.size === 0) return plan;

  const kept = (plan.timeline || [])
    .filter((c) => !disabled.has(c.sectionId || ""))
    .sort((a, b) => a.atFrame - b.atFrame);

  /** @type {Map<number, number>} original atFrame -> new atFrame */
  const moved = new Map();
  let playhead = 0;
  const timeline = kept.map((c, i) => {
    moved.set(c.atFrame, playhead);
    const next = {
      ...c,
      atFrame: playhead,
      fadeInFrames: i === 0 ? 0 : c.fadeInFrames,
      fadeOutFrames: i === kept.length - 1 ? 0 : c.fadeOutFrames,
    };
    playhead += c.durationFrames;
    return next;
  });

  const shift = (item) => {
    // Nearest preceding surviving clip decides where a graphic or marker lands.
    let best = null;
    for (const [orig, now] of moved) {
      if (orig <= item.atFrame && (best === null || orig > best[0])) best = [orig, now];
    }
    return best ? { ...item, atFrame: best[1] + (item.atFrame - best[0]) } : null;
  };

  return {
    ...plan,
    timeline,
    graphics: (plan.graphics || [])
      .filter((g) => !disabled.has(g.sectionId || ""))
      .map(shift)
      .filter(Boolean),
    effects: (plan.effects || []).filter((e) => !disabled.has(e.sectionId || "")),
    markers: (plan.markers || []).map(shift).filter(Boolean),
  };
}

/**
 * Convert a plan clip into the numbers the apply step needs, in one place so
 * rounding happens exactly once.
 * @param {any} plan @param {any} clip
 */
function clipTimes(plan, clip) {
  const tb = plan.timebase;
  return {
    inSeconds: clip.inSeconds,
    outSeconds: clip.outSeconds,
    atSeconds: toSeconds(tb, clip.atFrame),
    endSeconds: toSeconds(tb, clip.atFrame + clip.durationFrames),
    durationSeconds: toSeconds(tb, clip.durationFrames),
    atTimecode: timecode(tb, clip.atFrame),
  };
}

/**
 * Short stable tag for a plan, so subclips from different plans cannot collide.
 * @param {{jobId?: string, createdAt?: string}} plan
 */
function planTag(plan) {
  const seed = `${(plan && plan.jobId) || ""}@${(plan && plan.createdAt) || ""}`;
  let hash = 5381;
  for (let i = 0; i < seed.length; i++) hash = ((hash * 33) ^ seed.charCodeAt(i)) >>> 0;
  return hash.toString(36).padStart(7, "0").slice(-7);
}

/**
 * A subclip's name is its source range plus the plan that asked for it.
 *
 * Two bugs came out of getting this wrong. Named by position (`MUSIC_0008`), a
 * rebuild found the PREVIOUS build's subclip under the same name and used its
 * ranges -- the bed was retrimmed to 20s in the plan and the sequence still got
 * 60s. Named by range alone, a subclip built under older apply logic was still
 * reused, because the name records what was ASKED for, not what was made. The
 * plan tag closes both: a regenerated plan never inherits an older build's
 * subclips, while rebuilding the same plan reuses them, which is correct and
 * keeps the bin from filling up.
 *
 * @param {{mediaId: string, inSeconds: number, outSeconds: number}} clip
 * @param {{jobId?: string, createdAt?: string}} plan
 */
function subclipName(clip, plan) {
  const ms = (seconds) => String(Math.round(seconds * 1000));
  // NFC: mediaId comes from a filename, and a Japanese one can reach us composed
  // or decomposed depending on which API produced it.
  const id = String(clip.mediaId || "").normalize("NFC");
  return `${id}__${ms(clip.inSeconds)}-${ms(clip.outSeconds)}__${planTag(plan)}`;
}

/**
 * Group words into sentence-ish segments.
 *
 * NOTE: this shape is NOT yet accepted -- Premiere answers
 * "Failed to parse input string into JSON", and the expected schema is
 * undocumented. The self-test's transcript/schema-discovered check dumps the
 * real shape from any clip that already has a transcript; until that has been
 * captured on a machine with one, transcript import degrades to a warning and
 * the rest of the build proceeds.
 */
function toPremiereTranscript(t) {
  const language = t.language || "en";
  const segments = [];
  let current = null;
  for (const w of t.words) {
    if (!current) current = { start: w.start, end: w.end, text: w.text, speaker: w.speaker || "" };
    else {
      current.text += wordJoiner(language) + w.text;
      current.end = w.end;
    }
    if (SENTENCE_END.test(w.text) || current.end - current.start > 12) {
      segments.push(current);
      current = null;
    }
  }
  if (current) segments.push(current);
  return { language, segments };
}

/**
 * Languages written without spaces between words.
 *
 * Mirrors NO_SPACE_LANGUAGES in engine/autoedit/detect.py. Joining Japanese
 * words with spaces produces text no Japanese reader would accept -- and
 * Whisper does not even emit Japanese words, it emits sub-word token runs, so
 * the spaces would land inside words rather than between them.
 *
 * @param {string} language
 */
function wordJoiner(language) {
  const base = String(language).toLowerCase().split("-")[0];
  return ["ja", "zh", "yue", "th", "lo", "my", "km"].includes(base) ? "" : " ";
}

/**
 * Sentence-final punctuation, full-width included. A Japanese transcript ends
 * its sentences with 。！？ and never with a full stop, so the ASCII-only test
 * this replaces found no boundaries at all and fell back to the 12-second cap.
 */
const SENTENCE_END = /[.!?。！？]$/;

/**
 * The last path segment, for either separator.
 * @param {string} p
 */
function basenameOf(p) {
  const parts = String(p).split(/[\\/]/);
  return parts[parts.length - 1] || String(p);
}

/**
 * Is this project item the master for its media, rather than a subclip of it?
 *
 * This distinction is the fix for a defect that made whole clips play as one
 * frozen frame, and it is worth spelling out because nothing downstream could
 * see it.
 *
 * A subclip reports the SAME `getMediaFilePath()` as the master it came from.
 * The media index was built with a plain `set()`, so the LAST item walked won --
 * and after a build or two, that was one of our own subclips. `createSubclips`
 * then called `createSubClipAction` on a SUBCLIP, whose in/out points are
 * relative to that subclip's own start rather than to the media. The ranges
 * compounded with every build, marched past the end of the file, and Premiere
 * showed the last frame of the media, held, for the clip's entire duration.
 *
 * Everything downstream looked healthy: durations right, positions right, the
 * verifier clean. Only the pictures were wrong, which is why this survived so
 * long -- the checks in place could not see content.
 *
 * There is no `isSubclip()` on ClipProjectItem and `getContentType()` only
 * separates MEDIA from SEQUENCE, so the name is the available signal: an item
 * imported from disk carries its filename, and a subclip carries whatever it
 * was christened.
 *
 * @param {any} item @param {string} mediaPath
 */
function isMasterFor(item, mediaPath) {
  const name = String((item && item.name) || "").normalize("NFC");
  return name === basenameOf(mediaPath).normalize("NFC");
}

module.exports = {
  isMasterFor,
  basenameOf,
  toPremiereTranscript,
  subclipName,
  planTag,
  SUPPORTED_SCHEMA,
  validatePlan,
  summarize,
  sections,
  withoutSections,
  clipTimes,
  toFrames,
  toSeconds,
};
