"use strict";
/**
 * Live diagnostic against the running Premiere.
 *
 * Exists because clicking through a panel to test it is slow and tells you
 * almost nothing when it fails. This runs the whole surface, reads the timeline
 * back, and writes a structured report to disk -- so a fix-retry cycle is
 * "reload, click once, read the JSON" rather than a squint at the UI.
 *
 * It is also what an editor runs when the tool misbehaves on their machine, so
 * it is not throwaway scaffolding.
 *
 * Report:  /tmp/autoedit-selftest/report.json   (plus the plugin data folder)
 * Config:  /tmp/autoedit-selftest/config.json   (optional: { mediaPath })
 */

const ppro = require("premierepro");
const fs = require("uxp").storage.localFileSystem;

const { applyPlan, verifyStrategy, indexProjectMedia, normalizePath, STRATEGY } = require("./apply");
const { toSeconds } = require("./timebase");

const IO_DIR = "/tmp/autoedit-selftest";

/**
 * Every API member the panel depends on. Checked before anything is exercised,
 * because "method does not exist" is a far clearer failure than whatever it
 * turns into six calls deep inside an apply.
 */
const STATIC_SURFACE = [
  ["Project", "getActiveProject"],
  ["TickTime", "createWithSeconds"],
  ["TickTime", "createWithFrameAndFrameRate"],
  ["SequenceEditor", "getEditor"],
  ["ClipProjectItem", "cast"],
  ["FolderItem", "cast"],
  ["Markers", "getMarkers"],
  ["VideoFilterFactory", "createComponent"],
  ["Transcript", "importFromJSON"],
  ["Transcript", "createImportTextSegmentsAction"],
  ["Transcript", "hasTranscript"],
];

const INSTANCE_SURFACE = {
  project: ["executeTransaction", "createSequence", "importFiles", "getRootItem", "getSequences"],
  sequence: ["getVideoTrack", "getAudioTrack", "getVideoTrackCount", "getEndTime"],
  editor: ["createOverwriteItemAction", "createInsertProjectItemAction", "insertMogrtFromPath"],
  clipProjectItem: ["createSetInOutPointsAction", "createSubClipAction", "getMediaFilePath"],
  trackItem: ["getStartTime", "getEndTime", "getDuration", "getComponentChain"],
};

class Report {
  constructor() {
    this.startedAt = new Date().toISOString();
    /** @type {{name:string, pass:boolean, error?:string, missing?:string[], [k:string]:any}[]} */
    this.checks = [];
    /** @type {string|null} */
    this.premiereVersion = null;
    /** @type {string|null} */
    this.note = null;
    /** @type {string|null} */
    this.strategy = null;
    /** @type {string|null} */
    this.writtenTo = null;
  }
  add(name, pass, detail) {
    this.checks.push({ name, pass: !!pass, ...detail });
    return pass;
  }
  fail(name, err) {
    return this.add(name, false, { error: String((err && err.message) || err) });
  }
  get summary() {
    const real = this.checks.filter((c) => !c.characterisation);
    const passed = real.filter((c) => c.pass).length;
    return {
      passed,
      failed: real.length - passed,
      total: real.length,
      characterisation: this.checks.length - real.length,
    };
  }
}

async function writeJson(path, data) {
  const text = JSON.stringify(data, null, 2);
  try {
    const folder = await fs.getEntryWithUrl(`file://${IO_DIR}`);
    const file = await folder.createFile(path, { overwrite: true });
    await file.write(text);
    return `${IO_DIR}/${path}`;
  } catch {
    // Fall back to the plugin's own data folder, which never needs permission.
    const data_ = await fs.getDataFolder();
    const file = await data_.createFile(path, { overwrite: true });
    await file.write(text);
    return `${data_.nativePath}/${path}`;
  }
}

async function readConfig() {
  try {
    const file = await fs.getEntryWithUrl(`file://${IO_DIR}/config.json`);
    return JSON.parse(await file.read());
  } catch {
    return {};
  }
}

