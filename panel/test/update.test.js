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
const { updateOutcome } = require("../src/update");

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
