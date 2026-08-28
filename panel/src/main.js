"use strict";
/**
 * Panel UI.
 *
 * The interaction model is deliberate: the panel proposes, the editor disposes.
 * Nothing is applied until the editor has seen the clip count, the warnings and
 * the reasons, and has had a chance to switch sections off.
 */

const { applyPlan, ApplyError } = require("./apply");
const {
  validatePlan, summarize, sections, withoutSections,
  withSwaps, candidatesFor, groupKeyOf, colourFor, thumbForClip,
  slotKey, isPictureSlot, toSeconds, thumbForSource, sectionLabel,
} = require("./plan");
const {
  LocalFolderTransport, pickFolder, folderFromToken, listMediaFiles,
  makeResolver, readImageDataUri, lastImageError,
  loadSettings, saveSettings,
} = require("./transport");
const {
  isVideoFile, buildRequest, validateRequest, requestFileName, describeRequest,
  musicChoices, parseMusicValue, parseTimecode, formatTimecode, parseSeconds,
  normaliseJobId, jobIdWasChanged,
} = require("./request");
const { audition, playheadSeconds, AuditionError } = require("./audition");
const { readPromptSettings } = require("./prompt");
const { runSelfTest } = require("./selftest");
const { LANGUAGES, makeTranslator } = require("./i18n");

/** @type {(id: string) => any} document.getElementById is typed HTMLElement; the
 * panel needs the concrete input/select members. */
const $ = (id) => /** @type {any} */ (document.getElementById(id));

/** Narrower than this and a frame on a strip block is a sliver, not a picture. */
const MIN_FRAME_BLOCK = 22;

const state = {
  settings: loadSettings(),
  transport: null,
  plans: [],
  plan: null,
  planName: null,
  disabled: new Set(),
  capabilities: null,
  mediaFiles: [],
  mediaIndex: new Map(),
  musicFiles: [],
  libraryFiles: [],
  // Set once the editor types a chunk length, so it stops being refilled from
  // the edit length behind their back. A plain flag rather than dataset, which
  // UXP's DOM does not reliably provide.
  musicLengthTouched: false,
  // The editor's interface language. Swapped live rather than on reload, because
  // "restart Premiere to read the label you cannot read" is not an instruction.
  t: makeTranslator("en"),
  selectedMedia: new Set(),
  // Shot swaps the editor has made, keyed by the slot's atFrame. Panel memory
  // only, deliberately: nothing is written back to the jobs folder, so closing
  // the panel discards them. The editor is told that, and warned before the one
  // accidental discard they will actually hit -- opening a different plan.
  swaps: new Map(),
  selectedSlot: null,      // atFrame of the block whose alternates are showing
  // Every shot the helper has found across the media library, not just the
  // clips in this job. Null until it has written the index once.
  format: null,          // the deliverable card the editor picked
  // What the description last set, so a later keystroke only pushes what
  // actually changed and a hand-made adjustment is not overwritten.
  promptApplied: { seconds: null, aspect: null, cutRate: null, visual: null,
                   subtitles: null, recipe: null },
  lastReceipt: null,     // the previous build of this plan, if there was one
  libraryShots: null,
  thumbFailureLogged: false,
  watching: null,          // interval id while a job is being worked on
};

function log(message, kind) {
  const el = document.createElement("div");
  if (kind) el.className = kind;
  el.textContent = message;
  $("log").appendChild(el);
  $("log").scrollTop = $("log").scrollHeight;
}

async function showFolder(token, el, fallback) {
  const folder = token ? await folderFromToken(token) : null;
  el.textContent = folder ? folder.nativePath || folder.name : fallback;
  el.classList.toggle("unset", !folder);
  return folder;
}

/**
 * Setup is finished work; it should not head the panel forever.
 *
 * Once media and jobs are both chosen there is nothing here to do again, so it
 * folds to a single line with a way back. Music stays optional and is not part
 * of the test -- waiting for it would keep the block open on every machine that
 * never uses music.
 *
 * `setupOpen` is deliberately not persisted: reopening it is one click, and a
 * remembered-open state is indistinguishable from the bug it replaces.
 * @param {any} media @param {any} jobs
 */
let setupOpen = false;
function showSetup(media, jobs) {
  const done = Boolean(media && jobs) && !setupOpen;
  $("setup-fields").classList.toggle("hidden", done);
  $("setup-done").classList.toggle("hidden", !done);
  if (!done) return;
  const name = (f) => (f ? f.name || f.nativePath : "");
  $("setup-summary").textContent = `${name(media)} → ${name(jobs)}`;
}

/**
 * Push the current language into the markup.
 *
 * Elements carry `data-i18n` (and `data-i18n-placeholder`) and keep their
 * English as the literal text, so the panel is readable even if this never runs.
 */
function applyTranslations() {
  const t = state.t;
  for (const el of Array.from(document.querySelectorAll("[data-i18n]"))) {
    const key = el.getAttribute("data-i18n");
    if (key) el.textContent = t(key, {}, el.textContent);
  }
  for (const el of Array.from(document.querySelectorAll("[data-i18n-placeholder]"))) {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) el.setAttribute("placeholder", t(key, {}, el.getAttribute("placeholder") || ""));
  }
  // Selects are filled from data, so they have to be rebuilt in the new language.
  fillSelect("opt-ui-language", LANGUAGES, LANGUAGES);
  $("opt-ui-language").value = t.lang;
  fillSelect("opt-language", spokenLanguages(), spokenLanguages());
  if (state.capabilities) applyCapabilityLabels(state.capabilities);
  fillMusicSelect();
  // The shot list is built in JavaScript, so its labels are whatever language
  // was current when it was drawn -- they are not markup and this function
  // cannot reach them. Left alone, switching language gave a heading in the new
  // one and buttons still in the old, which reads as the panel translating at
  // random. Redraw it.
  // Every view built in JavaScript, not just the strip: the summary and the
  // section list read their labels through state.t at draw time too.
  if (state.plan) {
    renderSummary();
    renderSections();
    renderStrip();
  }
  renderSummary_();
  // The folder rows hold values, not labels, so the generic walk skips them --
  // but "not set" is still a phrase and still has to follow the language.
  refreshSetup().catch(() => { /* first run, before any folder is chosen */ });
}

/** Spoken-language choices. Detection first: it is right almost every time. */
function spokenLanguages() {
  const t = state.t;
  return [
    { value: "auto", label: t("lang.auto") },
    { value: "ja", label: t("lang.ja") },
    { value: "en", label: t("lang.en") },
  ];
}

/** Relabel the helper's English enums in the editor's language. */
function applyCapabilityLabels(caps) {
  const t = state.t;
  const relabel = (list, prefix) =>
    (list || []).map((x) => ({ value: x.value, label: t(`${prefix}.${x.value}`, {}, x.label) }));
  fillSelect("opt-aspect", relabel(caps.aspects, "aspect"), [{ value: "source", label: t("aspect.source") }]);
  fillSelect("opt-pacing", relabel(caps.pacing, "pacing"), [{ value: "standard", label: t("pacing.standard") }]);
  fillSelect(
    "opt-cut-rate",
    [{ value: "", label: t("cutRate.auto") }, ...relabel(caps.cutRates, "cutRate")],
    [{ value: "", label: t("cutRate.auto") }],
  );
  fillSelect("opt-duration-mode", relabel(caps.durationModes, "duration"), [{ value: "none", label: t("duration.none") }]);
  fillSelect("opt-look", [{ value: "", label: t("look.none") }, ...(caps.looks || [])], [{ value: "", label: t("look.none") }]);
  // Filling the dropdown can change which mode is selected, so the Seconds box
  // has to be re-checked here and not only on an editor's own change.
  syncDurationField();
  // A description typed before the capabilities landed still gets read.
  applyPromptSettings();
}