/** @param {(msg: string) => void} [onProgress] */
async function runSelfTest(onProgress) {
  const say = onProgress || (() => {});
  const report = new Report();
  const config = await readConfig();
  /** @type {any[]} */
  const scratchSequences = [];

  // --- environment ---------------------------------------------------------
  let project = null;
  try {
    report.premiereVersion = ppro.Application ? ppro.Application.version : "unknown";
    project = await ppro.Project.getActiveProject();
    report.add("environment/project-open", !!project, {
      premiereVersion: report.premiereVersion,
      projectName: project ? project.name : null,
    });
  } catch (err) {
    report.fail("environment/project-open", err);
  }
  if (!project) {
    report.note = "No project open. Open or create one in Premiere, then re-run.";
    await writeJson("report.json", finalize(report));
    return report;
  }

  // --- static API surface --------------------------------------------------
  say("probing API surface");
  const missingStatics = [];
  for (const [obj, method] of STATIC_SURFACE) {
    const holder = ppro[obj];
    if (!holder || typeof holder[method] !== "function") missingStatics.push(`${obj}.${method}`);
  }
  report.add("api/statics", missingStatics.length === 0, {
    checked: STATIC_SURFACE.length,
    missing: missingStatics,
  });

  // --- media ---------------------------------------------------------------
  say("locating test media");
  let mediaPath = null;
  try {
    mediaPath = await findOrImportMedia(project, config.mediaPath);
    report.add("media/available", !!mediaPath, {
      requested: config.mediaPath || "(first clip in project)",
      resolved: mediaPath,
      matchedRequest: !config.mediaPath || normalizePath(mediaPath) === normalizePath(config.mediaPath),
    });
  } catch (err) {
    report.fail("media/available", err);
  }
  if (!mediaPath) {
    report.note =
      "No usable media. Put a clip in the project, or write " +
      `${IO_DIR}/config.json with {"mediaPath": "/abs/path.mov"}.`;
    await writeJson("report.json", finalize(report));
    return report;
  }

  // --- instance API surface ------------------------------------------------
  try {
    const probeSeq = await project.createSequence("AutoEdit probe surface");
    scratchSequences.push(probeSeq);
    const editor = ppro.SequenceEditor.getEditor(probeSeq);
    // Re-fetch after createSequence: references held across it are invalidated.
    const freshIndex = await indexProjectMedia(project);
    const freshItem = freshIndex.get(normalizePath(mediaPath));
    const missing = [];
    check(missing, "project", project, INSTANCE_SURFACE.project);
    check(missing, "sequence", probeSeq, INSTANCE_SURFACE.sequence);
    check(missing, "editor", editor, INSTANCE_SURFACE.editor);
    check(missing, "clipProjectItem", ppro.ClipProjectItem.cast(freshItem), INSTANCE_SURFACE.clipProjectItem);
    report.add("api/instances", missing.length === 0, { missing });
  } catch (err) {
    report.fail("api/instances", err);
  }

  // --- narrow down object lifetimes ---------------------------------------
  say("probing object lifetimes");
  try {
    await lifetimeProbe(project, mediaPath, report);
  } catch (err) {
    report.fail("lifetime/probe", err);
  }

  say("probing transcript schema");
  await probeTranscriptSchema(project, report);

  say("probing whether a local image renders");
  await probeImageRendering(report);

  // --- the question we came for -------------------------------------------
  say("determining clip placement strategy");
  let strategy = STRATEGY.IN_OUT;
  try {
    const result = await verifyStrategy(mediaPath);
    strategy = result.strategy;
    // Measured, not asserted: either answer is a valid outcome as long as the
    // right strategy is then used. What would be a defect is guessing.
    report.add("strategy/measured", true, {
      expected: result.expected,
      actual: result.actual,
      chosen: result.strategy,
      inOutHonoured: result.strategy === STRATEGY.IN_OUT,
      note: result.note,
      characterisation: true,
    });
  } catch (err) {
    report.fail("strategy/in-out-honoured", err);
  }

  // --- assembly round trip -------------------------------------------------
  say("running an assembly and reading it back");
  try {
    const roundTrip = await assemblyRoundTrip(project, mediaPath, strategy);
    scratchSequences.push(roundTrip.sequence);
    report.add("assembly/round-trip", roundTrip.pass, roundTrip.detail);
  } catch (err) {
    report.fail("assembly/round-trip", err);
  }

  report.strategy = strategy;
  const path = await writeJson("report.json", finalize(report));
  report.writtenTo = path;
  say(`report written to ${path}`);
  return report;
}

