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

// --- exact ticks -----------------------------------------------------------
//
// Premiere counts in ticks. Building a time value from a float number of seconds
// lands a tick below the true boundary on about one frame in thirty, and a tick
// below a frame boundary is a whole frame below it once Premiere aligns -- which
// is a black flash on screen between two shots.

const TPS = 254016000000n; // the tick rate Premiere reports; asserted below

const R30 = { fpsNum: 30, fpsDen: 1 };
const R5994 = { fpsNum: 60000, fpsDen: 1001 };
const R2997 = { fpsNum: 30000, fpsDen: 1001 };

test("the tick rate divides every broadcast frame rate exactly", () => {
  // This is why the constant is the number it is, and why exact tick arithmetic
  // is possible at all rather than merely more precise.
  for (const r of [R30, R25, R5994, R2997, R2398]) {
    assert.strictEqual((TPS * BigInt(r.fpsDen)) % BigInt(r.fpsNum), 0n,
      `${r.fpsNum}/${r.fpsDen} does not divide the tick rate`);
  }
});

test("a frame index converts to ticks and back without loss", () => {
  for (const r of [R30, R25, R5994, R2997, R2398]) {
    for (let f = 0; f < 40000; f += 331) {
      const ticks = BigInt(tb.ticksForFrames(TPS, r, f));
      assert.strictEqual(Number((ticks * BigInt(r.fpsNum)) / (TPS * BigInt(r.fpsDen))), f);
    }
  }
});

test("the float path really does fall short, which is the bug being fixed", () => {
  // Not a hypothetical: count the frames where seconds-as-a-double truncates
  // below the exact tick. If this ever comes back zero the helper is pointless
  // and should go -- but on this arithmetic it does not.
  let short = 0;
  for (let f = 0; f < 20000; f++) {
    const exact = BigInt(tb.ticksForFrames(TPS, R5994, f));
    const viaFloat = BigInt(Math.trunc(tb.toSeconds(R5994, f) * Number(TPS)));
    if (viaFloat < exact) short++;
  }
  assert.ok(short > 100, `expected the float path to fall short often, saw ${short}`);
});

test("seconds convert to ticks for sources with no frame grid", () => {
  assert.strictEqual(tb.ticksForSeconds(TPS, 1), String(TPS));
  assert.strictEqual(tb.ticksForSeconds(TPS, 0), "0");
  assert.strictEqual(tb.ticksForSeconds(TPS, 2.5), String((TPS * 5n) / 2n));
});

test("ticks are exact past the point where seconds-as-a-double are not", () => {
  // An hour in, a double still has plenty of room; this is about the arithmetic
  // being exact by construction rather than by staying inside a safe range.
  const anHour = tb.ticksForFrames(TPS, R5994, 215784);
  assert.strictEqual(anHour, String((TPS * 215784n * 1001n) / 60000n));
});
