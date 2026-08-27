"use strict";
/**
 * Applies an EditPlan to a Premiere Pro project.
 *
 * Three rules this file exists to enforce:
 *
 * 1. **Build into a new sequence.** Nothing here ever mutates the sequence the
 *    editor is working in.
 * 2. **One transaction per stage, each named.** A single Cmd-Z undoes "apply
 *    brand pass", not the last of two hundred individual edits.
 * 3. **Fail before touching anything.** Media is resolved and the plan is
 *    validated up front, so a bad plan is a dialog rather than a half-built
 *    timeline the editor has to unpick by hand.
 */

/**
 * @typedef {string | {messageKey: string, params: Record<string, any>, message: string}} BuildWarning
 */

/**
 * A build warning that can be translated.
 *
 * Same shape the engine writes: the English text so it is never lost, plus the
 * key and numbers so the panel can render it in the editor's language. Warnings
 * that only ever appear when something unusual has gone wrong stay plain
 * strings -- they are diagnostics, and translating them is a cost with no reader.
 *
 * @param {string} messageKey @param {Record<string, any>} params @param {string} message
 */
function note(messageKey, params, message) {
  return { messageKey, params, message };
}

/** @typedef {import("@adobe/premierepro").ProjectItem} ProjectItem */
/** @typedef {import("@adobe/premierepro").ClipProjectItem} ClipProjectItem */

const ppro = require("premierepro");
const {
  validatePlan, summarize, subclipName, planTag, toPremiereTranscript, isMasterFor, basenameOf,
} = require("./plan");
const { verifyBrandkit } = require("./brandkit");
const { toSeconds, toFrames, ticksForFrames, ticksForSeconds } = require("./timebase");

/**
 * Ticks per second, asked of Premiere rather than hardcoded.
 *
 * It has been 254016000000 for as long as anyone can remember -- the number is
 * chosen so that 24, 25, 30, 50, 60 and their 1001-based NTSC cousins all divide
 * it exactly -- but reading the constant costs nothing and means this code is
 * correct by measurement rather than by folklore.
 */
let ticksPerSecond = null;
function tps() {
  if (ticksPerSecond === null) {
    try {
      ticksPerSecond = BigInt(ppro.TickTime.TIME_ONE_SECOND.ticks);
    } catch {
      ticksPerSecond = 254016000000n;
    }
  }
  return ticksPerSecond;
}

/**
 * Where a subclip ends, as an exact TickTime.
 *
 * For anything with a frame grid this is just its out point. For a source
 * without one -- a music bed -- the plan's `outSeconds` is a decimal rounded to
 * four places, and rounding both ends can leave the span a hundredth of a frame
 * short: enough for Premiere to truncate the bed one frame below what the plan
 * asked for, which the verifier then reports as 2877 against 2878.
 * `durationFrames` is the authority for how long the clip is, so derive from it.
 *
 * @param {any} plan @param {any} clip
 */
function sourceOutTime(plan, clip) {
  const srcTb = sourceTimebase(plan, clip.mediaId);
  if (srcTb) return sourceTime(srcTb, clip.outSeconds, NUDGE_FRAMES);
  const start = BigInt(ticksForSeconds(tps(), clip.inSeconds));
  const span = BigInt(ticksForFrames(tps(), plan.timebase, clip.durationFrames));
  return ppro.TickTime.createWithTicks(String(start + span));
}

/**
 * The frame grid a source's in/out points live on, or null when it has none.
 * @param {any} plan @param {string} mediaId
 */
function sourceTimebase(plan, mediaId) {
  const media = mediaOf(plan, mediaId);
  if (media.hasVideo === false) return null;
  return media.timebase || plan.timebase;
}

/** A sequence position, as an exact TickTime. @param {any} tb @param {number} frames */
function atTime(tb, frames) {
  return ppro.TickTime.createWithTicks(ticksForFrames(tps(), tb, frames));
}

/**
 * A point in a source clip, as an exact TickTime.
 *
 * The plan's in/out points are already snapped to the source's own frame grid,
 * so the honest conversion is back to a frame index and then to ticks. Going
 * via a float number of seconds loses a tick on about one frame in thirty, and
 * a tick below a boundary is a whole frame below it once Premiere aligns.
 *
 * @param {any} srcTb source timebase, or null for something with no frame grid
 * @param {number} seconds @param {number} [plusFrames]
 */
function sourceTime(srcTb, seconds, plusFrames = 0) {
  if (!srcTb) {
    // A music bed is a waveform; there are no frames to land on.
    return ppro.TickTime.createWithTicks(ticksForSeconds(tps(), seconds));
  }
  return ppro.TickTime.createWithTicks(
    ticksForFrames(tps(), srcTb, toFrames(srcTb, seconds) + plusFrames)
  );
}

/**
 * How a clip's source range gets applied.
 *
 * IN_OUT sets in/out on the master ProjectItem and then overwrites, which would
 * keep the bin clean. **Measured broken on Premiere 26.3.2**: every clip in the
 * transaction inherits a single in/out, so a three-range probe asking for 1s/2s/3s
 * produced 1.001s/1.001s/1.001s. The timeline looks populated and only the ranges
 * are wrong, which is the worst way for this to fail.
 *
 * SUBCLIP creates a real subclip per range first. Unambiguous, at the cost of a
 * bin full of subclips. This is the default because it is the one that works.
 *
 * `verifyStrategy()` re-measures this on any given build -- run it via the panel
 * self-test when moving to a new Premiere version rather than assuming.
 */
const STRATEGY = { IN_OUT: "in-out", SUBCLIP: "subclip" };

const UNDO = {
  clips: "AutoEdit: Assemble rough cut",
  graphics: "AutoEdit: Apply brand graphics",
  effects: "AutoEdit: Apply brand look",
  markers: "AutoEdit: Add markers",
  crop: "AutoEdit: Scale to fill frame",
  transcript: "AutoEdit: Import transcript",
  subclips: "AutoEdit: Create subclips",
};