/**
 * Narrow down exactly where a ProjectItem reference dies.
 *
 * "The script object is no longer valid" says nothing about which object or
 * which call killed it, so this walks progressively more demanding scenarios
 * and reports the first that breaks. Cheap to run, and it turns a guessing game
 * into a single answer.
 */
async function lifetimeProbe(project, mediaPath, report) {
  const freshItem = async () => {
    const index = await indexProjectMedia(project);
    const item = index.get(normalizePath(mediaPath));
    if (!item) throw new Error("probe media not found in project");
    return item;
  };
  // Some of these steps are EXPECTED to fail -- they document which patterns
  // Premiere refuses. Reporting them as plain failures would make a healthy run
  // look broken, so they carry `characterisation: true` and are excluded from
  // the pass/fail tally.
  const step = async (name, fn, expectFailure) => {
    try {
      const detail = await fn();
      report.add(`lifetime/${name}`, true, {
        ...(detail || {}),
        characterisation: !!expectFailure,
        ...(expectFailure ? { surprise: "expected this to fail on 26.3.x but it succeeded" } : {}),
      });
      return true;
    } catch (err) {
      report.add(`lifetime/${name}`, !!expectFailure, {
        error: String((err && err.message) || err),
        characterisation: !!expectFailure,
        ...(expectFailure ? { note: "expected: this pattern is unsupported, hence transact()" } : {}),
      });
      return false;
    }
  };

  await step("immediate-use", async () => {
    const item = await freshItem();
    return { path: await ppro.ClipProjectItem.cast(item).getMediaFilePath() };
  });

  await step("after-unrelated-await", async () => {
    const item = await freshItem();
    await project.getSequences();
    return { path: await ppro.ClipProjectItem.cast(item).getMediaFilePath() };
  });

  await step("after-create-sequence", async () => {
    const item = await freshItem();
    await project.createSequence(`AutoEdit probe lifetime ${report.checks.length}`);
    return { path: await ppro.ClipProjectItem.cast(item).getMediaFilePath() };
  });

  await step("cast-then-build-action", async () => {
    const item = await freshItem();
    const clip = ppro.ClipProjectItem.cast(item);
    const action = clip.createSetInOutPointsAction(
      ppro.TickTime.createWithSeconds(0),
      ppro.TickTime.createWithSeconds(1)
    );
    return { builtAction: !!action };
  }, true);

  const seq = await project.createSequence(`AutoEdit probe txn ${report.checks.length}`);
  const editor = ppro.SequenceEditor.getEditor(seq);

  await step("txn-overwrite-only", async () => {
    const item = await freshItem();
    const ok = project.executeTransaction((compound) => {
      compound.addAction(editor.createOverwriteItemAction(item, ppro.TickTime.TIME_ZERO, 0, 0));
    }, "AutoEdit: probe overwrite");
    return { committed: ok };
  }, true);

  await step("txn-inout-then-overwrite", async () => {
    const item = await freshItem();
    const clip = ppro.ClipProjectItem.cast(item);
    const ok = project.executeTransaction((compound) => {
      compound.addAction(
        clip.createSetInOutPointsAction(
          ppro.TickTime.createWithSeconds(2),
          ppro.TickTime.createWithSeconds(4)
        )
      );
      compound.addAction(
        editor.createOverwriteItemAction(item, ppro.TickTime.createWithSeconds(5), 0, 0)
      );
    }, "AutoEdit: probe in/out + overwrite");
    return { committed: ok };
  }, true);

  await step("locked-access-txn", async () => {
    const item = await freshItem();
    const clip = ppro.ClipProjectItem.cast(item);
    let ok = false;
    project.lockedAccess(() => {
      ok = project.executeTransaction((compound) => {
        compound.addAction(
          clip.createSetInOutPointsAction(
            ppro.TickTime.createWithSeconds(1),
            ppro.TickTime.createWithSeconds(2)
          )
        );
        compound.addAction(
          editor.createOverwriteItemAction(item, ppro.TickTime.createWithSeconds(10), 0, 0)
        );
      }, "AutoEdit: probe locked");
    });
    return { committed: ok };
  });
}

