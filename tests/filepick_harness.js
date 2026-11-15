/* THE FILE-CONTROL HARNESS — the shipped `/static/filepick.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The card asks for a chosen file's NAME to appear beside
 * the console's own control, and the markup alone cannot show that — the readout is written by the
 * script on the input's `change`. So this loads the REAL shipped file — no copy, no re-implementation
 * — into a minimal DOM stub and fires the events a browser would. The stub implements only what
 * filepick.js uses: `document.querySelectorAll`, `readyState`, the control's two `querySelector`
 * lookups, the input's `files` + `addEventListener`, and the span's `textContent`.
 *
 * Usage: node filepick_harness.js <path to filepick.js> -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");

function boot() {
  const handlers = {};
  const input = {
    files: [],
    addEventListener(kind, fn) { (handlers[kind] = handlers[kind] || []).push(fn); },
    fire(kind) { (handlers[kind] || []).forEach((fn) => fn({})); },
  };
  const name = { textContent: "" };
  const control = {
    querySelector(selector) {
      if (selector === 'input[type="file"]') { return input; }
      if (selector === "[data-file-name]") { return name; }
      return null;
    },
  };
  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelectorAll(selector) { return selector === "[data-file-pick]" ? [control] : []; },
  };
 /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  const run = new Function("document", "window", SOURCE);
  run(document, {});
  return { input, name };
}

const result = {};

/* 1. Nothing chosen on arrival: the console shows nothing (never the browser's "no file chosen"). */
{
  const { input, name } = boot();
  result.initial = name.textContent;

 /* 2. A file is picked: its NAME appears, in the console's own words. */
  input.files = [{ name: "Training.miz" }];
  input.fire("change");
  result.firstPick = name.textContent;

 /* 3. Picking again changes it. */
  input.files = [{ name: "Rotated.miz" }];
  input.fire("change");
  result.secondPick = name.textContent;

 /* 4. The pick is cleared: the name clears with it. */
  input.files = [];
  input.fire("change");
  result.cleared = name.textContent;
}

process.stdout.write(JSON.stringify(result));
