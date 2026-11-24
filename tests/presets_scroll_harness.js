/* THE SCROLL-CARRY HARNESS — the shipped `/static/presets-wizard.js` driven against a minimal DOM to
 * prove a re-render keeps the operator where they were.
 *
 * The script is read from the shipped file (no copy), booted with a stub `window`/`sessionStorage`/
 * `document`. A mode control's change submits the step's GET form (proved by the sibling harness) AND
 * remembers the changed control; a Next/Back submit remembers the scroll offset. On the next load the
 * stored position is restored: the control scrolled into view, or the offset restored.
 *
 * usage: node presets_scroll_harness.js <presets-wizard.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SCRIPT_PATH = process.argv[2];

const listeners = {};
const store = {};
const calls = { scrolled: [], scrolledTo: null };

const window = {
  sessionStorage: {
    getItem(key) { return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null; },
    setItem(key, value) { store[key] = String(value); },
    removeItem(key) { delete store[key]; },
  },
  scrollY: 1234,
  scrollTo(x, y) { calls.scrolledTo = [x, y]; },
};

const field = {
  tagName: "SELECT",
  getAttribute(name) { return name === "name" ? "clouds" : null; },
};
const restoreTarget = { scrollIntoView(options) { calls.scrolled.push(options); } };

const document = {
  addEventListener(kind, handler) { (listeners[kind] = listeners[kind] || []).push(handler); },
  activeElement: null,
  querySelector(selector) {
    return selector === '[name="clouds"]' ? restoreTarget : null;
  },
};

global.window = window;
global.document = document;
(0, eval)(fs.readFileSync(SCRIPT_PATH, "utf8"));

function fire(kind, target) {
  (listeners[kind] || []).forEach((handler) => handler({ target: target }));
}

function form(method) {
  return { method: method, submitted: false, submit() { this.submitted = true; } };
}

// 1. a mode control's change: submits the step AND remembers the control (scrollY carried too)
const modeForm = form("get");
document.activeElement = field;
fire("change", { matches: (selector) => selector === "[data-pw-mode]", form: modeForm });
const afterModeChange = {
  submitted: modeForm.submitted,
  stored: store["dcssb:presets-scroll"] || null,
};

// 2. the next load restores it: the control is scrolled into view, and the slot is consumed
fire("DOMContentLoaded", {});
const afterRestore = { scrolled: calls.scrolled.length, left: store["dcssb:presets-scroll"] || null };

// 3. a Next/Back press (a submit button, not a field): remembers the OFFSET, so the offset is restored
const nextForm = form("get");
document.activeElement = { tagName: "BUTTON", getAttribute: () => "step" };
fire("submit", nextForm);
const afterNext = { stored: store["dcssb:presets-scroll"] || null };
fire("DOMContentLoaded", {});
const afterNextRestore = { scrolledTo: calls.scrolledTo, left: store["dcssb:presets-scroll"] || null };

console.log(JSON.stringify({
  bound: (listeners.change || []).length,
  afterModeChange: afterModeChange,
  afterRestore: afterRestore,
  afterNext: afterNext,
  afterNextRestore: afterNextRestore,
}));
