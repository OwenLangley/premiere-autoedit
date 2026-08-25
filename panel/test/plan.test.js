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

// --- Subclip naming --------------------------------------------------------
//
// The bug this guards: subclips were named by position in the plan, so a
// rebuild after the plan changed found the PREVIOUS build's subclip under the
// same name and silently used its ranges.

const { subclipName, planTag } = require("../src/plan");

const PLAN_A = { jobId: "musictest", createdAt: "2026-08-25T07:07:49Z" };
const PLAN_B = { jobId: "musictest", createdAt: "2026-08-25T07:18:02Z" };

test("a subclip is named by its source range", () => {
  assert.match(
    subclipName({ mediaId: "MUSIC", inSeconds: 0, outSeconds: 19.92 }, PLAN_A),
    /^MUSIC__0-19920__/
  );
});

test("a changed range is a different subclip", () => {
  const before = subclipName({ mediaId: "MUSIC", inSeconds: 0, outSeconds: 60 }, PLAN_A);
  const after = subclipName({ mediaId: "MUSIC", inSeconds: 0, outSeconds: 19.92 }, PLAN_A);
  assert.notEqual(before, after);
});

test("rebuilding the same plan reuses its subclips", () => {
  const clip = { mediaId: "C1367", inSeconds: 4.6547, outSeconds: 5.7724 };
  assert.equal(subclipName(clip, PLAN_A), subclipName(clip, PLAN_A));
});

test("a regenerated plan never inherits an older build's subclips", () => {
  // The range is identical; the plan is not. Reusing here is how a subclip built
  // under older apply logic survived into a new build one frame too long.
  const clip = { mediaId: "MUSIC", inSeconds: 0, outSeconds: 19.92 };
  assert.notEqual(subclipName(clip, PLAN_A), subclipName(clip, PLAN_B));
});

test("clips from different sources never collide", () => {
  const a = subclipName({ mediaId: "C1367", inSeconds: 0, outSeconds: 1 }, PLAN_A);
  const b = subclipName({ mediaId: "C1371", inSeconds: 0, outSeconds: 1 }, PLAN_A);
  assert.notEqual(a, b);
});

test("the plan tag is short, stable and filename-safe", () => {
  assert.equal(planTag(PLAN_A), planTag(PLAN_A));
  assert.match(planTag(PLAN_A), /^[a-z0-9]{7}$/);
});

// --- transcript segments handed to Text-Based Editing ----------------------
//
// The cut is already made by the time this runs; what this shapes is Premiere's
// own view of the words. Getting it wrong for Japanese would put spaces inside
// words -- Whisper emits sub-word token runs for Japanese, not words -- and find
// no sentence boundaries at all.

const { toPremiereTranscript } = require("../src/plan");

const words = (language, ...texts) => ({
  language,
  words: texts.map((text, i) => ({ text, start: i * 0.5, end: i * 0.5 + 0.4 })),
});

test("English words are joined with spaces", () => {
  const out = toPremiereTranscript(words("en", "we", "shot", "this", "today."));
  assert.strictEqual(out.segments.length, 1);
  assert.strictEqual(out.segments[0].text, "we shot this today.");
});

test("Japanese words are joined with nothing", () => {
  const out = toPremiereTranscript(words("ja", "今日は", "ダンスの", "撮影を", "しました。"));
  assert.strictEqual(out.segments[0].text, "今日はダンスの撮影をしました。");
});

test("a full-width full stop ends a sentence", () => {
  const out = toPremiereTranscript(words("ja", "そうですね。", "以上です。"));
  assert.strictEqual(out.segments.length, 2, "。 has to break a segment, as . does");
});

test("a regional tag still counts as Japanese", () => {
  const out = toPremiereTranscript(words("ja-JP", "撮影を", "しました"));
  assert.strictEqual(out.segments[0].text, "撮影をしました");
});

test("an unknown language falls back to spaces rather than running words together", () => {
  const out = toPremiereTranscript(words(undefined, "hello", "there"));
  assert.strictEqual(out.segments[0].text, "hello there");
  assert.strictEqual(out.language, "en");
});

test("a segment is capped even when nothing punctuates it", () => {
  const many = Array.from({ length: 40 }, (_, i) => `w${i}`);
  const out = toPremiereTranscript(words("en", ...many));
  assert.ok(out.segments.length > 1, "a 20s run with no full stop must still break");
});

// --- telling a master from a subclip ---------------------------------------
//
// The defect: a subclip reports the SAME media file path as its master, and the
// media index kept the LAST item walked. After a couple of builds that was one
// of our own subclips, so createSubClipAction ran against a SUBCLIP -- whose
// in/out points are relative to its own start, not the media's. Ranges
// compounded every build, ran past the end of the file, and Premiere played the
// media's last frame, held, for the clip's whole duration.
//
// Durations were right, positions were right, the verifier was clean. Only the
// pictures were wrong.

const { isMasterFor, basenameOf } = require("../src/plan");

test("an item named after its file is the master", () => {
  assert.ok(isMasterFor({ name: "C1367.MP4" }, "/Volumes/rushes/C1367.MP4"));
});

test("one of our own subclips is not a master", () => {
  // subclipName() produces names of this shape
  assert.ok(!isMasterFor({ name: "C1367__1101-6573__12n2x4l" }, "/rushes/C1367.MP4"));
});

test("an editor's hand-named subclip is not a master either", () => {
  assert.ok(!isMasterFor({ name: "goal - wide" }, "/rushes/C1367.MP4"));
});

test("Japanese filenames survive the comparison", () => {
  // NFC on both sides: macOS hands back decomposed forms from some APIs.
  assert.ok(isMasterFor({ name: "ダンス.MP4" }, "/rushes/ダンス.MP4"));
  assert.ok(!isMasterFor({ name: "ダンス__0-500__ab12" }, "/rushes/ダンス.MP4"));
});

test("a missing or nameless item is never mistaken for a master", () => {
  assert.ok(!isMasterFor(null, "/rushes/C1367.MP4"));
  assert.ok(!isMasterFor({}, "/rushes/C1367.MP4"));
});

test("basenameOf handles both separators and bare names", () => {
  assert.strictEqual(basenameOf("/a/b/C1367.MP4"), "C1367.MP4");
  assert.strictEqual(basenameOf("C:\\rushes\\C1367.MP4"), "C1367.MP4");
  assert.strictEqual(basenameOf("C1367.MP4"), "C1367.MP4");
});
