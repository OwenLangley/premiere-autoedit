"use strict";
/**
 * Swapping a shot without moving anything.
 *
 * The first test here is the one the whole feature rests on. Every cut position
 * in a plan was placed on a musical beat, so a swap that changes any duration
 * knocks every clip after it off the grid -- and it would do so invisibly,
 * because the sequence would still build and still look like a finished edit.
 * If `positions are identical` ever fails, nothing else in this file matters.
 */
const test = require("node:test");
const assert = require("node:assert");
const {
  withSwaps, candidatesFor, groupKeyOf, colourFor, withoutSections, validatePlan,
} = require("../src/plan");

const TB = { fpsNum: 25, fpsDen: 1 };

function clip(over = {}) {
  return {
    mediaId: "A001", inSeconds: 0, outSeconds: 2,
    atFrame: 0, durationFrames: 50, videoTrack: 0, audioTrack: 0,
    fadeInFrames: 0, fadeOutFrames: 0, confidence: 0.9, ...over,
  };
}

/** Four slots, 2s each, back to back. */
function plan(over = {}) {
  return {
    schemaVersion: "1.0", jobId: "j1", recipe: "social-short", timebase: TB,
    media: [
      { id: "A001", relPath: "a/A001.mov", durationSeconds: 60 },
      { id: "B002", relPath: "b/B002.mov", durationSeconds: 60 },
    ],
    sequence: { name: "S", videoTracks: 2, audioTracks: 2 },
    timeline: [
      clip({ atFrame: 0, inSeconds: 0, outSeconds: 2 }),
      clip({ atFrame: 50, inSeconds: 10, outSeconds: 12 }),
      clip({ atFrame: 100, mediaId: "B002", inSeconds: 4, outSeconds: 6 }),
      clip({ atFrame: 150, mediaId: "B002", inSeconds: 20, outSeconds: 22 }),
    ],
    candidates: [
      { mediaId: "A001", inSeconds: 30, outSeconds: 36, score: 0.9 },
      { mediaId: "A001", inSeconds: 40, outSeconds: 41, score: 0.8 },  // too short
      { mediaId: "B002", inSeconds: 50, outSeconds: 55, score: 0.7 },
    ],
    ...over,
  };
}

// --------------------------------------------------------------- the contract

test("swapping every clip leaves every position and duration identical", () => {
  const before = plan();
  const swaps = new Map(before.timeline.map((c) => [
    c.atFrame, { mediaId: "A001", inSeconds: 30 },
  ]));
  const after = withSwaps(before, swaps);

  assert.deepStrictEqual(
    after.timeline.map((c) => [c.atFrame, c.durationFrames]),
    before.timeline.map((c) => [c.atFrame, c.durationFrames]),
    "a swap moved or resized a slot"
  );
  const end = (p) => Math.max(...p.timeline.map((c) => c.atFrame + c.durationFrames));
  assert.strictEqual(end(after), end(before), "the sequence changed length");
});

test("the swapped clip reads from the new source at the new in point", () => {
  const after = withSwaps(plan(), new Map([[50, { mediaId: "B002", inSeconds: 33 }]]));
  const slot = after.timeline.find((c) => c.atFrame === 50);
  assert.strictEqual(slot.mediaId, "B002");
  assert.strictEqual(slot.inSeconds, 33);
  assert.strictEqual(slot.outSeconds, 35, "out point must come from the slot's length");
  assert.ok(slot.swapped);
});

test("out point follows the slot's frames, not the candidate's own span", () => {
  // The candidate is 6s long; the slot is 2s. Taking the candidate's out point
  // would stretch the slot and shift everything after it.
  const after = withSwaps(plan(), new Map([[0, { mediaId: "A001", inSeconds: 30 }]]));
  assert.strictEqual(after.timeline[0].outSeconds - after.timeline[0].inSeconds, 2);
});

test("swapping does not mutate the plan it was given", () => {
  const before = plan();
  const snapshot = JSON.stringify(before);
  withSwaps(before, new Map([[0, { mediaId: "B002", inSeconds: 30 }]]));
  assert.strictEqual(JSON.stringify(before), snapshot);
});

test("no swaps returns the plan untouched", () => {
  const p = plan();
  assert.strictEqual(withSwaps(p, new Map()), p);
  assert.strictEqual(withSwaps(p, null), p);
});

test("swapping the same slot twice keeps the last choice, not both", () => {
  const swaps = new Map();
  swaps.set(0, { mediaId: "A001", inSeconds: 30 });
  swaps.set(0, { mediaId: "B002", inSeconds: 50 });
  const after = withSwaps(plan(), swaps);
  assert.strictEqual(after.timeline.filter((c) => c.atFrame === 0).length, 1);
  assert.strictEqual(after.timeline[0].mediaId, "B002");
});