async function refreshSetup() {
  const media = await showFolder(state.settings.mediaToken, $("media-path"), state.t("setup.notSet"));
  const jobs = await showFolder(state.settings.jobsToken, $("jobs-path"), state.t("setup.notSet"));
  await showFolder(state.settings.musicToken, $("music-path"), state.t("setup.notSetOptional"));
  showSetup(media, jobs);
  state.transport = state.settings.jobsToken
    ? new LocalFolderTransport(state.settings.jobsToken)
    : null;
}

async function refreshPlans() {
  const list = $("plan-list");
  list.innerHTML = "";
  if (!state.transport) {
    list.innerHTML = `<option>${state.t("plan.none")}</option>`;
    return;
  }
  try {
    state.plans = await state.transport.listPlans();
  } catch (err) {
    log(err.message, "err");
    return;
  }
  if (!state.plans.length) {
    list.innerHTML = "<option>No plans found</option>";
    clearPlan();
    return;
  }
  for (const p of state.plans) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    list.appendChild(opt);
  }
  await selectPlan(state.plans[0]);
}

function clearPlan() {
  state.plan = null;
  state.planName = null;
  state.disabled.clear();
  state.swaps.clear();
  state.selectedSlot = null;
  $("plan-summary").classList.add("hidden");
  $("sections-block").classList.add("hidden");
  $("swap-block").classList.add("hidden");
  $("detail-block").classList.add("hidden");
  state.lastReceipt = null;
  $("plan-messages").innerHTML = "";
  $("apply").disabled = true;
}

function message(text, bad) {
  const el = document.createElement("div");
  el.className = bad ? "msg bad" : "msg";
  el.textContent = text;
  $("plan-messages").appendChild(el);
}

async function selectPlan(ref) {
  // Swaps live in memory only, so switching plans destroys them. Ask first --
  // this is the one way an editor loses ten minutes of work without meaning to.
  if (state.swaps.size && ref.name !== state.planName) {
    // window.confirm is not a thing UXP guarantees, and nothing else in this
    // panel has ever called it. Where it exists, ask. Where it does not, the
    // switch still happens -- refusing to change plans because a dialog is
    // unavailable would be a worse bug than the one being guarded against --
    // but it is said loudly rather than silently.
    const ask = typeof window !== "undefined" && typeof window.confirm === "function"
      ? window.confirm
      : null;
    if (ask) {
      if (!ask.call(window, state.t("swap.discard", { count: state.swaps.size }))) {
        $("plan-list").value = state.planName || "";
        return;
      }
    } else {
      log(state.t("swap.discard", { count: state.swaps.size }), "err");
    }
  }
  clearPlan();
  let plan;
  try {
    plan = await state.transport.readPlan(ref.entry);
  } catch (err) {
    message(err.message, true);
    return;
  }

  const problems = validatePlan(plan);
  if (problems.length) {
    problems.forEach((p) => message(p, true));
    log(`${ref.name} rejected: ${problems.length} problem(s)`, "err");
    return;
  }

  state.plan = plan;
  state.planName = ref.name;
  renderSummary();
  renderSections();
  try {
    state.libraryShots = state.transport ? await state.transport.listLibraryShots() : null;
  } catch {
    state.libraryShots = null;   // the strip still works from the plan alone
  }
  try {
    state.lastReceipt = state.transport
      ? await state.transport.readReceipt(ref.name) : null;
  } catch {
    state.lastReceipt = null;
  }
  renderStrip();
  $("detail-block").classList.remove("hidden");
  if (!$("console-block").classList.contains("hidden")) renderConsole();
  $("apply").disabled = false;
  log(`Loaded ${ref.name}`);
}

function renderSummary() {
  const s = summarize(state.plan);
  const box = $("plan-summary");
  box.classList.remove("hidden");
  box.innerHTML = "";
  const t = state.t;
  const rows = [
    [t("plan.sequence"), state.plan.sequence.name],
    [t("plan.recipe"), state.plan.recipe],
    [t("plan.clips"), String(s.clipCount)],
    [t("plan.duration"), s.timecode],
    [t("plan.sources"), String(s.mediaCount)],
  ];
  if (s.graphicsCount) rows.push(["Graphics", String(s.graphicsCount)]);
  if (s.markerCount) rows.push(["Markers", String(s.markerCount)]);
  rows.push([t("plan.confidence"), `${Math.round(s.meanConfidence * 100)}%`]);

  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "stat";
    row.innerHTML = `<span>${label}</span><span>${value}</span>`;
    box.appendChild(row);
  }

  $("plan-messages").innerHTML = "";
  if (s.lowConfidence) {
    message(state.t("warn.plan.lowConfidence", { count: s.lowConfidence }));
  }
  // Rendered from the warning's key and params when we have a translation, and
  // from the English the engine already wrote when we do not.
  for (const w of s.warnings) message(state.t.warning(w));
}

function renderSections() {
  const list = sections(state.plan);
  const block = $("sections-block");
  const box = $("sections");
  box.innerHTML = "";
  if (list.length <= 1) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");
  for (const sect of list) {
    const row = document.createElement("div");
    row.className = "sect";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener("change", () => {
      if (cb.checked) state.disabled.delete(sect.id);
      else state.disabled.add(sect.id);
      // The strip dims what is switched off, so it has to hear about this.
      renderStrip();
    });
    const label = document.createElement("div");
    label.className = "grow";
    label.textContent = sect.label;
    const meta = document.createElement("div");
    meta.className = "meta";
    // Was hardcoded English, which left "3 clips" sitting inside an otherwise
    // Japanese panel -- the same defect as the buttons, pointing the other way.
    const bits = [state.t("sections.clips", { count: sect.clips })];
    if (sect.graphics) bits.push(state.t("sections.graphics", { count: sect.graphics }));
    meta.textContent = bits.join(" · ");
    row.append(cb, label, meta);
    box.appendChild(row);
  }
}

// ------------------------------------------------------------------ console

/**
 * Every value the job will use, and every check the build ran.
 *
 * Behind a toggle rather than always open, because a colleague opening this on
 * day one should meet a tool and not a diagnostic readout. But it exists at all
 * because the panel has spent this month failing quietly: a length that was
 * never set looked exactly like a length that was, and a clip reading from the
 * wrong footage passed a verifier that only checked position and duration.
 * Stated values cannot fail quietly.
 */
