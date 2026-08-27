"use strict";
/**
 * Brand kit: resolves the abstract names in an EditPlan onto real files and
 * parameter indices.
 *
 * The awkward part this exists to manage: Premiere addresses MOGRT and effect
 * parameters by **zero-based index**, and the order is defined by the template
 * itself. There is no lookup by field name. So a plan that says
 * `{ name: "Jane Doe" }` cannot be applied without a name -> index map, and that
 * map is only valid for the exact template build it was generated from.
 *
 * Re-export a MOGRT with its fields reordered and every value silently lands in
 * the wrong slot. `discoverMogrtParams` regenerates the map; `verifyBrandkit`
 * checks the files still exist before an apply depends on them.
 */

const ppro = require("premierepro");
const fs = require("uxp").storage.localFileSystem;
const { indexProjectMedia, normalizePath } = require("./apply");

/**
 * Insert a MOGRT on a scratch sequence, enumerate its parameters, and return a
 * name -> index map to paste into brandkit.json.
 *
 * Run this once per template, and again after any re-export.
 *
 * @param {string} mogrtPath
 * @returns {Promise<{params: Record<string, number>, order: {index:number,name:string}[]}>}
 */
async function discoverMogrtParams(mogrtPath) {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("open a project first");

  const sequence = await project.createSequence("AutoEdit MOGRT probe");
  const editor = ppro.SequenceEditor.getEditor(sequence);

  const inserted = editor.insertMogrtFromPath(
    mogrtPath, ppro.TickTime.TIME_ZERO, 0, -1
  );
  if (!inserted || !inserted.length) {
    throw new Error(`Premiere did not insert ${mogrtPath} -- check the path and that it opens by hand`);
  }

  const chain = await inserted[0].getComponentChain();
  const count = chain.getComponentCount();

  /** @type {{index:number,name:string}[]} */
  const order = [];
  /** @type {Record<string, number>} */
  const params = {};

  for (let c = 0; c < count; c++) {
    const component = chain.getComponentAtIndex(c);
    const matchName = await component.getMatchName();
    if (!matchName || matchName.indexOf("MGT") === -1) continue;

    const paramCount = component.getParamCount();
    for (let i = 0; i < paramCount; i++) {
      let label = `param${i}`;
      try {
        label = component.getParam(i).displayName || label;
      } catch {
        /* some slots are structural and have no display name */
      }
      order.push({ index: i, name: label });
      // First occurrence wins: duplicate display names exist in some templates,
      // and the earlier one is the editable field.
      if (!(label in params)) params[label] = i;
    }
  }
  return { params, order };
}

/**
 * Enumerate an effect's parameters, so a LUT can be pointed at a slot rather
 * than guessed into one.
 *
 * The same problem `discoverMogrtParams` exists for, from the other direction.
 * A plan names a look -- `brand-punch` -- and the brand kit knows which .cube
 * that is, but Premiere's Lumetri exposes its controls as a flat, zero-based
 * list with no lookup by name. Until that list is known on a given build,
 * "apply the LUT" cannot be written, only guessed at, and a wrong index either
 * throws or quietly sets some unrelated control.
 *
 * Returns everything: index, display name, and whether the slot would take a
 * string. A LUT slot is the plausible one that accepts a path.
 *
 * @param {string} matchName e.g. "AE.ADBE Lumetri"
 * @param {string} [mediaPath] a clip to hang the effect on; any video will do
 */