test("a swap never reads past the end of its new source", () => {
  // The slot wants 2s and the source has 60s, so an in point at 59 would read
  // a second past the end -- which Premiere renders as a held final frame.
  const after = withSwaps(plan(), new Map([[0, { mediaId: "B002", inSeconds: 59 }]]));
  const slot = after.timeline[0];
  assert.strictEqual(slot.outSeconds, 60, "should have been pulled back to the tail");
  assert.strictEqual(slot.inSeconds, 58);
  assert.strictEqual(slot.durationFrames, 50, "and the slot still has its length");
});

test("a source shorter than the slot clamps at zero rather than going negative", () => {
  const p = plan({ media: [{ id: "T", relPath: "t.mov", durationSeconds: 1 }] });
  const after = withSwaps(p, new Map([[0, { mediaId: "T", inSeconds: 0.5 }]]));
  assert.ok(after.timeline[0].inSeconds >= 0);
});

// -------------------------------------------------------------- the shortlist

test("a candidate shorter than the slot is never offered", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[0]);
  assert.ok(
    offered.every((c) => c.outSeconds - c.inSeconds >= 2),
    "offered a span too short to fill the slot"
  );
  assert.ok(!offered.some((c) => c.inSeconds === 40), "the 1s span should be out");
});

test("a candidate exactly the slot's length still fits", () => {
  const p = plan({ candidates: [{ mediaId: "A001", inSeconds: 5, outSeconds: 7, score: 0.5 }] });
  assert.strictEqual(candidatesFor(p, p.timeline[0]).length, 1);
});

test("same-group candidates come first but the rest are still offered", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[2]);   // a B002 slot
  assert.strictEqual(offered[0].mediaId, "B002", "same source should lead");
  assert.ok(offered.some((c) => c.mediaId === "A001"), "other sources must remain available");
});

test("a slot whose own group offers nothing still gets the others", () => {
  const p = plan({ candidates: [{ mediaId: "B002", inSeconds: 50, outSeconds: 55, score: 0.7 }] });
  const offered = candidatesFor(p, p.timeline[0]);   // an A001 slot, no A001 candidates
  assert.strictEqual(offered.length, 1);
  assert.strictEqual(offered[0].sameGroup, false);
});

test("the span already in the slot is marked, not hidden", () => {
  const p = plan({
    candidates: [{ mediaId: "A001", inSeconds: 0, outSeconds: 6, score: 0.9 }],
  });
  const [only] = candidatesFor(p, p.timeline[0]);
  assert.strictEqual(only.current, true);
});

test("a plan with no candidates offers no swaps rather than throwing", () => {
  const p = plan({ candidates: undefined });
  assert.deepStrictEqual(candidatesFor(p, p.timeline[0]), []);
});

test("story sections take over the grouping when present", () => {
  assert.strictEqual(groupKeyOf({ mediaId: "A001", sectionId: "cooking" }), "cooking");
  assert.strictEqual(groupKeyOf({ mediaId: "A001" }), "A001");
});

// ----------------------------------------------------------------- library

const LIBRARY = {
  complete: true,
  files: [
    { relPath: "lib/L100.mov", inSeconds: 3, outSeconds: 9, score: 0.95, durationSeconds: 30 },
    { relPath: "lib/L101.mov", inSeconds: 0, outSeconds: 1, score: 0.99, durationSeconds: 30 },
    // Same file the plan already uses: must not appear twice.
    { relPath: "a/A001.mov", inSeconds: 0, outSeconds: 9, score: 0.99, durationSeconds: 60 },
  ],
};

test("library shots are offered alongside the plan's own", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  assert.ok(offered.some((c) => c.relPath === "lib/L100.mov"), "library shot missing");
});

test("a library shot too short for the slot is still excluded", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  assert.ok(!offered.some((c) => c.relPath === "lib/L101.mov"), "1s span should be out");
});

test("footage the plan already has candidates for is not listed twice", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  assert.strictEqual(offered.filter((c) => c.relPath === "a/A001.mov").length, 0,
    "the plan's own candidates already cover that file");
});

test("a plan with no candidates of its own still gets the library", () => {
  // The case that emptied the list in practice: a speech-cut job carries no
  // candidates, and the library holds exactly the footage it used. Filtering
  // those out as duplicates left nothing to offer.
  const p = plan({ candidates: undefined });
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  assert.ok(offered.length > 0, "no alternates were offered at all");
  assert.ok(offered.some((c) => c.relPath === "a/A001.mov"),
    "footage in the plan should be offered when nothing else describes it");
});

