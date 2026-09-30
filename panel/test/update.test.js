"use strict";
/**
 * What an editor is told after pressing Update.
 *
 * A colleague pressed it and was told the background helper was not running.
 * It was running. It was busy -- the helper handles one thing at a time, and
 * while it analyses footage it cannot look at its folder at all, so the request
 * sat unread past the panel's sixty-second patience.
 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const {
  updateOutcome, updateButton, updateNote, shortSubject, formatElapsed,
} = require("../src/update");

const i18n = require("../src/i18n");

test("a request nothing ever read does not blame the helper for being absent", () => {
  const got = updateOutcome({ result: null, pickedUp: false, waited: true });
  assert.strictEqual(got.key, "msg.updateNeverHeard");
  assert.strictEqual(got.kind, "err");
});

test("a request that WAS picked up reports an unfinished update, not a missing helper", () => {
  // The distinction the panel could not previously make. The helper deletes
  // `update.request` the moment it picks it up, so this is knowable.
  const got = updateOutcome({ result: null, pickedUp: true, waited: true });
  assert.strictEqual(got.key, "msg.updateStillRunning");
  assert.notStrictEqual(got.key, "msg.updateNeverHeard");
});

test("nothing is said while the wait is still going", () => {
  assert.strictEqual(updateOutcome({ result: null, pickedUp: false, waited: false }).key, "");
  assert.strictEqual(updateOutcome({ result: null, pickedUp: true, waited: false }).key, "");
});

test("each finished status gets its own wording", () => {
  const cases = [
    { result: { status: "updated", detail: "a1b2c3 -> d4e5f6" }, key: "msg.updateDone", kind: "ok" },
    { result: { status: "current", detail: "already up to date" }, key: "msg.updateCurrent", kind: "ok" },
    { result: { status: "dirty", detail: "local changes block it" }, key: "msg.updateFailed", kind: "err" },
    { result: { status: "unreachable", detail: "could not reach it" }, key: "msg.updateFailed", kind: "err" },
  ];
  for (const c of cases) {
    const got = updateOutcome({ result: c.result, pickedUp: true, waited: true });
    assert.strictEqual(got.key, c.key, JSON.stringify(c.result));
    assert.strictEqual(got.kind, c.kind);
    assert.strictEqual(got.detail, c.result.detail);
  }
});

test("a result arriving beats any waiting state", () => {
  // The helper restarts itself mid-update, so `pickedUp` can be observed either
  // way by the time the answer lands. The answer wins.
  for (const pickedUp of [true, false]) {
    const got = updateOutcome({ result: { status: "updated", detail: "x" }, pickedUp, waited: true });
    assert.strictEqual(got.key, "msg.updateDone");
  }
});

test("a result with no detail does not produce 'undefined' on screen", () => {
  const got = updateOutcome({ result: { status: "failed" }, pickedUp: true, waited: true });
  assert.strictEqual(got.detail, "failed");
});

test("every message this can produce exists in both catalogues", () => {
  const keys = new Set();
  for (const result of [null, { status: "updated" }, { status: "current" }, { status: "oops" }]) {
    for (const pickedUp of [true, false]) {
      const { key } = updateOutcome({ result, pickedUp, waited: true });
      if (key) keys.add(key);
    }
  }
  assert.ok(keys.size >= 4, [...keys].join(", "));
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "i18n.js"), "utf8");
  for (const key of keys) {
    assert.ok(i18n.translate("en", key), `${key} missing from EN`);
    const ja = i18n.translate("ja", key);
    assert.ok(ja, `${key} missing from JA`);
    assert.notStrictEqual(ja, i18n.translate("en", key), `${key} left as English`);
    assert.ok(src.includes(`"${key}"`), `${key} not literally in i18n.js`);
  }
});


// --- the button in the top-right corner -------------------------------------
//
// It replaced a full-width "Update to the latest version" inside a Diagnostics
// section, which said nothing about whether there WAS a latest version and
// installed one the moment it was pressed out of curiosity.

test("a button that has never checked offers to check, not to install", () => {
  const got = updateButton({ check: null });
  assert.strictEqual(got.key, "update.check");
  assert.strictEqual(got.action, "check");
});

test("checking and installing are two different presses", () => {
  // The distinction the whole design rests on. A press while up to date must
  // never install, and a press while behind must never be a silent no-op.
  assert.strictEqual(updateButton({ check: { status: "current" } }).action, "check");
  assert.strictEqual(updateButton({ check: { status: "behind", behind: 2 } }).action, "update");
});

test("what it says is where things stand", () => {
  const label = (check) => updateButton({ check }).key;
  assert.strictEqual(label({ status: "current" }), "update.current");
  assert.strictEqual(label({ status: "behind", behind: 1 }), "update.available");
  assert.strictEqual(label({ status: "unreachable" }), "update.offline");
  assert.strictEqual(label({ status: "failed" }), "update.checkFailed");
});

test("a machine that cannot reach the repository is not shown an error", () => {
  // An editing machine on a train, or one whose credential has expired, is in
  // an ordinary state and can still cut. Red would be a lie about the job.
  assert.strictEqual(updateButton({ check: { status: "unreachable" } }).kind, "");
  assert.strictEqual(updateButton({ check: { status: "failed" } }).kind, "err");
});

test("a busy button cannot be pressed into doing something else", () => {
  /** @type {("checking"|"updating")[]} */
  const busy = ["checking", "updating"];
  for (const phase of busy) {
    const got = updateButton({ check: { status: "behind", behind: 3 }, phase });
    assert.strictEqual(got.action, null, phase);
  }
});