class ApplyError extends Error {
  /** @param {string} message @param {string} [stage] */
  constructor(message, stage) {
    super(message);
    this.name = "ApplyError";
    this.stage = stage;
  }
}

/**
 * @param {any} plan
 * @param {{
 *   resolveAbsolutePath: (relPath: string, root?: string) => Promise<string>,
 *   onProgress?: (stage: string, detail: string) => void,
 *   strategy?: string,
 *   applyGraphics?: boolean,
 *   applyEffects?: boolean,
 *   brandkit?: any,
 *   strictBrandkit?: boolean,
 * }} options
 */
async function applyPlan(plan, options) {
  /** @type {{stages: string[], warnings: BuildWarning[], sequenceName: string|null, verification?: any, summary?: any}} */
  const report = { stages: [], warnings: [], sequenceName: null };
  const progress = options.onProgress || (() => {});
  const strategy = options.strategy || STRATEGY.SUBCLIP;

  const problems = validatePlan(plan);
  if (problems.length) {
    throw new ApplyError(`plan is not usable:\n  - ${problems.join("\n  - ")}`, "validate");
  }

  const project = await ppro.Project.getActiveProject();
  if (!project) throw new ApplyError("No project is open in Premiere.", "project");

  // Pre-flight the brand kit while nothing has been created yet. A missing MOGRT
  // discovered halfway through leaves a half-branded sequence to unpick by hand.
  if (options.applyGraphics !== false || options.applyEffects !== false) {
    const brandProblems = await verifyBrandkit(options.brandkit, plan);
    if (brandProblems.length) {
      if (options.strictBrandkit) {
        throw new ApplyError(`brand kit is not usable:\n  - ${brandProblems.join("\n  - ")}`, "brandkit");
      }
      report.warnings.push(...brandProblems);
    }
  }

  // --- validate and import before creating anything ------------------------
  // Only paths are carried forward; live references are fetched after the
  // sequence exists, because creating it invalidates any held earlier.
  progress("resolve", `resolving ${plan.media.length} source file(s)`);
  const mediaPaths = await resolveMedia(project, plan, options);

  // --- new sequence --------------------------------------------------------
  progress("sequence", `creating "${plan.sequence.name}"`);
  const sequence = await createSequence(project, plan);
  report.sequenceName = plan.sequence.name;
  report.stages.push("sequence");
  const rateNote = await checkSequenceRate(sequence, plan);
  if (rateNote) report.warnings.push(rateNote);

  // --- clips ---------------------------------------------------------------
  progress("clips", `placing ${plan.timeline.length} clip(s)`);
  const items = await fetchItems(project, mediaPaths);

  // Attach proxies before anything is placed, so the first playback the editor
  // tries is already the fast one.
  const attached = await attachProxies(plan, items);
  if (attached.count) {
    report.warnings.push(attached.note);
    report.stages.push("proxies");
  }
  report.warnings.push(...attached.warnings);

  const sources =
    strategy === STRATEGY.SUBCLIP
      ? await createSubclips(project, plan, items)
      : items;
  report.warnings.push(...(await placeClips(project, sequence, plan, sources, strategy)));
  report.stages.push("clips");

  // --- reframe -------------------------------------------------------------
  if (plan.sequence.frameWidth && plan.sequence.frameHeight) {
    progress("reframe", `scaling to fill ${plan.sequence.frameWidth}x${plan.sequence.frameHeight}`);
    try {
      report.warnings.push(...(await applyCropToFill(project, sequence, plan)));
      report.stages.push("reframe");
    } catch (err) {
      report.warnings.push(`could not scale clips to fill the frame: ${err.message}`);
    }
  }

  // --- graphics ------------------------------------------------------------
  if (options.applyGraphics !== false && (plan.graphics || []).length) {
    progress("graphics", `inserting ${plan.graphics.length} graphic(s)`);
    const gWarnings = await placeGraphics(sequence, plan, options.brandkit);
    report.warnings.push(...gWarnings);
    report.stages.push("graphics");
  }

  // --- effects -------------------------------------------------------------
  if (options.applyEffects !== false && (plan.effects || []).length) {
    progress("effects", `applying ${plan.effects.length} effect(s)`);
    const eWarnings = await applyEffects(project, sequence, plan);
    report.warnings.push(...eWarnings);
    report.stages.push("effects");
  }

  // --- markers -------------------------------------------------------------
  if ((plan.markers || []).length) {
    progress("markers", `adding ${plan.markers.length} marker(s)`);
    await addMarkers(project, sequence, plan);
    report.stages.push("markers");
  }

  // --- transcript ----------------------------------------------------------
  if ((plan.transcripts || []).length) {
    progress("transcript", "importing transcript");
    const tWarnings = await importTranscripts(project, plan, await fetchItems(project, mediaPaths));
    report.warnings.push(...tWarnings);
    report.stages.push("transcript");
  }

  // --- verify what actually landed ----------------------------------------
  progress("verify", "checking the timeline against the plan");
  try {
    const check = await verifyApplied(sequence, plan, strategy);
    report.verification = check;
    if (!check.ok) {
      for (const w of check.wrongSource) {
        report.warnings.push(
          `the clip at frame ${w.atFrame} on V${w.track + 1} is "${w.actual}", but the plan ` +
          `asked for "${w.expected}" -- the timeline is the right shape and the wrong footage`
        );
      }
      for (const g of check.gaps) {
        report.warnings.push(
          `${g.gapFrames}-frame gap after clip ${g.afterIndex} on V${g.track + 1} -- that is black on screen`
        );
      }
      for (const m of check.mismatches) {
        report.warnings.push(`timeline does not match plan: ${JSON.stringify(m)}`);
      }
    }
  } catch (err) {
    report.warnings.push(`could not verify the built timeline: ${err.message}`);
  }

  report.summary = summarize(plan);
  progress("done", `built "${plan.sequence.name}"`);
  return report;
}

