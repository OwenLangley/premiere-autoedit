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

// --------------------------------------------------------------- job names

const nameVectors = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "..", "schema", "job-name-vectors.json"), "utf8")
);
const jobSchema = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "..", "schema", "job-request.schema.json"), "utf8")
);
const jobIdPattern = new RegExp(jobSchema.properties.jobId.pattern);
const req = require("../src/request");

/** The panel's own pipeline: normalise what was typed, then check it. */
function panelAccepts(typed) {
  const jobId = req.normaliseJobId(typed);
  const errors = req.validateRequest({
    jobId, recipe: "promo-silent", media: ["a.mp4"], options: {},
  });
  return { jobId, ok: !errors.some((e) => e.startsWith("err.name")) };
}

test("the panel never sends a job name the helper would refuse", () => {
  // This is the bug this file exists to prevent. `normaliseJobId` was widened
  // for Japanese and the schema's jobId pattern was not, so the panel wrote
  // requests it considered perfectly valid and the helper rejected them at the
  // gate -- showing a video editor a raw regex. Neither suite noticed, because
  // neither knew about the other.
  for (const c of [...nameVectors.accept, ...nameVectors.reject]) {
    const { jobId, ok } = panelAccepts(c.name);
    if (!ok) continue;                      // refused in the panel: nothing is sent
    assert.ok(jobIdPattern.test(jobId),
      `panel would send ${JSON.stringify(jobId)} (typed ${JSON.stringify(c.name)}) ` +
      `but the schema rejects it -- ${c.why}`);
  }
});

test("a name that is already safe is passed through untouched", () => {
  for (const c of nameVectors.accept) {
    assert.strictEqual(req.normaliseJobId(c.name), c.name, c.why);
    assert.ok(panelAccepts(c.name).ok, `panel refused ${JSON.stringify(c.name)} -- ${c.why}`);
    assert.strictEqual(req.jobIdWasChanged(c.name), false, c.why);
  }
});

test("dropping characters from a name is reported, not done quietly", () => {
  // `エピソード1` used to arrive as `1` and was accepted without a word, and
  // `ダンス動画` arrived empty and came back as "give the job a name" to someone
  // who just had. Tidying surrounding whitespace is not that, and is exempt.
  for (const c of nameVectors.reject) {
    const { jobId, ok } = panelAccepts(c.name);
    if (!ok) continue;                      // refused outright: nothing is sent
    const tidied = c.name.normalize("NFC").trim().replace(/\s+/g, " ");
    assert.ok(jobId === tidied || req.jobIdWasChanged(c.name),
      `${JSON.stringify(c.name)} was sent as ${JSON.stringify(jobId)} ` +
      `with nothing said about it -- ${c.why}`);
  }
});