test("after an update the only step left belongs to the editor", () => {
  // UXP loads the panel once at startup: the new code is on disk and
  // unreachable until Premiere restarts, so "Up to date" would be true and
  // useless.
  const got = updateButton({ check: { status: "current" }, phase: "restart" });
  assert.strictEqual(got.key, "update.restartNeeded");
  assert.strictEqual(got.action, null);
  assert.strictEqual(got.notice, true);
});

test("a failed update says so rather than inviting the same press again", () => {
  const got = updateButton({ check: { status: "behind", behind: 1 }, phase: "failed" });
  assert.strictEqual(got.key, "update.failed");
  assert.strictEqual(got.kind, "err");
  assert.strictEqual(got.action, "update", "a retry must still be possible");
});

test("the line under the button carries what the label cannot", () => {
  // The log moved behind a button, so a check that failed had nowhere left to
  // explain itself.
  const behind = updateNote({ check: { status: "behind", behind: 3, subjects: ["5920f3d Decode each file once"] } });
  assert.strictEqual(behind.key, "update.behindNote");
  assert.strictEqual(behind.params.count, 3);
  assert.strictEqual(behind.params.plural, "s");
  assert.match(behind.params.latest, /Decode/);
  assert.doesNotMatch(behind.params.latest, /5920f3d/,
    "the hash is the one part of the line an editor cannot use");

  const one = updateNote({ check: { status: "behind", behind: 1, subjects: ["x"] } });
  assert.strictEqual(one.params.plural, "", "one change is not one changes");

  const bare = updateNote({ check: { status: "behind", behind: 2 } });
  assert.strictEqual(bare.key, "update.behindNoteCount",
    "without a subject the other wording ends on 'newest:' and stops");

  const off = updateNote({ check: { status: "unreachable", detail: "Host key verification failed" } });
  assert.strictEqual(off.key, "update.offlineNote");
  assert.match(off.params.detail, /Host key/);
});

test("nothing is said when there is nothing to say", () => {
  assert.strictEqual(updateNote({ check: null }), null);
  assert.strictEqual(updateNote({ check: { status: "current" } }), null);
  assert.strictEqual(updateNote({ check: { status: "behind", behind: 1 }, phase: "checking" }), null);
});

test("every label and note this can produce exists in both catalogues", () => {
  const keys = new Set();
  const checks = [null, { status: "current" }, { status: "behind", behind: 2, subjects: ["a b"] },
                  { status: "behind", behind: 1 }, { status: "unreachable", detail: "x" },
                  { status: "failed", detail: "x" }];
  for (const check of checks) {
    /** @type {("idle"|"checking"|"updating"|"restart"|"failed")[]} */
    const phases = ["idle", "checking", "updating", "restart", "failed"];
    for (const phase of phases) {
      keys.add(updateButton({ check, phase }).key);
      const note = updateNote({ check, phase, failure: { key: "msg.updateFailed", detail: "x" } });
      if (note) keys.add(note.key);
    }
  }
  assert.ok(keys.size >= 10, [...keys].join(", "));
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "i18n.js"), "utf8");
  for (const key of keys) {
    assert.ok(i18n.translate("en", key), `${key} missing from EN`);
    const ja = i18n.translate("ja", key);
    assert.ok(ja, `${key} missing from JA`);
    assert.notStrictEqual(ja, i18n.translate("en", key), `${key} left as English`);
    assert.ok(src.includes(`"${key}"`), `${key} not literally in i18n.js`);
  }
});