/**
 * Run a mutating transaction under locked access.
 *
 * Premiere requires this. Building an Action outside `lockedAccess` -- even just
 * calling createSetInOutPointsAction, with no transaction in sight -- throws
 * "The script object is no longer valid". Reads (getMediaFilePath, getStartTime)
 * work anywhere; anything that creates or commits an Action does not.
 *
 * The callback is synchronous by necessity: lockedAccess guarantees the project
 * will not change while it runs, so fetch every reference you need before
 * calling it and do only action-building inside.
 *
 * @param {any} project
 * @param {(compound: any) => void} build
 * @param {string} undoLabel
 */
function transact(project, build, undoLabel) {
  let committed = false;
  project.lockedAccess(() => {
    committed = project.executeTransaction(build, undoLabel);
  });
  return committed;
}

// --------------------------------------------------------------- media

/** macOS filesystems are case-insensitive; compare paths accordingly. */
function normalizePath(p) {
  // NFC because macOS returns decomposed forms from some APIs and composed from
  // others. With ASCII names the two are identical and this never mattered; with
  // a Japanese filename the mismatch made an imported clip look missing, so it
  // was imported again on every build.
  return String(p || "").normalize("NFC").replace(/\/+$/, "").toLowerCase();
}

/**
 * Index every clip already in the project by its media path.
 *
 * Matching on media path rather than clip name, because editors rename bin items
 * constantly and a renamed clip is still the same file. Note that
 * `findItemsMatchingMediaPath` is an instance method on ClipProjectItem, so it is
 * useless for the "do I have this file yet?" question -- hence the manual walk.
 */
async function indexProjectMedia(project) {
  /** @type {Map<string, ProjectItem>} */
  const index = new Map();
  const root = await project.getRootItem();
  const queue = [root];
  let guard = 10000; // bins can nest; this only bounds a pathological project

  while (queue.length && guard-- > 0) {
    const folder = queue.shift();
    let items;
    try {
      items = await folder.getItems();
    } catch {
      continue;
    }
    for (const item of items || []) {
      let asFolder = null;
      try {
        asFolder = ppro.FolderItem.cast(item);
      } catch {
        /* not a bin */
      }
      if (asFolder) {
        queue.push(asFolder);
        continue;
      }
      try {
        const clip = ppro.ClipProjectItem.cast(item);
        if (!clip) continue;
        const mediaPath = await clip.getMediaFilePath();
        if (mediaPath) {
          const key = normalizePath(mediaPath);
          const name = String(item.name || "").normalize("NFC");
          // A master is named after its file; a subclip never is. See below.
          const isMaster = name === basenameOf(mediaPath).normalize("NFC");
          if (isMaster || !index.has(key)) index.set(key, item);
        }
      } catch {
        /* sequences and other non-media items have no media path */
      }
    }
  }
  return index;
}



/**
 * Ensure every source is present in the project, importing what is missing, and
 * return a mediaId -> absolute path map.
 *
 * Deliberately returns PATHS rather than live ProjectItem references. Premiere
 * invalidates object references when the project mutates -- creating a sequence
 * is enough -- and using one afterwards throws "The script object is no longer
 * valid." Paths survive; object references must be re-fetched just before use.
 * @returns {Promise<Map<string, string>>}
 */
async function resolveMedia(project, plan, options) {
  /** @type {Map<string, string>} */
  const paths = new Map();
  /** @type {{id:string, abs:string}[]} */
  const missing = [];

  const index = await indexProjectMedia(project);

  for (const m of plan.media) {
    let abs;
    try {
      abs = await options.resolveAbsolutePath(m.relPath, m.root);
    } catch (err) {
      const where = m.root === "music" ? "music folder" : "media root";
      throw new ApplyError(
        `cannot locate media "${m.relPath}" (${m.id}). Check the ${where} in panel settings.\n${err.message}`,
        "resolve"
      );
    }
    paths.set(m.id, abs);
    if (!index.has(normalizePath(abs))) missing.push({ id: m.id, abs });
  }

  if (missing.length) {
    const ok = await project.importFiles(
      missing.map((x) => x.abs), true, await project.getRootItem(), false
    );
    if (!ok) {
      throw new ApplyError(
        `Premiere could not import:\n  - ${missing.map((x) => x.abs).join("\n  - ")}`,
        "import"
      );
    }
    const after = await indexProjectMedia(project);
    for (const entry of missing) {
      if (!after.has(normalizePath(entry.abs))) {
        throw new ApplyError(
          `imported ${entry.abs} but could not find it in the project afterwards`,
          "import"
        );
      }
    }
  }
  return paths;
}

/**
 * Fetch live ProjectItem references for the given paths.
 *
 * Call this immediately before the transaction that uses them, never before a
 * project mutation. See resolveMedia for why.
 * @param {any} project @param {Map<string,string>} paths
 * @returns {Promise<Map<string, ProjectItem>>}
 */
async function fetchItems(project, paths) {
  const index = await indexProjectMedia(project);
  /** @type {Map<string, ProjectItem>} */
  const items = new Map();
  for (const [id, abs] of paths) {
    const hit = index.get(normalizePath(abs));
    if (!hit) throw new ApplyError(`media for ${id} vanished from the project: ${abs}`, "resolve");
    items.set(id, hit);
  }
  return items;
}

async function createSequence(project, plan) {
  const name = plan.sequence.name;
  const sequence = plan.sequence.presetPath
    ? await project.createSequenceWithPresetPath(name, plan.sequence.presetPath)
    : await project.createSequence(name);
  if (!sequence) throw new ApplyError(`could not create sequence "${name}"`, "sequence");
  return sequence;
}

/**
 * The frame rate the sequence actually ended up with, or null if it will not say.
 *
 * Worth asking rather than assuming. Without a preset, `createSequence` gives
 * Premiere's default rate, which has nothing to do with the plan -- and every
 * clip then lands on THAT grid. A one-second clip on a 29.97 grid is 29.97
 * frames, which cannot exist, so it truncates to 29 and the clip is short. The
 * positions still look right, because they are far enough apart that rounding
 * absorbs the error, so the damage shows up only as durations.
 * @param {any} sequence
 */
