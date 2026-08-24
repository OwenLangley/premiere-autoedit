"use strict";
/**
 * The Python engine re-implements this maths. If the two drift apart, clips land
 * on the wrong frame and no other test in either suite would notice.
 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const tb = require("../src/timebase");

const vectors = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "..", "schema", "timebase-vectors.json"), "utf8")
);

for (const c of vectors.cases) {
  const t = c.timebase;
  const label = `${t.fpsNum}/${t.fpsDen}${t.dropFrame ? " DF" : ""}`;
  test(`JS matches the shared vectors at ${label}`, () => {
    for (const v of c.toFrames) assert.strictEqual(tb.toFrames(t, v.seconds), v.frames);
    for (const v of c.snap) assert.ok(Math.abs(tb.snap(t, v.seconds) - v.snapped) < 1e-9);
    for (const v of c.toSeconds) assert.ok(Math.abs(tb.toSeconds(t, v.frames) - v.seconds) < 1e-9);
    for (const v of c.timecode) assert.strictEqual(tb.timecode(t, v.frames), v.timecode);
  });
}
