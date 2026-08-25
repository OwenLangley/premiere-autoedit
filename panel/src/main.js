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
  LocalFolderTransport, pickFolder, folderFromToken,
  makeResolver, loadSettings, saveSettings,
} = require("./transport");
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
    log(`Media root: ${picked.path}`);
  }
});

$("pick-jobs").addEventListener("click", async () => {
  const picked = await pickFolder("jobs folder");
  if (picked) {
    state.settings = saveSettings({ jobsToken: picked.token });
    await refreshSetup();
    await refreshPlans();
    log(`Jobs folder: ${picked.path}`);
  }
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
    log("Panel ready.");
  } catch (err) {
    // Surfacing this in the panel matters: a throw during init leaves every
    // button inert with nothing in the UXP log to explain why.
    log(`Init failed: ${err && err.message ? err.message : String(err)}`, "err");
  }
})();