async function sequenceFps(sequence) {
  try {
    const settings = await sequence.getSettings();
    const rate = settings && settings.getVideoFrameRate();
    const fps = rate && rate.value;
    return Number.isFinite(fps) && fps > 0 ? fps : null;
  } catch {
    return null;
  }
}

/**
 * Warn when the sequence is not on the grid the plan was written for.
 *
 * Every position and duration in a plan is a frame count in the plan's timebase.
 * If the sequence runs at another rate, those frame counts mean a different
 * amount of time than the engine intended, and clip lengths quietly change. The
 * engine writes a preset precisely so this cannot happen, so reaching this
 * warning means a plan arrived without one.
 * @param {any} sequence @param {any} plan
 */
async function checkSequenceRate(sequence, plan) {
  const actual = await sequenceFps(sequence);
  if (actual === null) return null;
  const wanted = plan.timebase.fpsNum / plan.timebase.fpsDen;
  if (Math.abs(actual - wanted) < 0.001) return null;
  const fmt = (n) => n.toFixed(3).replace(/\.?0+$/, "");
  return note(
    "sequence.rateMismatch",
    { actual: fmt(actual), wanted: fmt(wanted) },
    `the sequence is ${fmt(actual)}fps but the plan was written for ${fmt(wanted)}fps, `
    + `so clip lengths will not be what the plan asked for`
    + (plan.sequence.presetPath ? "" : " -- this plan carries no sequence preset")
  );
}

// --------------------------------------------------------------- clips

/**
 * SUBCLIP strategy: materialise one subclip per source range.
 * @param {any} project @param {any} plan @param {Map<string, ProjectItem>} items
 * @returns {Promise<Map<string, ProjectItem>>}
 */
/**
 * Frames added to a subclip's out point. Zero, and that is a measured result.
 *
 * There used to be a one-frame nudge here, added because `createSubClipAction`
 * returned clips a frame short (25/50/25 requested, 24/49/24 placed). That
 * measurement was taken when out points were built from a float number of
 * seconds -- and a float frame boundary sits a tick BELOW the real one about 3%
 * of the time, so Premiere aligned down and lost the frame. The nudge was
 * papering over the float, not over Premiere.
 *
 * With exact ticks the out point is exact and the nudge over-corrects. It showed
 * as the "residual, uncharacterised" final clip that came back a frame long --
 * every other clip hid it, because the next clip's overwrite trimmed the extra
 * frame off. Rebuilt with this at 0: 29 clips, zero gaps, zero mismatches.
 */
const NUDGE_FRAMES = 0;

async function createSubclips(project, plan, items) {
  /** @type {Map<string, ProjectItem>} */
  const byKey = new Map();
  const wanted = [];

  plan.timeline.forEach((clip) => {
    const key = `${clip.mediaId}@${clip.inSeconds}-${clip.outSeconds}`;
    if (!byKey.has(key)) {
      byKey.set(key, null);
      wanted.push({ key, clip });
    }
  });

  // Premiere's subclip out point is inclusive of the last frame, so a range
  // passed verbatim comes back exactly one frame short. Measured: asking for
  // 25/50/25 frames yielded 24/49/24, positions exact. The correction is one
  // frame of the SOURCE timebase -- a 23.976 source in a 25fps sequence would
  // otherwise be nudged by the wrong amount -- and it is now applied as a whole
  // frame index rather than added to a float, so it cannot drift.
  //
  // An audio-only source is exempt: the nudge corrects an inclusive last VIDEO
  // frame, and applying it to the music bed made it land one frame long, which
  // the verifier caught as 499 frames against a planned 498.


  // Never build a subclip from a subclip. When the resolver hands back the wrong
  // item the result is silent and awful -- correct durations, correct positions,
  // and a single frozen frame where the picture should be -- so this is checked
  // rather than assumed.
  const mediaPaths = plan.media.reduce((m, e) => m.set(e.id, e.relPath || e.id), new Map());
  for (const { clip } of wanted) {
    const item = items.get(clip.mediaId);
    if (item && !isMasterFor(item, mediaPaths.get(clip.mediaId) || "")) {
      throw new ApplyError(
        `${clip.mediaId} resolved to "${item.name}", which is not the master clip for ` +
        `${mediaPaths.get(clip.mediaId)}. Subclipping that would compound its source ` +
        `range and play a frozen frame. Remove stale subclips from the project bin.`,
        "clips"
      );
    }
  }

  transact(project, (compound) => {
    for (const { clip } of wanted) {
      const master = ppro.ClipProjectItem.cast(items.get(clip.mediaId));
      // Asking an audio-only master for video yields no subclip at all, and the
      // music bed then vanished with no error anywhere -- the plan had it, the
      // sequence did not. Take only the streams the source actually has.
      const media = mediaOf(plan, clip.mediaId);
      compound.addAction(
        master.createSubClipAction(
          subclipName(clip, plan),
          sourceTime(sourceTimebase(plan, clip.mediaId), clip.inSeconds),
          sourceOutTime(plan, clip),
          true,
          { takeVideo: media.hasVideo !== false, takeAudio: media.hasAudio !== false }
        )
      );
    }
  }, UNDO.subclips);

  // Subclips are created by name; look each one back up.
  const root = await project.getRootItem();
  const all = await root.getItems();
  for (const { key, clip } of wanted) {
    const name = subclipName(clip, plan).normalize("NFC");
    const match = all.find((it) => String(it.name || "").normalize("NFC") === name);
    if (match) byKey.set(key, match);
  }
  return byKey;
}


/**
 * @param {any} project @param {any} sequence @param {any} plan
 * @param {Map<string, ProjectItem>} sources @param {string} strategy
 */
