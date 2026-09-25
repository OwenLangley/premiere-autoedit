"use strict";
/**
 * How the panel receives EditPlans.
 *
 * v1 uses a shared folder rather than HTTP on purpose: `http://localhost` is an
 * unreliable UXP network target on macOS (macOS blocks plain http, and there are
 * open reports of installed plugins only getting network permission after the
 * UXP Developer Tool loads a dev build in the same session). A watched folder
 * needs no network permission at all and works offline.
 *
 * `HttpsTransport` is the seam for when media moves to the NAS and a shared
 * service exists. Both satisfy the same three methods, so switching is config.
 */

const fs = require("uxp").storage.localFileSystem;
const formats = require("uxp").storage.formats;
const { keepFileName } = require("./request");

/**
 * Get at a file two ways, because only one of them is blessed.
 *
 * `getEntryWithUrl` on an absolute path is the obvious route and may simply be
 * refused: UXP's real currency is the persistent token an editor granted through
 * the folder picker, and `localFileSystem: fullAccess` does not clearly extend
 * to arbitrary paths nobody picked.
 *
 * Every thumbnail lives under the work directory, which by default sits inside
 * the jobs folder -- and the panel holds a token for exactly that. So when the
 * direct route fails, walk down from the token instead. That is access the
 * editor explicitly granted, which is the kind UXP is built around.
 *
 * @param {string} absPath @param {string} [jobsToken]
 */
async function entryForImage(absPath, jobsToken) {
  try {
    const entry = await fs.getEntryWithUrl(`file://${absPath}`);
    if (entry) return entry;
  } catch {
    /* fall through to the token */
  }
  if (!jobsToken) return null;
  const folder = await folderFromToken(jobsToken);
  if (!folder || !folder.nativePath) return null;
  const root = folder.nativePath.replace(/\/+$/, "");
  if (absPath !== root && !absPath.startsWith(`${root}/`)) return null;
  const parts = absPath.slice(root.length + 1).split("/").filter(Boolean);
  let node = folder;
  for (const part of parts) {
    node = await node.getEntry(part);
    if (!node) return null;
  }
  return node;
}

/**
 * A local image as a data URI the panel can actually display.
 *
 * `<img src="file:///Users/...">` does not work for arbitrary paths. The
 * self-test proved a file:// src renders, but it proved it for a file in the
 * plugin's OWN data folder, which UXP always permits -- so it answered a
 * narrower question than it looked like it was answering. Thumbnails live in
 * the work directory, well outside that sandbox, and there the same src is
 * silently inert: no error, no load event, just an empty box.
 *
 * Reading the bytes through the storage API and inlining them sidesteps the
 * question entirely. `getEntryWithUrl` is the same call brandkit.js already
 * uses to read arbitrary absolute paths, so this is a route with mileage on it.
 *
 * Results are cached: a shot list re-renders on every click, and re-reading and
 * re-encoding thirty JPEGs each time would be felt.
 *
 * @param {string} absPath
 * @returns {Promise<string|null>} a data: URI, or null if it cannot be read
 */
const _imageCache = new Map();
/** The first failure, kept so the panel can say WHY rather than showing blanks. */
let _imageError = null;
function lastImageError() { return _imageError; }

async function readImageDataUri(absPath, jobsToken) {
  if (!absPath) return null;
  // Only successes are cached. Caching a null would make one transient failure
  // permanent for the life of the panel, and the editor's only symptom would be
  // pictures that never come back.
  if (_imageCache.has(absPath)) return _imageCache.get(absPath);
  let uri = null;
  try {
    const file = await entryForImage(absPath, jobsToken);
    if (!file) throw new Error("could not open the file by path or through the jobs folder");
    const bytes = await file.read({ format: formats.binary });
    const view = new Uint8Array(bytes);
    let binary = "";
    // Chunked: a 25KB still is 25,000 arguments to String.fromCharCode in one
    // go if applied naively, which blows the argument limit on bigger files.
    const CHUNK = 8192;
    for (let i = 0; i < view.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, view.subarray(i, i + CHUNK));
    }
    const b64 = typeof btoa === "function" ? btoa(binary) : Buffer.from(view).toString("base64");
    const ext = absPath.toLowerCase().endsWith(".png") ? "png" : "jpeg";
    uri = `data:image/${ext};base64,${b64}`;
  } catch (err) {
    // A missing still is a card without a picture, not a failed build -- but
    // swallowing the reason is how this went two rounds without a diagnosis.
    // Keep the first one so something can report it.
    if (!_imageError) {
      _imageError = `${absPath}: ${String((err && err.message) || err)}`;
    }
    return null;
  }
  _imageCache.set(absPath, uri);
  return uri;
}