test("a label never renders a leftover placeholder", () => {
  // `{count}` on screen is the failure this catches: the params the button
  // reports have to be the ones its own string asks for.
  for (const check of [{ status: "behind", behind: 4, subjects: ["deadbee something"] },
                       { status: "current" }, { status: "unreachable", detail: "no route" }]) {
    for (const lang of ["en", "ja"]) {
      const b = updateButton({ check });
      assert.doesNotMatch(i18n.translate(lang, b.key, b.params), /[{}]/, b.key);
      const note = updateNote({ check });
      if (note) assert.doesNotMatch(i18n.translate(lang, note.key, note.params), /[{}]/, note.key);
    }
  }
});


test("a commit subject is cut to something a docked panel can hold", () => {
  // Measured at 360px, which is how this panel is usually docked: the real
  // subject below wrapped the note onto a second line, hash and all.
  const long = shortSubject("5920f3d Decode each file once, not three or four times");
  assert.ok(!long.startsWith("5920f3d"));
  assert.ok(long.length <= 46, `${long.length}: ${long}`);
  assert.ok(long.endsWith("..."), long);
  // Short ones are left exactly alone -- no dangling dots on a line that fits.
  assert.strictEqual(shortSubject("06322e2 Drop the dropdown"), "Drop the dropdown");
  assert.strictEqual(shortSubject(""), "");
  assert.strictEqual(shortSubject(undefined), "");
  // A subject that merely begins with a hex word is not a hash: `deadbeef` is
  // eight hex characters and `fade in` starts with four.
  assert.strictEqual(shortSubject("fade in the second shot"), "fade in the second shot");
});


// --- what an editor is told WHILE it runs, and WHY it stopped ---------------
//
// The panel could not distinguish success from failure from nothing-happened.
// Four separate reasons, all of them in the panel rather than the updater:
// nothing was drawn during the wait, the failure lost its own sentence, `kind`
// was computed and discarded, and no version was ever shown.

test("elapsed time reads as a clock", () => {
  assert.strictEqual(formatElapsed(0), "0:00");
  assert.strictEqual(formatElapsed(9), "0:09");
  assert.strictEqual(formatElapsed(59), "0:59");
  assert.strictEqual(formatElapsed(60), "1:00");
  assert.strictEqual(formatElapsed(600), "10:00");
  // Nonsense in, still a clock out. "NaN:aN" in a panel is worse than 0:00.
  assert.strictEqual(formatElapsed(-5), "0:00");
  assert.strictEqual(formatElapsed(undefined), "0:00");
  assert.strictEqual(formatElapsed(NaN), "0:00");
});

test("the line under the button never goes quiet while an update runs", () => {
  // The complaint that produced all of this. The wait runs to five minutes, the
  // button is disabled and says one static word, and `log()` only badges lines
  // marked as errors -- so the narration in the drawer was both shut away and
  // uncounted. This branch used to `return null`.
  const sent = updateNote({ check: null, phase: "updating", step: "sent", elapsed: 3 });
  assert.strictEqual(sent.key, "update.noteSent");
  assert.strictEqual(sent.params.elapsed, "0:03");

  const running = updateNote({ check: null, phase: "updating", step: "running", elapsed: 92 });
  assert.strictEqual(running.key, "update.noteRunning");
  assert.strictEqual(running.params.elapsed, "1:32");
  assert.notStrictEqual(running.key, sent.key,
    "waiting to be heard and being worked on are different news");
});

test("a failure keeps the sentence that explains it", () => {
  // `msg.updateNeverHeard` names ./setup.sh --check and is a whole sentence. It
  // used to be forced through "Update did not complete: {detail}" with an empty
  // detail, so an editor read a colon and nothing, and the sentence went only to
  // a drawer that was shut.
  const never = updateNote({
    check: null, phase: "failed", failure: { key: "msg.updateNeverHeard", detail: "" },
  });
  assert.strictEqual(never.key, "msg.updateNeverHeard");

  // update.sh's own classifications still arrive with their detail attached.
  const dirty = updateNote({
    check: null, phase: "failed",
    failure: { key: "msg.updateFailed", detail: "local changes block the update: README.md" },
  });
  assert.strictEqual(dirty.key, "msg.updateFailed");
  assert.match(dirty.params.detail, /README\.md/);
});