async function placeClips(project, sequence, plan, sources, strategy) {
  const editor = ppro.SequenceEditor.getEditor(sequence);
  const tb = plan.timebase;
  /** @type {BuildWarning[]} */
  const warnings = [];

  transact(project, (compound) => {
    for (const clip of plan.timeline) {
      const at = atTime(tb, clip.atFrame);

      let source;
      if (strategy === STRATEGY.SUBCLIP) {
        source = sources.get(`${clip.mediaId}@${clip.inSeconds}-${clip.outSeconds}`);
      } else {
        source = sources.get(clip.mediaId);
        if (!source) {
          warnings.push(describeMissingSource(plan, clip));
          continue;
        }
        // createSetInOutPointsAction is defined on ClipProjectItem, not on the
        // plain ProjectItem the resolver hands back, so the cast is required.
        const asClip = ppro.ClipProjectItem.cast(source);
        // Order matters: the in/out must be set before the overwrite that reads it.
        compound.addAction(
          asClip.createSetInOutPointsAction(
            sourceTime(sourceTimebase(plan, clip.mediaId), clip.inSeconds),
            sourceTime(sourceTimebase(plan, clip.mediaId), clip.outSeconds)
          )
        );
      }
      // A `continue` here used to be silent, and silence is exactly wrong: the
      // clip is in the plan, absent from the sequence, and nothing says so.
      if (!source) {
        warnings.push(describeMissingSource(plan, clip));
        continue;
      }

      compound.addAction(
        editor.createOverwriteItemAction(source, at, clip.videoTrack, clip.audioTrack)
      );
    }
  }, UNDO.clips);

  return warnings;
}

/** @param {any} plan @param {string} mediaId */
function mediaOf(plan, mediaId) {
  return (plan.media || []).find((m) => m.id === mediaId) || {};
}

/** Says which clip went missing, in terms an editor can act on. */
function describeMissingSource(plan, clip) {
  const media = mediaOf(plan, clip.mediaId);
  const what = media.role === "music" ? "music bed" : "clip";
  const where = clip.videoTrack < 0
    ? `A${clip.audioTrack + 1}`
    : `V${clip.videoTrack + 1} at frame ${clip.atFrame}`;
  return `${what} "${media.relPath || clip.mediaId}" could not be placed on ${where} -- it is missing from the sequence`;
}

/**
 * Read the built timeline back and compare it to the plan.
 *
 * Runs after every apply, not just in tests. The failure this catches is the
 * quiet one: a clip landing a frame short leaves a single black frame between
 * cuts, which survives review and shows up in the delivered master.
 *
 * It also checks WHICH source landed in each slot, not only where and how long.
 * Position and duration alone once passed a sequence made entirely of frozen
 * stills, and they would equally pass one where every slot holds the same shot:
 * both are correct to the frame and wrong to look at. In subclip mode the
 * placed item's name is the subclip name the plan asked for, so identity is
 * readable rather than inferred.
 *
 * @param {any} sequence @param {any} plan @param {string} [strategy]
 * @returns {Promise<{ok: boolean, gaps: any[], mismatches: any[], wrongSource: any[]}>}
 */
async function verifyApplied(sequence, plan, strategy) {
  const tb = plan.timebase;
  const toFrames = (seconds) => Math.round((seconds * tb.fpsNum) / tb.fpsDen);
  /** @type {any[]} */
  const mismatches = [];
  /** @type {any[]} */
  const gaps = [];
  /** @type {any[]} */
  const wrongSource = [];

  const wanted = new Map();
  /** @type {any[]} */
  const audioOnly = [];
  for (const c of plan.timeline) {
    // videoTrack -1 marks an audio-only clip (a music bed). It has no video lane
    // to read back -- getVideoTrack(-1) would throw -- so it is checked below
    // against its audio track instead. Skipping it outright was how a missing
    // music bed passed verification.
    if (c.videoTrack < 0) {
      audioOnly.push(c);
      continue;
    }
    const list = wanted.get(c.videoTrack) || [];
    list.push(c);
    wanted.set(c.videoTrack, list);
  }

  for (const [trackIndex, planned] of wanted) {
    const track = await sequence.getVideoTrack(trackIndex);
    if (!track) {
      mismatches.push({ track: trackIndex, error: "track missing" });
      continue;
    }
    const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];
    /** @type {{at:number,dur:number,source:string|null}[]} */
    const actual = [];
    for (const item of items) {
      const start = await item.getStartTime();
      const end = await item.getEndTime();
      let source = null;
      try {
        const pi = await item.getProjectItem();
        source = pi ? String(pi.name || "").normalize("NFC") : null;
      } catch {
        source = null;   // unreadable identity is not a reason to fail the build
      }
      actual.push({
        at: toFrames(start.seconds),
        dur: toFrames(end.seconds - start.seconds),
        source,
      });
    }
    actual.sort((a, b) => a.at - b.at);

    planned.sort((a, b) => a.atFrame - b.atFrame);
    if (actual.length !== planned.length) {
      mismatches.push({ track: trackIndex, expectedClips: planned.length, actualClips: actual.length });
    }

    // Identity. Only meaningful in subclip mode, where each slot gets its own
    // named item; with shared in/out points every slot legitimately holds the
    // same master and the name says nothing.
    if (strategy === STRATEGY.SUBCLIP) {
      for (let i = 0; i < Math.min(actual.length, planned.length); i += 1) {
        const want = subclipName(planned[i], plan).normalize("NFC");
        const got = actual[i].source;
        if (got && got !== want) {
          wrongSource.push({
            track: trackIndex, atFrame: planned[i].atFrame,
            expected: want, actual: got, mediaId: planned[i].mediaId,
          });
        }
      }
    }
    planned.forEach((c, i) => {
      const got = actual[i];
      if (!got) return;
      if (got.at !== c.atFrame || got.dur !== c.durationFrames) {
        mismatches.push({
          track: trackIndex, index: i,
          expected: { at: c.atFrame, dur: c.durationFrames },
          actual: got,
        });
      }
    });

    // Gaps between consecutive clips, in frames.
    for (let i = 1; i < actual.length; i++) {
      const gap = actual[i].at - (actual[i - 1].at + actual[i - 1].dur);
      if (gap > 0) gaps.push({ track: trackIndex, afterIndex: i - 1, gapFrames: gap });
    }
  }
  // Audio-only clips: position AND length on their own track. Length matters --
  // the first version of this check waved it through as "a bed may outrun the
  // picture", and a 60s track under a 20s edit passed verification clean.
  for (const c of audioOnly) {
    /** @type {{at:number,dur:number}|null} */
    let found = null;
    try {
      const track = await sequence.getAudioTrack(c.audioTrack);
      if (!track) {
        mismatches.push({ audioTrack: c.audioTrack, error: "track missing" });
        continue;
      }
      const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];
      for (const item of items) {
        const start = await item.getStartTime();
        if (toFrames(start.seconds) !== c.atFrame) continue;
        const end = await item.getEndTime();
        found = { at: toFrames(start.seconds), dur: toFrames(end.seconds - start.seconds) };
        break;
      }
    } catch (err) {
      mismatches.push({ audioTrack: c.audioTrack, error: err.message });
      continue;
    }
    if (!found) {
      mismatches.push({
        audioTrack: c.audioTrack, mediaId: c.mediaId, atFrame: c.atFrame,
        error: "audio-only clip is in the plan but not on the track",
      });
    } else if (found.dur !== c.durationFrames) {
      mismatches.push({
        audioTrack: c.audioTrack, mediaId: c.mediaId,
        expected: { at: c.atFrame, dur: c.durationFrames }, actual: found,
      });
    }
  }

  return {
    ok: mismatches.length === 0 && gaps.length === 0 && wrongSource.length === 0,
    gaps, mismatches, wrongSource,
  };
}