function renderConsole() {
  const body = $("console-body");
  body.innerHTML = "";
  if (!state.plan) return;
  const p = state.plan;

  const group = (titleKey) => {
    const h = document.createElement("div");
    h.className = "sec-label";
    h.style.marginTop = "10px";
    h.textContent = state.t(titleKey);
    body.appendChild(h);
  };
  const row = (label, value, tone) => {
    const r = document.createElement("div");
    r.className = "kv";
    const k = document.createElement("b");
    k.textContent = label;
    const v = document.createElement("span");
    v.className = "num";
    v.textContent = value;
    if (tone) v.style.color = `var(--${tone})`;
    r.append(k, v);
    body.appendChild(r);
  };

  const tb = p.timebase;
  const frames = (p.timeline || []).reduce(
    (m, c) => Math.max(m, c.atFrame + c.durationFrames), 0);

  group("console.settings");
  row(state.t("edit.recipe"), p.recipe);
  row(state.t("plan.sequence"), p.sequence.name);
  row(state.t("console.timebase"), `${(tb.fpsNum / tb.fpsDen).toFixed(3)} fps`);
  row(state.t("console.length"), `${toSeconds(tb, frames).toFixed(2)}s`);
  row(state.t("console.clips"), String((p.timeline || []).length));
  if (p.sequence.frameWidth) {
    row(state.t("edit.shape"), `${p.sequence.frameWidth}\u00d7${p.sequence.frameHeight}`);
  }

  // Warnings are the engine's own account of what it had to compromise on.
  group("console.checks");
  const warnings = p.warnings || [];
  if (!warnings.length) {
    row(state.t("console.warnings"), state.t("console.none"), "good");
  } else {
    for (const w of warnings) {
      const line = document.createElement("div");
      line.className = "logline";
      line.textContent = state.t.warning(w);
      body.appendChild(line);
    }
  }

  if (state.lastReceipt) {
    group("console.lastBuild");
    const r = state.lastReceipt;
    row(state.t("console.builtAt"), (r.appliedAt || "").replace("T", " ").slice(0, 19));
    row(state.t("console.stages"), (r.stages || []).join(", "));
    const bad = (r.warnings || []).filter((w) => /wrong|gap|does not match/i.test(String(w)));
    row(state.t("console.problems"),
      bad.length ? String(bad.length) : state.t("console.none"),
      bad.length ? "bad" : "good");
    for (const w of bad) {
      const line = document.createElement("div");
      line.className = "logline";
      line.style.color = "var(--bad)";
      line.textContent = String(w);
      body.appendChild(line);
    }
  }
}

// --------------------------------------------------------------- shot review

/** The slot as it stands now, with any swap already applied. */
function slotAt(key) {
  const live = withSwaps(state.plan, state.swaps);
  return (live.timeline || []).find((c) => slotKey(c) === key) || null;
}

function mediaName(mediaId) {
  const m = (state.plan.media || []).find((x) => x.id === mediaId);
  if (!m) return mediaId;
  const rel = m.relPath || "";
  return rel.split("/").pop() || mediaId;
}

/**
 * The proportional strip: one block per clip, width by duration, colour by group.
 *
 * Blocks are `flex-grow` on duration rather than a fixed pixel width, so the
 * whole edit always fills the panel however wide it is docked. A minimum width
 * stops a half-second clip in a two-minute edit becoming unclickable -- at which
 * point proportion is a slight lie, but an invisible block is a worse one.
 */
function renderStrip() {
  const block = $("swap-block");
  const strip = $("strip");
  strip.innerHTML = "";
  const clips = (state.plan && state.plan.timeline) || [];
  if (!clips.length) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");

  const live = withSwaps(state.plan, state.swaps);
  // How wide each block will be, before any are built: the strip's own width
  // divided by the number of pictures going into it.
  const pictureCount = live.timeline.filter(isPictureSlot).length || 1;
  const stripWidth = strip.getBoundingClientRect
    ? strip.getBoundingClientRect().width || 380 : 380;
  const wideEnoughForFrames = stripWidth / pictureCount >= MIN_FRAME_BLOCK;

  // Pictures only. The music bed is a timeline entry too, and it is not a shot:
  // it has no frames to choose between, and it was the slot that wiped the
  // sequence when a swap keyed on frame 0 reached it as well as the picture.
  for (const c of live.timeline.filter(isPictureSlot)) {
    const colour = colourFor(groupKeyOf(c));
    const b = document.createElement("div");
    b.className = "blk";
    b.style.flexGrow = String(Math.max(c.durationFrames, 1));
    b.style.background = colour;
    if (state.selectedSlot === slotKey(c)) b.classList.add("on");
    // Dimmed rather than removed: the editor should see that a section is
    // switched off without the edit appearing to change shape underneath them.
    if (c.sectionId && state.disabled.has(c.sectionId)) b.classList.add("off");
    b.title = (captionOf(c) ? `${captionOf(c)}\n` : "")
      + (c.sectionId ? `${sectionLabel(c.sectionId, state.plan)}\n` : "")
      + `${mediaName(c.mediaId)} — ${(c.outSeconds - c.inSeconds).toFixed(2)}s`;
    strip.appendChild(b);

    // A picture on the block, not just a colour: colour says which shots group
    // together, only the frame says what is actually there.
    //
    // A real <img>, appended and then given its src -- the sequence the
    // alternates grid uses and the only one measured to work here. This block
    // previously set a CSS background-image, which was the single use of that
    // property in the panel and rendered as nothing.
    // A frame only goes on the block when the block is wide enough to read one.
    // Measured on a real 25-shot edit at docked width, each block is 13.8px --
    // a sliver that reads as noise and costs a JPEG decode apiece. Below the
    // threshold the group colour carries the strip on its own, which is what it
    // was there for before the pictures arrived.
    const tp = wideEnoughForFrames ? thumbForClip(state.plan, c, state.libraryShots) : null;
    if (tp) {
      readImageDataUri(tp, state.settings.jobsToken).then((uri) => {
        if (!uri) return;
        const img = document.createElement("img");
        img.className = "blk-shot";
        b.appendChild(img);
        img.src = uri;
        // The band goes in after the picture so it sits on top of it.
        const band = document.createElement("div");
        band.className = "blk-band";
        band.style.background = colour;
        b.appendChild(band);
        if (c.swapped) {
          const mark = document.createElement("div");
          mark.className = "blk-mark";
          b.appendChild(mark);
        }
      }).catch(() => { /* the colour alone still reads */ });
    } else if (c.swapped) {
      const mark = document.createElement("div");
      mark.className = "blk-mark";
      b.appendChild(mark);
    }

    b.addEventListener("click", () => {
      const key = slotKey(c);
      state.selectedSlot = state.selectedSlot === key ? null : key;
      renderStrip();
    });
  }

  renderSlotDetail();
  const n = state.swaps.size;
  $("swap-count").textContent = n ? state.t("swap.count", { count: n }) : state.t("swap.lost");
  $("swap-reset").disabled = n === 0;
}

/**
 * What a shot shows, in the editor's language.
 *
 * The engine ranks the pictures against an English vocabulary -- that is the
 * language the image model was trained in -- and sends the winner as an id.
 * Only the label is translated, so the same shot reads the same way in both
 * languages rather than two encoders disagreeing about what it is.
 *
 * Falls back to the English text the engine sent alongside the id, so a
 * descriptor added to the engine before its translation still says something.
 * @param {{caption?: string, captionId?: string}} c
 */
function captionOf(c) {
  if (!c || !c.caption) return "";
  return c.captionId ? state.t(`shot.${c.captionId}`, undefined, c.caption) : c.caption;
}

