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

module.exports = { discoverMogrtParams, verifyBrandkit };