test("a library span of footage the plan holds swaps without minting a media entry", () => {
  const p = plan({ candidates: undefined });
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  const known = offered.find((c) => c.relPath === "a/A001.mov");
  assert.strictEqual(known.fromLibrary, false, "the plan already carries this file");
  assert.strictEqual(known.mediaId, "A001", "it should resolve to the existing id");
  const after = withSwaps(p, new Map([[0, {
    relPath: known.relPath, inSeconds: known.inSeconds, durationSeconds: 60,
  }]]));
  assert.strictEqual(after.media.length, p.media.length, "media should not have grown");
  assert.strictEqual(after.timeline[0].mediaId, "A001");
});

test("the plan's own shots sort ahead of the library", () => {
  const p = plan();
  const offered = candidatesFor(p, p.timeline[0], LIBRARY);
  const firstLib = offered.findIndex((c) => c.fromLibrary);
  const lastOwn = offered.map((c) => c.fromLibrary).lastIndexOf(false);
  assert.ok(firstLib === -1 || firstLib > lastOwn, "library shots jumped the queue");
});

test("swapping in a library shot adds the media entry the build needs", () => {
  const p = plan();
  const after = withSwaps(p, new Map([[0, {
    relPath: "lib/L100.mov", inSeconds: 3, durationSeconds: 30,
  }]]));
  const added = after.media.find((m) => m.relPath === "lib/L100.mov");
  assert.ok(added, "no media entry was created for the library file");
  assert.strictEqual(after.timeline[0].mediaId, added.id);
  assert.match(added.id, /^[^\s/\\:*?"<>|.]+$/, "id must satisfy the schema pattern");
  assert.deepStrictEqual(validatePlan(after), [], "the swapped plan must still validate");
});

test("a library swap is still clamped inside its own source", () => {
  const p = plan();
  const after = withSwaps(p, new Map([[0, {
    relPath: "lib/L100.mov", inSeconds: 29.5, durationSeconds: 30,
  }]]));
  assert.strictEqual(after.timeline[0].outSeconds, 30);
  assert.strictEqual(after.timeline[0].durationFrames, 50);
});

test("a library file whose name collides with a plan id gets its own", () => {
  const p = plan();
  const after = withSwaps(p, new Map([[0, {
    relPath: "other/A001.mov", inSeconds: 0, durationSeconds: 30,
  }]]));
  const ids = after.media.map((m) => m.id);
  assert.strictEqual(new Set(ids).size, ids.length, "two media entries share an id");
});

// ------------------------------------------------------------------- colour

test("a group's colour is stable across calls", () => {
  assert.strictEqual(colourFor("cooking"), colourFor("cooking"));
  assert.notStrictEqual(colourFor("cooking"), colourFor("serving"));
});

test("colour is a usable CSS value", () => {
  assert.match(colourFor("A001"), /^hsl\(\d+ \d+% \d+%\)$/);
});

// -------------------------------------------------------------- composition

test("swapping then dropping a section matches dropping it outright", () => {
  const p = plan({
    timeline: [
      clip({ atFrame: 0, sectionId: "keep" }),
      clip({ atFrame: 50, sectionId: "bin", inSeconds: 10, outSeconds: 12 }),
      clip({ atFrame: 100, sectionId: "keep", inSeconds: 20, outSeconds: 22 }),
    ],
  });
  // Swap a clip inside the section that is about to be dropped: the result must
  // not depend on a choice the editor made about material they then removed.
  const swapped = withSwaps(p, new Map([[50, { mediaId: "B002", inSeconds: 50 }]]));
  const viaSwap = withoutSections(swapped, ["bin"]);
  const direct = withoutSections(p, ["bin"]);
  assert.deepStrictEqual(
    viaSwap.timeline.map((c) => [c.atFrame, c.durationFrames, c.mediaId]),
    direct.timeline.map((c) => [c.atFrame, c.durationFrames, c.mediaId])
  );
});

test("a swap outside the dropped section survives the ripple", () => {
  const p = plan({
    timeline: [
      clip({ atFrame: 0, sectionId: "bin" }),
      clip({ atFrame: 50, sectionId: "keep", inSeconds: 10, outSeconds: 12 }),
    ],
  });
  const out = withoutSections(
    withSwaps(p, new Map([[50, { mediaId: "B002", inSeconds: 50 }]])), ["bin"]
  );
  assert.strictEqual(out.timeline.length, 1);
  assert.strictEqual(out.timeline[0].mediaId, "B002");
  assert.strictEqual(out.timeline[0].atFrame, 0, "the survivor should ripple to the head");
});