function renderSlotDetail() {
  const detail = $("slot-detail");
  const alts = $("alts");
  detail.innerHTML = "";
  alts.innerHTML = "";

  if (state.selectedSlot === null) {
    detail.innerHTML = `<div class="why">${state.t("swap.pickPrompt")}</div>`;
    return;
  }
  const slot = slotAt(state.selectedSlot);
  if (!slot) return;

  const who = document.createElement("div");
  who.className = "who";
  const left = document.createElement("div");
  left.style.cssText = "display:flex;align-items:center;gap:6px;min-width:0";
  const sw = document.createElement("div");
  sw.className = "swatch";
  sw.style.background = colourFor(groupKeyOf(slot));
  const name = document.createElement("b");
  name.textContent = mediaName(slot.mediaId);
  left.append(sw, name);
  if (slot.swapped) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = state.t("swap.swapped");
    left.appendChild(tag);
  }
  const time = document.createElement("span");
  time.className = "why";
  time.textContent = `${slot.inSeconds.toFixed(2)}–${slot.outSeconds.toFixed(2)}s`;
  who.append(left, time);
  detail.appendChild(who);

  if (slot.caption) {
    // What the shot shows, above everything else about it. An editor scanning a
    // strip is asking "what is this", and a filename does not answer it.
    const what = document.createElement("div");
    what.style.cssText = "color:var(--text);font-weight:600;margin-top:2px";
    what.textContent = captionOf(slot);
    detail.appendChild(what);
  }
  if (slot.sectionId) {
    // Which described shot this slot is serving. Without it the strip is
    // colour-coded by something the editor cannot name.
    const sect = document.createElement("div");
    sect.className = "why";
    sect.style.color = "var(--text)";
    sect.textContent = sectionLabel(slot.sectionId, state.plan);
    detail.appendChild(sect);
  }
  if (slot.reason) {
    const why = document.createElement("div");
    why.className = "why";
    why.textContent = slot.reason;
    detail.appendChild(why);
  }
  if (slot.swapped) {
    const undo = document.createElement("button");
    undo.textContent = state.t("swap.revert");
    undo.style.marginTop = "6px";
    undo.addEventListener("click", () => {
      state.swaps.delete(slotKey(slot));
      renderStrip();
    });
    detail.appendChild(undo);
  }

  renderAlternates(slot);
}

/** Report the first thumbnail failure and then stay quiet about it. */
function reportThumbFailure(why) {
  if (!why || state.thumbFailureLogged) return;
  state.thumbFailureLogged = true;
  log(`thumbnails: ${why}`, "err");
}

/** What to call a candidate: a plan clip has a mediaId, a library shot a path. */
function candidateName(c) {
  if (c.relPath) return c.relPath.split("/").pop() || c.relPath;
  return mediaName(c.mediaId);
}

function renderAlternates(slot) {
  const alts = $("alts");
  // Against the ORIGINAL slot: its duration is what a candidate has to cover,
  // and that never changes, but the group follows whatever is in the slot now
  // so the list re-sorts around a swap the editor has already made.
  const offered = candidatesFor(state.plan, slot, state.libraryShots);
  if (!offered.length) {
    const empty = (state.plan.candidates || []).length ? "swap.none" : "swap.noCandidates";
    alts.innerHTML = `<div class="why head">${state.t(empty)}</div>`;
    return;
  }

  const head = document.createElement("div");
  head.className = "why head";
  const fromLib = offered.filter((c) => c.fromLibrary).length;
  head.textContent = state.t("swap.alternates")
    + (fromLib ? ` · ${state.t("swap.fromLibrary", { count: fromLib })}` : "");
  // The library index is written progressively, so say when it is still filling
  // -- an editor who cannot find a clip should know whether to wait or to look
  // somewhere else.
  if (state.libraryShots && state.libraryShots.complete === false) {
    head.textContent += ` · ${state.t("swap.libraryBuilding")}`;
  }
  alts.appendChild(head);

  for (const c of offered) {
    const card = document.createElement("div");
    card.className = c.current ? "alt current" : "alt";

    // A thumbnail if the engine made one. An <img> whose src will not decode
    // never fires load, so it swaps itself for a labelled placeholder rather
    // than leaving a broken box -- the card still works either way.
    const shot = document.createElement("div");
    shot.className = "noshot";
    shot.textContent = state.t("swap.noThumb");
    card.appendChild(shot);

    if (c.thumbPath) {
      readImageDataUri(c.thumbPath, state.settings.jobsToken).then((uri) => {
        if (!uri) {
          reportThumbFailure(lastImageError());
          return;
        }
        // ORDER MATTERS, and it is the whole bug.
        //
        // The element goes into the document FIRST and is visible when `src` is
        // assigned. Both earlier versions broke that: one set src before the
        // <img> was ever in the tree, the other set it while the element was
        // display:none and unhid it afterwards. Neither ever decoded, and
        // neither reported anything, because there is no error -- an image that
        // was not laid out when its src arrived simply stays blank.
        //
        // This is the exact sequence selftest.js uses in renderProbe, which is
        // the one image path measured to work on this machine: append, then src.
        const img = document.createElement("img");
        img.className = "thumb";
        card.appendChild(img);
        img.addEventListener("load", () => { shot.style.display = "none"; });
        img.addEventListener("error", () => {
          reportThumbFailure(`${c.thumbPath}: the <img> refused the data URI`);
        });
        img.src = uri;
      }).catch((err) => {
        // Never silent again. This catch is what hid an earlier bug.
        reportThumbFailure(String((err && err.message) || err));
      });
    }

    const txt = document.createElement("div");
    txt.className = "txt";
    const title = document.createElement("div");
    title.textContent = captionOf(c) || candidateName(c);
    for (const [when, key] of [
      [c.current, "swap.current"],
      [!c.current && c.sameGroup, "swap.sameSource"],
      [c.fromLibrary, "swap.library"],
    ]) {
      if (!when) continue;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = state.t(key);
      title.appendChild(tag);
    }
    const sub = document.createElement("div");
    sub.className = "sub";
    // The filename moves down here once there is something better to lead with.
    sub.textContent = (c.caption ? `${candidateName(c)} · ` : "")
      + `${c.inSeconds.toFixed(1)}s`
      + (c.score ? ` · ${c.score.toFixed(2)}` : "");
    txt.append(title, sub);
    card.appendChild(txt);

    const acts = document.createElement("div");
    acts.className = "acts";
    const preview = document.createElement("button");
    preview.textContent = state.t("swap.preview");
    preview.addEventListener("click", async () => {
      try {
        await previewCandidate(c);
      } catch (err) {
        log(err instanceof AuditionError ? state.t("swap.previewFailed") : String(err), "err");
      }
    });
    acts.appendChild(preview);
    if (!c.current) {
      const use = document.createElement("button");
      use.className = "primary";
      use.textContent = state.t("swap.use");
      use.addEventListener("click", () => {
        // A library shot is identified by path -- the plan has no id for it yet,
        // and withSwaps mints one along with the media entry the build needs.
        state.swaps.set(slotKey(slot), c.fromLibrary
          ? { relPath: c.relPath, inSeconds: c.inSeconds,
              durationSeconds: c.durationSeconds, reason: c.reason }
          : { mediaId: c.mediaId, inSeconds: c.inSeconds, reason: c.reason });
        renderStrip();
      });
      acts.appendChild(use);
    }
    card.appendChild(acts);
    alts.appendChild(card);
  }
}

/**
 * Show a candidate in the Source Monitor, parked at its in point.
 *
 * Reuses `audition()` rather than adding a second opener: it already opens a
 * path at a given second WITHOUT importing it into the project, which is the
 * property that matters here -- flicking through eight candidates must not
 * leave eight items in the bin.
 */
async function previewCandidate(c) {
  const resolve = makeResolver(state.settings.mediaToken, state.settings.musicToken);
  // A library shot has no media entry yet -- it is a path under the media root,
  // which is exactly what the resolver takes.
  let relPath = c.relPath;
  let root;
  if (!relPath) {
    const entry = (state.plan.media || []).find((m) => m.id === c.mediaId);
    if (!entry) throw new AuditionError(`no media entry for ${c.mediaId}`);
    relPath = entry.relPath;
    root = entry.root;
  }
  const abs = await resolve(relPath, root);
  await audition(abs, { atSeconds: c.inSeconds, play: true });
}

