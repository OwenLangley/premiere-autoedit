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

/**
 * What the top-right button says, and what pressing it would do.
 *
 * One button carries the whole update story, so its label is the state rather
 * than a verb. Two wordings were available and both lie: a button reading
 * "Update to the latest version" says nothing about whether there is one --
 * which is why it was pressed out of curiosity and then needed a Premiere
 * restart -- and a button reading "Check for updates" that installs on the way
 * past is worse, because it takes an action nobody asked for.
 *
 * So checking and installing are two presses, and the label between them says
 * where things stand. `action` is what a press means; null is a button that is
 * busy, or one whose only remaining step belongs to the editor.
 *
 * @param {{check: any, phase?: "idle"|"checking"|"updating"|"restart"|"failed"}} state
 *   `check` the parsed update-check.result.json, or null if never asked
 *   `phase` what the panel is doing right now
 * @returns {{key: string, params: Record<string, any>, action: "check"|"update"|null,
 *            kind: ""|"ok"|"err", notice: boolean}}
 */
function updateButton(state) {
  // Annotated, or `kind` widens to `string` the moment it is spread and every
  // return below stops matching what this function promises. The panel has been
  // caught twice by a value that was implicitly `any`.
  /** @type {{params: Record<string, any>, action: null, kind: "", notice: boolean}} */
  const plain = { params: {}, action: null, kind: "", notice: false };
  switch (state.phase || "idle") {
    case "checking": return { ...plain, key: "update.checking" };
    case "updating": return { ...plain, key: "update.updating" };
    // Nothing else can be done from here: UXP loads the panel once at startup,
    // so the new code is on disk and unreachable until Premiere is restarted.
    // Saying "up to date" now would be true and useless.
    case "restart": return { ...plain, key: "update.restartNeeded", kind: "ok", notice: true };
    // Offering "Update available" again after a failure invites the same press
    // with no idea why the last one did not take.
    case "failed": return { ...plain, key: "update.failed", kind: "err", action: "update", notice: true };
  }
  const check = state.check;
  if (!check || !check.status) return { ...plain, key: "update.check", action: "check" };
  switch (check.status) {
    case "current":
      return { ...plain, key: "update.current", kind: "ok", action: "check" };
    case "behind":
      return {
        key: "update.available", params: { count: check.behind || 1 },
        action: "update", kind: "", notice: true,
      };
    // Not an error: an editing machine on a train, or one whose GitHub
    // credential has expired, is in a normal state and can still cut.
    case "unreachable":
      return { ...plain, key: "update.offline", action: "check" };
    default:
      return { ...plain, key: "update.checkFailed", kind: "err", action: "check" };
  }
}

/**
 * A commit subject, as a line in a panel rather than as git prints it.
 *
 * The hash goes: it is the one part of the line an editor cannot use, and at
 * 360px -- which is how this panel is usually docked -- it is the difference
 * between one wrapped line and two. Length capped for the same reason. The full
 * detail is in the log, where someone reporting a bug will look for it.
 *
 * @param {string} subject
 */
function shortSubject(subject) {
  const text = String(subject || "").replace(/^[0-9a-f]{7,40}\s+/i, "").trim();
  return text.length > 44 ? `${text.slice(0, 43).trimEnd()}...` : text;
}

/**
 * The line under the button, when there is something the label cannot hold.
 *
 * It exists because the log moved behind a button: a check that failed used to
 * explain itself in the log, and a drawer nobody opened would have swallowed
 * the explanation.
 *
 * @param {{check: any, phase?: string, detail?: string}} state
 * @returns {{key: string, params: Record<string, any>}|null}
 */
function updateNote(state) {
  if (state.phase === "failed") {
    return { key: "msg.updateFailed", params: { detail: state.detail || "" } };
  }
  if (state.phase === "restart") return { key: "msg.updateRestart", params: {} };
  if (state.phase === "checking" || state.phase === "updating") return null;
  const check = state.check;
  if (!check) return null;
  if (check.status === "behind") {
    const count = check.behind || 1;
    // The newest subject, because "3 changes" says nothing about whether they
    // matter. Two keys rather than one with an empty tail: a result without
    // subjects would otherwise read "3 changes waiting, latest:" and stop.
    // `sum.musicFrom` / `sum.musicFromFor` split for the same reason.
    const latest = shortSubject((check.subjects || [])[0]);
    const params = { count, plural: count === 1 ? "" : "s" };
    return latest
      ? { key: "update.behindNote", params: { ...params, latest } }
      : { key: "update.behindNoteCount", params };
  }
  if (check.status === "unreachable" || check.status === "failed") {
    return { key: "update.offlineNote", params: { detail: check.detail || "" } };
  }
  return null;
}

module.exports = { updateOutcome, updateButton, updateNote, shortSubject };
