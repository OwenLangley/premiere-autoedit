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

test("the Premiere version warning fires on old builds and stays quiet otherwise", () => {
  // It gates a warning shown on every build. Wrong in one direction it cries
  // wolf until warnings are ignored; wrong in the other it stays silent while
  // an editor hits missing calls one at a time, which is what happened.
  const { MIN_PREMIERE, olderThanSupported, parseVersion } = require("../src/version");
  assert.deepEqual(MIN_PREMIERE, [26, 3], "measured: createSubClipAction arrived in 26.3.0");

  for (const v of [[26, 0, 1], [26, 2, 0], [25, 9, 9], [26, 2]]) {
    assert.ok(olderThanSupported(v), `${v.join(".")} should warn`);
  }
  for (const v of [[26, 3, 0], [26, 3, 2], [26, 5, 0], [27, 0, 0], [26, 3]]) {
    assert.ok(!olderThanSupported(v), `${v.join(".")} should not warn`);
  }
  // An unreadable version must not warn: a false alarm on every build teaches
  // editors to ignore the one that matters.
  assert.ok(!olderThanSupported(null));
  assert.deepEqual(parseVersion("26.3.2"), [26, 3, 2]);
  assert.equal(parseVersion(""), null);
  assert.equal(parseVersion(undefined), null);
});
