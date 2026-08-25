"use strict";
/**
 * Auditioning a music track before committing to it.
 *
 * UXP has no audio playback -- no `<audio>`, no Web Audio -- so the panel cannot
 * play a track itself. It does not need to: Premiere's Source Monitor already
 * does this well, with a waveform and scrubbing the editor knows, and the
 * SourceMonitor API exposes enough to drive it and read the playhead back.
 *
 * Kept apart from the pure request logic because anything importing `premierepro`
 * cannot be loaded by `node --test`.
 */

const ppro = require("premierepro");

class AuditionError extends Error {
  constructor(message) {
    super(message);
    this.name = "AuditionError";
  }
}

/**
 * Open a track in the Source Monitor and start it playing.
 *
 * Deliberately does NOT import the file into the project: auditioning five
 * tracks to choose one would otherwise leave four of them in the bin.
 *
 * @param {string} absPath
 * @param {{play?: boolean, atSeconds?: number}} [options]
 */
async function audition(absPath, options = {}) {
  const opened = await ppro.SourceMonitor.openFilePath(absPath);
  if (!opened) {
    throw new AuditionError(
      `Premiere would not open ${absPath} in the Source Monitor. ` +
      `Check the file is still there and is a format Premiere reads.`
    );
  }
  if (options.atSeconds) {
    // Best effort: landing at the right place is a convenience, and failing to
    // is not a reason to refuse to play the track.
    try {
      await ppro.SourceMonitor.setPosition(ppro.TickTime.createWithSeconds(options.atSeconds));
    } catch {
      /* the editor can scrub there themselves */
    }
  }
  if (options.play !== false) {
    try {
      await ppro.SourceMonitor.play(1);
    } catch (err) {
      // Opened but would not play -- still useful, so say so rather than throw.
      return { opened: true, playing: false, detail: err.message };
    }
  }
  return { opened: true, playing: options.play !== false };
}

/**
 * Where the Source Monitor playhead is sitting, in seconds.
 *
 * This is the whole point of the feature: the editor finds the drop by ear and
 * the panel reads the position rather than asking them to transcribe a timecode.
 *
 * @returns {Promise<number>}
 */
async function playheadSeconds() {
  const position = await ppro.SourceMonitor.getPosition();
  if (!position) {
    throw new AuditionError(
      "Nothing is open in the Source Monitor. Press Audition first."
    );
  }
  return position.seconds;
}

/** The clip currently open there, so the panel can check it is the right one. */
async function auditionedName() {
  try {
    const item = await ppro.SourceMonitor.getProjectItem();
    return item ? item.name : null;
  } catch {
    return null;
  }
}

module.exports = { audition, playheadSeconds, auditionedName, AuditionError };
