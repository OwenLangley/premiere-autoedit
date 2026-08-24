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

const ppro = require("premierepro");
const { validatePlan, summarize } = require("./plan");
const { verifyBrandkit } = require("./brandkit");
const { toSeconds } = require("./timebase");

/**
 * How a clip's source range gets applied.
 *
 * IN_OUT sets in/out on the master ProjectItem and then overwrites, which keeps
 * the bin clean. It relies on Premiere evaluating each action at the point it is
 * added to the CompoundAction rather than at commit; if it evaluates at commit,
 * every clip inherits the LAST in/out and the assembly is silently wrong.
 *
 * SUBCLIP creates a real subclip per range first. Unambiguous, at the cost of a
 * bin full of subclips.
 *
 * `verifyStrategy()` below settles which one this Premiere build actually does.
 * Run it once during setup -- do not guess.
 */
const STRATEGY = { IN_OUT: "in-out", SUBCLIP: "subclip" };

const UNDO = {
  clips: "AutoEdit: Assemble rough cut",
  graphics: "AutoEdit: Apply brand graphics",
  effects: "AutoEdit: Apply brand look",
  markers: "AutoEdit: Add markers",
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
 *   resolveAbsolutePath: (relPath: string) => Promise<string>,
 *   onProgress?: (stage: string, detail: string) => void,
 *   strategy?: string,
 *   applyGraphics?: boolean,
 *   applyEffects?: boolean,
 *   brandkit?: any,
 *   strictBrandkit?: boolean,
 * }} options
 */
async function applyPlan(plan, options) {
  const report = { stages: [], warnings: [], sequenceName: null };
  const progress = options.onProgress || (() => {});
  const strategy = options.strategy || STRATEGY.IN_OUT;

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

  // --- resolve every source before creating anything -----------------------
  progress("resolve", `resolving ${plan.media.length} source file(s)`);
  const items = await resolveMedia(project, plan, options);

  // --- new sequence --------------------------------------------------------
  progress("sequence", `creating "${plan.sequence.name}"`);
  const sequence = await createSequence(project, plan);
  report.sequenceName = plan.sequence.name;
  report.stages.push("sequence");

  // --- clips ---------------------------------------------------------------
  progress("clips", `placing ${plan.timeline.length} clip(s)`);
  const sources =
    strategy === STRATEGY.SUBCLIP
      ? await createSubclips(project, plan, items)
      : items;
  await placeClips(project, sequence, plan, sources, strategy);
  report.stages.push("clips");

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
    const tWarnings = await importTranscripts(project, plan, items);
    report.warnings.push(...tWarnings);
    report.stages.push("transcript");
  }

  report.summary = summarize(plan);
  progress("done", `built "${plan.sequence.name}"`);
  return report;
}

// --------------------------------------------------------------- media

/** macOS filesystems are case-insensitive; compare paths accordingly. */
function normalizePath(p) {
  return String(p || "").replace(/\/+$/, "").toLowerCase();
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
  /** @type {Map<string, any>} */
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
        if (mediaPath) index.set(normalizePath(mediaPath), item);
      } catch {
        /* sequences and other non-media items have no media path */
      }
    }
  }
  return index;
}

/** Find each source already in the project, importing only what is missing. */
async function resolveMedia(project, plan, options) {
  /** @type {Map<string, any>} */
  const found = new Map();
  /** @type {{id:string, abs:string}[]} */
  const missing = [];

  let index = await indexProjectMedia(project);

  for (const m of plan.media) {
    let abs;
    try {
      abs = await options.resolveAbsolutePath(m.relPath);
    } catch (err) {
      throw new ApplyError(
        `cannot locate media "${m.relPath}" (${m.id}). Check the media root in panel settings.\n${err.message}`,
        "resolve"
      );
    }
    const hit = index.get(normalizePath(abs));
    if (hit) found.set(m.id, hit);
    else missing.push({ id: m.id, abs });
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
    index = await indexProjectMedia(project);
    for (const entry of missing) {
      const hit = index.get(normalizePath(entry.abs));
      if (!hit) {
        throw new ApplyError(
          `imported ${entry.abs} but could not find it in the project afterwards`,
          "import"
        );
      }
      found.set(entry.id, hit);
    }
  }
  return found;
}

async function createSequence(project, plan) {
  const name = plan.sequence.name;
  const sequence = plan.sequence.presetPath
    ? await project.createSequenceWithPresetPath(name, plan.sequence.presetPath)
    : await project.createSequence(name);
  if (!sequence) throw new ApplyError(`could not create sequence "${name}"`, "sequence");
  return sequence;
}

// --------------------------------------------------------------- clips

