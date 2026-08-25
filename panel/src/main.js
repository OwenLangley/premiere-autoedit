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
} = require("./request");
const { runSelfTest } = require("./selftest");

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

async function refreshSetup() {
  await showFolder(state.settings.mediaToken, $("media-path"), "not set");
  await showFolder(state.settings.jobsToken, $("jobs-path"), "not set");
  state.transport = state.settings.jobsToken
    ? new LocalFolderTransport(state.settings.jobsToken)
    : null;
}

async function refreshPlans() {
  const list = $("plan-list");
  list.innerHTML = "";
  if (!state.transport) {
    list.innerHTML = "<option>No jobs folder set</option>";
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
  const rows = [
    ["Sequence", state.plan.sequence.name],
    ["Recipe", state.plan.recipe],
    ["Clips", String(s.clipCount)],
    ["Duration", s.timecode],
    ["Sources", String(s.mediaCount)],
  ];
  if (s.graphicsCount) rows.push(["Graphics", String(s.graphicsCount)]);
  if (s.markerCount) rows.push(["Markers", String(s.markerCount)]);
  rows.push(["Mean confidence", `${Math.round(s.meanConfidence * 100)}%`]);

  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "stat";
    row.innerHTML = `<span>${label}</span><span>${value}</span>`;
    box.appendChild(row);
  }

  $("plan-messages").innerHTML = "";
  if (s.lowConfidence) {
    message(
      `${s.lowConfidence} clip(s) scored low confidence \u2014 either speech the ` +
      `transcript was unsure about, or shots that only just passed the quality ` +
      `gates. Review those before trusting the cut.`
    );
  }
  for (const w of s.warnings) message(w.message || String(w));
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
      resolveAbsolutePath: makeResolver(state.settings.mediaToken),
      brandkit: state.settings.brandkit,
      onProgress: (stage, detail) => log(`  ${stage}: ${detail}`),
    });
    report.warnings.forEach((w) => log(`  warning: ${w}`, "err"));
    log(`Done — ${report.stages.join(", ")}`, "ok");
    if (state.transport && state.planName) {
      await state.transport.writeReceipt(state.planName, {
        appliedAt: new Date().toISOString(),
        sequence: report.sequenceName,
        stages: report.stages,
        warnings: report.warnings,
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
    log("Helper has not run yet -- start it to enable new edits.", "err");
    $("create").disabled = true;
    return;
  }
  state.capabilities = caps;
  $("create").disabled = false;

  fillSelect("opt-recipe", (caps.recipes || []).map((r) => ({ value: r.name, label: r.name })), []);
  fillSelect("opt-aspect", caps.aspects, [{ value: "source", label: "Match source" }]);
  fillSelect("opt-pacing", caps.pacing, [{ value: "standard", label: "Standard" }]);
  // Neutral defaults: the first entry in a list is not necessarily the sane one.
  if (!$("opt-pacing").value || $("opt-pacing").value === caps.pacing[0].value) {
    $("opt-pacing").value = "standard";
  }
  fillSelect("opt-duration-mode", caps.durationModes, [{ value: "none", label: "No limit" }]);
  fillSelect("opt-look", [{ value: "", label: "None" }, ...(caps.looks || [])], [{ value: "", label: "None" }]);
  renderSummary_();
}

async function loadMediaList() {
  const box = $("media-list");
  box.innerHTML = "";
  if (!state.settings.mediaToken) {
    box.innerHTML = '<div class="empty">Set a media root above.</div>';
    return;
  }
  try {
    // Prefer the helper's index: it knows which files actually carry video, which
    // an extension check cannot. Fall back to extensions when it has not run.
    const index = state.transport ? await state.transport.listMediaIndex() : null;
    if (index && Array.isArray(index.files)) {
      state.mediaIndex = new Map(index.files.map((f) => [f.name, f]));
      state.mediaFiles = index.files.filter((f) => f.hasVideo).map((f) => f.name);
    } else {
      state.mediaIndex = new Map();
      state.mediaFiles = await listMediaFiles(state.settings.mediaToken, isVideoFile);
    }
  } catch (err) {
    box.innerHTML = `<div class="empty">Could not read the media root: ${err.message}</div>`;
    return;
  }
  if (!state.mediaFiles.length) {
    box.innerHTML = '<div class="empty">No video files in the media root.</div>';
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
    look: $("opt-look").value || null,
    visual: $("opt-visual").checked,
    durationMode: $("opt-duration-mode").value,
    durationSeconds: Number.isFinite(seconds) ? seconds : null,
  };
}

function renderSummary_() {
  $("media-count").textContent = state.mediaFiles.length
    ? `(${state.selectedMedia.size} of ${state.mediaFiles.length})`
    : "";
  const request = buildRequest(currentForm());
  $("request-summary").textContent = state.selectedMedia.size
    ? describeRequest(request, state.capabilities || {})
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
      el.textContent = p;
      $("request-errors").appendChild(el);
    });
    return;
  }

  $("create").disabled = true;
  log(`Requested "${request.jobId}" — ${describeRequest(request, state.capabilities || {})}`);
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
for (const id of ["job-name", "opt-recipe", "opt-aspect", "opt-pacing",
                  "opt-look", "opt-duration-mode", "opt-duration-seconds"]) {
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
    await refreshSetup();
    await refreshPlans();
    await loadCapabilities();
    await loadMediaList();
    log("Panel ready.");
  } catch (err) {
    // Surfacing this in the panel matters: a throw during init leaves every
    // button inert with nothing in the UXP log to explain why.
    log(`Init failed: ${err && err.message ? err.message : String(err)}`, "err");
  }
})();
