"use strict";
const test = require("node:test");
const assert = require("node:assert");
const {
  isVideoFile, normaliseJobId, buildRequest, validateRequest, describeRequest,
} = require("../src/request");

const form = (over = {}) => ({
  jobId: "EP001", recipe: "promo-silent", media: ["a.mp4"], ...over,
});

test("recognises video files and ignores sidecars", () => {
  assert.ok(isVideoFile("C1367.MP4"));
  assert.ok(isVideoFile("clip.mov"));
  assert.ok(!isVideoFile("track.wav"));
  assert.ok(!isVideoFile("._C1367.MP4"), "dot-underscore sidecars are not footage");
  assert.ok(!isVideoFile(".DS_Store"));
});

test("job ids are made safe for filenames and sequence names", () => {
  // Punctuation is stripped first, then runs of whitespace collapse -- so the
  // gap the slash leaves behind does not survive into the filename.
  assert.strictEqual(normaliseJobId("  EP 001 / final?  "), "EP 001 final");
  assert.strictEqual(normaliseJobId("a".repeat(200)).length, 64);
});

test("defaults are not written as explicit options", () => {
  const r = buildRequest(form());
  assert.strictEqual(r.options.aspect, "source");
  assert.strictEqual(r.options.pacing, "standard");
  assert.ok(!("duration" in r.options), "no duration block when there is no target");
  assert.ok(!("look" in r.options));
});

test("chosen options are carried through", () => {
  const r = buildRequest(form({
    aspect: "vertical", pacing: "punchy", look: "brand-punch",
    durationMode: "exactly", durationSeconds: 30, visual: true,
  }));
  assert.strictEqual(r.options.aspect, "vertical");
  assert.deepStrictEqual(r.options.duration, { mode: "exactly", seconds: 30 });
  assert.strictEqual(r.options.visual, true);
});

test("a duration mode of none drops the seconds", () => {
  const r = buildRequest(form({ durationMode: "none", durationSeconds: 30 }));
  assert.ok(!("duration" in r.options));
});

test("a complete form validates", () => {
  assert.deepStrictEqual(validateRequest(buildRequest(form())), []);
});

test("problems are phrased for an editor, not a log", () => {
  assert.ok(validateRequest(buildRequest(form({ jobId: "" })))[0].includes("Give the job a name"));
  assert.ok(validateRequest(buildRequest(form({ media: [] })))[0].includes("at least one clip"));
  assert.ok(validateRequest(buildRequest(form({ recipe: "" })))[0].includes("Choose a recipe"));
});

test("a nonsense length is rejected", () => {
  const r = buildRequest(form({ durationMode: "upTo", durationSeconds: 30 }));
  r.options.duration.seconds = 0;
  assert.ok(validateRequest(r).some((e) => e.includes("more than zero")));
});

test("the summary reads as a sentence an editor can check", () => {
  const caps = {
    aspects: [{ value: "vertical", label: "Vertical 9:16" }],
    pacing: [{ value: "punchy", label: "Punchy" }],
  };
  const r = buildRequest(form({
    media: ["a.mp4", "b.mp4"], aspect: "vertical", pacing: "punchy",
    durationMode: "upTo", durationSeconds: 30,
  }));
  assert.strictEqual(describeRequest(r, caps), "2 clips · Vertical 9:16 · up to 30s · punchy");
});

test("a plain job summarises without noise", () => {
  assert.strictEqual(describeRequest(buildRequest(form()), {}), "1 clip");
});