const SETTINGS_KEY = "autoedit.settings";

/** Folder access has to be granted by the editor once; the token persists it. */
async function pickFolder(promptLabel) {
  const folder = await fs.getFolder({ initialDomain: undefined });
  if (!folder) return null;
  const token = await fs.createPersistentToken(folder);
  return { token, name: folder.name, path: folder.nativePath || folder.name, label: promptLabel };
}

async function folderFromToken(token) {
  if (!token) return null;
  try {
    return await fs.getEntryForPersistentToken(token);
  } catch {
    // Token goes stale when the folder is moved, renamed or on an unmounted volume.
    return null;
  }
}

class LocalFolderTransport {
  /** @param {string} jobsToken persistent token for the jobs folder */
  constructor(jobsToken) {
    this.jobsToken = jobsToken;
  }

  async available() {
    return (await folderFromToken(this.jobsToken)) !== null;
  }

  /** Newest first, so the plan the editor just generated is at the top. */
  async listPlans() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) {
      throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    }
    const entries = await folder.getEntries();
    const plans = [];
    for (const e of entries) {
      if (!e.isFile || !/\.editplan\.json$/i.test(e.name)) continue;
      let stamp = 0;
      try {
        const meta = await e.getMetadata();
        stamp = meta.dateModified ? new Date(meta.dateModified).getTime() : 0;
      } catch {
        /* metadata is a nicety; a plan without it still lists */
      }
      plans.push({ name: e.name, entry: e, modified: stamp });
    }
    return plans.sort((a, b) => b.modified - a.modified);
  }

  async readPlan(entry) {
    const text = await entry.read();
    try {
      return JSON.parse(text);
    } catch (err) {
      throw new Error(`${entry.name} is not valid JSON: ${err.message}`);
    }
  }

  /** Read the helper's capabilities file, or null when it has never run. */
  async listCapabilities() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const file = await folder.getEntry("capabilities.json");
      return JSON.parse(await file.read());
    } catch {
      return null;
    }
  }

  /** The helper's index of the music library, or null when none is set. */
  async listMusicIndex() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const file = await folder.getEntry("music-index.json");
      return JSON.parse(await file.read());
    } catch {
      return null;
    }
  }

  /**
   * Panel-owned settings the helper re-reads while running.
   *
   * The helper owns the roots, but the editor is the one who knows where the
   * music lives -- routing that through config.json means choosing a folder is
   * a click rather than a helper restart.
   */
  async writeConfig(patch) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    let current = {};
    try {
      current = JSON.parse(await (await folder.getEntry("config.json")).read());
    } catch {
      /* first write */
    }
    const next = { ...current, ...patch };
    const file = await folder.createFile("config.json", { overwrite: true });
    await file.write(JSON.stringify(next, null, 2));
    return next;
  }

  /** The helper's index of the media root, or null if it has not run. */
  async listMediaIndex() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const file = await folder.getEntry("media-index.json");
      return JSON.parse(await file.read());
    } catch {
      return null;
    }
  }

  /**
   * Every shot the helper has found across the whole media library.
   *
   * Separate from the plan on purpose. The plan is a record of one job and must
   * not change after it is written; this is a rolling index of the footage, and
   * it grows as the helper works through the library in the background. Null
   * until the helper has written it once.
   */
  async listLibraryShots() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const file = await folder.getEntry("library-shots.json");
      return JSON.parse(await file.read());
    } catch {
      return null;
    }
  }

  /** Write a job request for the helper to pick up. */
  async writeRequest(name, request) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    const file = await folder.createFile(name, { overwrite: true });
    await file.write(JSON.stringify(request, null, 2));
    return name;
  }

  /** Current status of one job, or null before the helper has touched it. */
  async readStatus(jobId) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const file = await folder.getEntry(`${jobId}.status.json`);
      return JSON.parse(await file.read());
    } catch {
      return null;
    }
  }

  /**
   * Ask the helper to collect a diagnostic bundle.
   *
   * A marker file, because that is the only channel these two have: the panel
   * cannot run a shell script -- UXP has no child process -- and an editor
   * should not have to open Terminal to report a bug.
   */
  async requestReport() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    try {
      await (await folder.getEntry("report.result.json")).delete();
    } catch { /* no previous result is the ordinary case */ }
    const file = await folder.createFile("report.request", { overwrite: true });
    await file.write(new Date().toISOString());
  }

  /** Where the helper put it, or null while it is still working. */
  async readReportResult() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      return JSON.parse(await (await folder.getEntry("report.result.json")).read());
    } catch {
      return null;
    }
  }

  /**
   * Ask the helper how far behind this checkout is.
   *
   * Separate from `requestUpdate` because the answers are separate acts: this
   * one installs nothing, which is what makes it safe to ask on every panel
   * load. An editor should not have to wonder whether there is a fix.
   */
  async requestUpdateCheck() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    try {
      await (await folder.getEntry("update-check.result.json")).delete();
    } catch { /* no previous answer is the ordinary case */ }
    const file = await folder.createFile("update-check.request", { overwrite: true });
    await file.write(new Date().toISOString());
  }

  /**
   * The last answer, or null if none has been written.
   *
   * Worth reading BEFORE asking for a fresh one: the helper does one thing at a
   * time, so a check can sit unread for as long as a job takes, and the last
   * answer is better than a button that says "Checking" for five minutes.
   */
  async readUpdateCheckResult() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      return JSON.parse(await (await folder.getEntry("update-check.result.json")).read());
    } catch {
      return null;
    }
  }

  /** Ask the helper to pull and install the latest version. */
  async requestUpdate() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    try {
      await (await folder.getEntry("update.result.json")).delete();
    } catch { /* no previous result is the ordinary case */ }
    const file = await folder.createFile("update.request", { overwrite: true });
    await file.write(new Date().toISOString());
  }

  /**
   * True while `update.request` is still sitting there unread.
   *
   * The helper deletes the marker the moment it picks the request up, so this
   * separates "the helper has not heard me" from "the update is running" --
   * which the panel previously could not tell apart, and reported both as the
   * helper not running.
   */
  async updateRequestPending() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return false;
    try {
      await folder.getEntry("update.request");
      return true;
    } catch {
      return false;               // picked up, or never written
    }
  }

  /** How the update went, or null while it is still running. */
  async readUpdateResult() {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      return JSON.parse(await (await folder.getEntry("update.result.json")).read());
    } catch {
      return null;
    }
  }

  /** Write a result summary back beside the plan, for the engine to pick up. */
  async writeReceipt(planName, receipt) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return;
    const name = planName.replace(/\.editplan\.json$/i, ".receipt.json");
    const file = await folder.createFile(name, { overwrite: true });
    await file.write(JSON.stringify(receipt, null, 2));
  }

  /**
   * The last build of a plan, or null if it has never been built.
   *
   * The receipt is where the verifier's account of the build lives -- which
   * clips landed wrong, which gaps opened -- and until now nothing read it
   * back. It was written and never looked at.
   */
  async readReceipt(planName) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return null;
    try {
      const name = planName.replace(/\.editplan\.json$/i, ".receipt.json");
      return JSON.parse(await (await folder.getEntry(name)).read());
    } catch {
      return null;   // never built is the ordinary case, not a failure
    }
  }

  /**
   * Tell the helper a reference is in play, so it can start listening to the
   * music library before Create is pressed.
   *
   * Best effort and deliberately quiet: this is a head start, not a feature.
   * If the folder is unreachable the editor still gets their edit, just without
   * a music match on this run -- so nothing here is worth an error in the log.
   */
  async requestMusicIndex() {
    try {
      const folder = await folderFromToken(this.jobsToken);
      if (!folder) return;
      const file = await folder.createFile("music-index.request", { overwrite: true });
      await file.write(new Date().toISOString());
    } catch { /* a head start that did not happen is not a failure */ }
  }

  /**
   * Is this plan marked to survive the helper's sweep?
   *
   * A marker file beside the plan rather than a field inside it, for two
   * reasons: the plan is the engine's output and the panel does not own it, and
   * the helper has to read this decision without parsing a megabyte of JSON for
   * every plan in the folder.
   */
  async isKept(planName) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return false;
    try {
      await folder.getEntry(keepFileName(planName));
      return true;
    } catch {
      return false;     // not marked is the ordinary case
    }
  }

  async setKept(planName, keep) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) throw new Error("Jobs folder is not reachable. Re-select it in settings.");
    if (keep) {
      const file = await folder.createFile(keepFileName(planName), { overwrite: true });
      await file.write(new Date().toISOString());
      return;
    }
    try {
      await (await folder.getEntry(keepFileName(planName))).delete();
    } catch { /* already unmarked */ }
  }
}


