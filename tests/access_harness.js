/* THE ACCESS MATRIX'S CHANGE BAR HARNESS — the shipped `/static/access.js` driven against a minimal
 * DOM, no browser.
 *
 * WHY A BROWSER-LESS HARNESS. The page works with JavaScript off (the form posts the whole matrix
 * and the server computes the delta), so what the script adds is FEEDBACK: does moving one cell make
 * the bar name it, count it, and does a second move name both? That is a behaviour of the loaded
 * file, not of the markup, so this loads the REAL shipped script (no copy) into a DOM stub that
 * implements only what it uses:
 *   document.querySelector/querySelectorAll/getElementById/createElement/createTextNode,
 *   element.addEventListener/appendChild/classList, input.checked/value/getAttribute, row.hidden,
 *   group.nextElementSibling.
 *
 * The document it is given is the shape `templates/access.html` renders: one `.acc-form` holding
 * group rows (`tr.grp`) and matrix rows (`tr.acc-row[data-cap]`), each row carrying a role checkbox
 * per column (with `data-was`) and the `managed_by` checkbox (with `data-cap-kind="manager"`), plus
 * the search box and the "show changes only" toggle in the page head.
 *
 * Usage:  node access_harness.js <path to access.js>
 *         -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const SCRIPT = process.argv[2];

// ------------------------------------------------------------------ a DOM stub, only what it uses

function ClassList() {
  const names = new Set();
  return {
    contains: (name) => names.has(name),
    add: (name) => names.add(name),
    remove: (name) => names.delete(name),
  };
}

function Element(tag, attributes) {
  const element = {
    tagName: tag.toUpperCase(),
    attributes: attributes || {},
    children: [],
    classList: ClassList(),
    hidden: false,
    checked: false,
    value: "",
    getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    addEventListener(kind, fn) { (this.listeners = this.listeners || {})[kind] = fn; },
    dispatch(kind, target) { if (this.listeners && this.listeners[kind]) { this.listeners[kind]({ target: target || this }); } },
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  // `textContent` behaves like the real one in the two directions the script uses: ASSIGNING it
  // replaces everything inside the element (that is how the bar is cleared before it is refilled),
  // and READING it is the concatenated text of the element's descendants
  let text = "";
  Object.defineProperty(element, "textContent", {
    get() {
      if (text) { return text; }
      return element.children.map((child) => child.textContent || child.nodeValue || "").join("");
    },
    set(value) {
      text = String(value);
      element.children.length = 0;
    },
  });
  if (attributes && attributes.class) {
    String(attributes.class).split(/\s+/).filter(Boolean).forEach((name) => element.classList.add(name));
  }
  return element;
}

function checkbox(kind, capability, role, was, checked) {
  const box = Element("input", { type: "checkbox", name: kind, "data-cap-kind": kind__kind(kind),
                                 value: capability, "data-was": was ? "1" : "0",
                                 "aria-label": capability + " \u2014 " + role });
  box.checked = !!checked;
  box.role = role;
  return box;
}

function kind__kind(kind) { return kind === "manager" ? "manager" : "role"; }

function row(capability, cells) {
  const tr = Element("tr", { class: "acc-row", "data-cap": capability, "data-search": capability });
  tr.boxes = cells;
  tr.querySelectorAll = () => tr.boxes;
  return tr;
}

// ---------------------------------------------------------------------- the document the page renders

const form = Element("form", {
  class: "acc-form",
  "data-added": "%(role)s added",
  "data-removed": "%(role)s removed",
  "data-manager-on": "managed_by granted",
  "data-manager-off": "managed_by removed",
  "data-pending": "%(count)s unsaved change(s)",
  "data-no-changes": "No overrides",
});

const group = Element("tr", { class: "grp" });
const music = row("music.write", [
  checkbox("cell", "music.write|DCS Admin", "DCS Admin", true, true),
  checkbox("cell", "music.write|Media Manager", "Media Manager", false, false),
  checkbox("manager", "music.write", "", false, false),
]);
const serverRow = row("servers.restart", [
  checkbox("cell", "servers.restart|DCS Admin", "DCS Admin", true, true),
]);
group.nextElementSibling = music;
music.nextElementSibling = serverRow;
serverRow.nextElementSibling = null;

form.querySelectorAll = (selector) => {
  if (selector.indexOf("tr.acc-row") >= 0) { return [music, serverRow]; }
  if (selector.indexOf("tr.grp") >= 0) { return [group]; }
  return [];
};

const count = Element("span", { id: "acc-count" });
const meta = Element("span", { id: "acc-meta" });
const apply = Element("button", { id: "acc-apply" });
const search = Element("input", { type: "search" });
const changes = Element("input", { type: "checkbox" });

const byId = { "acc-count": count, "acc-meta": meta, "acc-apply": apply };

const document = {
  querySelector(selector) {
    if (selector.indexOf(".acc-form") >= 0) { return form; }
    if (selector.indexOf("input[type=\"search\"]") >= 0) { return search; }
    if (selector.indexOf(".acc-toggle") >= 0) { return changes; }
    return null;
  },
  querySelectorAll() { return []; },
  getElementById(id) { return byId[id] || null; },
  createElement(tag) { return Element(tag, {}); },
  createTextNode(text) { return { nodeValue: text, textContent: text }; },
};

const sandbox = { document: document, window: {}, console: console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });

// ------------------------------------------------------------------------------------ the scenario

function barText() {
  return meta.textContent;
}

// nothing has been touched: the bar is whatever the server rendered
const before = { count: count.textContent, touched: barText() };

// move ONE cell: grant Media Manager on music.write
music.boxes[1].checked = true;
form.dispatch("change", music.boxes[1]);

const afterOne = { count: count.textContent, text: barText(), tag: count.classList.contains("a") };

// move a second one, in the OTHER row: grant managed_by on music.write
music.boxes[2].checked = true;
form.dispatch("change", music.boxes[2]);

const afterTwo = { count: count.textContent, text: barText() };

// take the first move back: one change left
music.boxes[1].checked = false;
form.dispatch("change", music.boxes[1]);

const afterUndo = { count: count.textContent, text: barText() };

// the filter narrows the rows (a search for a capability nobody declares hides everything)
search.value = "servers";
search.dispatch("input");
const filtered = { music: music.hidden, servers: serverRow.hidden, group: group.hidden };

console.log(JSON.stringify({ before, afterOne, afterTwo, afterUndo, filtered }, null, 2));