/**
 * Learn Premiere's transcript JSON schema from a clip that already has one.
 *
 * `Transcript.importFromJSON` rejects our shape with "Failed to parse input
 * string into JSON", and Adobe does not document the format. Rather than guess,
 * this dumps the schema of any existing transcript into the report: run the
 * self-test once on a machine where a clip has been transcribed in Premiere and
 * the answer is in the output.
 */
async function probeTranscriptSchema(project, report) {
  try {
    const index = await indexProjectMedia(project);
    for (const [, item] of index) {
      const clip = ppro.ClipProjectItem.cast(item);
      if (!ppro.Transcript.hasTranscript(clip)) continue;
      const json = await ppro.Transcript.exportToJSON(clip);
      const parsed = JSON.parse(json);
      report.add("transcript/schema-discovered", true, {
        topLevelKeys: Object.keys(parsed),
        sample: JSON.stringify(parsed).slice(0, 1200),
        characterisation: true,
      });
      return;
    }
    report.add("transcript/schema-discovered", true, {
      note: "no clip in this project has a Premiere transcript yet. Transcribe one "
          + "(Window > Text > Transcribe) and re-run to capture the expected schema.",
      characterisation: true,
    });
  } catch (err) {
    report.add("transcript/schema-discovered", true, {
      error: String((err && err.message) || err),
      characterisation: true,
    });
  }
}

/**
 * Can this build show a picture the panel read off disk?
 *
 * Nothing in the panel has ever displayed an image. There is no <img> anywhere,
 * and the bundled uxp typings declare no binary read and no object URL, so the
 * honest answer before running this is "nobody knows". A thumbnail grid of
 * candidate shots depends entirely on the answer, and building ffmpeg frame
 * extraction first and finding out afterwards would be minutes of CPU per job
 * spent on files nothing can display.
 *
 * Three separate things have to work and they fail differently, so each is
 * reported on its own:
 *
 *   1. reading a file as binary at all      (storage.formats.binary)
 *   2. turning those bytes into a data URI  (base64 of an ArrayBuffer)
 *   3. an <img> actually decoding it        (naturalWidth > 0 after load)
 *
 * Step 3 is the one that cannot be reasoned about. An <img> whose src is
 * rejected does not throw -- it just stays 0x0 forever -- so this waits for
 * load/error with a timeout rather than assuming silence means success.
 *
 * Writes a 1x1 PNG itself rather than hunting for a JPEG on disk: the question
 * is whether the pipeline works, and a file that is certainly there and
 * certainly valid keeps a missing fixture from being read as a UXP limitation.
 */
