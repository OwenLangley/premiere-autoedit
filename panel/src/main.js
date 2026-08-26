"use strict";
/**
 * Panel UI.
 *
 * The interaction model is deliberate: the panel proposes, the editor disposes.
 * Nothing is applied until the editor has seen the clip count, the warnings and
 * the reasons, and has had a chance to switch sections off.
 */

const { applyPlan, ApplyError } = require("./apply");
const { validatePlan, summarize, sections, withoutSections } = require("./plan");
const {
  LocalFolderTransport, pickFolder, folderFromToken, listMediaFiles,
  makeResolver, loadSettings, saveSettings,
} = require("./transport");
const {
  isVideoFile, buildRequest, validateRequest, requestFileName, describeRequest,
  musicChoices, parseMusicValue, parseTimecode, formatTimecode, parseSeconds,
  normaliseJobId, jobIdWasChanged,
} = require("./request");
const { audition, playheadSeconds, AuditionError } = require("./audition");
const { runSelfTest } = require("./selftest");
const { LANGUAGES, makeTranslator } = require("./i18n");

/** @type {(id: string) => any} document.getElementById is typed HTMLElement; the
 * panel needs the concrete input/select members. */
const $ = (id) => /** @type {any} */ (document.getElementById(id));

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
}

async function refreshSetup() {
  await showFolder(state.settings.mediaToken, $("media-path"), state.t("setup.notSet"));
  await showFolder(state.settings.jobsToken, $("jobs-path"), state.t("setup.notSet"));
  await showFolder(state.settings.musicToken, $("music-path"), state.t("setup.notSetOptional"));
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
  $("plan-summary").classList.add("hidden");
  $("sections-block").classList.add("hidden");
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
    });
    const label = document.createElement("div");
    label.className = "grow";
    label.textContent = sect.label;
    const meta = document.createElement("div");
    meta.className = "meta";
    const bits = [`${sect.clips} clip${sect.clips === 1 ? "" : "s"}`];
    if (sect.graphics) bits.push(`${sect.graphics} gfx`);
    meta.textContent = bits.join(" · ");
    row.append(cb, label, meta);
    box.appendChild(row);
  }
}

async function onApply() {
  if (!state.plan) return;
  const plan = withoutSections(state.plan, [...state.disabled]);
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
  if (picked) {
    state.settings = saveSettings({ mediaToken: picked.token });
    await refreshSetup();
    state.selectedMedia.clear();
    await loadMediaList();
    log(`Media root: ${picked.path}`);
  }
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
    state.libraryFiles = (musicIndex && Array.isArray(musicIndex.files)) ? musicIndex.files : [];
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
    box.innerHTML = `<div class="empty">${state.t("edit.noVideoFiles")}</div>`;
    return;
  }
  for (const name of state.mediaFiles) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.selectedMedia.has(name);
    cb.addEventListener("change", () => {
      if (cb.checked) state.selectedMedia.add(name);
      else state.selectedMedia.delete(name);
      renderSummary_();
    });
    const text = document.createElement("span");
    const meta = state.mediaIndex && state.mediaIndex.get(name);
    text.textContent = meta && meta.durationSeconds
      ? `${name}  (${Math.round(meta.durationSeconds)}s)`
      : name;
    label.append(cb, text);
    box.appendChild(label);
  }
  renderSummary_();
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
for (const id of ["job-name", "opt-recipe", "opt-aspect", "opt-pacing", "opt-cut-rate",
                  "opt-look", "opt-duration-mode", "opt-duration-seconds",
                  "opt-music", "opt-music-start", "opt-music-length"]) {
  $(id).addEventListener("change", renderSummary_);
  $(id).addEventListener("input", renderSummary_);
  // Scrolling the panel past a dropdown would otherwise cycle its value, so an
  // editor scrolling to reach Create silently changes what they are asking for.
  $(id).addEventListener("wheel", (e) => e.preventDefault());
}
$("apply").addEventListener("click", onApply);
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
