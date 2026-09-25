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
  updateOutcome, updateButton, updateNote, shortSubject,
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
      const note = updateNote({ check, phase, detail: "x" });
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