async function onApply() {
  if (!state.plan) return;
  // Swap first, while every slot is still where the engine put it, then drop
  // sections and let that ripple. The other order would have the ripple move
  // slots out from under the atFrame keys the swaps are held by.
  const plan = withoutSections(withSwaps(state.plan, state.swaps), [...state.disabled]);
  if (!plan.timeline.length) {
    log("Nothing selected to build.", "err");
    return;
  }

  $("apply").disabled = true;
  log(`Building "${plan.sequence.name}" (${plan.timeline.length} clips)...`);

  try {
    const report = await applyPlan(plan, {
      resolveAbsolutePath: makeResolver(state.settings.mediaToken, state.settings.musicToken),
      brandkit: state.settings.brandkit,
      onProgress: (stage, detail) => log(`  ${stage}: ${detail}`),
    });
    report.warnings.forEach((w) =>
      log(state.t("msg.buildWarning", { message: state.t.warning(w) }), "err"));
    log(state.t("msg.built", { stages: report.stages.join(", ") }), "ok");
    if (state.transport && state.planName) {
      await state.transport.writeReceipt(state.planName, {
        appliedAt: new Date().toISOString(),
        sequence: report.sequenceName,
        stages: report.stages,
        // English in the receipt, whatever the panel is showing. A receipt is a
        // record that gets read later, often by someone else and often on
        // another machine -- it should not depend on who happened to build it.
        warnings: report.warnings.map((w) => (typeof w === "string" ? w : w.message)),
        excludedSections: [...state.disabled],
        // What the editor changed by hand. The receipt is the only record of it
        // anywhere, since swaps are never written back to the plan.
        swaps: [...state.swaps.entries()].map(([slot, pick]) => ({
          slot, mediaId: pick.mediaId, relPath: pick.relPath, inSeconds: pick.inSeconds,
        })),
      });
    }
  } catch (err) {
    log(err instanceof ApplyError ? `${err.stage || "apply"}: ${err.message}` : String(err), "err");
  } finally {
    $("apply").disabled = false;
  }
}

$("pick-media").addEventListener("click", async () => {
  const picked = await pickFolder("media root");
  if (!picked) return;
  state.settings = saveSettings({ mediaToken: picked.token });
  // The token is the PANEL's handle on the folder; the helper cannot use it and
  // has its own media root, fixed in the launchd plist when setup ran. Writing
  // the path to config is what tells the helper the footage has moved. Without
  // this the picker changed nothing an editor could see, because the clip list
  // comes from the helper's index.
  if (state.transport) {
    try {
      await state.transport.writeConfig({ mediaRoot: picked.path });
    } catch (err) {
      log(state.t("msg.mediaRootNotShared", { message: err.message }), "err");
    }
  }
  await refreshSetup();
  state.selectedMedia.clear();
  await loadMediaList();
  log(state.t("msg.mediaRootSet", { path: picked.path }));
});

/** Absolute path of the chosen track, for the Source Monitor. */
async function chosenTrackPath() {
  const chosen = parseMusicValue($("opt-music").value);
  if (chosen.kind !== "track") throw new AuditionError(state.t("msg.chooseTrackFirst"));
  const resolve = makeResolver(state.settings.mediaToken, state.settings.musicToken);
  return resolve(chosen.relPath, chosen.root);
}

$("opt-ui-language").addEventListener("change", () => {
  const lang = $("opt-ui-language").value || "en";
  state.settings = saveSettings({ uiLanguage: lang });
  state.t = makeTranslator(lang);
  applyTranslations();
});

$("music-audition").addEventListener("click", async () => {
  try {
    const path = await chosenTrackPath();
    const start = parseTimecode($("opt-music-start").value) || 0;
    const result = await audition(path, { atSeconds: start });
    log(result.playing
      ? state.t("music.auditioning")
      : state.t("msg.auditionFailed", { message: result.detail }),
      result.playing ? undefined : "err");
  } catch (err) {
    log(state.t("msg.auditionFailed", { message: err.message }), "err");
  }
});

$("music-playhead").addEventListener("click", async () => {
  try {
    const seconds = await playheadSeconds();
    $("opt-music-start").value = formatTimecode(seconds);
    renderSummary_();
    log(state.t("music.startSetTo", { time: formatTimecode(seconds) }));
  } catch (err) {
    log(state.t("msg.playheadFailed", { message: err.message }), "err");
  }
});

$("opt-music-length").addEventListener("input", () => {
  state.musicLengthTouched = true;
});

$("pick-music").addEventListener("click", async () => {
  const picked = await pickFolder("music folder");
  if (!picked) return;
  state.settings = saveSettings({ musicToken: picked.token });
  await refreshSetup();
  // The helper does the indexing, and it only knows the path -- the token is
  // this panel's alone. Writing it to config.json is what makes the folder
  // reachable from the other side without a restart.
  if (state.transport) {
    try {
      await state.transport.writeConfig({ musicRoot: picked.path });
      log(state.t("msg.musicFolderSet", { path: picked.path }));
    } catch (err) {
      log(`Music folder set, but could not tell the helper: ${err.message}`, "err");
    }
  } else {
    log(state.t("msg.musicFolderNoHelper"), "err");
  }
  await loadMediaList();
});

$("pick-jobs").addEventListener("click", async () => {
  const picked = await pickFolder("jobs folder");
  if (picked) {
    state.settings = saveSettings({ jobsToken: picked.token });
    await refreshSetup();
    await refreshPlans();
    await loadCapabilities();
    log(`Jobs folder: ${picked.path}`);
  }
});



// --------------------------------------------------------------- new edit form

/** Fill a <select> from a capabilities list, keeping any current choice. */
function fillSelect(id, entries, fallback) {
  const el = $(id);
  const previous = el.value;
  el.innerHTML = "";
  const list = entries && entries.length ? entries : fallback;
  for (const item of list) {
    const opt = document.createElement("option");
    opt.value = item.value;
    opt.textContent = item.label;
    el.appendChild(opt);
  }
  // UXP does not implicitly select the first option, so a freshly filled select
  // renders blank and reads as an empty value until the editor opens it.
  if (previous && list.some((i) => i.value === previous)) el.value = previous;
  else if (list.length) el.value = list[0].value;
}

async function loadCapabilities() {
  if (!state.transport) return;
  const caps = await state.transport.listCapabilities();
  if (!caps) {
    // The helper has never run here. Say so plainly rather than offering an
    // empty form that fails on submit.
    log(state.t("msg.helperMissing"), "err");
    $("create").disabled = true;
    return;
  }
  state.capabilities = caps;
  $("create").disabled = false;

  // Recipe and look names are identifiers and brand-kit names, not prose.
  fillSelect("opt-recipe", (caps.recipes || []).map((r) => ({ value: r.name, label: r.name })), []);
  applyCapabilityLabels(caps);
  // Neutral defaults: the first entry in a list is not necessarily the sane one.
  if (!$("opt-pacing").value || $("opt-pacing").value === caps.pacing[0].value) {
    $("opt-pacing").value = "standard";
  }
  // Music comes from the media index rather than capabilities, but the dropdown
  // must never render empty while waiting for it.
  fillMusicSelect();
  renderSummary_();
}

