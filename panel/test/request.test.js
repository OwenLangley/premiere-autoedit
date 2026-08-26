"use strict";
const test = require("node:test");
const assert = require("node:assert");
const {
  isVideoFile, normaliseJobId, buildRequest, validateRequest, describeRequest,
  musicChoices, formatDuration, parseMusicValue, parseTimecode, formatTimecode, parseSeconds,
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

test("problems come back as keys, so the form can be translated", () => {
  // Prose here would have made the form the one untranslatable part of the
  // panel -- and the form is where a Japanese editor spends all their time.
  assert.deepEqual(validateRequest(buildRequest(form({ jobId: "" }))), ["err.nameRequired"]);
  assert.deepEqual(validateRequest(buildRequest(form({ media: [] }))), ["err.clipsRequired"]);
  assert.deepEqual(validateRequest(buildRequest(form({ recipe: "" }))), ["err.recipeRequired"]);
});

test("every error key has a translation in both languages", () => {
  const { EN, JA } = require("../src/i18n");
  const cases = [
    form({ jobId: "" }), form({ media: [] }), form({ recipe: "" }),
    form({ jobId: ".hidden" }),
  ];
  for (const f of cases) {
    for (const key of validateRequest(buildRequest(f))) {
      assert.ok(EN[key], `no English for ${key}`);
      assert.ok(JA[key], `no Japanese for ${key}`);
    }
  }
});

test("a nonsense length is rejected", () => {
  const r = buildRequest(form({ durationMode: "upTo", durationSeconds: 30 }));
  r.options.duration.seconds = 0;
  assert.ok(validateRequest(r).includes("err.lengthPositive"));
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
  assert.strictEqual(describeRequest(r, caps), "2 clips · Vertical 9:16 · up to 30s · Punchy");
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
  assert.ok(validateRequest(request).includes("err.musicType"));
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

// --- Choosing part of a track ----------------------------------------------

test("a music value splits back into a path and its root", () => {
  assert.deepEqual(parseMusicValue("library:Upbeat/drive.mp3"),
    { kind: "track", relPath: "Upbeat/drive.mp3", root: "music" });
  assert.deepEqual(parseMusicValue("theme.wav"),
    { kind: "track", relPath: "theme.wav", root: "media" });
  assert.equal(parseMusicValue("auto").kind, "auto");
  assert.equal(parseMusicValue("none").kind, "none");
  assert.equal(parseMusicValue("").kind, "auto");
});

test("parseMusicValue inverts musicChoices for both roots", () => {
  const choices = musicChoices(
    [{ relPath: "scratch.wav", hasVideo: false, hasAudio: true, durationSeconds: 10 }],
    [{ relPath: "Upbeat/drive.mp3", hasVideo: false, hasAudio: true, durationSeconds: 132 }],
  );
  assert.deepEqual(parseMusicValue(choices[2].value),
    { kind: "track", relPath: "Upbeat/drive.mp3", root: "music" });
  assert.deepEqual(parseMusicValue(choices[3].value),
    { kind: "track", relPath: "scratch.wav", root: "media" });
});

test("timecode is read the way an editor types it", () => {
  assert.equal(parseTimecode("1:23.5"), 83.5);
  assert.equal(parseTimecode("0:42.4"), 42.4);
  assert.equal(parseTimecode("83.5"), 83.5);
  assert.equal(parseTimecode("1:02:03"), 3723);
  assert.equal(parseTimecode(42.4), 42.4);
});

test("an unreadable timecode is null, never a silent zero", () => {
  // Treating a typo as 0:00 would score the promo from the intro and say nothing.
  for (const bad of ["", "  ", "abc", "1:2:3:4", "-5", "1:234"]) {
    assert.equal(parseTimecode(bad), null, `expected null for ${JSON.stringify(bad)}`);
  }
});

test("timecode round-trips to tenths", () => {
  for (const seconds of [0, 9, 42.4, 125.7]) {
    assert.equal(parseTimecode(formatTimecode(seconds)), seconds);
  }
});

test("a chunk reaches the request and the summary", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"],
    music: "library:Upbeat/drive.mp3", musicStart: 42.4, musicLength: 20,
  });
  assert.deepEqual(request.options.musicChunk, { startSeconds: 42.4, lengthSeconds: 20 });
  assert.match(describeRequest(request, {}), /music: Upbeat\/drive from 0:42\.4 for 20s/);
});

test("no chunk is sent when the start and length are untouched", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"],
    music: "drive.mp3", musicStart: 0, musicLength: 0,
  });
  assert.equal(request.options.musicChunk, undefined);
});

test("a chunk is dropped when no track is chosen", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"],
    music: "auto", musicStart: 42.4,
  });
  assert.equal(request.options.musicChunk, undefined);
});

test("a chunk against automatic is rejected if it gets that far", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"], music: "auto",
  });
  request.options.musicChunk = { startSeconds: 42.4 };
  assert.ok(validateRequest(request).includes("err.musicNeedsTrack"));
});

test("a negative length is rejected", () => {
  const request = buildRequest({
    jobId: "EP001", recipe: "social-short", media: ["a.mp4"], music: "drive.mp3",
  });
  request.options.musicChunk = { lengthSeconds: -3 };
  assert.ok(validateRequest(request).includes("err.musicLength"));
});

test("an empty length field means 'follow the edit', not zero seconds", () => {
  // UXP hands back the literal string "nan" for an empty number input, which is
  // how "nan" ended up rendered in the Length field on screen.
  for (const empty of ["", "  ", "nan", "NaN", undefined, null, "abc", "0", "-4"]) {
    assert.equal(parseSeconds(empty), 0, `expected 0 for ${JSON.stringify(empty)}`);
  }
  assert.equal(parseSeconds("20"), 20);
  assert.equal(parseSeconds(" 12.5 "), 12.5);
  assert.equal(parseSeconds(20), 20);
});

// --- the cut-rate dial ------------------------------------------------------
//
// Pacing was the only control over how often the picture changes, and it is
// three coarse steps that also move silence and clip-length thresholds. This is
// the direct dial: beats per shot, in musical units so it means the same thing
// whatever the track's tempo.

test("no choice means no option, so the recipe's pacing still decides", () => {
  const r = buildRequest({ jobId: "EP1", recipe: "social-short", media: ["a.mp4"] });
  assert.strictEqual(r.options.cutRate, undefined);
});

test("a chosen rate reaches the request as a number", () => {
  const r = buildRequest({ jobId: "EP1", recipe: "social-short", media: ["a.mp4"], cutRate: "2" });
  assert.strictEqual(r.options.cutRate, 2);
});

test("an empty or nonsense value is treated as no choice", () => {
  for (const v of ["", "auto", null, undefined, "0", "-1"]) {
    const r = buildRequest({ jobId: "EP1", recipe: "social-short", media: ["a.mp4"], cutRate: v });
    assert.strictEqual(r.options.cutRate, undefined, `${JSON.stringify(v)} should mean "follow pacing"`);
  }
});

test("the summary names the rate the editor picked", () => {
  const caps = { cutRates: [{ value: "4", label: "Every bar" }] };
  const r = buildRequest({ jobId: "EP1", recipe: "social-short", media: ["a.mp4"], cutRate: "4" });
  assert.match(describeRequest(r, caps), /Every bar/);
});
