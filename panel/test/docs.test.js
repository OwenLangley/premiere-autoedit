"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

/**
 * The contract between a document and its translation.
 *
 * Prose is deliberately not compared. Wording drifts, and a test that fires on
 * every reworded sentence gets switched off within a month. What must not drift
 * is the part a reader copies into a terminal: a stale `brew install` line in a
 * language the author cannot proofread is how somebody ends up installing the
 * wrong Python and having no idea why.
 *
 * Reaches out of `panel/` exactly as `i18n.test.js` reaches into the engine's
 * note catalogue -- these files are the product too.
 */

const ROOT = path.join(__dirname, "..", "..");
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");

// Adding a language is adding a row here. Nothing else in this file changes.
const PAIRS = [
  { en: "README.md", ja: "README.ja.md" },
  { en: "docs/cutting-guide.md", ja: "docs/cutting-guide.ja.md" },
];

// Every markdown file this test knows how to walk, for the link check.
const ALL_DOCS = [
  ...PAIRS.flatMap((p) => [p.en, p.ja]),
  "CONTRIBUTING.md",
  "docs/how-it-works.md",
  "docs/setting-up-a-new-machine.md",
];

/**
 * Fenced blocks carrying a language tag, as "tag\nbody".
 *
 * Tagged and untagged are treated differently on purpose. A ```bash block is
 * commands and a ```jsonc block is a data sample: both are language-neutral and
 * must be identical in every translation. An untagged block is prose drawn as a
 * diagram, or output the engine prints in English, and translating or leaving
 * it are both legitimate.
 */
function taggedBlocks(text) {
  const blocks = [];
  let open = null;
  for (const line of text.split("\n")) {
    if (open === null) {
      const m = line.match(/^```([A-Za-z][\w-]*)\s*$/);
      if (m) open = { tag: m[1], body: [] };
    } else if (/^```\s*$/.test(line)) {
      blocks.push(`${open.tag}\n${open.body.join("\n")}`);
      open = null;
    } else {
      open.body.push(line);
    }
  }
  return blocks;
}

/**
 * Anything shaped like a version or a measurement: 26.3, 26.3.2, python@3.11.
 *
 * Catches ordinary decimals in prose as well -- "quality 0.71" -- which is
 * wanted rather than tolerated. A number that vanishes in translation is either
 * a dropped sentence or a typo, and both are worth a failing test.
 */
function versionTokens(text) {
  return [...new Set([
    ...[...text.matchAll(/\d+\.\d+(?:\.\d+)?/g)].map((m) => m[0]),
    ...[...text.matchAll(/python@\S+/g)].map((m) => m[0]),
  ])];
}

/** Relative markdown links, split into the file part and the #anchor part. */
function relativeLinks(text) {
  return [...text.matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)]
    .map((m) => m[1])
    .filter((href) => !/^(https?:|mailto:)/.test(href))
    .map((href) => {
      const hash = href.indexOf("#");
      return hash === -1
        ? { file: href, anchor: null }
        : { file: href.slice(0, hash), anchor: href.slice(hash + 1) };
    });
}

/** GitHub's heading slug, near enough for the anchors these docs actually use. */
function slug(heading) {
  return heading
    .replace(/^#+\s*/, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s+/g, "-");
}

function anchorsIn(rel) {
  return new Set(
    read(rel).split("\n").filter((l) => /^#{1,6}\s/.test(l)).map(slug)
  );
}

for (const { en, ja } of PAIRS) {
  test(`${ja} carries the same commands as ${en}`, () => {
    const a = taggedBlocks(read(en));
    const b = taggedBlocks(read(ja));
    // Both directions: a translation must not invent a command either.
    assert.deepEqual(
      b.filter((x) => !a.includes(x)), [],
      `these appear only in ${ja}`);
    assert.deepEqual(
      a.filter((x) => !b.includes(x)), [],
      `these appear in ${en} and not in ${ja}`);
  });

  test(`${ja} claims the same versions as ${en}`, () => {
    const translated = read(ja);
    const missing = versionTokens(read(en)).filter((t) => !translated.includes(t));
    assert.deepEqual(missing, [], `${ja} has lost or changed these`);
  });

  test(`${en} and ${ja} point at each other`, () => {
    // An orphaned translation is one nobody can find and nobody maintains.
    assert.ok(read(en).includes(path.basename(ja)), `${en} does not link to ${ja}`);
    assert.ok(read(ja).includes(path.basename(en)), `${ja} does not link to ${en}`);
  });
}

test("every relative link in the documentation resolves", () => {
  const broken = [];
  for (const doc of ALL_DOCS) {
    const from = path.dirname(doc);
    for (const link of relativeLinks(read(doc))) {
      const target = link.file === "" ? doc : path.normalize(path.join(from, link.file));
      if (!fs.existsSync(path.join(ROOT, target))) {
        broken.push(`${doc} -> ${link.file} (no such file)`);
        continue;
      }
      if (link.anchor && target.endsWith(".md") && !anchorsIn(target).has(link.anchor)) {
        broken.push(`${doc} -> ${link.file}#${link.anchor} (no such heading)`);
      }
    }
  }
  assert.deepEqual(broken, []);
});