/** Rebuild the Music dropdown from whatever the last index gave us. */
function fillMusicSelect() {
  // Rebuilt rather than cached, so the labels follow the panel language.
  const choices = musicChoices(
    [...state.mediaIndex.values()], state.libraryFiles, state.t);
  fillSelect("opt-music", choices, choices);
  const tracks = choices.length - 2;   // minus Automatic and No music
  $("music-count").textContent = tracks
    ? state.t("music.tracksFound", { count: tracks, plural: tracks === 1 ? "" : "s" })
    : state.t("music.noTracks");
}

async function loadMediaList() {
  const box = $("media-list");
  box.innerHTML = "";
  if (!state.settings.mediaToken) {
    box.innerHTML = `<div class="empty">${state.t("edit.noMediaRoot")}</div>`;
    return;
  }
  try {
    // Prefer the helper's index: it knows which files actually carry video, which
    // an extension check cannot. Fall back to extensions when it has not run.
    const index = state.transport ? await state.transport.listMediaIndex() : null;
    const musicIndex = state.transport ? await state.transport.listMusicIndex() : null;
    // The clip picker draws from the same library index the shot list uses, and
    // it runs long before any plan is opened -- so load it here too, or the
    // cards are nameplates until an editor happens to select a plan.
    state.libraryShots = state.transport ? await state.transport.listLibraryShots() : null;
    state.libraryFiles = (musicIndex && Array.isArray(musicIndex.files)) ? musicIndex.files : [];
    state.mediaIndexMeta = index || null;
    if (index && Array.isArray(index.files)) {
      // Keyed by relPath, not name: the scan descends into subfolders now, so
      // two `theme.wav` under different folders are different files.
      state.mediaIndex = new Map(index.files.map((f) => [f.relPath || f.name, f]));
      state.mediaFiles = index.files.filter((f) => f.hasVideo).map((f) => f.relPath || f.name);
      state.musicFiles = musicChoices(index.files, state.libraryFiles, state.t);
      if (index.truncated) {
        log(state.t("msg.indexTruncated", { count: index.files.length }), "err");
      }
    } else {
      state.mediaIndex = new Map();
      state.mediaFiles = await listMediaFiles(state.settings.mediaToken, isVideoFile);
      state.musicFiles = musicChoices([], state.libraryFiles, state.t);
    }
    fillMusicSelect();
  } catch (err) {
    box.innerHTML = `<div class="empty">${state.t("edit.mediaUnreadable", { message: err.message })}</div>`;
    return;
  }
  if (!state.mediaFiles.length) {
    // "No video files in the media root" is true and useless: it names neither
    // the folder looked in nor the reason. The two real causes are a folder
    // with no footage in it, and footage iCloud has evicted -- which is visible
    // in Finder with a cloud badge and unreadable to everything else.
    const gone = state.mediaIndexMeta && state.mediaIndexMeta.evictedCount;
    const where = (state.mediaIndexMeta && state.mediaIndexMeta.mediaRoot) || "";
    box.innerHTML = `<div class="empty">${
      gone ? state.t("edit.mediaInICloud", { count: gone, where })
           : state.t("edit.noVideoFiles", { where })}</div>`;
    return;
  }
  // Cards, not a checkbox list. `C1376.MP4` says nothing about what is in the
  // file, and an editor who cannot tell opens it in Premiere to look -- which
  // is the step this is meant to remove. The stills already exist; the helper
  // indexes the whole media root in the background for the shot list.
  //
  // The checkbox stays inside each card. It is the affordance that says these
  // are multi-select, and dropping it for a highlight would make selection a
  // guess.
  for (const name of state.mediaFiles) {
    const meta = state.mediaIndex && state.mediaIndex.get(name);
    const card = document.createElement("label");
    card.className = "clip";
    if (state.selectedMedia.has(name)) card.classList.add("on");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.selectedMedia.has(name);
    cb.addEventListener("change", () => {
      if (cb.checked) state.selectedMedia.add(name);
      else state.selectedMedia.delete(name);
      card.classList.toggle("on", cb.checked);
      renderSummary_();
    });

    const shot = document.createElement("div");
    shot.className = "clip-shot";
    const tp = thumbForSource(name, state.libraryShots);
    if (tp) {
      readImageDataUri(tp, state.settings.jobsToken).then((uri) => {
        if (!uri) return;
        // Appended and then given its src: an <img> that is not in the document
        // when src arrives never decodes, and says nothing about it.
        const img = document.createElement("img");
        shot.appendChild(img);
        img.src = uri;
      }).catch(() => { /* the name still identifies the clip */ });
    }

    const text = document.createElement("div");
    text.className = "clip-name";
    text.textContent = name.split("/").pop() || name;
    const dur = document.createElement("div");
    dur.className = "clip-dur";
    dur.textContent = meta && meta.durationSeconds
      ? `${Math.round(meta.durationSeconds)}s` : "";

    card.append(cb, shot, text, dur);
    box.appendChild(card);
  }
  renderSummary_();
}

/**
 * Keep the Seconds box honest about whether anything is reading it.
 *
 * Length defaults to "No limit", and the Seconds box next to it defaults to 30
 * and stays editable. So the form showed "No limit / 30" and an editor could
 * reasonably read that as thirty seconds -- while `buildRequest` omitted the
 * duration entirely and the engine ran to whatever the footage gave. A form
 * that displays a number nothing reads is lying, quietly, in the one place the
 * editor is most likely to trust it.
 */
function syncDurationField() {
  const off = $("opt-duration-mode").value === "none";
  const box = $("opt-duration-seconds");
  box.disabled = off;
  // Disabled inputs are easy to miss at this size; dim the whole field so the
  // pair reads as one control rather than two that disagree.
  //
  // By id rather than closest(): UXP implements a subset of the DOM, and this
  // panel has one hard-won example of a method that is simply absent. Reaching
  // for the element directly needs nothing that is not used twenty times over
  // elsewhere in this file.
  $("duration-seconds-field").style.opacity = off ? "0.45" : "";
}

/**
 * Read the settings back in plain English, so they can be checked in one line.
 *
 * This is the whole point of moving them behind Adjust: hidden is only
 * acceptable if what they say is still visible. A job once ran to forty seconds
 * against a fifteen-second intention because twelve dropdowns cannot be
 * proofread at a glance.
 */
function renderFormatSummary() {
  const el = $("format-summary");
  if (!el) return;
  const label = (id) => {
    const sel = $(id);
    const opt = sel && sel.options[sel.selectedIndex];
    return opt ? opt.textContent : "";
  };
  const bits = [];
  const mode = $("opt-duration-mode").value;
  bits.push(mode === "none"
    ? state.t("format.noLimit")
    : `${label("opt-duration-mode")} ${$("opt-duration-seconds").value}s`);
  bits.push(label("opt-aspect"));
  bits.push($("opt-visual").checked
    ? label("opt-cut-rate")
    : state.t("format.cutsToSpeech"));
  if ($("opt-look").value) bits.push(label("opt-look"));
  el.textContent = bits.filter(Boolean).join(" \u00b7 ");
}

/**
 * Let the description drive the controls it speaks to.
 *
 * The engine reads the same sentence and would apply these anyway, at plan
 * time. Doing it here as well is not duplication for its own sake: without it
 * the form says "Client promo, 60s, landscape" while the prompt says "12 second
 * tiktok", and the editor is looking at a form that is lying about what it will
 * make. This project has already shipped one of those.
 *
 * Only what CHANGED since the last reading is applied. Re-asserting every
 * setting on every keystroke would undo an adjustment the editor made by hand
 * halfway through typing, which is its own kind of lie.
 */
