"use strict";
const test = require("node:test");
const assert = require("node:assert");
const {
  isVideoFile, normaliseJobId, buildRequest, validateRequest, describeRequest,
  musicChoices, formatDuration,
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

// --- Music selection -------------------------------------------------------

test("music choices always offer automatic and none", () => {
  const choices = musicChoices([]);
  assert.deepEqual(choices.map((c) => c.value), ["auto", "none"]);
});

test("music choices list audio-only files and skip footage", () => {
  const choices = musicChoices([
    { name: "C1367.MP4", relPath: "C1367.MP4", hasVideo: true, hasAudio: true, durationSeconds: 13.5 },
    { name: "theme.wav", relPath: "Music/theme.wav", hasVideo: false, hasAudio: true, durationSeconds: 64.2 },
    { name: "silent.mp4", relPath: "silent.mp4", hasVideo: true, hasAudio: false, durationSeconds: 4 },
  ]);
  assert.deepEqual(choices.map((c) => c.value), ["auto", "none", "Music/theme.wav"]);
  assert.equal(choices[2].label, "Music/theme · 1:04");
});

test("a music file exported as .mp4 is still offered as music", () => {
  // The real one: a track delivered as .mp4 with no video stream. Extension
  // cannot tell; the index's probe can.
  const choices = musicChoices([
    { name: "million dollar baby.mp4", relPath: "million dollar baby.mp4",
      hasVideo: false, hasAudio: true, durationSeconds: 60 },
  ]);
  assert.equal(choices[2].value, "million dollar baby.mp4");
  assert.equal(choices[2].label, "million dollar baby · 1:00");
});

test("automatic is not labelled with a guessed track", () => {
  // The engine's auto search is top-level only while this list recurses, so a
  // label naming the file would sometimes name one auto would never pick.
  const choices = musicChoices([
    { name: "a.wav", relPath: "Music/a.wav", hasVideo: false, hasAudio: true, durationSeconds: 30 },
  ]);
  assert.equal(choices[0].label, "Automatic");
});

test("a chosen track reaches the request and the summary", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"],
    music: "Music/theme.wav",
  });
  assert.equal(request.options.music, "Music/theme.wav");
  assert.match(describeRequest(request, {}), /music: Music\/theme/);
});

test("automatic music is not mentioned in the summary", () => {
  const request = buildRequest({ jobId: "EP001", recipe: "social-short", media: ["a.mp4"] });
  assert.equal(request.options.music, "auto");
  assert.doesNotMatch(describeRequest(request, {}), /music/);
});

test("no music is still called out in the summary", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"], music: "none",
  });
  assert.match(describeRequest(request, {}), /no music/);
});

test("a non-string music value is rejected", () => {
  const request = buildRequest({ jobId: "EP001", recipe: "social-short", media: ["a.mp4"] });
  // Deliberately the wrong shape -- the point is that validation catches it.
  request.options.music = /** @type {any} */ ({ path: "theme.wav" });
  assert.match(validateRequest(request).join(" "), /Music must be/);
});

test("durations round to the nearest second", () => {
  assert.equal(formatDuration(0), "0:00");
  assert.equal(formatDuration(9.4), "0:09");
  assert.equal(formatDuration(59.6), "1:00");
  assert.equal(formatDuration(605), "10:05");
});

// --- A music library outside the footage tree ------------------------------

test("library tracks are marked so the helper knows which root they hang off", () => {
  const choices = musicChoices([], [
    { relPath: "Upbeat/drive.mp3", hasVideo: false, hasAudio: true, durationSeconds: 132 },
  ]);
  assert.equal(choices[2].value, "library:Upbeat/drive.mp3");
  assert.equal(choices[2].label, "Upbeat/drive · 2:12");
});

test("library tracks come before ones sitting with the footage", () => {
  const choices = musicChoices(
    [{ relPath: "scratch.wav", hasVideo: false, hasAudio: true, durationSeconds: 10 }],
    [{ relPath: "drive.mp3", hasVideo: false, hasAudio: true, durationSeconds: 132 }],
  );
  assert.deepEqual(choices.map((c) => c.value), ["auto", "none", "library:drive.mp3", "scratch.wav"]);
  // Only distinguished once there is something to distinguish from.
  assert.match(choices[3].label, /with the footage/);
});

test("the library marker is not shown to the editor", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"],
    music: "library:Upbeat/drive.mp3",
  });
  assert.equal(request.options.music, "library:Upbeat/drive.mp3");
  assert.match(describeRequest(request, {}), /music: Upbeat\/drive/);
  assert.doesNotMatch(describeRequest(request, {}), /library:/);
});

test("video in the library is not offered as a track", () => {
  const choices = musicChoices([], [
    { relPath: "promo.mp4", hasVideo: true, hasAudio: true, durationSeconds: 30 },
  ]);
  assert.equal(choices.length, 2);
});
