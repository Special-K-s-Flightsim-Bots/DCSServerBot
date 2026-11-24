/* THE MODE-CONTROL HARNESS — the shipped `/static/presets-wizard.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS. The script's contract is a BEHAVIOUR — a change on a control marked
 * `data-pw-mode` submits the step's GET form, and nothing else does — and no assertion on the rendered
 * markup can prove what a script does with that markup. So this loads the REAL shipped file (no copy,
 * no re-implementation) into a minimal DOM stub: it models only what the file uses (`document
 * .addEventListener` for the delegated `change` listener, a target's `matches`, its `form`, the form's
 * `method` and its `submit`).
 *
 * Usage: node presets_wizard_harness.js <path to presets-wizard.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SCRIPT_PATH = process.argv[2];

const listeners = {};
global.document = {
  addEventListener(kind, fn) { (listeners[kind] = listeners[kind] || []).push(fn); },
};

/* boot the shipped file in the GLOBAL scope, so its `document` resolves to the stub above */
(0, eval)(fs.readFileSync(SCRIPT_PATH, "utf8"));

/* fire the same `change` event a browser would, on the given target */
function fire(target) {
  (listeners.change || []).forEach(function (fn) { fn({ target: target }); });
}

function form(method) {
  return { method: method, submitted: false, submit: function () { this.submitted = true; } };
}

function select(hostForm, isMode) {
  return {
    form: hostForm,
    matches: function (selector) { return isMode && selector === "[data-pw-mode]"; },
  };
}

const modeForm = form("get");
const valueForm = form("get");
const postForm = form("post");

fire(select(modeForm, true));      /* a mode control on the step's GET form -> submits */
fire(select(valueForm, false));    /* a plain value select -> nothing */
fire(select(postForm, true));      /* a mode control on a POST form (the review) -> nothing */

let bareTargetThrew = false;
try { fire({}); } catch (error) { bareTargetThrew = true; }   /* a target with no `matches` */

console.log(JSON.stringify({
  bound: (listeners.change || []).length,
  modeSubmits: modeForm.submitted,
  valueSubmits: valueForm.submitted,
  postSubmits: postForm.submitted,
  bareTargetThrew: bareTargetThrew,
}));
