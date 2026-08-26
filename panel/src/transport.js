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

  /** Write a result summary back beside the plan, for the engine to pick up. */
  async writeReceipt(planName, receipt) {
    const folder = await folderFromToken(this.jobsToken);
    if (!folder) return;
    const name = planName.replace(/\.editplan\.json$/i, ".receipt.json");
    const file = await folder.createFile(name, { overwrite: true });
    await file.write(JSON.stringify(receipt, null, 2));
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
function makeResolver(mediaRootToken, musicRootToken) {
  const tokens = { media: mediaRootToken, music: musicRootToken };
  const names = { media: "Media root", music: "Music folder" };
  /**
   * @param {string} relPath
   * @param {string} [rootName] which configured root the path hangs off
   */
  return async function resolveAbsolutePath(relPath, rootName) {
    const which = rootName === "music" ? "music" : "media";
    const root = await folderFromToken(tokens[which]);
    if (!root) throw new Error(`${names[which]} is not set or is no longer reachable.`);
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
  loadSettings,
  saveSettings,
};
