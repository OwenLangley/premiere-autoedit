"use strict";
/**
 * Frame/second conversion. Mirrors engine/autoedit/timebase.py exactly --
 * if the two ever disagree, clips land on the wrong frame and nothing in the
 * pipeline notices.
 */

/** @typedef {{ fpsNum: number, fpsDen: number, dropFrame?: boolean }} Timebase */

/** @param {Timebase} tb @param {number} seconds @returns {number} */
function toFrames(tb, seconds) {
  // floor(x + 0.5) rather than Math.round, to match Python's behaviour on .5
  // exactly. Math.round rounds -0.5 toward zero; this does not.
  return Math.floor((seconds * tb.fpsNum) / tb.fpsDen + 0.5);
}

/** @param {Timebase} tb @param {number} frames @returns {number} */
function toSeconds(tb, frames) {
  return (frames * tb.fpsDen) / tb.fpsNum;
}

/** @param {Timebase} tb @returns {number} */
function frameDuration(tb) {
  return tb.fpsDen / tb.fpsNum;
}

/** @param {Timebase} tb @returns {number} */
function fps(tb) {
  return tb.fpsNum / tb.fpsDen;
}

/** @param {Timebase} tb @param {number} seconds @returns {number} */
function snap(tb, seconds) {
  return toSeconds(tb, toFrames(tb, seconds));
}

/**
 * Frames -> HH:MM:SS:FF, for display only.
 * @param {Timebase} tb @param {number} frames @returns {string}
 */
function timecode(tb, frames) {
  const nominal = Math.round(fps(tb));
  let f = frames;
  if (tb.dropFrame && (nominal === 30 || nominal === 60)) {
    const drop = nominal === 30 ? 2 : 4;
    const per10 = nominal * 600 - 9 * drop;
    const per1 = nominal * 60 - drop;
    const blocks = Math.floor(frames / per10);
    const rem = frames % per10;
    f += 9 * drop * blocks;
    if (rem >= drop) f += drop * Math.floor((rem - drop) / per1);
  }
  const ff = f % nominal;
  const total = Math.floor(f / nominal);
  const pad = (n) => String(n).padStart(2, "0");
  const sep = tb.dropFrame ? ";" : ":";
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}${sep}${pad(ff)}`;
}

module.exports = { toFrames, toSeconds, frameDuration, fps, snap, timecode };
