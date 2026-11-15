/* Drive the DCS update dialog's rendered inline script against a minimal DOM and report what the
 * VERSION pick does: the read it issues on open, the one a branch change issues, the entries it
 * draws (newest first, the installed one marked) and what it leaves behind when the read cannot
 * answer.
 *
 * The script is read from the RENDERED page (pytest extracts the inline <script> and writes it to a
 * file), so this exercises the shipped bytes rather than a re-spelling of them. `node` has no DOM,
 * so only the surfaces THIS script touches are faked: the dialog's form, the two <select>s, an
 * <option> factory, and `fetch`.
 *
 * usage: node dcs_versions_harness.js <rendered-inline-script.js> <answer-json> [initial-json]
 *        the answer-json is what the stubbed route answers: {"versions": [...]} or
 *        {"versions": [], "reason": "..."}; the initial-json is the entries the SERVER drew
 *        (the dialog renders the cached newest/installed build, so the pick is not empty when the
 *        page opens).
 * prints one JSON object: the read URLs, the pick's entries and its value, before and after a
 * branch change.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const answer = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

/* ---------------------------------------------------------------- the faked surfaces */

function makeOption() {
  return {
    value: "",
    textContent: "",
    disabled: false,
    selected: false,
    _notice: false,
    parentNode: null,
    setAttribute(name) { if (name === "data-versions-notice") { this._notice = true; } },
  };
}

const options = [];
const parentNode = {
  removeChild(node) {
    const at = options.indexOf(node);
    if (at >= 0) { options.splice(at, 1); }
  },
};

function append(node) {
  node.parentNode = parentNode;
  options.push(node);
  return node;
}

const versionPick = {
  value: "",
  title: "",
  get options() { return options; },
  get firstChild() { return options.length ? options[0] : null; },
  appendChild(node) { return append(node); },
  remove(index) { options.splice(index, 1); },
  insertBefore(node, reference) {
    const at = reference ? options.indexOf(reference) : -1;
    node.parentNode = parentNode;
    options.splice(at < 0 ? options.length : at, 0, node);
    return node;
  },
  querySelector(selector) {
    if (selector !== "[data-versions-notice]") { return null; }
    return options.find((option) => option._notice) || null;
  },
  setAttribute() {},
  removeAttribute() {},
};

const listeners = {};
const branchPick = {
  value: "",
  addEventListener(event, handler) { (listeners[event] = listeners[event] || []).push(handler); },
};

const noBranch = process.argv[5] === "--no-branch";

const form = {
  querySelector(selector) {
    if (!noBranch && selector.indexOf("branch") >= 0) { return branchPick; }
    if (selector.indexOf("version") >= 0) { return versionPick; }
    return null;
  },
};

const document = {
  getElementById(id) { return id === "dlg-form" ? form : null; },
  createElement(tag) { return makeOption(tag); },
};

/* THE ENTRIES THE SERVER DREW: the page's own <select> is not empty when it opens. */
if (process.argv[4]) {
  JSON.parse(fs.readFileSync(process.argv[4], "utf8")).forEach((entry) => {
    const option = makeOption();
    option.value = entry.value;
    option.textContent = entry.label;
    append(option);
  });
}

const asked = [];
const fetchStub = (url) => {
  asked.push(url);
  return Promise.resolve({ json: () => Promise.resolve(answer) });
};

class AbortControllerStub {
  constructor() { this.signal = {}; }
  abort() { this.aborted = true; }
}

const sandbox = {
  document,
  fetch: fetchStub,
  encodeURIComponent,
  console,
  window: { AbortController: AbortControllerStub },
  setTimeout,
};
sandbox.globalThis = sandbox;

/* ---------------------------------------------------------------- the run */

function snapshot() {
  return {
    asked: asked.slice(),
    branch: branchPick.value,
    value: versionPick.value,
    title: versionPick.title,
    options: options.map((option) => ({
      value: option.value,
      label: option.textContent,
      disabled: !!option.disabled,
      selected: !!option.selected,
    })),
  };
}

function tick() { return new Promise((resolve) => setTimeout(resolve, 0)); }

(async () => {
  /* the pick states the branch the console knows as installed, exactly as the server rendered it */
  const facts = /"installed_branch":\s*"([^"]*)"/.exec(source);
  branchPick.value = "";
  vm.runInNewContext(source, sandbox);
  /* the script reads `page_facts.installed_branch`; the empty pick falls back to it */
  await tick();
  await tick();
  const onOpen = snapshot();

  /* A BRANCH CHANGE is the pick's own event: the same read, for the chosen branch. A dialog with
     NO branch pick has no such event — its one read stands for the installed branch. */
  let onBranchChange = null;
  if (!noBranch) {
    branchPick.value = "testing";
    (listeners.change || []).forEach((handler) => handler({}));
    await tick();
    await tick();
    onBranchChange = snapshot();
  }

  process.stdout.write(JSON.stringify({ onOpen, onBranchChange }));
})();