function applyPromptSettings() {
  const words = state.capabilities && state.capabilities.promptWords;
  if (!words) return;                     // capabilities not written yet
  const formats = (state.capabilities && state.capabilities.formats) || [];
  const got = readPromptSettings($("opt-story").value, words, formats);
  const last = state.promptApplied;
  const from = [];

  const push = (id, value) => {
    const el = $(id);
    if (!el) return;
    el.value = String(value);
    el.dispatchEvent(new Event("change"));
  };

  // The recipe first: it decides thresholds, tracks and filler handling, and
  // anything the description states explicitly below should outrank the
  // defaults that come with it.
  if (got.recipe !== last.recipe && got.recipe) {
    push("opt-recipe", got.recipe);
    const fmt = formats.find((f) => f.recipe === got.recipe);
    if (fmt) {
      from.push(fmt.label);
      // The recipe's own defaults fill in whatever the sentence did not say.
      if (got.seconds === null && fmt.duration) {
        push("opt-duration-mode", fmt.duration_mode || "about");
        push("opt-duration-seconds", Math.round(fmt.duration));
        syncDurationField();
      }
      if (got.aspect === null && fmt.aspect) push("opt-aspect", fmt.aspect);
      const box = $("opt-visual");
      if (box && fmt.visual && !box.checked) {
        box.checked = true;
        box.dispatchEvent(new Event("change"));
      }
      if (got.cutRate === null && fmt.cut_rate && $("opt-visual").checked) {
        push("opt-cut-rate", String(fmt.cut_rate));
      }
    }
  }

  if (got.seconds !== last.seconds && got.seconds !== null) {
    push("opt-duration-mode", "exactly");
    push("opt-duration-seconds", Math.round(got.seconds));
    syncDurationField();
    from.push(`${Math.round(got.seconds)}s`);
  }
  if (got.aspect !== last.aspect && got.aspect !== null) {
    push("opt-aspect", got.aspect);
    from.push(got.aspect);
  }
  if (got.visual !== last.visual && got.visual) {
    const box = $("opt-visual");
    if (box && !box.checked) {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
      from.push(state.t("prompt.fromPictures"));
    }
  }
  // Asking for subtitles in the description ticks the box, and ticking the box
  // is what makes the setting visible. Left untouched, an editor would write
  // "with subtitles", get them, and see a form saying they had not asked.
  if (got.subtitles && !last.subtitles) {
    const box = $("opt-subtitles");
    if (box && !box.checked) {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
      from.push(state.t("prompt.subtitles"));
    }
  }
  // The rate goes last, and only once cutting from pictures is on: a rate on a
  // speech edit is a setting the engine accepts and never acts on.
  if (got.cutRate !== last.cutRate && got.cutRate !== null && $("opt-visual").checked) {
    push("opt-cut-rate", String(got.cutRate));
    // Name the rate, not the fact that there is one. "cut rate" tells an editor
    // nothing they could check against what they typed.
    const sel = $("opt-cut-rate");
    const opt = sel && sel.options[sel.selectedIndex];
    from.push(opt ? opt.textContent.toLowerCase() : String(got.cutRate));
  }

  state.promptApplied = got;
  const note = $("prompt-applied");
  if (note) {
    note.textContent = from.length
      ? state.t("prompt.applied", { settings: from.join(", ") }) : "";
    note.classList.toggle("hidden", from.length === 0);
  }
  renderFormatSummary();
}

function currentForm() {
  const seconds = Number($("opt-duration-seconds").value);
  return {
    jobId: $("job-name").value,
    recipe: $("opt-recipe").value,
    media: [...state.selectedMedia],
    aspect: $("opt-aspect").value,
    pacing: $("opt-pacing").value,
    cutRate: $("opt-cut-rate").value,
    look: $("opt-look").value || null,
    visual: $("opt-visual").checked,
    subtitles: $("opt-subtitles").checked,
    protectSpeech: $("opt-protect-speech").checked,
    removeSilence: $("opt-remove-silence").checked,
    silenceAllowed: Number($("opt-silence-allowed").value),
    story: $("opt-story").value,
    durationMode: $("opt-duration-mode").value,
    durationSeconds: Number.isFinite(seconds) ? seconds : null,
    language: $("opt-language").value || "auto",
    music: $("opt-music").value || "auto",
    musicStart: parseTimecode($("opt-music-start").value) || 0,
    musicLength: parseSeconds($("opt-music-length").value),
  };
}

/**
 * The chunk only means something once a track is chosen. Shown rather than
 * disabled, so the form does not carry two dead fields most of the time.
 */
function renderMusicControls() {
  const chosen = parseMusicValue($("opt-music").value).kind === "track";
  for (const id of ["music-tools", "music-chunk"]) {
    $(id).style.display = chosen ? "" : "none";
  }
  if (!chosen) return;
  // Default the chunk to the length asked of the edit -- the common case is a
  // trend where the two are the same number.
  //
  // Including when that length goes away. The field used to be filled once and
  // never cleared, so setting a 15s target, choosing a track, then switching the
  // length back to "as long as it needs" left 15 behind: a 15-second bed under a
  // two-minute cut, reported only as one warning at the end of a list of seven.
  // An untouched field mirrors the target; once the editor types in it, it is
  // theirs and nothing here overwrites it.
  const el = $("opt-music-length");
  if (!state.musicLengthTouched) {
    el.value = $("opt-duration-mode").value === "none"
      ? ""
      : String($("opt-duration-seconds").value || "");
  }
}

function renderSummary_() {
  renderMusicControls();
  // Say so when normalising changed the name. Silently turning `a/b` into `ab`
  // is the same class of bug as the one that used to eat Japanese entirely.
  const typed = $("job-name").value;
  const notice = $("job-name-notice");
  if (jobIdWasChanged(typed)) {
    notice.textContent = state.t("msg.jobIdChanged", { cleaned: normaliseJobId(typed) });
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }
  $("media-count").textContent = state.mediaFiles.length
    ? state.t("edit.clipsCount", { selected: state.selectedMedia.size, total: state.mediaFiles.length })
    : "";
  const request = buildRequest(currentForm());
  $("request-summary").textContent = state.selectedMedia.size
    ? describeRequest(request, state.capabilities || {}, state.t)
    : "";
  $("request-errors").innerHTML = "";
}

async function onCreate() {
  const request = buildRequest(currentForm());
  const problems = validateRequest(request);
  $("request-errors").innerHTML = "";
  if (problems.length) {
    problems.forEach((p) => {
      const el = document.createElement("div");
      el.className = "msg bad";
      el.textContent = state.t(p, {}, p);
      $("request-errors").appendChild(el);
    });
    return;
  }

  $("create").disabled = true;
  log(state.t("msg.requested", {
    name: request.jobId,
    summary: describeRequest(request, state.capabilities || {}, state.t),
  }));
  try {
    await state.transport.writeRequest(requestFileName(request.jobId), request);
    watchJob(request.jobId);
  } catch (err) {
    log(`Could not write the request: ${err.message}`, "err");
    $("create").disabled = false;
  }
}

/**
 * Poll until the helper finishes. Analysis of 4K footage takes minutes, so the
 * panel has to show progress rather than an unresponsive button.
 */
function watchJob(jobId) {
  if (state.watching) clearInterval(state.watching);
  let lastMessage = "";

  state.watching = setInterval(async () => {
    let status = null;
    try {
      status = await state.transport.readStatus(jobId);
    } catch {
      return;
    }
    if (!status) return;

    if (status.message && status.message !== lastMessage) {
      lastMessage = status.message;
      log(`  ${jobId}: ${status.message}`);
    }
    if (status.state === "ready" || status.state === "failed") {
      clearInterval(state.watching);
      state.watching = null;
      $("create").disabled = false;
      if (status.state === "failed") {
        log(`${jobId} failed: ${status.message}`, "err");
      } else {
        log(`${jobId} is ready.`, "ok");
        await refreshPlans();
      }
    }
  }, 2000);
}

