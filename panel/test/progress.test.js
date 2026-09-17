"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { progressView, QUIET_SECONDS } = require("../src/progress");

const NOW = Date.parse("2026-09-17T12:00:00Z");
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString();

function status(extra) {
  return Object.assign({
    schemaVersion: "1.0", jobId: "EP001", state: "analysing",
    startedAt: ago(90), updatedAt: ago(2),
  }, extra);
}

test("a running job shows the bar, the stage and the clock", () => {
  const v = progressView(
    status({ percent: 42, step: "progress.scanShots", stepDetail: "C0001.MP4" }), NOW);
  assert.equal(v.visible, true);
  assert.equal(v.percent, 42);
  assert.equal(v.stepKey, "progress.scanShots");
  assert.equal(v.detail, "C0001.MP4");
  assert.equal(v.elapsed, "1:30");
  assert.equal(v.quiet, false);
});

test("a finished job hides the bar rather than parking it at 100%", () => {
  // The log already says it is ready. A full bar left on screen is furniture,
  // and a full bar on a FAILED job actively misreports what happened.
  for (const state of ["ready", "failed"]) {
    assert.equal(progressView(status({ state, percent: 100 }), NOW).visible, false);
  }
});

test("no status file yet still shows something, because the helper may be busy", () => {
  // The mistake the Update button made: a helper doing one thing at a time is
  // not a helper that is missing. A request can sit unread for minutes.
  const v = progressView(null, NOW, NOW - 30_000);
  assert.equal(v.visible, true);
  assert.equal(v.stepKey, "progress.queued");
  assert.equal(v.elapsed, "0:30");
});

test("no status file and no request time shows nothing at all", () => {
  assert.equal(progressView(null, NOW).visible, false);
  assert.equal(progressView(null, NOW, NaN).visible, false);
});

test("an unknown position is null, never nought", () => {
  // Nought is a claim -- "none of the work is done". A missing percent is the
  // absence of a claim, and the bar draws them differently.
  assert.equal(progressView(status({}), NOW).percent, null);
  assert.equal(progressView(status({ percent: "soon" }), NOW).percent, null);
  assert.equal(progressView(null, NOW, NOW).percent, null);
  assert.equal(progressView(status({ percent: 0 }), NOW).percent, 0);
});

test("a percent outside the range is clamped rather than drawn off the end", () => {
  assert.equal(progressView(status({ percent: 140 }), NOW).percent, 100);
  assert.equal(progressView(status({ percent: -5 }), NOW).percent, 0);
});

test("a long silence is reported as a fact, not diagnosed as a stall", () => {
  const v = progressView(status({ updatedAt: ago(QUIET_SECONDS + 60) }), NOW);
  assert.equal(v.quiet, true);
  assert.equal(v.quietFor, "6:00");
  // Still visible and still holding its position: a quiet job is not a dead
  // one, and the panel cannot tell which it is.
  assert.equal(v.visible, true);
});

test("an ordinary gap between ffmpeg passes is not called quiet", () => {
  // The engine reports between passes and never inside one, so a gap the length
  // of a pass is normal. A threshold that fired here would cry wolf on every
  // large file.
  assert.equal(progressView(status({ updatedAt: ago(QUIET_SECONDS - 1) }), NOW).quiet, false);
});

test("an unreadable timestamp is not an accusation", () => {
  const v = progressView(status({ updatedAt: "whenever", startedAt: "" }), NOW);
  assert.equal(v.quiet, false);
  assert.equal(v.elapsed, "");
});

test("a status written a moment in the future reads as just now", () => {
  const v = progressView(status({ startedAt: ago(-10), updatedAt: ago(-10) }), NOW);
  assert.equal(v.elapsed, "0:00");
  assert.equal(v.quiet, false);
});