async function probeImageRendering(report) {
  // A 1x1 red PNG. Bytes rather than a base64 string so step 1 is a real read
  // of a real file, not a round trip through a constant.
  const PNG_1X1 = [
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
    0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xde, 0x00, 0x00, 0x00,
    0x0c, 0x49, 0x44, 0x41, 0x54, 0x08, 0xd7, 0x63, 0xf8, 0xcf, 0xc0, 0x00,
    0x00, 0x03, 0x01, 0x01, 0x00, 0x18, 0xdd, 0x8d, 0xb0, 0x00, 0x00, 0x00,
    0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
  ];

  const formats = require("uxp").storage.formats;
  let file = null;
  try {
    const folder = await fs.getDataFolder();
    file = await folder.createFile("probe.png", { overwrite: true });
    await file.write(new Uint8Array(PNG_1X1).buffer, { format: formats.binary });
    report.add("image/write-binary", true, { characterisation: true });
  } catch (err) {
    report.add("image/write-binary", false, {
      error: String((err && err.message) || err),
      note: "cannot write binary at all; thumbnails are impossible on this build",
      characterisation: true,
    });
    return;
  }

  let bytes = null;
  try {
    bytes = await file.read({ format: formats.binary });
    report.add("image/read-binary", !!bytes, {
      byteLength: bytes && (bytes.byteLength || bytes.length),
      characterisation: true,
    });
  } catch (err) {
    report.add("image/read-binary", false, {
      error: String((err && err.message) || err), characterisation: true,
    });
    return;
  }

  let dataUri = null;
  try {
    const view = new Uint8Array(bytes);
    let binary = "";
    for (let i = 0; i < view.length; i += 1) binary += String.fromCharCode(view[i]);
    // btoa is not guaranteed present in UXP; Buffer is, via the Node-ish shim.
    const b64 = typeof btoa === "function"
      ? btoa(binary)
      : Buffer.from(view).toString("base64");
    dataUri = `data:image/png;base64,${b64}`;
    report.add("image/data-uri", true, {
      length: dataUri.length,
      method: typeof btoa === "function" ? "btoa" : "Buffer",
      characterisation: true,
    });
  } catch (err) {
    report.add("image/data-uri", false, {
      error: String((err && err.message) || err), characterisation: true,
    });
    return;
  }

  // The real question. Both src forms are tried because they can fail
  // independently: a data URI may be blocked by CSP while file:// is allowed,
  // or the reverse.
  for (const [label, src] of [
    ["data-uri", dataUri],
    ["file-url", `file://${file.nativePath}`],
  ]) {
    try {
      const width = await renderProbe(src);
      report.add(`image/renders-${label}`, width > 0, {
        naturalWidth: width,
        note: width > 0
          ? "an <img> decoded it; a thumbnail grid is possible"
          : "the <img> never reported a size, so this src form does not display",
        characterisation: true,
      });
    } catch (err) {
      report.add(`image/renders-${label}`, false, {
        error: String((err && err.message) || err), characterisation: true,
      });
    }
  }
}

/**
 * Put an <img> in the document and wait for it to decide.
 *
 * Resolves with naturalWidth (0 means it never decoded). A rejected src is
 * silent in UXP -- no throw, no error event guaranteed -- so the timeout is the
 * actual answer in the failure case, not a safety net.
 * @param {string} src
 */
function renderProbe(src) {
  return new Promise((resolve) => {
    let img;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      const w = (img && img.naturalWidth) || 0;
      if (img && img.parentNode) img.parentNode.removeChild(img);
      resolve(w);
    };
    try {
      img = document.createElement("img");
      // Off-screen but still laid out; display:none can stop a decode.
      img.style.cssText = "position:absolute;left:-9999px;top:0;width:1px;height:1px";
      img.addEventListener("load", finish);
      img.addEventListener("error", finish);
      document.body.appendChild(img);
      img.src = src;
    } catch {
      finish();
      return;
    }
    setTimeout(finish, 3000);
  });
}

function check(missing, label, obj, methods) {
  for (const m of methods) {
    if (!obj || typeof obj[m] !== "function") missing.push(`${label}.${m}`);
  }
}

async function safe(fn) {
  try {
    return await fn();
  } catch {
    return null;
  }
}

function finalize(report) {
  return {
    startedAt: report.startedAt,
    finishedAt: new Date().toISOString(),
    premiereVersion: report.premiereVersion,
    strategy: report.strategy || null,
    summary: report.summary,
    note: report.note || null,
    checks: report.checks,
  };
}