test("no note ever renders as a label and a naked colon", () => {
  // The defect exactly as it appeared on screen.
  const failures = [
    null,
    { key: "msg.updateFailed", detail: "" },
    { key: "msg.updateNeverHeard", detail: "" },
    { key: "msg.updateStillRunning", detail: "" },
    { key: "msg.updateFailed", detail: "could not reach the repository" },
  ];
  for (const failure of failures) {
    const note = updateNote({ check: null, phase: "failed", failure });
    assert.ok(note, "a failure always has something to say");
    for (const lang of ["en", "ja"]) {
      const text = i18n.translate(lang, note.key, note.params);
      assert.doesNotMatch(text, /[:\uff1a]\s*$/, `${note.key} in ${lang}: ${text}`);
      assert.doesNotMatch(text, /[{}]/, `${note.key} in ${lang}: ${text}`);
    }
  }
});

test("an installed update is confirmed, with or without a previous sha", () => {
  // After the restart the panel read "Up to date", which is also what it says
  // when the update never ran. update.result.json outlives the restart.
  const both = updateNote({
    check: { status: "current" }, installed: { before: "47e8d3f", after: "f6ca4e5" },
  });
  assert.strictEqual(both.key, "update.installedNote");
  assert.strictEqual(both.params.after, "f6ca4e5");

  // `before` is "unknown" when git could not read it, and update.sh can write it
  // empty -- "updated from ." is not a sentence.
  const bare = updateNote({
    check: { status: "current" }, installed: { before: "", after: "f6ca4e5" },
  });
  assert.strictEqual(bare.key, "update.installedNoteBare");
  for (const lang of ["en", "ja"]) {
    assert.doesNotMatch(i18n.translate(lang, both.key, both.params), /[{}]/, both.key);
    assert.doesNotMatch(i18n.translate(lang, bare.key, bare.params), /[{}]/, bare.key);
  }
});

test("a fresher answer is never masked by the last confirmation", () => {
  // Priority, not politeness. A press that failed, or one still running, is what
  // the editor is waiting to hear about -- not an update from ten minutes ago.
  const failed = updateNote({
    check: { status: "current" }, phase: "failed",
    failure: { key: "msg.updateFailed", detail: "x" },
    installed: { before: "a1b2c3d", after: "e4f5a6b" },
  });
  assert.strictEqual(failed.key, "msg.updateFailed");

  const updating = updateNote({
    check: { status: "current" }, phase: "updating", step: "running", elapsed: 1,
    installed: { before: "a1b2c3d", after: "e4f5a6b" },
  });
  assert.strictEqual(updating.key, "update.noteRunning");
});

test("a check nobody answered says so where it can be seen", () => {
  // The other half of the same defect. The update path narrated its failure
  // under the button; the check path logged it into a drawer that was shut and
  // let the button revert to whatever it had said before, so an editor had to go
  // looking to discover the helper was down.
  const view = updateButton({ check: { status: "noanswer" } });
  assert.strictEqual(view.key, "update.checkFailed");
  assert.strictEqual(view.kind, "err", "renderUpdate paints kind err red");
  assert.strictEqual(view.action, "check", "pressing again has to stay possible");

  const note = updateNote({ check: { status: "noanswer" } });
  assert.strictEqual(note.key, "msg.updateNeverHeard",
    "same cause as an unanswered update, so the same sentence");
  for (const lang of ["en", "ja"]) {
    const text = i18n.translate(lang, note.key, note.params);
    assert.match(text, /setup\.sh/, `${lang} has to name the thing to run`);
    assert.doesNotMatch(text, /[{}]/, text);
  }
});

test("an unanswered check is not reported as an unreachable repository", () => {
  // They used to be indistinguishable, because both produced silence. "Could not
  // reach the repository" sends somebody off to check a GitHub sign-in that was
  // never involved -- the same misdiagnosis update.sh was corrected for twice.
  const noanswer = updateNote({ check: { status: "noanswer" } });
  const offline = updateNote({ check: { status: "unreachable", detail: "no route to host" } });
  assert.strictEqual(offline.key, "update.offlineNote");
  assert.notStrictEqual(noanswer.key, offline.key);
  assert.doesNotMatch(i18n.translate("en", noanswer.key, noanswer.params), /repositor/i);
});
