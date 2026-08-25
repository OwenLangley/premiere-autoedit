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


// --- exact tick arithmetic -------------------------------------------------
//
// Premiere counts time in ticks, and every time value this panel handed it used
// to be built from a float number of seconds. 187/30 is 6.233333333333333 as a
// double, which is a hair under the real value, and a hair under a frame
// boundary truncates to the frame BELOW -- so clips landed one frame early and
// the joins showed as black. Ticks are integers; doing the arithmetic in BigInt
// means the value handed over is the one that was meant, exactly.

/** Rounded integer division, for non-negative values. @param {bigint} a @param {bigint} b */
function divRound(a, b) {
  return (a * 2n + b) / (2n * b);
}

/**
 * Exact tick count for a frame index on a given timebase.
 * @param {number|string|bigint} ticksPerSecond from `TickTime.TIME_ONE_SECOND.ticks`
 * @param {Timebase} tb @param {number} frames @returns {string}
 */
function ticksForFrames(ticksPerSecond, tb, frames) {
  const tps = BigInt(ticksPerSecond);
  const n = BigInt(Math.round(frames));
  return String(divRound(n * tps * BigInt(tb.fpsDen), BigInt(tb.fpsNum)));
}

/**
 * Exact tick count for a plain number of seconds, for sources with no frame
 * grid to land on -- a music bed is a waveform, not frames.
 * @param {number|string|bigint} ticksPerSecond @param {number} seconds @returns {string}
 */
function ticksForSeconds(ticksPerSecond, seconds) {
  // Plan times carry four decimal places, so microseconds is finer than the
  // data it is converting and nothing is lost in the scaling.
  const micros = BigInt(Math.round(seconds * 1e6));
  return String(divRound(micros * BigInt(ticksPerSecond), 1000000n));
}

module.exports = {
  toFrames, toSeconds, frameDuration, fps, snap, timecode,
  ticksForFrames, ticksForSeconds,
};
