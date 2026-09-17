"use strict";
/**
 * What the progress bar should say, decided where it can be tested.
 *
 * Pulled out for the same reason `update.js` and `version.js` were: drawing the
 * bar needs UXP -- a folder token, a real status file, a helper writing to it --
 * and none of that can be exercised in a test. The DECISION can, and the
 * decision is the part with rules in it.
 *
 * **Why a bar and not a spinner.** The request was "a loading bar or circle".
 * A circle has to spin, spinning needs `@keyframes`, and this panel already
 * carries a note about that (`index.html`, the media-list "working" style): the
 * failure mode of an animation UXP has not implemented is a shape that sits
 * perfectly still while claiming to be progress -- the precise thing an editor
 * would misread as a hang. A bar whose width is set from JS on every poll
 * cannot fail that way. If the panel stops polling the bar stops moving, which
 * is true.
 *
 * **The bar is a position, not a prediction.** `percent` comes from the engine
 * weighted by the seconds of video each part of the run has to decode
 * (`engine/autoedit/progress.py`). It is approximate and it says so by not
 * offering an estimated finish time, which the data does not support.
 *
 * **Quiet is reported, not diagnosed.** The panel cannot tell a dead helper
 * from a slow ffmpeg pass; both look like a status file that has stopped
 * changing. So it says how long it has been since the last word and leaves the
 * conclusion to the person, rather than announcing a stall that may not be one.
 */

const { formatDuration } = require("./request");

/**
 * How long without a status write before the panel mentions it.
 *
 * Deliberately generous. The engine reports between ffmpeg passes and never
 * inside one -- ffmpeg cannot tell us where it is in these filter chains, which
 * is measured in progress.py -- so an ordinary gap is however long one pass
 * takes. On a 27-minute 480p reference that is tens of seconds; on 4K it is
 * minutes. A threshold tight enough to catch a hang quickly would cry wolf on
 * every large file, and a progress display that cries wolf is worse than none.
 */
const QUIET_SECONDS = 300;

/**
 * What the panel should draw. Named so `main.js` can annotate the function that
 * draws it: an un-annotated parameter is `any` under this tsconfig, and a typo
 * on an `any` is exactly the mistake that once shipped an empty dropdown.
 *
 * @typedef {{visible: boolean, percent: number|null, stepKey: string,
 *            detail: string, elapsed: string, quiet: boolean,
 *            quietFor: string}} ProgressView
 */

/**
 * @param {object|null} status  the parsed `<job>.status.json`, or null
 * @param {number} nowMs        Date.now(), passed in so this stays pure
 * @param {number} [requestedAtMs]  when the panel wrote the request. Without a
 *   status file there is nothing else to go on, and something has to be said:
 *   the helper does one thing at a time, so a request routinely waits minutes
 *   behind a running job before it is even read. That is the mistake the Update
 *   button made -- it called a busy helper a missing one.
 * @returns {ProgressView}
 */
function progressView(status, nowMs, requestedAtMs) {
  const hidden = {
    visible: false, percent: null, stepKey: "", detail: "",
    elapsed: "", quiet: false, quietFor: "",
  };
  const elapsedSince = (ms) => Math.max(0, (nowMs - ms) / 1000);

  if (!status) {
    if (!Number.isFinite(requestedAtMs)) return hidden;
    const waited = elapsedSince(Number(requestedAtMs));
    return {
      visible: true,
      // Not nought: nought is a position, and no position is known yet.
      percent: null,
      stepKey: "progress.queued",
      detail: "",
      elapsed: formatDuration(waited),
      quiet: waited >= QUIET_SECONDS,
      quietFor: formatDuration(waited),
    };
  }
  // Finished, either way. The log says what happened; a bar left on screen at
  // 100% is just furniture.
  if (status.state === "ready" || status.state === "failed") return hidden;

  const percent = Number.isFinite(Number(status.percent))
    ? Math.min(Math.max(Number(status.percent), 0), 100)
    : null;

  const since = (iso) => {
    const at = Date.parse(iso || "");
    if (!Number.isFinite(at)) return null;
    // Never negative. The helper's clock and the panel's are the same clock
    // here, but a status file written a moment in the future should read as
    // "just now" rather than as a countdown.
    return Math.max(0, (nowMs - at) / 1000);
  };

  const running = since(status.startedAt);
  const waiting = since(status.updatedAt);

  return {
    visible: true,
    percent,
    stepKey: String(status.step || ""),
    detail: String(status.stepDetail || ""),
    elapsed: running === null ? "" : formatDuration(running),
    quiet: waiting !== null && waiting >= QUIET_SECONDS,
    quietFor: waiting === null ? "" : formatDuration(waiting),
  };
}

module.exports = { progressView, QUIET_SECONDS };