/** SUBCLIP strategy: materialise one subclip per source range. */
async function createSubclips(project, plan, items) {
  /** @type {Map<string, any>} */
  const byKey = new Map();
  const wanted = [];

  plan.timeline.forEach((clip, i) => {
    const key = `${clip.mediaId}@${clip.inSeconds}-${clip.outSeconds}`;
    if (!byKey.has(key)) {
      byKey.set(key, null);
      wanted.push({ key, clip, index: i });
    }
  });

  project.executeTransaction((compound) => {
    for (const { clip, index } of wanted) {
      const master = ppro.ClipProjectItem.cast(items.get(clip.mediaId));
      compound.addAction(
        master.createSubClipAction(
          `${clip.mediaId}_${String(index).padStart(4, "0")}`,
          ppro.TickTime.createWithSeconds(clip.inSeconds),
          ppro.TickTime.createWithSeconds(clip.outSeconds),
          true,
          { takeVideo: true, takeAudio: true }
        )
      );
    }
  }, UNDO.subclips);

  // Subclips are created by name; look each one back up.
  const root = await project.getRootItem();
  const all = await root.getItems();
  for (const { key, index, clip } of wanted) {
    const name = `${clip.mediaId}_${String(index).padStart(4, "0")}`;
    const match = all.find((it) => it.name === name);
    if (match) byKey.set(key, match);
  }
  return byKey;
}

async function placeClips(project, sequence, plan, sources, strategy) {
  const editor = ppro.SequenceEditor.getEditor(sequence);
  const tb = plan.timebase;

  project.executeTransaction((compound) => {
    for (const clip of plan.timeline) {
      const at = ppro.TickTime.createWithSeconds(toSeconds(tb, clip.atFrame));

      let source;
      if (strategy === STRATEGY.SUBCLIP) {
        source = sources.get(`${clip.mediaId}@${clip.inSeconds}-${clip.outSeconds}`);
      } else {
        source = sources.get(clip.mediaId);
        // Order matters: the in/out must be set before the overwrite that reads it.
        compound.addAction(
          source.createSetInOutPointsAction(
            ppro.TickTime.createWithSeconds(clip.inSeconds),
            ppro.TickTime.createWithSeconds(clip.outSeconds)
          )
        );
      }
      if (!source) continue;

      compound.addAction(
        editor.createOverwriteItemAction(source, at, clip.videoTrack, clip.audioTrack)
      );
    }
  }, UNDO.clips);
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
  /** @type {string[]} */
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
        ppro.TickTime.createWithSeconds(toSeconds(tb, g.atFrame)),
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
  /** @type {string[]} */
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
      const param = component.getParam(index);
      const keyframe = param.createKeyframe(value);
      // Direct commit: these are per-field and already outside the clip transaction.
      await param.createSetValueAction(keyframe, true);
    } catch (err) {
      warnings.push(`"${graphic.mogrt}".${field}: ${err.message}`);
    }
  }
  return warnings;
}

async function findMogrtComponent(chain) {
  const count = chain.getComponentCount ? await chain.getComponentCount() : 0;
  for (let i = 0; i < count; i++) {
    const c = await chain.getComponentAtIndex(i);
    const name = await c.getMatchName();
    if (name && name.indexOf("MGT") !== -1) return c;
  }
  return null;
}

// --------------------------------------------------------------- effects

async function applyEffects(project, sequence, plan) {
  /** @type {string[]} */
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

    project.executeTransaction((compound) => {
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
  const items = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
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

  project.executeTransaction((compound) => {
    for (const m of plan.markers) {
      compound.addAction(
        markers.createAddMarkerAction(
          m.name,
          typeFor[m.type || "Comment"] || ppro.Marker.MARKER_TYPE_COMMENT,
          ppro.TickTime.createWithSeconds(toSeconds(tb, m.atFrame)),
          ppro.TickTime.createWithSeconds(toSeconds(tb, m.durationFrames || 0)),
          m.comment || ""
        )
      );
    }
  }, UNDO.markers);
}

// --------------------------------------------------------------- transcript

/**
 * Push our transcript into Premiere so the assembled cut lines up with the
 * native Text-Based Editing panel, rather than the editor having Premiere
 * re-transcribe the same audio and get slightly different word boundaries.
 */
async function importTranscripts(project, plan, items) {
  /** @type {string[]} */
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
      project.executeTransaction((compound) => {
        compound.addAction(ppro.Transcript.createImportTextSegmentsAction(segments, clipItem));
      }, UNDO.transcript);
    } catch (err) {
      warnings.push(`could not import transcript for ${t.mediaId}: ${err.message}`);
    }
  }
  return warnings;
}

/** Group words into sentence-ish segments, which is the shape Premiere expects. */
function toPremiereTranscript(t) {
  const segments = [];
  let current = null;
  for (const w of t.words) {
    if (!current) current = { start: w.start, end: w.end, text: w.text, speaker: w.speaker || "" };
    else {
      current.text += ` ${w.text}`;
      current.end = w.end;
    }
    const endsSentence = /[.!?]$/.test(w.text);
    if (endsSentence || current.end - current.start > 12) {
      segments.push(current);
      current = null;
    }
  }
  if (current) segments.push(current);
  return { language: t.language || "en", segments };
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
async function verifyStrategy(projectItem) {
  const project = await ppro.Project.getActiveProject();
  const sequence = await project.createSequence("AutoEdit strategy probe");
  const editor = ppro.SequenceEditor.getEditor(sequence);
  const ranges = [[0, 1], [2, 4], [5, 8]];

  project.executeTransaction((compound) => {
    let at = 0;
    for (const [inS, outS] of ranges) {
      compound.addAction(
        projectItem.createSetInOutPointsAction(
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
  const placed = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
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

module.exports = { applyPlan, verifyStrategy, ApplyError, STRATEGY, UNDO };
