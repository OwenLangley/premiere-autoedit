"use strict";
const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const fs = require("node:fs");
const { EN, JA, translate, translateWarning, missingKeys, makeTranslator } = require("../src/i18n");

test("the Japanese catalogue covers every English key", () => {
  // Drift is what goes wrong with translations: an English string changes, the
  // Japanese one does not, and nobody notices. This is the tripwire.
  assert.deepEqual(missingKeys("ja"), []);
});

test("no Japanese entry was left as its English text", () => {
  const untranslated = Object.keys(EN).filter((k) => JA[k] === EN[k] && /[A-Za-z]{4}/.test(EN[k]));
  assert.deepEqual(untranslated, [], "these look copied rather than translated");
});

test("parameters are substituted in both languages", () => {
  assert.equal(translate("en", "sum.upTo", { seconds: 15 }), "up to 15s");
  assert.equal(translate("ja", "sum.upTo", { seconds: 15 }), "最大 15 秒");
});

test("a missing parameter leaves the placeholder rather than printing undefined", () => {
  assert.match(translate("en", "sum.upTo", {}), /\{seconds\}/);
});

test("floats are rounded to something a person would read", () => {
  assert.equal(translate("en", "warn.music.stopsEarly", { shortfall: 0.09999999 }),
    "the music stops 0.1s before the picture does");
});

test("an unknown language falls back to English rather than showing keys", () => {
  assert.equal(translate("de", "plan.build"), "Build sequence");
});

// --- engine warnings --------------------------------------------------------

test("an engine warning renders in Japanese from its key and params", () => {
  const warning = {
    code: "music",
    messageKey: "music.stopsEarly",
    params: { shortfall: 0.1 },
    message: "the music stops 0.1s before the picture does",
  };
  assert.equal(translateWarning("ja", warning), "音楽が映像より 0.1 秒早く終わります");
  assert.equal(translateWarning("en", warning), warning.message);
});

test("a warning with no translation falls back to the engine's English", () => {
  const warning = { code: "x", messageKey: "brand.newThing", params: {}, message: "something new happened" };
  assert.equal(translateWarning("ja", warning), "something new happened");
});

test("a warning written before this mechanism existed still displays", () => {
  assert.equal(translateWarning("ja", { code: "cut", message: "an older plan" }), "an older plan");
});

// --- the contract with the engine ------------------------------------------

test("every engine warning key has a Japanese translation", () => {
  // Parsed from the engine's own catalogue, so adding a warning there without a
  // translation fails here rather than surfacing as English to an editor who
  // cannot read it.
  const notes = fs.readFileSync(
    path.join(__dirname, "..", "..", "engine", "autoedit", "notes.py"), "utf8");
  const body = notes.slice(notes.indexOf("CATALOGUE"), notes.indexOf("def render"));
  const keys = [...body.matchAll(/^\s*"([a-z]+\.[A-Za-z]+)":/gm)].map((m) => m[1]);
  assert.ok(keys.length >= 15, `expected the engine catalogue, found ${keys.length} keys`);
  const missing = keys.filter((k) => JA[`warn.${k}`] === undefined);
  assert.deepEqual(missing, [], "engine warnings with no Japanese translation");
});

test("makeTranslator carries the language", () => {
  const t = makeTranslator("ja");
  assert.equal(t.lang, "ja");
  assert.equal(t("plan.build"), "シーケンスを作成");
  assert.equal(t.warning({ messageKey: "visual.noBeats", message: "x" }),
    "ビートを検出できなかったため、固定長のカットに切り替えました");
});

test("the music dropdown follows the panel language", () => {
  const { musicChoices } = require("../src/request");
  const files = [{ relPath: "theme.wav", hasVideo: false, hasAudio: true, durationSeconds: 30 }];
  assert.equal(musicChoices(files, [], makeTranslator("ja"))[0].label, "自動");
  assert.equal(musicChoices(files, [], makeTranslator("en"))[0].label, "Automatic");
});

test("the summary line reads as Japanese, not as English with numbers swapped", () => {
  const { buildRequest, describeRequest } = require("../src/request");
  const request = buildRequest({
    jobId: "エピソード1", recipe: "social-short", media: ["a.mp4", "b.mp4"],
    aspect: "vertical", durationMode: "upTo", durationSeconds: 15, visual: true,
  });
  const summary = describeRequest(request, {}, makeTranslator("ja"));
  assert.equal(summary, "クリップ 2 件 · 縦位置 9:16 · 最大 15 秒 · 映像からカット");
});

test("a Japanese job name survives into the request", () => {
  const { buildRequest, validateRequest } = require("../src/request");
  const request = buildRequest({ jobId: "エピソード1", recipe: "social-short", media: ["a.mp4"] });
  assert.equal(request.jobId, "エピソード1");
  assert.deepEqual(validateRequest(request), []);
});

test("a plain-string warning passes through rather than vanishing", () => {
  // Most apply-side warnings are still plain English strings. An earlier version
  // read `.message` off them, got undefined, and returned "" -- so a warning an
  // editor could at least have read in English disappeared entirely instead.
  assert.equal(
    translateWarning("ja", "effect \"Lumetri Color\" is not installed on this machine"),
    "effect \"Lumetri Color\" is not installed on this machine"
  );
});

test("a keyed build warning is translated, and falls back when it is not known", () => {
  const known = translateWarning("ja", {
    messageKey: "reframe.scaled",
    params: { count: 4, width: 3840, height: 2160 },
    message: "4 clip(s) scaled to fill 3840x2160; anything at the edge of frame is now cropped out",
  });
  assert.ok(known.includes("3840x2160"));
  assert.ok(!known.includes("clip(s)"), "should be the Japanese, not the English");

  const unknown = translateWarning("ja", {
    messageKey: "nothing.likeThis", params: {}, message: "the English still says it",
  });
  assert.equal(unknown, "the English still says it");
});

test("every string the shot list draws exists in both catalogues", () => {
  // These labels are built in JavaScript rather than carried on data-i18n
  // markup, so nothing else in this suite would notice one going missing --
  // and the symptom would be an English hole in an otherwise Japanese panel.
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "main.js"), "utf8");
  const used = [...src.matchAll(/state\.t\("([a-zA-Z.]+)"/g)].map((m) => m[1]);
  const missing = [...new Set(used)].filter((k) => !(k in EN) || !(k in JA));
  assert.deepStrictEqual(missing, [], "keys drawn by main.js with no translation");
});

test("no shot-list label is left as English in the Japanese catalogue", () => {
  const keys = Object.keys(EN).filter((k) => k.startsWith("swap.") || k.startsWith("sections."));
  const untranslated = keys.filter((k) => JA[k] === EN[k] && /[A-Za-z]{4}/.test(EN[k]));
  assert.deepStrictEqual(untranslated, []);
});

test("every shot descriptor the engine can choose has a label here", () => {
  // The other half of engine/tests/fixtures/shot-descriptors.json, which Python
  // asserts still matches autoedit.describe.DESCRIPTORS. A descriptor added to
  // the engine without a translation shows English inside a Japanese panel, and
  // nobody finds out until an editor does.
  const { ids } = JSON.parse(fs.readFileSync(
    path.join(__dirname, "..", "..", "engine", "tests", "fixtures",
              "shot-descriptors.json"), "utf8"));
  assert.ok(ids.length > 0, "fixture is empty");
  const missing = { en: [], ja: [] };
  for (const id of ids) {
    if (EN[`shot.${id}`] === undefined) missing.en.push(id);
    if (JA[`shot.${id}`] === undefined) missing.ja.push(id);
  }
  assert.deepEqual(missing, { en: [], ja: [] });
});
