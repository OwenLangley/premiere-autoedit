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
      out.set(id, {
        id, label: id === "" ? "Rough cut" : sectionLabel(id),
        clips: 0, graphics: 0, effects: 0, frames: 0,
      });
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
 * What groups a clip with its alternates, and what colours it.
 *
 * A story section when the job has one, otherwise the source it came from. That
 * fallback is not a placeholder: before story tagging exists, "other moments of
 * this same clip" is a genuinely useful set to be offered, and it means the swap
 * machinery ships and gets used without waiting on anything.
 * @param {{sectionId?: string, mediaId: string}} entry
 */
function groupKeyOf(entry) {
  return (entry && entry.sectionId) || (entry && entry.mediaId) || "";
}

/**
 * A stable colour for a group.
 *
 * Hashed from the key rather than handed out in encounter order, so a clip is
 * the same colour every time the plan is opened, on every editor's machine.
 * Colour is the only thing carrying structure in a 400px strip; if it shuffled
 * between refreshes it would be worse than no colour at all.
 *
 * Golden-angle stepping over the hash spreads neighbouring hues apart, so two
 * groups landing on similar hues is unlikely rather than a coin toss.
 * @param {string} key
 */
function colourFor(key) {
  let h = 2166136261;
  for (let i = 0; i < String(key).length; i += 1) {
    h ^= String(key).charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const hue = Math.round((((h >>> 0) % 360) * 137.508) % 360);
  return `hsl(${hue} 62% 52%)`;
}

/**
 * Alternates that could stand in for one timeline slot, best first.
 *
 * Two rules, both load-bearing:
 *
 * A candidate is only offered when its span is at least as long as the slot.
 * The swap keeps the slot's exact length (see `withSwaps`), so a shorter span
 * cannot fill it -- offering one would mean either a gap or a re-time, and
 * re-timing moves every cut after it off the beat.
 *
 * Same-group candidates come first, but the others are NOT filtered out. An
 * editor who wants a different shot entirely should not be stuck because the
 * grouping is currently "same source file" and this slot's file has nothing
 * else long enough.
 * @param {any} plan
 * @param {{atFrame:number, durationFrames:number, mediaId:string, inSeconds:number, sectionId?:string}} slot
 */
function candidatesFor(plan, slot, library) {
  if (!plan || !slot) return [];
  const tb = plan.timebase;
  const needed = toSeconds(tb, slot.durationFrames);
  const group = groupKeyOf(slot);
  // A hair under, because a span measured to 4 decimal places can sit a
  // rounding error below a duration derived from frames. Half a frame is far
  // tighter than any cut the editor can see and far looser than float noise.
  const slack = toSeconds(tb, 1) / 2;

  const inPlan = (plan.candidates || []).map((c) => ({ ...c, fromLibrary: false }));

  // Shots from the wider library, which the helper indexes in the background.
  // An editor picking a replacement is thinking about their footage, not about
  // the six files they happened to tick for this job -- so the pool is the
  // library, and the ones already in the edit merely sort first.
  const idByRelPath = new Map((plan.media || []).map((m) => [m.relPath, m.id]));
  const relPathById = new Map((plan.media || []).map((m) => [m.id, m.relPath]));

  // Drop a library span only when the plan has its OWN candidates for that file,
  // because then it is already represented and better described.
  //
  // This used to skip any file the plan merely referenced, which quietly emptied
  // the list in the commonest case there is: a speech-cut job carries no
  // candidates at all, and its footage is exactly what the library has indexed,
  // so every span was discarded as a duplicate of nothing.
  const covered = new Set(
    (plan.candidates || []).map((c) => relPathById.get(c.mediaId)).filter(Boolean)
  );
  const fromLibrary = ((library && library.files) || [])
    .filter((c) => !covered.has(c.relPath))
    .map((c) => ({
      ...c,
      // A library span of footage the plan already carries is not "from the
      // library" as far as the editor or the build is concerned -- it has a
      // media entry, so it groups and swaps like any other span.
      mediaId: idByRelPath.get(c.relPath),
      fromLibrary: !idByRelPath.has(c.relPath),
    }));

  return [...inPlan, ...fromLibrary]
    .filter((c) => c.outSeconds - c.inSeconds >= needed - slack)
    .map((c) => ({
      ...c,
      sameGroup: !c.fromLibrary && groupKeyOf(c) === group,
      current: !!c.mediaId && c.mediaId === slot.mediaId
        && Math.abs(c.inSeconds - slot.inSeconds) < slack,
    }))
    .sort((a, b) => {
      // Already in the edit first, then the rest of the library. Not a filter:
      // the whole point is that the library is reachable.
      if (a.sameGroup !== b.sameGroup) return a.sameGroup ? -1 : 1;
      if (a.fromLibrary !== b.fromLibrary) return a.fromLibrary ? 1 : -1;
      return (b.score || 0) - (a.score || 0);
    });
}

/**
 * A section id as an editor should read it.
 *
 * Story sections arrive as `b1-empty-indoor-sports-hall` -- numbered so two
 * beats describing the same thing stay apart, slugged so they are safe to put
 * in a plan. Neither is any use on screen, so the number becomes a position and
 * the slug becomes words again.
 *
 * Anything that is not a story id is returned untouched: a recipe's own section
 * names are already written for people.
 * @param {string} id
 */
function sectionLabel(id) {
  const m = /^b(\d+)-(.+)$/.exec(String(id || ""));
  if (!m) return id || "";
  return `${m[1]}. ${m[2].replace(/-/g, " ")}`;
}

/**
 * A key that identifies one slot, and only one.
 *
 * `atFrame` alone is not it, and choosing it cost a wiped timeline. A music bed
 * starts at frame 0 on the audio track and so does the first picture on V1, so
 * a swap keyed on the frame number hit both: the shot got its new footage, and
 * the twelve-second music slot was handed the same video, clamped back from the
 * end of the file to fit its length, and overwritten at frame 0 -- on top of
 * everything else in the sequence.
 *
 * The track is what separates them, and a plan may not place two clips at the
 * same frame on the same track: validatePlan rejects that outright.
 * @param {{atFrame:number, videoTrack?:number, audioTrack?:number}} clip
 */
function slotKey(clip) {
  const v = clip.videoTrack === undefined ? 0 : clip.videoTrack;
  const a = clip.audioTrack === undefined ? 0 : clip.audioTrack;
  return `${clip.atFrame}:${v}:${a}`;
}

/**
 * Is this slot a picture an editor could swap?
 *
 * A music bed is a timeline entry like any other and is nothing like a shot: it
 * has no frames to choose between, and replacing it with footage is never what
 * "swap this shot" means. `videoTrack < 0` is how the plan marks audio-only.
 * @param {{videoTrack?:number}} clip
 */
function isPictureSlot(clip) {
  return (clip.videoTrack === undefined ? 0 : clip.videoTrack) >= 0;
}

/**
 * The still that best represents what a timeline slot is showing.
 *
 * Timeline entries carry no thumbPath of their own -- they were written before
 * stills existed, and a speech-cut plan has no candidates to hang one off. So
 * this finds the indexed span of the same footage that CONTAINS the slot's in
 * point, which is by definition a picture of the same moment.
 *
 * Falls back to the nearest span in the same file rather than giving up: an
 * approximate frame from the right clip tells an editor which shot they are
 * looking at, and that is the entire job here.
 *
 * @param {any} plan @param {any} clip @param {any} library
 * @returns {string|null}
 */
function thumbForClip(plan, clip, library) {
  const spans = [
    ...(plan.candidates || []).filter((c) => c.mediaId === clip.mediaId && c.thumbPath),
  ];
  const rel = (plan.media || []).find((m) => m.id === clip.mediaId);
  if (rel && library && Array.isArray(library.files)) {
    for (const f of library.files) {
      if (f.relPath === rel.relPath && f.thumbPath) spans.push(f);
    }
  }
  if (!spans.length) return null;
  const inside = spans.find(
    (sp) => clip.inSeconds >= sp.inSeconds && clip.inSeconds < sp.outSeconds
  );
  if (inside) return inside.thumbPath;
  let best = spans[0];
  let bestGap = Math.abs(best.inSeconds - clip.inSeconds);
  for (const sp of spans) {
    const gap = Math.abs(sp.inSeconds - clip.inSeconds);
    if (gap < bestGap) { best = sp; bestGap = gap; }
  }
  return best.thumbPath;
}

/**
 * A still for a whole source file, for the clip picker.
 *
 * `thumbForClip` answers "what does this moment look like" and needs a plan.
 * Choosing footage happens before a plan exists, so this answers the simpler
 * question -- what is in this file -- from the library index alone, taking the
 * best-scoring span rather than the first, since the first frame of a clip is
 * often a lens cap or a hand reaching for the camera.
 *
 * @param {string} relPath @param {any} library
 * @returns {string|null}
 */
function thumbForSource(relPath, library) {
  if (!relPath || !library || !Array.isArray(library.files)) return null;
  let best = null;
  for (const f of library.files) {
    if (f.relPath !== relPath || !f.thumbPath) continue;
    if (!best || (f.score || 0) > (best.score || 0)) best = f;
  }
  return best ? best.thumbPath : null;
}

/**
 * A media id for a library file the plan has never referenced.
 *
 * Same shape the engine derives, and checked against what is already in the
 * plan: two files called `C1367.MP4` in different folders must not collapse
 * onto one id, or a swap would silently read from the wrong footage.
 * @param {any} plan @param {string} relPath
 */
function libraryMediaId(plan, relPath) {
  const stem = (relPath.split("/").pop() || "clip").replace(/\.[^.]*$/, "");
  const base = ([...stem].filter((ch) => /[A-Za-z0-9_-]/.test(ch)).join("") || "LIB").slice(0, 24);
  const taken = new Set((plan.media || []).map((m) => m.id));
  if (!taken.has(base)) return base;
  for (let n = 2; n < 1000; n += 1) {
    if (!taken.has(`${base}_${n}`)) return `${base}_${n}`;
  }
  return `${base}_x`;
}

/**
 * Apply the editor's swaps, keeping every slot exactly where and as long as it
 * was.
 *
 * This is the whole contract of the feature. `atFrame` and `durationFrames` are
 * carried through untouched and only the source changes -- because the cut
 * positions were placed on musical beats, and any change to a duration shifts
 * every clip after it off the grid. A swap that re-times is not a swap, it is a
 * different edit.
 *
 * Keyed on `atFrame`: it is the slot's identity, it is unique on a track, and
 * it survives the swap itself, so swapping the same slot twice replaces the
 * first choice rather than stacking.
 *
 * @param {any} plan
 * A pick carries EITHER a mediaId (a span of footage the plan already has) or a
 * relPath and durationSeconds (a shot from the wider library, which the plan has
 * never referenced and needs a media entry minted for).
 *
 * Keyed by `slotKey(clip)` -- frame AND track. Not by frame alone; see slotKey.
 *
 * @param {Map<string, {mediaId?:string, relPath?:string, durationSeconds?:number, inSeconds:number, reason?:string}> | null} swaps
 */
function withSwaps(plan, swaps) {
  if (!swaps || swaps.size === 0) return plan;
  const tb = plan.timebase;
  const media = [...(plan.media || [])];
  const sourceLength = new Map(media.map((m) => [m.id, m.durationSeconds]));

  // A swap can name a file the plan has never seen. `media` is what the apply
  // side imports and resolves, so the entry has to be created here or the
  // build would reference a mediaId that does not exist -- and validatePlan
  // would reject it, which is the good outcome, but only after the editor had
  // already made the choice.
  const byRelPath = new Map(media.map((m) => [m.relPath, m]));
  for (const pick of swaps.values()) {
    if (!pick.relPath || byRelPath.has(pick.relPath)) continue;
    const entry = {
      id: libraryMediaId({ media }, pick.relPath),
      relPath: pick.relPath,
      durationSeconds: pick.durationSeconds || 0,
      hasVideo: true,
    };
    media.push(entry);
    byRelPath.set(entry.relPath, entry);
    sourceLength.set(entry.id, entry.durationSeconds);
  }

  const timeline = (plan.timeline || []).map((c) => {
    const raw = swaps.get(slotKey(c));
    if (!raw) return c;
    // A library pick carries a path; a pick from the plan's own pool carries an
    // id. Resolve to an id here so everything below is uniform.
    const pick = raw.relPath && byRelPath.has(raw.relPath)
      ? { ...raw, mediaId: byRelPath.get(raw.relPath).id }
      : raw;
    const seconds = toSeconds(tb, c.durationFrames);
    // Pull the in point back if taking the slot's full length from here would
    // read past the end of the file. The shortlist already only offers spans
    // long enough, so this is the rounding tail -- but reading half a frame
    // past the end is precisely how this project produced timelines of stills
    // once already, and it costs one line to make impossible rather than
    // unlikely. The slot's LENGTH is what must not move; where inside the
    // source it starts is free.
    const limit = sourceLength.get(pick.mediaId);
    const inSeconds = Number.isFinite(limit)
      ? Math.max(0, Math.min(pick.inSeconds, limit - seconds))
      : pick.inSeconds;
    return {
      ...c,
      mediaId: pick.mediaId,
      inSeconds: Number(inSeconds.toFixed(6)),
      // Derived from the slot's frame count, never from the candidate's own
      // out point: the slot's length is what must survive.
      outSeconds: Number((inSeconds + seconds).toFixed(6)),
      reason: pick.reason || "swapped by the editor",
      swapped: true,
    };
  });
  return { ...plan, media, timeline };
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
  groupKeyOf,
  colourFor,
  candidatesFor,
  thumbForClip,
  thumbForSource,
  sectionLabel,
  slotKey,
  isPictureSlot,
  withSwaps,
  libraryMediaId,
  clipTimes,
  toFrames,
  toSeconds,
};
