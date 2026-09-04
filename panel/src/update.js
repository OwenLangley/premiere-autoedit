"use strict";
/**
 * What to tell an editor who pressed Update.
 *
 * Pulled out of main.js for the same reason `version.js` was pulled out of
 * apply.js: the wait itself needs UXP -- a folder token, a real jobs folder, a
 * helper running beside it -- and none of that can be exercised in a test. The
 * DECISION can, and it is the part that was wrong.
 *
 * It was wrong in a specific way. The panel waited sixty seconds for a result
 * file and, not finding one, said the background helper was not running. The
 * helper handles one thing at a time: while it is analysing footage or indexing
 * a library it cannot look at its folder at all, so a request routinely sits
 * unread for minutes. An editor was sent to `setup.sh --check` to diagnose a
 * helper that was working perfectly and merely busy.
 *
 * The two cases are distinguishable, because the helper deletes `update.request`
 * the moment it picks it up. If the marker is still there, nothing has read it.
 * If it is gone and no result has appeared, the update is running.
 */

/**
 * @param {{result: object|null, pickedUp: boolean, waited: boolean}} state
 *   `result`   the parsed update.result.json, or null
 *   `pickedUp` true once `update.request` has been consumed by the helper
 *   `waited`   true once the whole wait has elapsed
 * @returns {{key: string, kind: "ok"|"err"|"", detail?: string}}
 */
function updateOutcome(state) {
  const { result, pickedUp, waited } = state;

  if (result) {
    const detail = result.detail || result.status || "";
    if (result.status === "updated") return { key: "msg.updateDone", kind: "ok", detail };
    if (result.status === "current") return { key: "msg.updateCurrent", kind: "ok", detail };
    return { key: "msg.updateFailed", kind: "err", detail };
  }
  if (!waited) return { key: "", kind: "" };            // still waiting; say nothing
  if (!pickedUp) return { key: "msg.updateNeverHeard", kind: "err" };
  return { key: "msg.updateStillRunning", kind: "err" };
}

module.exports = { updateOutcome };
