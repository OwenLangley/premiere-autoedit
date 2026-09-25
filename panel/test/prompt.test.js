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

const {
  readPromptSettings, readDuration, hasWord, chooseRecipe,
} = require("../src/prompt");

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
  // Both halves, because the engine splits them by script and the panel does
  // its own boundary check per term.
  subtitles: [...pyTuple("SUBTITLE_WORDS"), ...pyTuple("SUBTITLE_WORDS_CJK")],
};

const CASES = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "..", "engine", "tests", "fixtures",
            "prompt-settings.json"), "utf8")).cases;

test("the vocabulary was actually parsed out of the engine", () => {
  // If these come back empty the tests below would pass by reading nothing.
  assert.ok(Object.keys(WORDS.platforms).length >= 8, "platforms");
  assert.ok(Object.keys(WORDS.pace).length >= 8, "pace");
  assert.ok(WORDS.montage.length >= 8, "montage");
  assert.ok(WORDS.subtitles.length >= 8, "subtitles");
  assert.ok(WORDS.subtitles.includes("字幕"), "japanese subtitle word");
  assert.strictEqual(WORDS.platforms.tiktok, "vertical");
});

for (const c of CASES) {
  test(`reads: ${c.text.slice(0, 52) || "(empty)"}`, () => {
    const got = readPromptSettings(c.text, WORDS);
    assert.strictEqual(got.seconds, c.seconds, "seconds");
    assert.strictEqual(got.aspect, c.aspect, "aspect");
    assert.strictEqual(got.cutRate, c.cutRate, "cutRate");
    assert.strictEqual(got.visual, c.visual, "visual");
    assert.strictEqual(got.subtitles, c.subtitles === true, "subtitles");
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

test("a Japanese term matches without word boundaries", () => {
  // \b never fires between two CJK characters, so a boundary check would find
  // 字幕 in no sentence anyone would actually write.
  assert.ok(hasWord("字幕付きの動画", "字幕"));
  assert.ok(hasWord("テロップを入れて", "テロップ"));
  assert.ok(!hasWord("a promo video", "字幕"));
});

test("no vocabulary yet means no settings, not a crash", () => {
  // capabilities.json may not have been written when the panel first loads.
  const got = readPromptSettings("a 15s tiktok", null);
  assert.deepStrictEqual(got,
    { seconds: null, aspect: null, cutRate: null, visual: false, subtitles: false,
      recipe: null });
});


// --- which kind of edit -----------------------------------------------------

const FORMATS = (() => {
  // Read the recipes' own keyword lists, so this test cannot pass against a
  // list that has drifted from what ships.
  const dir = path.join(__dirname, "..", "..", "engine", "recipes");
  return fs.readdirSync(dir).filter((f) => f.endsWith(".yaml")).map((file) => {
    const text = fs.readFileSync(path.join(dir, file), "utf8");
    const block = /\n  keywords:\n([\s\S]*?)\n  [a-z]/.exec(text);
    return {
      recipe: file.replace(/\.yaml$/, ""),
      keywords: block ? [...block[1].matchAll(/^\s*-\s*(.+)$/gm)].map((m) => m[1].trim()) : [],
    };
  });
})();

test("the recipe keywords were actually read", () => {
  const total = FORMATS.reduce((n, f) => n + f.keywords.length, 0);
  assert.ok(total >= 20, `only found ${total} keywords across ${FORMATS.length} recipes`);
});

test("a description picks the kind of edit it describes", () => {
  const pick = (t) => readPromptSettings(t, WORDS, FORMATS).recipe;
  assert.strictEqual(pick("a b-roll montage of the kitchen"), "promo-silent");
  assert.strictEqual(pick("a two camera podcast episode"), "podcast-2cam");
  assert.strictEqual(pick("a client testimonial, 60 seconds"), "client-promo");
  assert.strictEqual(pick("a 15 sec reel of me talking to camera"), "social-short");
});

test("a platform word does not choose the recipe", () => {
  // "tiktok video" once outranked "promo" on length and picked the talking-head
  // recipe for an explicit montage. Platform says the SHAPE; it is read
  // separately.
  const got = readPromptSettings(
    "make a 12 second tiktok video with a fast beat that acts as a dramatic promo",
    WORDS, FORMATS);
  assert.strictEqual(got.recipe, "promo-silent");
  assert.strictEqual(got.aspect, "vertical");
});

test("saying nothing about the kind leaves the recipe alone", () => {
  // Guessing from silence would change thresholds, tracks and filler handling
  // on no evidence.
  assert.strictEqual(
    readPromptSettings("a 15 sec video of the storefront", WORDS, FORMATS).recipe, null);
});


// --- what the request carries, now that no dropdown answers this -------------

test("a description that names no kind of edit still carries a recipe", () => {
  // The dropdown was the only thing answering this, and a request with no
  // recipe is one the helper refuses at the gate. The recipes declare where
  // silence lands; the panel is told the name rather than knowing one.
  const got = chooseRecipe("a 15 sec video of the storefront", WORDS, FORMATS, "client-promo");
  assert.strictEqual(got.recipe, "client-promo");
  assert.strictEqual(got.named, false, "nothing was named, so nothing is read back");
});

test("the words outrank the default", () => {
  const got = chooseRecipe("a b-roll montage of the kitchen", WORDS, FORMATS, "client-promo");
  assert.strictEqual(got.recipe, "promo-silent");
  assert.strictEqual(got.named, true);
});

test("a kind of edit the description stops naming is not kept", () => {
  // The dropdown could not do this. Its value was whatever the last keystroke
  // had pushed into it, so an editor who typed "podcast", thought again and
  // wrote something else kept the podcast thresholds under the new
  // description -- with the control closed inside Adjust, unread.
  const typed = chooseRecipe("a two camera podcast episode", WORDS, FORMATS, "client-promo");
  const rewritten = chooseRecipe("a video of the kitchen", WORDS, FORMATS, "client-promo");
  assert.strictEqual(typed.recipe, "podcast-2cam");
  assert.strictEqual(rewritten.recipe, "client-promo");
});

test("no capabilities yet means no recipe, not an invented one", () => {
  // Create is disabled until they land. Naming something here would put a
  // recipe in a request that no engine had said it supports.
  const got = chooseRecipe("a two camera podcast episode", null, null, undefined);
  assert.strictEqual(got.recipe, null);
  assert.strictEqual(got.named, false);
  assert.strictEqual(got.format, null);
});

test("a Japanese description names the kind of edit too", () => {
  // Found by running it: 厨房のプロモを 15 秒で chose nothing, because every
  // keyword but long-form's was English and the dropdown had been the way round
  // that. With no dropdown, keywords in both languages are the whole mechanism.
  const pick = (t) => chooseRecipe(t, WORDS, FORMATS, "client-promo");
  assert.strictEqual(pick("厨房のプロモを 15 秒で").recipe, "promo-silent");
  assert.strictEqual(pick("2カメの対談を編集して").recipe, "podcast-2cam");
  assert.strictEqual(pick("お客様の声、60秒").recipe, "client-promo");
  assert.strictEqual(pick("長尺のドキュメンタリー").recipe, "long-form");
  assert.strictEqual(pick("ショート動画、15秒、字幕付き").recipe, "social-short");
  for (const text of ["厨房のプロモを 15 秒で", "お客様の声、60秒"]) {
    assert.strictEqual(pick(text).named, true, `${text} was read as naming nothing`);
  }
  // A shop in Yokohama is not a landscape edit, and an ad agency's own profile
  // video is not a montage: 広告 was left out of promo-silent for this, and 会社
  // 紹介 is what actually describes the video.
  assert.strictEqual(pick("広告代理店の会社紹介").recipe, "client-promo");
  // Nothing about the kind of edit, in Japanese, still falls to the default.
  const quiet = pick("厨房と店先の動画");
  assert.strictEqual(quiet.recipe, "client-promo");
  assert.strictEqual(quiet.named, false);
});

test("the format that came with the recipe comes back with it", () => {
  // The caller applies its defaults -- length, shape, whether it cuts from
  // pictures -- so finding the block is part of choosing, not a second lookup
  // that can disagree.
  const got = chooseRecipe("a client testimonial", WORDS, FORMATS, "client-promo");
  assert.strictEqual(got.format && got.format.recipe, "client-promo");
});
