"use strict";
/**
 * Plugin entry point.
 *
 * Must live at the plugin root and be loaded by `<script src="index.js">`, which
 * is the structure Adobe's own Premiere samples use. A `<script src="src/....js">`
 * pointing into a subdirectory does not execute -- the panel renders, every
 * button is inert, and nothing appears in the UXP log to say why.
 *
 * Submodules are require()d from here, exactly as the samples do.
 */

(function () {
  function line(msg, cls) {
    var logEl = document.getElementById("log");
    if (!logEl) return;
    var d = document.createElement("div");
    if (cls) d.className = cls;
    d.textContent = msg;
    logEl.appendChild(d);
  }

  try {
    require("./src/main.js");
  } catch (e) {
    // Surface startup failures in the panel itself; UXP swallows them otherwise.
    line("Startup error: " + (e && e.message ? e.message : String(e)), "err");
    if (e && e.stack) {
      String(e.stack).split("\n").slice(0, 6).forEach(function (l) {
        line("  " + l, "err");
      });
    }
  }
})();