$("update").addEventListener("click", async () => {
  const button = $("update");
  button.disabled = true;
  log(state.t("msg.updateChecking"));
  try {
    if (!state.transport || !state.transport.requestUpdate) {
      throw new Error(state.t("msg.reportNoJobs"));
    }
    await state.transport.requestUpdate();
    // The updater restarts the helper on its way past, so this waits on a file
    // rather than on the helper answering.
    let result = null;
    for (let i = 0; i < 60 && !result; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      result = await state.transport.readUpdateResult();
    }
    if (!result) {
      log(state.t("msg.reportNoHelper"), "err");
    } else if (result.status === "updated") {
      log(state.t("msg.updateDone", { detail: result.detail || "" }), "ok");
      log(state.t("msg.updateRestart"), "ok");
    } else if (result.status === "current") {
      log(state.t("msg.updateCurrent", { detail: result.detail || "" }), "ok");
    } else {
      log(state.t("msg.updateFailed", { detail: result.detail || result.status }), "err");
    }
  } catch (err) {
    log(`${err && err.message ? err.message : String(err)}`, "err");
  } finally {
    button.disabled = false;
  }
});

$("bug-report").addEventListener("click", async () => {
  const button = $("bug-report");
  button.disabled = true;
  log(state.t("msg.reportCollecting"));
  try {
    if (!state.transport || !state.transport.requestReport) {
      throw new Error(state.t("msg.reportNoJobs"));
    }
    await state.transport.requestReport();
    // The helper polls its folder; this polls back. Thirty tries at a second
    // apiece covers a slow collection without hanging the panel if the helper
    // is not running at all -- which is itself the likeliest thing to be wrong
    // when someone is reporting a bug.
    let result = null;
    for (let i = 0; i < 30 && !result; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      result = await state.transport.readReportResult();
    }
    if (!result) {
      log(state.t("msg.reportNoHelper"), "err");
    } else if (result.ok && result.path) {
      log(state.t("msg.reportReady", { path: result.path }), "ok");
      log(state.t("msg.reportContents"));
    } else {
      log(state.t("msg.reportFailed", { detail: (result.output || "").slice(-300) }), "err");
    }
  } catch (err) {
    log(`${err && err.message ? err.message : String(err)}`, "err");
  } finally {
    button.disabled = false;
  }
});

$("setup-edit").addEventListener("click", () => {
  setupOpen = true;
  $("setup-fields").classList.remove("hidden");
  $("setup-done").classList.add("hidden");
});

$("selftest").addEventListener("click", async () => {
  $("selftest").disabled = true;
  log("Running self-test against this Premiere build...");
  try {
    const report = await runSelfTest((msg) => log(`  ${msg}`));
    const { passed, failed, total } = report.summary;
    for (const c of report.checks) {
      log(`  ${c.pass ? "PASS" : "FAIL"}  ${c.name}`, c.pass ? "ok" : "err");
      if (!c.pass && c.error) log(`        ${c.error}`, "err");
      if (c.missing && c.missing.length) log(`        missing: ${c.missing.join(", ")}`, "err");
    }
    if (report.note) log(`  ${report.note}`);
    log(`Self-test: ${passed}/${total} passed${failed ? `, ${failed} failed` : ""}`,
        failed ? "err" : "ok");
    if (report.strategy) log(`  clip strategy for this build: ${report.strategy}`, "ok");
  } catch (err) {
    log(`Self-test crashed: ${err && err.message ? err.message : String(err)}`, "err");
  } finally {
    $("selftest").disabled = false;
  }
});

$("refresh").addEventListener("click", refreshPlans);
$("create").addEventListener("click", onCreate);
$("media-reload").addEventListener("click", loadMediaList);
$("media-all").addEventListener("click", () => {
  state.mediaFiles.forEach((n) => state.selectedMedia.add(n));
  loadMediaList();
});
$("media-none").addEventListener("click", () => {
  state.selectedMedia.clear();
  loadMediaList();
});
$("opt-visual").addEventListener("change", renderSummary_);

/**
 * The allowance only means something once silence is being removed, and
 * "keep whole sentences" is not a choice once subtitles are on -- the engine
 * turns it on regardless, so a box the editor could untick would be a lie.
 */
function syncSpeechFields() {
  const removing = $("opt-remove-silence").checked;
  $("silence-allowed-field").classList.toggle("hidden", !removing);
  const subs = $("opt-subtitles").checked;
  const protect = $("opt-protect-speech");
  if (subs) protect.checked = true;
  protect.disabled = subs;
}
for (const id of ["opt-subtitles", "opt-protect-speech", "opt-remove-silence",
                  "opt-silence-allowed"]) {
  $(id).addEventListener("change", () => { syncSpeechFields(); renderSummary_(); });
}
syncSpeechFields();
for (const id of ["job-name", "opt-story", "opt-recipe", "opt-aspect", "opt-pacing", "opt-cut-rate",
                  "opt-look", "opt-duration-mode", "opt-duration-seconds",
                  "opt-music", "opt-music-start", "opt-music-length"]) {
  $(id).addEventListener("change", renderSummary_);
  $(id).addEventListener("input", renderSummary_);
  $(id).addEventListener("change", renderFormatSummary);
  $(id).addEventListener("input", renderFormatSummary);
  if (id === "opt-duration-mode") {
    $(id).addEventListener("change", syncDurationField);
  }
  if (id === "opt-story") {
    $(id).addEventListener("input", applyPromptSettings);
  }
  // Scrolling the panel past a dropdown would otherwise cycle its value, so an
  // editor scrolling to reach Create silently changes what they are asking for.
  $(id).addEventListener("wheel", (e) => e.preventDefault());
}
$("apply").addEventListener("click", onApply);

$("console-toggle").addEventListener("click", () => {
  const block = $("console-block");
  const open = block.classList.contains("hidden");
  block.classList.toggle("hidden", !open);
  $("console-toggle").textContent = state.t(open ? "console.hide" : "console.show");
  if (open) renderConsole();
});

$("adjust-toggle").addEventListener("click", () => {
  const block = $("adjust-block");
  const open = block.classList.contains("hidden");
  block.classList.toggle("hidden", !open);
  $("adjust-toggle").textContent = state.t(open ? "edit.adjustHide" : "edit.adjustShow");
});

$("swap-reset").addEventListener("click", () => {
  state.swaps.clear();
  renderStrip();
});
$("plan-list").addEventListener("change", (e) => {
  const value = /** @type {any} */ (e.target).value;
  const ref = state.plans.find((p) => p.name === value);
  if (ref) selectPlan(ref);
});

(async function init() {
  try {
    // Language before anything else, so a Japanese editor never sees an English
    // panel flash past on the way in.
    state.t = makeTranslator(state.settings.uiLanguage || "en");
    applyTranslations();
    await refreshSetup();
    await refreshPlans();
    await loadCapabilities();
    await loadMediaList();
    applyTranslations();
    log("Panel ready.");
  } catch (err) {
    // Surfacing this in the panel matters: a throw during init leaves every
    // button inert with nothing in the UXP log to explain why.
    log(`Init failed: ${err && err.message ? err.message : String(err)}`, "err");
  }
})();