/**
 * Ensure test media exists in the project and return its absolute path.
 *
 * Returns a path rather than an item because everything downstream re-fetches
 * live references after its own mutations. When a specific file is configured
 * it must be that file -- an earlier version returned whatever clip happened to
 * be first in the bin, so the test silently exercised the wrong media.
 */
async function findOrImportMedia(project, mediaPath) {
  if (mediaPath) {
    const root = await project.getRootItem();
    const index = await indexProjectMedia(project);
    if (!index.has(normalizePath(mediaPath))) {
      const ok = await project.importFiles([mediaPath], true, root, false);
      if (!ok) throw new Error(`Premiere refused to import ${mediaPath}`);
    }
    const after = await indexProjectMedia(project);
    if (!after.has(normalizePath(mediaPath))) {
      throw new Error(`imported ${mediaPath} but it is not in the project`);
    }
    return mediaPath;
  }

  // No configured media: fall back to any clip already in the project.
  const index = await indexProjectMedia(project);
  for (const path of index.keys()) return path;
  return null;
}

/**
 * Apply a known plan, then read the timeline back and compare. This is the real
 * assertion: not "did it throw", but "did the clips land where the plan said".
 */
async function assemblyRoundTrip(project, mediaPath, strategy) {
  const timebase = { fpsNum: 25, fpsDen: 1, dropFrame: false };
  const name = mediaPath.split("/").pop();

  const plan = {
    schemaVersion: "1.0",
    jobId: "selftest",
    recipe: "selftest",
    timebase,
    media: [{ id: "SRC", relPath: name, durationSeconds: 7.0 }],
    sequence: { name: `AutoEdit selftest ${Date.now()}`, videoTracks: 1, audioTracks: 1 },
    timeline: [
      { mediaId: "SRC", inSeconds: 0.0, outSeconds: 1.0, atFrame: 0,  durationFrames: 25, videoTrack: 0, audioTrack: 0, fadeInFrames: 0, fadeOutFrames: 0 },
      { mediaId: "SRC", inSeconds: 2.0, outSeconds: 4.0, atFrame: 25, durationFrames: 50, videoTrack: 0, audioTrack: 0, fadeInFrames: 0, fadeOutFrames: 0 },
      { mediaId: "SRC", inSeconds: 5.0, outSeconds: 6.0, atFrame: 75, durationFrames: 25, videoTrack: 0, audioTrack: 0, fadeInFrames: 0, fadeOutFrames: 0 },
    ],
  };

  await applyPlan(plan, {
    strategy,
    applyGraphics: false,
    applyEffects: false,
    resolveAbsolutePath: async () => mediaPath,
  });

  const sequences = await project.getSequences();
  const sequence = sequences.find((s) => s.name === plan.sequence.name);
  if (!sequence) return { pass: false, detail: { error: "sequence was not created" }, sequence: null };

  const track = await sequence.getVideoTrack(0);
  const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false) || [];

  const actual = [];
  for (const item of items) {
    const start = await item.getStartTime();
    const end = await item.getEndTime();
    actual.push({
      atFrame: Math.round((start.seconds * timebase.fpsNum) / timebase.fpsDen),
      durationFrames: Math.round(((end.seconds - start.seconds) * timebase.fpsNum) / timebase.fpsDen),
    });
  }

  const expected = plan.timeline.map((c) => ({ atFrame: c.atFrame, durationFrames: c.durationFrames }));
  const pass =
    actual.length === expected.length &&
    expected.every((e, i) => actual[i] && actual[i].atFrame === e.atFrame && actual[i].durationFrames === e.durationFrames);

  return {
    pass,
    sequence,
    detail: {
      expected,
      actual,
      strategy,
      note: pass
        ? "clips landed exactly where the plan asked"
        : "timeline does not match the plan -- durations differing while positions match usually means source in/out was not applied per clip",
    },
  };
}

module.exports = { runSelfTest, IO_DIR };
