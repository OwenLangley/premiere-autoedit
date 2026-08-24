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

module.exports = {
  SUPPORTED_SCHEMA,
  validatePlan,
  summarize,
  sections,
  withoutSections,
  clipTimes,
  toFrames,
  toSeconds,
};
