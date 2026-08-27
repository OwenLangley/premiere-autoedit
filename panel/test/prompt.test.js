"use strict";
/**
 * The panel's reading of a description.
 *
 * The cases come from engine/tests/fixtures/prompt-settings.json, which the
 * Python parser's tests assert against too. Two implementations exist so the
 * form can reflect a prompt as it is typed; sharing the fixtures is what stops
 * them drifting apart quietly.
 */
const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const fs = require("node:fs");

const { readPromptSettings, readDuration, hasWord } = require("../src/prompt");

// The vocabulary the helper publishes, read straight from the engine so this
// test uses the same words the panel will at runtime.
const story = fs.readFileSync(
  path.join(__dirname, "..", "..", "engine", "autoedit", "story.py"), "utf8");

function pyDict(name) {
  const body = new RegExp(`${name}[^=]*=\\s*\\{([\\s\\S]*?)\\n\\}`).exec(story)[1];
  /** @type {Record<string, any>} */
  const out = {};
  for (const [, k, v] of body.matchAll(/"([^"]+)":\s*([0-9.]+|"[^"]*")/g)) {
    out[k] = v.startsWith('"') ? v.slice(1, -1) : Number(v);
  }
  return out;
}
function pyTuple(name) {
  const body = new RegExp(`${name}[^=]*=\\s*\\(([\\s\\S]*?)\\n\\)`).exec(story)[1];
  return [...body.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

const WORDS = {
  platforms: pyDict("PLATFORM_ASPECTS"),
  pace: pyDict("PACE_WORDS"),
  montage: pyTuple("MONTAGE_WORDS"),
};

const CASES = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "..", "engine", "tests", "fixtures",
            "prompt-settings.json"), "utf8")).cases;

test("the vocabulary was actually parsed out of the engine", () => {
  // If these come back empty the tests below would pass by reading nothing.
  assert.ok(Object.keys(WORDS.platforms).length >= 8, "platforms");
  assert.ok(Object.keys(WORDS.pace).length >= 8, "pace");
  assert.ok(WORDS.montage.length >= 8, "montage");
  assert.strictEqual(WORDS.platforms.tiktok, "vertical");
});

for (const c of CASES) {
  test(`reads: ${c.text.slice(0, 52) || "(empty)"}`, () => {
    const got = readPromptSettings(c.text, WORDS);
    assert.strictEqual(got.seconds, c.seconds, "seconds");
    assert.strictEqual(got.aspect, c.aspect, "aspect");
    assert.strictEqual(got.cutRate, c.cutRate, "cutRate");
    assert.strictEqual(got.visual, c.visual, "visual");
  });
}

test("a bare number is not a duration", () => {
  assert.strictEqual(readDuration("3 shots of the kitchen"), null);
  assert.strictEqual(readDuration("the chef and 2 waiters"), null);
});

test("words match whole, not inside other words", () => {
  assert.ok(hasWord("a fast promo", "fast"));
  assert.ok(!hasWord("a shortstop swinging", "short"));
});

test("no vocabulary yet means no settings, not a crash", () => {
  // capabilities.json may not have been written when the panel first loads.
  const got = readPromptSettings("a 15s tiktok", null);
  assert.deepStrictEqual(got,
    { seconds: null, aspect: null, cutRate: null, visual: false });
});