/** Seam for the NAS-era shared service. Same three methods. */
class HttpsTransport {
  constructor(baseUrl) {
    this.baseUrl = String(baseUrl || "").replace(/\/$/, "");
  }
  async available() {
    // Requires the host in manifest requiredPermissions.network.domains.
    try {
      const r = await fetch(`${this.baseUrl}/health`);
      return r.ok;
    } catch {
      return false;
    }
  }
  async listPlans() {
    const r = await fetch(`${this.baseUrl}/plans`);
    if (!r.ok) throw new Error(`service returned ${r.status}`);
    return (await r.json()).map((p) => ({ name: p.name, href: p.href, modified: p.modified }));
  }
  async readPlan(ref) {
    const r = await fetch(`${this.baseUrl}${ref.href}`);
    if (!r.ok) throw new Error(`could not fetch ${ref.name}: ${r.status}`);
    return r.json();
  }
  async writeReceipt(planName, receipt) {
    await fetch(`${this.baseUrl}/receipts/${encodeURIComponent(planName)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(receipt),
    });
  }
}

/**
 * Turn a plan-relative media path into something Premiere can open.
 * Today the root is a local drive; when it becomes a NAS mount only this
 * setting changes -- which is the whole reason plans never store absolute paths.
 */
/**
 * Resolve a plan's relative paths through the folders this panel was granted.
 *
 * `extraTokens` maps the additional root names -- media2, media3 -- to their own
 * folder tokens. UXP grants access per folder, so a second drive needs its own
 * grant; there is no way to widen the first one to cover it.
 *
 * @param {string} mediaRootToken @param {string} musicRootToken
 * @param {Record<string,string>} [extraTokens]
 */
function makeResolver(mediaRootToken, musicRootToken, extraTokens) {
  const tokens = { media: mediaRootToken, music: musicRootToken, ...(extraTokens || {}) };
  const names = { media: "Media root", music: "Music folder" };
  /**
   * @param {string} relPath
   * @param {string} [rootName] which configured root the path hangs off
   */
  return async function resolveAbsolutePath(relPath, rootName) {
    const which = tokens[rootName] ? rootName : (rootName === "music" ? "music" : "media");
    const root = await folderFromToken(tokens[which]);
    if (!root) {
      // Name the root that is missing. "Media root is not set" while the clip
      // is on a second drive sends someone to re-pick a folder that was never
      // the problem.
      throw new Error(
        `${names[which] || `Folder for "${which}"`} is not set or is no longer ` +
        `reachable. If the footage is on an external drive, check it is plugged in.`);
    }
    const parts = String(relPath).normalize("NFC").split("/").filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      node = await node.getEntry(parts[i]);
    }
    const file = await node.getEntry(parts[parts.length - 1]);
    return file.nativePath;
  };
}

// --------------------------------------------------------------- settings

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveSettings(patch) {
  const next = { ...loadSettings(), ...patch };
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
  return next;
}

/** Video files sitting in the media root, for the clip picker. */
async function listMediaFiles(mediaRootToken, isVideoFile) {
  const root = await folderFromToken(mediaRootToken);
  if (!root) return [];
  const entries = await root.getEntries();
  return entries
    .filter((e) => e.isFile && isVideoFile(e.name))
    .map((e) => e.name)
    .sort((a, b) => a.localeCompare(b));
}

module.exports = {
  listMediaFiles,
  LocalFolderTransport,
  HttpsTransport,
  pickFolder,
  folderFromToken,
  makeResolver,
  readImageDataUri,
  lastImageError,
  loadSettings,
  saveSettings,
};
