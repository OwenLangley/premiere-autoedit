"use strict";
/**
 * What a description implies about the settings.
 *
 * The engine reads the same sentence and is the authority on it: it decides the
 * running order, matches beats to footage, and builds the edit. This exists for
 * one reason -- so the form stops contradicting the prompt. An editor typing
 * "12 second tiktok" while the controls still read "Client promo, 60s,
 * landscape" is looking at a form that is lying to them, and this project has
 * already shipped one of those.
 *
 * It reads settings only. Splitting a sentence into shots stays in one place,
 * on the engine side, because that is the part with real grammar in it and two
 * implementations would drift. The shots are reviewed on the plan.
 *
 * The WORDS come from capabilities.json, written by the helper straight out of
 * story.py, so there is one vocabulary. Only the regex shapes are written twice,
 * and `engine/tests/fixtures/prompt-settings.json` is asserted by both sides so
 * they cannot disagree quietly.
 */

/** "15 sec", "15s", "90 seconds", "1 min 30 sec". */
const DURATION = /(?:(\d+)\s*(?:m|min|mins|minute|minutes)\b\s*)?(\d+)\s*(?:s|sec|secs|second|seconds)\b/i;
const MINUTES_ONLY = /\b(\d+)\s*(?:m|min|mins|minute|minutes)\b/i;
const CLOCK = /\b(\d{1,2}):(\d{2})\b/;

/** A word, not a fragment of one: "shorts" must not fire inside "shortstop". */
function hasWord(text, word) {
  const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`, "i").test(text);
}

/**
 * Seconds asked for, or null.
 *
 * A bare number is deliberately not a duration. "3 shots of the kitchen" is not
 * a three-second video, and guessing would set a length nobody asked for --
 * which is precisely the bug that produced a forty-second edit against a
 * fifteen-second intention.
 * @param {string} text
 */
function readDuration(text) {
  const clock = CLOCK.exec(text);
  if (clock) return Number(clock[1]) * 60 + Number(clock[2]);
  const m = DURATION.exec(text);
  if (m) return Number(m[2]) + (m[1] ? Number(m[1]) * 60 : 0);
  const mins = MINUTES_ONLY.exec(text);
  return mins ? Number(mins[1]) * 60 : null;
}

/**
 * Everything a description says about how the edit should be made.
 *
 * @param {string} text
 * @param {{platforms?: Record<string,string>, pace?: Record<string,number>, montage?: string[]}} words
 * @param {any[]} [formats] recipe formats, each with a `recipe` and `keywords`
 * @returns {{seconds: number|null, aspect: string|null, cutRate: number|null,
 *            visual: boolean, recipe: string|null}}
 */
function readPromptSettings(text, words, formats) {
  const empty = {
    seconds: null, aspect: null, cutRate: null, visual: false, recipe: null,
  };
  const source = String(text || "").trim();
  if (!source || !words) return empty;

  // Longest key first, so "instagram post" beats "post" and "high energy" is
  // not read as "energy".
  const longestFirst = (o) => Object.keys(o || {}).sort((a, b) => b.length - a.length);

  let aspect = null;
  for (const word of longestFirst(words.platforms)) {
    if (hasWord(source, word)) { aspect = words.platforms[word]; break; }
  }

  let cutRate = null;
  for (const word of longestFirst(words.pace)) {
    if (hasWord(source, word)) { cutRate = words.pace[word]; break; }
  }

  // Cutting to a rate means cutting from pictures: a rate has nothing to act on
  // otherwise, and the engine would accept it and quietly not use it.
  const montage = (words.montage || []).some((w) => hasWord(source, w));

  // Which KIND of edit. Longest keyword wins, so "case study" is not decided by
  // a shorter word in another format's list.
  let recipe = null;
  let longest = 0;
  for (const fmt of formats || []) {
    for (const word of fmt.keywords || []) {
      if (word.length > longest && hasWord(source, word)) {
        longest = word.length;
        recipe = fmt.recipe;
      }
    }
  }

  return {
    seconds: readDuration(source),
    aspect,
    cutRate,
    visual: montage || cutRate !== null,
    recipe,
  };
}

module.exports = { readPromptSettings, readDuration, hasWord };