// --------------------------------------------------------------- graphics

/**
 * MOGRT insertion is a direct call, not an Action, so it cannot join a
 * CompoundAction -- each insert lands as its own undo step. Worth knowing before
 * an editor presses Cmd-Z expecting the whole brand pass to disappear.
 */
async function placeGraphics(sequence, plan, brandkit) {
  const editor = ppro.SequenceEditor.getEditor(sequence);
  const tb = plan.timebase;
  /** @type {BuildWarning[]} */
  const warnings = [];

  for (const g of plan.graphics) {
    const entry = brandkit && brandkit.mogrts ? brandkit.mogrts[g.mogrt] : null;
    if (!entry || !entry.path) {
      warnings.push(`no brandkit entry for MOGRT "${g.mogrt}" -- skipped`);
      continue;
    }
    let inserted;
    try {
      inserted = editor.insertMogrtFromPath(
        entry.path,
        atTime(tb, g.atFrame),
        g.videoTrack,
        -1
      );
    } catch (err) {
      warnings.push(`could not insert "${g.mogrt}": ${err.message}`);
      continue;
    }
    if (g.fields && inserted && inserted.length) {
      warnings.push(...(await setMogrtFields(inserted[0], g, entry)));
    }
  }
  return warnings;
}

/**
 * MOGRT parameters are addressed by zero-based index, and the index order is
 * defined by the template itself -- there is no lookup by field name. The
 * brandkit therefore carries a name -> index map produced by `discoverMogrtParams`.
 * If a template is re-exported with fields reordered, that map must be regenerated
 * or values land in the wrong slots silently.
 */
async function setMogrtFields(trackItem, graphic, entry) {
  /** @type {BuildWarning[]} */
  const warnings = [];
  const map = entry.params || {};
  let chain;
  try {
    chain = await trackItem.getComponentChain();
  } catch (err) {
    return [`"${graphic.mogrt}": cannot read component chain (${err.message})`];
  }

  const component = await findMogrtComponent(chain);
  if (!component) return [`"${graphic.mogrt}": no Motion Graphics component found`];

  for (const [field, value] of Object.entries(graphic.fields)) {
    const index = map[field];
    if (index === undefined) {
      warnings.push(`"${graphic.mogrt}": no param index known for field "${field}" -- run discoverMogrtParams`);
      continue;
    }
    try {
      const project = await ppro.Project.getActiveProject();
      transact(project, (compound) => {
        const param = component.getParam(index);
        compound.addAction(param.createSetValueAction(param.createKeyframe(value), true));
      }, `AutoEdit: set ${graphic.mogrt}.${field}`);
    } catch (err) {
      warnings.push(`"${graphic.mogrt}".${field}: ${err.message}`);
    }
  }
  return warnings;
}

async function findMogrtComponent(chain) {
  const count = chain.getComponentCount ? chain.getComponentCount() : 0;
  for (let i = 0; i < count; i++) {
    const c = chain.getComponentAtIndex(i);
    const name = await c.getMatchName();
    if (name && name.indexOf("MGT") !== -1) return c;
  }
  return null;
}

// --------------------------------------------------------------- effects

async function applyEffects(project, sequence, plan) {
  /** @type {BuildWarning[]} */
  const warnings = [];

  for (const effect of plan.effects) {
    const target = parseTarget(effect.target);
    if (!target) {
      warnings.push(`unrecognised effect target "${effect.target}"`);
      continue;
    }

    let component;
    try {
      component = await ppro.VideoFilterFactory.createComponent(effect.matchName);
    } catch (err) {
      warnings.push(`effect "${effect.matchName}" is not installed on this machine (${err.message})`);
      continue;
    }

    const trackItems = await collectTrackItems(sequence, target);
    if (!trackItems.length) {
      warnings.push(`effect target "${effect.target}" matched no clips`);
      continue;
    }

    transact(project, (compound) => {
      for (const item of trackItems) {
        try {
          compound.addAction(item.__chain.createAppendComponentAction(component));
        } catch (err) {
          warnings.push(`could not apply "${effect.matchName}": ${err.message}`);
        }
      }
    }, UNDO.effects);
  }
  return warnings;
}

