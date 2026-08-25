"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { validatePlan, summarize, sections, withoutSections } = require("../src/plan");

function clip(over = {}) {
  return {
    mediaId: "A001", inSeconds: 0, outSeconds: 2,
    atFrame: 0, durationFrames: 50, videoTrack: 0, audioTrack: 0,
    fadeInFrames: 0, fadeOutFrames: 0, confidence: 0.9, ...over,
  };
}
function plan(over = {}) {
  return {
    schemaVersion: "1.0", jobId: "j1", recipe: "podcast-2cam",
    timebase: { fpsNum: 25, fpsDen: 1 },
    media: [{ id: "A001", relPath: "a/A001.mov", durationSeconds: 60 }],
    sequence: { name: "S", videoTracks: 2, audioTracks: 2 },
    timeline: [clip()], ...over,
  };
}

test("a well-formed plan validates", () => {
  assert.deepStrictEqual(validatePlan(plan()), []);
});

test("rejects a schema version this panel does not speak", () => {
  const errs = validatePlan(plan({ schemaVersion: "2.0" }));
  assert.ok(errs[0].includes("schema 2.0"));
});

test("rejects a dangling media reference", () => {
  const errs = validatePlan(plan({ timeline: [clip({ mediaId: "GHOST" })] }));
  assert.ok(errs.some((e) => e.includes("unknown media")));
});

test("rejects reading past the end of the source", () => {
  const errs = validatePlan(plan({ timeline: [clip({ outSeconds: 999 })] }));
  assert.ok(errs.some((e) => e.includes("past the end")));
});

test("rejects overlapping clips on one track", () => {
  const errs = validatePlan(plan({
    timeline: [clip(), clip({ atFrame: 10, inSeconds: 3, outSeconds: 5 })],
  }));
  assert.ok(errs.some((e) => e.includes("overlaps")));
});

test("allows the same frame range on different tracks", () => {
  const errs = validatePlan(plan({
    timeline: [clip(), clip({ videoTrack: 1, inSeconds: 3, outSeconds: 5 })],
  }));
  assert.deepStrictEqual(errs, []);
});

test("summarize reports duration and confidence", () => {
  const s = summarize(plan({ timeline: [clip(), clip({ atFrame: 50, confidence: 0.5 })] }));
  assert.strictEqual(s.clipCount, 2);
  assert.strictEqual(s.durationFrames, 100);
  assert.strictEqual(s.timecode, "00:00:04:00");
  assert.strictEqual(s.lowConfidence, 1);
});

test("sections group entries and label the unnamed one", () => {
  const s = sections(plan({
    timeline: [clip(), clip({ atFrame: 50, sectionId: "outro" })],
  }));
  assert.deepStrictEqual(s.map((x) => x.id).sort(), ["", "outro"]);
  assert.strictEqual(s.find((x) => x.id === "").label, "Rough cut");
});

test("dropping a section ripples the timeline closed", () => {
  const p = plan({
    timeline: [
      clip({ atFrame: 0, sectionId: "intro", durationFrames: 25 }),
      clip({ atFrame: 25, durationFrames: 50, inSeconds: 3, outSeconds: 5 }),
    ],
  });
  const out = withoutSections(p, ["intro"]);
  assert.strictEqual(out.timeline.length, 1);
  assert.strictEqual(out.timeline[0].atFrame, 0, "must close the gap, not leave black");
  assert.deepStrictEqual(validatePlan(out), []);
});

test("dropping a section moves its markers with it", () => {
  const p = plan({
    timeline: [
      clip({ atFrame: 0, sectionId: "intro", durationFrames: 25 }),
      clip({ atFrame: 25, durationFrames: 50, inSeconds: 3, outSeconds: 5 }),
    ],
    markers: [{ atFrame: 30, name: "later" }],
  });
  const out = withoutSections(p, ["intro"]);
  assert.strictEqual(out.markers[0].atFrame, 5, "marker must follow its clip");
});

test("dropping every section yields an empty but coherent timeline", () => {
  const p = plan({ timeline: [clip({ sectionId: "a" })] });
  const out = withoutSections(p, ["a"]);
  assert.strictEqual(out.timeline.length, 0);
});

test("first and last fades are cleared after a ripple", () => {
  const p = plan({
    timeline: [
      clip({ atFrame: 0, sectionId: "intro", durationFrames: 25 }),
      clip({ atFrame: 25, durationFrames: 25, inSeconds: 3, outSeconds: 5, fadeInFrames: 2, fadeOutFrames: 2 }),
      clip({ atFrame: 50, durationFrames: 25, inSeconds: 6, outSeconds: 8, fadeInFrames: 2, fadeOutFrames: 2 }),
    ],
  });
  const out = withoutSections(p, ["intro"]);
  assert.strictEqual(out.timeline[0].fadeInFrames, 0);
  assert.strictEqual(out.timeline.at(-1).fadeOutFrames, 0);
});

test("an audio-only clip does not collide on a video track", () => {
  const p = plan({
    timeline: [
      clip(),
      clip({ mediaId: "A001", videoTrack: -1, audioTrack: 1, inSeconds: 0, outSeconds: 5 }),
    ],
  });
  assert.deepStrictEqual(validatePlan(p), []);
});

test("a clip on neither track is rejected", () => {
  const errs = validatePlan(plan({ timeline: [clip({ videoTrack: -1, audioTrack: -1 })] }));
  assert.ok(errs.some((e) => e.includes("neither a video nor an audio track")));
});
