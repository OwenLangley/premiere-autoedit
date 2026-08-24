"use strict";
const test = require("node:test");
const assert = require("node:assert");
const tb = require("../src/timebase");

const R2398 = { fpsNum: 24000, fpsDen: 1001 };
const R25 = { fpsNum: 25, fpsDen: 1 };
const R2997DF = { fpsNum: 30000, fpsDen: 1001, dropFrame: true };

test("frame roundtrip is stable across a long timeline", () => {
  for (let f = 0; f < 200000; f += 997) {
    assert.strictEqual(tb.toFrames(R2398, tb.toSeconds(R2398, f)), f);
  }
});

test("rounds rather than truncates", () => {
  assert.strictEqual(tb.toFrames(R25, 0.0399), 1);
  assert.strictEqual(tb.toFrames(R25, 0.0), 0);
});

test("snap lands on a frame boundary", () => {
  const snapped = tb.snap(R25, 1.017);
  assert.ok(Math.abs(snapped / 0.04 - Math.round(snapped / 0.04)) < 1e-9);
});

test("drop-frame timecode matches SMPTE", () => {
  assert.strictEqual(tb.timecode(R2997DF, 0), "00:00:00;00");
  assert.strictEqual(tb.timecode(R2997DF, 107892), "01:00:00;00");
  assert.strictEqual(tb.timecode(R2997DF, 1800), "00:01:00;02");
});

test("non-drop-frame timecode uses a colon", () => {
  assert.strictEqual(tb.timecode(R25, 1501), "00:01:00:01");
});