function parseTarget(spec) {
  const m = /^track:([va])(\d+)$/.exec(spec || "");
  if (m) return { kind: "track", media: m[1], index: Number(m[2]) };
  const c = /^clip:(\d+)$/.exec(spec || "");
  if (c) return { kind: "clip", index: Number(c[1]) };
  return null;
}

async function collectTrackItems(sequence, target) {
  const out = [];
  if (target.kind !== "track" || target.media !== "v") return out;
  const track = await sequence.getVideoTrack(target.index);
  if (!track) return out;
  const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
  for (const item of items || []) {
    try {
      item.__chain = await item.getComponentChain();
      out.push(item);
    } catch {
      /* a clip with no component chain is not a failure worth reporting */
    }
  }
  return out;
}

/**
 * Scale clips to fill a differently-shaped frame.
 *
 * `createSetScaleToFrameSizeAction` FITS -- it letterboxes -- which is not what
 * "make it vertical" means. Filling means setting the Motion component's Scale.
 *
 * Unlike MOGRT parameters, Motion is a standard component with stable display
 * names, so the parameter index is discovered by name at runtime rather than
 * hardcoded. If the lookup fails the clip is left at its default scale and the
 * editor is told, which is a letterboxed clip rather than a broken one.
 *
 * @returns {Promise<BuildWarning[]>} warnings
 */
async function applyCropToFill(project, sequence, plan) {
  /** @type {BuildWarning[]} */
  const warnings = [];
  const frameW = plan.sequence.frameWidth;
  const frameH = plan.sequence.frameHeight;
  if (!frameW || !frameH) return warnings;

  const sizeFor = new Map(
    (plan.media || []).filter((m) => m.width && m.height).map((m) => [m.id, [m.width, m.height]])
  );
  if (!sizeFor.size) {
    return ["source dimensions are missing from the plan, so clips were not scaled to fill"];
  }

  const tracks = new Set(plan.timeline.filter((c) => c.videoTrack >= 0).map((c) => c.videoTrack));
  let scaled = 0;
  let cropped = 0;
  let missingParam = false;

  for (const trackIndex of tracks) {
    const track = await sequence.getVideoTrack(trackIndex);
    if (!track) continue;
    const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];

    for (let i = 0; i < items.length; i++) {
      const clip = plan.timeline.filter((c) => c.videoTrack === trackIndex)
        .sort((a, b) => a.atFrame - b.atFrame)[i];
      const size = clip && sizeFor.get(clip.mediaId);
      if (!size) continue;

      const [srcW, srcH] = size;
      // Fill: the larger of the two ratios, so neither axis leaves a gap.
      const percent = Math.max(frameW / srcW, frameH / srcH) * 100;
      if (Math.abs(percent - 100) < 0.5) continue;
      // Only a shape mismatch loses anything off the edges. A 4K vertical clip
      // in a 1080 vertical sequence is a clean 2:1 reduction, and telling the
      // editor their edges were cropped when they were not is a small lie that
      // costs trust in every other warning.
      if (Math.abs(srcW / srcH - frameW / frameH) > 0.01) cropped += 1;

      try {
        const chain = await items[i].getComponentChain();
        const param = await findMotionScale(chain);
        if (!param) {
          missingParam = true;
          continue;
        }
        transact(project, (compound) => {
          compound.addAction(param.createSetValueAction(param.createKeyframe(percent), true));
        }, UNDO.crop);
        scaled += 1;
      } catch (err) {
        warnings.push(`could not scale a clip to fill: ${err.message}`);
      }
    }
  }

  if (missingParam) {
    warnings.push(
      "could not find the Motion Scale parameter, so some clips were left at their " +
      "default scale -- they will letterbox rather than fill"
    );
  }
  if (scaled) {
    warnings.push(cropped
      ? note(
          "reframe.scaled",
          { count: cropped, width: frameW, height: frameH },
          `${cropped} clip(s) scaled to fill ${frameW}x${frameH}; anything at the edge of ` +
          "frame is now cropped out"
        )
      : note(
          "reframe.fitted",
          { count: scaled, width: frameW, height: frameH },
          `${scaled} clip(s) resized to ${frameW}x${frameH}; the shape already matched, ` +
          "so nothing is cropped"
        ));
  }
  return warnings;
}

/** Locate Motion's Scale parameter by display name. */
async function findMotionScale(chain) {
  const count = chain.getComponentCount ? chain.getComponentCount() : 0;
  for (let c = 0; c < count; c++) {
    const component = chain.getComponentAtIndex(c);
    let matchName = "";
    try {
      matchName = (await component.getMatchName()) || "";
    } catch {
      continue;
    }
    if (matchName.indexOf("Motion") === -1) continue;

    const params = component.getParamCount();
    for (let i = 0; i < params; i++) {
      try {
        const param = component.getParam(i);
        if ((param.displayName || "").trim().toLowerCase() === "scale") return param;
      } catch {
        /* structural slots have no display name */
      }
    }
  }
  return null;
}

// --------------------------------------------------------------- markers

async function addMarkers(project, sequence, plan) {
  const markers = await ppro.Markers.getMarkers(sequence);
  const tb = plan.timebase;
  const typeFor = {
    Comment: ppro.Marker.MARKER_TYPE_COMMENT,
    Chapter: ppro.Marker.MARKER_TYPE_CHAPTER,
    WebLink: ppro.Marker.MARKER_TYPE_WEBLINK,
    Segmentation: ppro.Marker.MARKER_TYPE_COMMENT,
  };

  transact(project, (compound) => {
    for (const m of plan.markers) {
      compound.addAction(
        markers.createAddMarkerAction(
          m.name,
          typeFor[m.type || "Comment"] || ppro.Marker.MARKER_TYPE_COMMENT,
          atTime(tb, m.atFrame),
          atTime(tb, m.durationFrames || 0),
          m.comment || ""
        )
      );
    }
  }, UNDO.markers);
}

