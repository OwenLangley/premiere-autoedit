"use strict";
const test = require("node:test");
const assert = require("node:assert");
const {
  isVideoFile, normaliseJobId, buildRequest, validateRequest, describeRequest,
  musicChoices, referenceChoices, formatDuration, parseMusicValue, parseTimecode, formatTimecode, parseSeconds,
  autoJobName, keepFileName, jobIdFromPlanName,
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
  assert.deepEqual(validateRequest(buildRequest(form({ media: [] }))), ["err.clipsRequired"]);
  assert.deepEqual(validateRequest(buildRequest(form({ recipe: "" }))), ["err.recipeRequired"]);
});

test("every error key has a translation in both languages", () => {
  const { EN, JA } = require("../src/i18n");
  const cases = [
    form({ media: [] }), form({ recipe: "" }), form({ jobId: ".hidden" }),
  ].map(buildRequest);
  // buildRequest fills an empty name in now, so the only way to reach the
  // nameRequired guard is a request this panel did not build -- which is
  // exactly who the guard is still there for.
  cases.push({
    ...buildRequest(form()), jobId: "",
  });
  for (const request of cases) {
    for (const key of validateRequest(request)) {
      assert.ok(EN[key], `no English for ${key}`);
      assert.ok(JA[key], `no Japanese for ${key}`);
    }
  }
});

test("a request built elsewhere with no name is still rejected", () => {
  assert.deepEqual(
    validateRequest({ ...buildRequest(form()), jobId: "" }),
    ["err.nameRequired"]);
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


// -------------------------------------------------- the reference dropdown

test("reference choices are shaped the way fillSelect needs them", () => {
  // It shipped passing bare filenames, so `fillSelect` built every option with
  // `value === undefined` and no text: the dropdown showed blank rows whatever
  // was in the folder, and an editor who had just put an mp4 there was told
  // nothing. Every other call site passes {value, label}; this one did not.
  const choices = referenceChoices(["tiktok-ref.mp4", "client-promo.mov"]);
  for (const c of choices) {
    assert.strictEqual(typeof c.value, "string", JSON.stringify(c));
    assert.strictEqual(typeof c.label, "string", JSON.stringify(c));
    assert.ok(c.label.length > 0, "an option with no text is an invisible option");
  }
});

test("the reference dropdown offers None first, then the folder in order", () => {
  const choices = referenceChoices(["a.mp4", "b.mov"]);
  assert.strictEqual(choices[0].value, "", "None must carry the empty value");
  assert.deepStrictEqual(choices.slice(1).map((c) => c.value), ["a.mp4", "b.mov"]);
  assert.deepStrictEqual(choices.slice(1).map((c) => c.label), ["a.mp4", "b.mov"]);
});

test("an empty or missing reference folder still offers None", () => {
  for (const names of [[], null, undefined]) {
    const choices = referenceChoices(names);
    assert.strictEqual(choices.length, 1);
    assert.strictEqual(choices[0].value, "");
  }
});


// --- a name nobody had to think of ------------------------------------------

test("an empty name is no longer an error, it is the ordinary case", () => {
  // The field was required, so an editor making a dozen cuts a day invented a
  // dozen names. Their jobs folder is the evidence: eadaeda, klklklkl, dial4.
  const request = buildRequest(form({ jobId: "", now: new Date("2026-09-25T14:32:07") }));
  assert.deepStrictEqual(validateRequest(request), []);
  assert.equal(request.jobId, "260925-143207");
});

test("a name the editor did type is never overridden", () => {
  assert.equal(buildRequest(form({ jobId: "EP001" })).jobId, "EP001");
});

test("an auto name still has to satisfy the schema that rejected Japanese once", () => {
  const { SCHEMA_JOB_ID } = { SCHEMA_JOB_ID: /^[^.\s/\\:*?"<>|\u0000-\u001f][^/\\:*?"<>|\u0000-\u001f]{0,63}$/ };
  for (const t of ["2026-01-01T00:00:00", "2026-12-31T23:59:59", "2026-09-05T04:03:02"]) {
    assert.match(autoJobName(new Date(t)), SCHEMA_JOB_ID);
  }
});

test("two edits started in the same minute do not overwrite each other", () => {
  // Minute resolution collides when a job fails in the first few seconds and
  // the editor immediately tries again -- and the collision does not warn, it
  // overwrites the earlier plan under the same name.
  const a = autoJobName(new Date("2026-09-25T14:32:07"));
  const b = autoJobName(new Date("2026-09-25T14:32:41"));
  assert.notEqual(a, b);
});

test("an auto name sorts in the order the edits were made", () => {
  const names = [
    "2026-09-25T14:32:07", "2026-09-25T09:00:00", "2026-10-01T08:00:00",
    "2027-01-01T00:00:00",
  ].map((t) => autoJobName(new Date(t)));
  assert.deepStrictEqual([...names].sort(), [names[1], names[0], names[2], names[3]]);
});

// --- marking a plan to keep -------------------------------------------------

test("the keep marker sits beside the plan it saves", () => {
  // Kept in step with KEEP_SUFFIX in helper/watch.py; the helper reads these
  // without opening a single plan.
  assert.equal(keepFileName("EP001.editplan.json"), "EP001.keep");
  assert.equal(jobIdFromPlanName("EP001.editplan.json"), "EP001");
});

test("a Japanese job name survives the round trip", () => {
  // The name field takes any language -- that was fixed once already, after a
  // real edit named in Japanese failed at the schema.
  const plan = "清田悠悟_小麦生まれ麺育ち.editplan.json";
  assert.equal(keepFileName(plan), "清田悠悟_小麦生まれ麺育ち.keep");
  assert.equal(jobIdFromPlanName(plan), "清田悠悟_小麦生まれ麺育ち");
});

test("a plan name that is not a plan is left alone rather than mangled", () => {
  assert.equal(keepFileName("notaplan.json"), "notaplan.json.keep");
  assert.equal(keepFileName(""), ".keep");
});
