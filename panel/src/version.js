"use strict";
/**
 * Which Premiere this panel needs, and whether this is it.
 *
 * Its own module because `apply.js` requires `premierepro`, which exists only
 * inside Premiere -- so nothing in apply.js can be tested outside it. This
 * logic gates a warning shown on every build: wrong one way it cries wolf until
 * warnings are ignored, wrong the other it stays silent while an editor meets
 * the missing calls one at a time, which is exactly what happened.
 */

/**
 * The oldest Premiere that has every call this panel makes.
 *
 * Measured against Adobe's own published type packages rather than guessed:
 * `createSubClipAction` is absent from @adobe/premierepro 26.2.0 and present in
 * 26.3.0, and `createSequenceWithPresetPath` is in 26.2.0 but not in a
 * colleague's 26.0.1.
 */
const MIN_PREMIERE = [26, 3];

/**
 * `"26.0.1"` as `[26, 0, 1]`, or null when it cannot be read.
 * @param {string|null|undefined} raw
 */
function parseVersion(raw) {
  if (!raw) return null;
  const parts = String(raw).split(".").map((n) => parseInt(n, 10));
  return parts.length && Number.isFinite(parts[0]) ? parts : null;
}

/**
 * Is this build older than the one the panel is written against?
 *
 * An unknown version is NOT old. A false alarm on every build teaches editors
 * to ignore the warning that matters.
 * @param {number[]|null} version
 */
function olderThanSupported(version) {
  if (!version) return false;
  const [major, minor = 0] = version;
  return major < MIN_PREMIERE[0]
    || (major === MIN_PREMIERE[0] && minor < MIN_PREMIERE[1]);
}

module.exports = { MIN_PREMIERE, parseVersion, olderThanSupported };