async function discoverEffectParams(matchName, mediaPath) {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error("open a project first");
  if (!mediaPath) throw new Error("a clip path is needed to attach the effect to");

  const made = await ppro.VideoFilterFactory.createComponent(matchName);
  if (!made) throw new Error(`${matchName} is not installed on this machine`);

  // The effect has to be ON something before it will talk about its parameters.
  // A component straight from the factory does not expose them -- the typings
  // say so and the MOGRT probe next door already works this way, inserting
  // first and reading the chain afterwards. So: scratch sequence, one clip,
  // append the effect, then read it back out of the chain it now belongs to.
  const sequence = await project.createSequence(`AutoEdit effect probe ${matchName}`);
  const editor = ppro.SequenceEditor.getEditor(sequence);
  const index = await indexProjectMedia(project);
  const item = index.get(normalizePath(mediaPath));
  if (!item) throw new Error(`probe media is not in the project: ${mediaPath}`);

  await project.executeTransaction((compound) => {
    compound.addAction(
      editor.createOverwriteItemAction(item, ppro.TickTime.TIME_ZERO, 0, 0)
    );
  }, "AutoEdit: effect probe");

  const track = await sequence.getVideoTrack(0);
  const placed = (track && track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false)) || [];
  if (!placed.length) throw new Error("the probe clip did not land on the timeline");

  const chain = await placed[0].getComponentChain();
  await project.executeTransaction((compound) => {
    compound.addAction(chain.createAppendComponentAction(made));
  }, "AutoEdit: effect probe");

  // Find it again by match name: appending returns nothing useful, and the
  // chain already holds the clip's own intrinsic components.
  let component = null;
  const total = chain.getComponentCount();
  for (let c = 0; c < total; c += 1) {
    const candidate = chain.getComponentAtIndex(c);
    if (String(await candidate.getMatchName()) === matchName) component = candidate;
  }
  if (!component) throw new Error(`${matchName} did not attach to the probe clip`);

  const count = component.getParamCount();
  /** @type {{index:number,name:string,type:string|null,sample:any}[]} */
  const order = [];
  /** @type {Record<string, number>} */
  const params = {};

  for (let i = 0; i < count; i++) {
    let label = `param${i}`;
    let type = null;
    let sample = null;
    try {
      const param = component.getParam(i);
      label = param.displayName || label;
      // There is no getParamType, so read the value instead -- which is better
      // evidence in any case. A LUT slot holds a string; a slider holds a
      // number. `createKeyframe` takes strings, so a string slot is settable.
      const value = await param.getValueAtTime(ppro.TickTime.TIME_ZERO);
      type = typeof value;
      sample = type === "string" ? String(value).slice(0, 80) : value;
    } catch {
      /* structural slots have neither a display name nor a readable value */
    }
    order.push({ index: i, name: label, type, sample });
    if (!(label in params)) params[label] = i;
  }
  return { matchName, count, params, order };
}

/**
 * Check every file a plan will reach for actually exists, before the apply
 * starts creating things.
 * @param {any} brandkit
 * @param {any} plan
 * @returns {Promise<string[]>} problems, empty when everything resolves
 */
async function verifyBrandkit(brandkit, plan) {
  /** @type {string[]} */
  const problems = [];
  if (!brandkit) {
    if ((plan.graphics || []).length || (plan.effects || []).some((e) => e.lut)) {
      return ["no brand kit is configured, but this plan uses graphics or LUTs"];
    }
    return problems;
  }

  const exists = async (path) => {
    try {
      const entry = await fs.getEntryWithUrl(`file://${path}`);
      return !!entry;
    } catch {
      return false;
    }
  };

  for (const g of plan.graphics || []) {
    const entry = (brandkit.mogrts || {})[g.mogrt];
    if (!entry) {
      problems.push(`MOGRT "${g.mogrt}" is not in the brand kit`);
      continue;
    }
    if (!(await exists(entry.path))) {
      problems.push(`MOGRT "${g.mogrt}" file is missing: ${entry.path}`);
    }
    for (const field of Object.keys(g.fields || {})) {
      if (!entry.params || entry.params[field] === undefined) {
        problems.push(
          `MOGRT "${g.mogrt}" has no known param index for field "${field}" -- ` +
          `run discoverMogrtParams and update brandkit.json`
        );
      }
    }
  }

  for (const e of plan.effects || []) {
    if (!e.lut) continue;
    const lut = (brandkit.luts || {})[e.lut];
    if (!lut) problems.push(`LUT "${e.lut}" is not in the brand kit`);
    else if (!(await exists(lut.path))) problems.push(`LUT "${e.lut}" file is missing: ${lut.path}`);
  }
  return problems;
}

module.exports = { discoverMogrtParams, discoverEffectParams, verifyBrandkit };