/**
 * Point heavy sources at their proxies.
 *
 * The engine decides which footage needs one and the helper builds them in the
 * background; this only hangs them on the project items. `attachProxy` is
 * explicitly not undoable, which is why it happens before the assembly rather
 * than inside one of the named transactions -- a Cmd-Z that unbuilt the cut but
 * left the proxies attached would be confusing, and one that claimed to unattach
 * them would be lying.
 *
 * @param {any} plan @param {Map<string, ProjectItem>} items
 */
async function attachProxies(plan, items) {
  /** @type {BuildWarning[]} */
  const warnings = [];
  const wanted = (plan.media || []).filter((m) => m.proxyPath);
  let count = 0;

  for (const media of wanted) {
    const item = items.get(media.id);
    if (!item) continue;
    try {
      const clip = ppro.ClipProjectItem.cast(item);
      if (await clip.hasProxy()) { count += 1; continue; }
      if (!(await clip.canProxy())) {
        warnings.push(`${media.id}: Premiere will not take a proxy for this item`);
        continue;
      }
      if (await clip.attachProxy(media.proxyPath, false)) count += 1;
      else warnings.push(`${media.id}: attaching the proxy was refused`);
    } catch (err) {
      warnings.push(`${media.id}: could not attach a proxy (${err.message})`);
    }
  }

  return {
    count,
    warnings,
    // Said even when everything worked, because attaching a proxy changes
    // nothing on screen until the editor turns Toggle Proxies on -- and without
    // being told that, this looks exactly like it did not work.
    note: note(
      "media.proxyAttached", { count },
      `${count} clip(s) are using proxies -- turn on Toggle Proxies in the ` +
      `program monitor to play them back smoothly`
    ),
  };
}

// --------------------------------------------------------------- transcript

/**
 * Push our transcript into Premiere so the assembled cut lines up with the
 * native Text-Based Editing panel, rather than the editor having Premiere
 * re-transcribe the same audio and get slightly different word boundaries.
 */
async function importTranscripts(project, plan, items) {
  /** @type {BuildWarning[]} */
  const warnings = [];

  for (const t of plan.transcripts) {
    const item = items.get(t.mediaId);
    if (!item) {
      warnings.push(`transcript for unknown media ${t.mediaId} -- skipped`);
      continue;
    }
    const clipItem = ppro.ClipProjectItem.cast(item);
    if (ppro.Transcript.hasTranscript(clipItem)) {
      warnings.push(`${t.mediaId} already has a transcript in Premiere -- left as is`);
      continue;
    }
    try {
      const segments = ppro.Transcript.importFromJSON(JSON.stringify(toPremiereTranscript(t)));
      transact(project, (compound) => {
        compound.addAction(ppro.Transcript.createImportTextSegmentsAction(segments, clipItem));
      }, UNDO.transcript);
    } catch (err) {
      // Say what it costs, not just that it failed. The cut is already made
      // from this transcript; what is missing is Premiere's own Text-Based
      // Editing view of it. Six lines of "could not import" on an otherwise
      // clean receipt reads like the build broke, and it did not.
      warnings.push(note(
        "transcript.notImported",
        { mediaId: t.mediaId, detail: err.message },
        `${t.mediaId}: transcript not handed to Text-Based Editing (${err.message}) -- ` +
        `the cut itself is unaffected`
      ));
    }
  }
  return warnings;
}



// --------------------------------------------------------------- setup check

/**
 * Settles the IN_OUT-vs-SUBCLIP question empirically on this Premiere build.
 *
 * Places three ranges of one source using IN_OUT and reports the durations that
 * actually landed. If they differ, IN_OUT is safe here. If all three match the
 * last range, this build evaluates actions at commit time and SUBCLIP is required.
 *
 * Run once during setup and record the answer -- this is exactly the kind of
 * thing that is cheap to measure and expensive to assume.
 */
async function verifyStrategy(mediaPath) {
  const project = await ppro.Project.getActiveProject();
  const sequence = await project.createSequence("AutoEdit strategy probe");
  const editor = ppro.SequenceEditor.getEditor(sequence);

  // Fetched AFTER createSequence: a reference obtained before it is already dead.
  const index = await indexProjectMedia(project);
  const projectItem = index.get(normalizePath(mediaPath));
  if (!projectItem) throw new ApplyError(`probe media not in project: ${mediaPath}`, "strategy");
  const asClip = ppro.ClipProjectItem.cast(projectItem);
  const ranges = [[0, 1], [2, 4], [5, 8]];

  transact(project, (compound) => {
    let at = 0;
    for (const [inS, outS] of ranges) {
      compound.addAction(
        asClip.createSetInOutPointsAction(
          ppro.TickTime.createWithSeconds(inS),
          ppro.TickTime.createWithSeconds(outS)
        )
      );
      compound.addAction(
        editor.createOverwriteItemAction(projectItem, ppro.TickTime.createWithSeconds(at), 0, 0)
      );
      at += outS - inS;
    }
  }, "AutoEdit: strategy probe");

  const track = await sequence.getVideoTrack(0);
  const placed = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
  const durations = [];
  for (const item of placed || []) {
    const s = await item.getStartTime();
    const e = await item.getEndTime();
    durations.push(Number((e.seconds - s.seconds).toFixed(3)));
  }
  const expected = ranges.map(([a, b]) => b - a);
  const ok = durations.length === 3 && durations.every((d, i) => Math.abs(d - expected[i]) < 0.05);
  return {
    strategy: ok ? STRATEGY.IN_OUT : STRATEGY.SUBCLIP,
    expected,
    actual: durations,
    note: ok
      ? "IN_OUT works on this build: per-action in/out is honoured."
      : "IN_OUT is unsafe on this build: use SUBCLIP. Clips did not get their intended ranges.",
  };
}

module.exports = { applyPlan, verifyStrategy, verifyApplied, fetchItems, indexProjectMedia, normalizePath, ApplyError, STRATEGY, UNDO };
