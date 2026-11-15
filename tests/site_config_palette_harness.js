/* THE PALETTE BLOCK'S PAINT HARNESS — the shipped `static/site-config.js` and
 * `static/site-palette-wizard.js` driven browser-lessly against a minimal DOM.
 *
 * WHY A HARNESS. What the block does is a WRITE into another document: which inline custom
 * properties land on the preview's `documentElement`, and which scheme its `data-theme` carries.
 * None of that is markup, so none of it can be asserted on a rendered page without a browser. This
 * loads the REAL shipped files (no copy, no re-implementation) into a DOM stub built from the
 * RENDERED card, and fires the events a browser would.
 *
 * THE TREE IS THE PAGE'S. The node tree handed in is parsed out of the response the application
 * renders, attributes and all — a stub built by hand here would prove nothing about the controls
 * the operator actually presses.
 *
 * THE STUB implements only what the two files touch: `document.querySelectorAll`/`querySelector`,
 * an element's attributes, `classList`, `closest`, `querySelector`/`querySelectorAll`,
 * `addEventListener`/`dispatchEvent`, and an iframe's `contentDocument.documentElement` with the
 * inline `style` and `data-theme` the paint writes. Nothing is provided that the scripts do not
 * use, so a script that grew a dependency on a real browser feature fails here.
 *
 * Usage: node site_config_palette_harness.js <site-config.js> <site-palette-wizard.js> <tree.json>
 *        <signals.json>   -> one JSON object on stdout
 *
 * `signals.json` carries the sheet text the operator "types": `lines` (declaration lines) and
 * `junk` (a line the grammar does not describe).
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const CONFIG_SOURCE = fs.readFileSync(process.argv[2], "utf8");
const WIZARD_SOURCE = fs.readFileSync(process.argv[3], "utf8");
const TREE = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const SIGNALS = JSON.parse(fs.readFileSync(process.argv[5], "utf8"));

/* ------------------------------------------------------------------ the minimal DOM */

function splitCompound(compound) {
  const parts = {tag: null, classes: [], attrs: [], checked: false};
  let rest = compound;
  const tag = rest.match(/^([a-zA-Z][\w-]*|\*)/);
  if (tag) {
    parts.tag = tag[1] === "*" ? null : tag[1].toUpperCase();
    rest = rest.slice(tag[1].length);
  }
  const token = /(\.([\w-]+))|(\[([\w-]+)(?:([~^$*|]?)=("([^"]*)"))?\])|(:checked)/g;
  let found;
  while ((found = token.exec(rest)) !== null) {
    if (found[2]) {
      parts.classes.push(found[2]);
    } else if (found[4]) {
      /* the group is the modifier alone; `=` sits outside it, so a plain match carries "" */
      const operator = found[6] === undefined ? "" : (found[5] || "") + "=";
      parts.attrs.push([found[4], operator, found[7]]);
    } else if (found[8]) {
      parts.checked = true;
    }
  }
  return parts;
}

function matchesCompound(node, compound) {
  const parts = splitCompound(compound);
  if (!node || !node.tagName) {
    return false;
  }
  if (parts.tag && node.tagName !== parts.tag) {
    return false;
  }
  if (!parts.classes.every((name) => node.classList.contains(name))) {
    return false;
  }
  if (!parts.attrs.every(([name, operator, value]) => {
    if (!node.hasAttribute(name)) {
      return false;
    }
    const have = node.getAttribute(name);
    if (!operator) {
      return true;
    }
    if (operator === "=") {
      return have === value;
    }
    if (operator === "^=") {
      return have.startsWith(value);
    }
    if (operator === "$=") {
      return have.endsWith(value);
    }
    return have.indexOf(value) !== -1;
  })) {
    return false;
  }
  if (parts.checked && !node.checked) {
    return false;
  }
  return true;
}

function elements(root) {
  const found = [];
  const walk = (node) => {
    (node.children || []).forEach((child) => {
      found.push(child);
      walk(child);
    });
  };
  walk(root);
  return found;
}

function select(root, selector) {
  const chain = String(selector).trim().split(/\s+/);
  return elements(root).filter((node) => {
    if (!matchesCompound(node, chain[chain.length - 1])) {
      return false;
    }
    let cursor = node.parentNode;
    for (let index = chain.length - 2; index >= 0; index -= 1) {
      while (cursor && !matchesCompound(cursor, chain[index])) {
        cursor = cursor.parentNode;
      }
      if (!cursor) {
        return false;
      }
      cursor = cursor.parentNode;
    }
    return true;
  });
}

function makeStyle() {
  const values = {};
  return {
    values,
    setProperty(name, value) { values[name] = String(value); },
    removeProperty(name) { delete values[name]; },
    getPropertyValue(name) { return Object.prototype.hasOwnProperty.call(values, name)
      ? values[name] : ""; },
  };
}

function makeRoot() {
  const attributes = {"data-theme": TREE.frame_theme === undefined ? "dark" : TREE.frame_theme};
  return {
    tagName: "HTML",
    style: makeStyle(),
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
    },
    setAttribute(name, value) { attributes[name] = String(value); },
    removeAttribute(name) { delete attributes[name]; },
  };
}

function makeElement(node) {
  const attributes = Object.assign({}, node.attrs || {});
  const classes = new Set(String(attributes["class"] || "").split(/\s+/).filter(Boolean));
  const listeners = {};
  const element = {
    tagName: String(node.tag || "").toUpperCase(),
    attributes,
    children: [],
    parentNode: null,
    listeners,
    value: node.value || "",
    textContent: node.text || "",
    hidden: Boolean(node.hidden),
    checked: Boolean(node.checked),
    dataset: {},
    get className() { return [...classes].join(" "); },
    classList: {
      contains: (name) => classes.has(name),
      add: (name) => { classes.add(name); },
      remove: (name) => { classes.delete(name); },
      toggle: (name, force) => {
        const on = force === undefined ? !classes.has(name) : Boolean(force);
        if (on) {
          classes.add(name);
        } else {
          classes.delete(name);
        }
        return on;
      },
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
    },
    setAttribute(name, value) { attributes[name] = String(value); },
    removeAttribute(name) { delete attributes[name]; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name); },
    addEventListener(type, handler) {
      (listeners[type] = listeners[type] || []).push(handler);
    },
    dispatchEvent(event) {
      let cursor = element;
      while (cursor) {
        (cursor.listeners[event.type] || []).forEach((handler) => {
          handler.call(cursor, Object.assign(event, {currentTarget: cursor}));
        });
        cursor = event.bubbles ? cursor.parentNode : null;
      }
      return true;
    },
    closest(selector) {
      let cursor = element;
      while (cursor) {
        if (matchesCompound(cursor, selector)) {
          return cursor;
        }
        cursor = cursor.parentNode;
      }
      return null;
    },
    querySelector(selector) { return select(element, selector)[0] || null; },
    querySelectorAll(selector) { return select(element, selector); },
    appendChild(child) { child.parentNode = element; element.children.push(child); return child; },
    insertBefore(child, before) {
      const at = element.children.indexOf(before);
      child.parentNode = element;
      element.children.splice(at < 0 ? element.children.length : at, 0, child);
      return child;
    },
  };
  Object.keys(attributes).forEach((name) => {
    if (name.startsWith("data-")) {
      element.dataset[name.slice(5).replace(/-([a-z])/g, (w, c) => c.toUpperCase())] = attributes[name];
    }
  });
  return element;
}

function build(node, parent) {
  const element = makeElement(node);
  element.parentNode = parent || null;
  if (node.frame) {
    element.contentDocument = {
      readyState: "complete",
      documentElement: makeRoot(),
      querySelector: () => null,
      querySelectorAll: () => [],
    };
  }
  (node.children || []).forEach((child) => {
    element.children.push(build(child, element));
  });
  return element;
}

const form = build(TREE.form, null);
const documentNode = {
  tagName: "#DOCUMENT",
  children: [form],
  parentNode: null,
  /* the page's own delegated listeners (`sc-preview-mounted`): recorded, never fired by the stub */
  listeners: {},
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  },
  querySelector: (selector) => select(documentNode, selector)[0] || null,
  querySelectorAll: (selector) => select(documentNode, selector),
  createElement: (tag) => makeElement({tag, attrs: {}}),
};

/* ------------------------------------------------------------------ the sandbox */

class DomEvent {
  constructor(type, options) {
    this.type = type;
    this.bubbles = Boolean(options && options.bubbles);
    this.detail = options ? options.detail : undefined;
  }
}

const sandbox = {
  document: documentNode,
  CustomEvent: DomEvent,
  Event: DomEvent,
  console,
  setTimeout,
  clearTimeout,
  /* the page's own lifecycle listeners (`pagehide`): the stub records none */
  addEventListener() { return undefined; },
  /* the live check posts the sheet on a debounce; nothing here may reach the network */
  FormData: class {
    constructor() { this.names = []; }
    append(name) { this.names.push(name); return undefined; }
    set(name) { if (!this.names.includes(name)) { this.names.push(name); } return undefined; }
    delete(name) {
      this.names = this.names.filter((entry) => entry !== name);
      return undefined;
    }
  },
  fetch: () => Promise.reject(new Error("the harness makes no request")),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(CONFIG_SOURCE, sandbox, {filename: "site-config.js"});
vm.runInContext(WIZARD_SOURCE, sandbox, {filename: "site-palette-wizard.js"});

/* ------------------------------------------------------------------ the scenarios */

const frame = documentNode.querySelector("iframe[data-sc-preview]");
const root = frame && frame.contentDocument ? frame.contentDocument.documentElement : null;
const section = documentNode.querySelector('section[data-sc-block="palette"]');
const sheet = documentNode.querySelector("[data-sc-x-text]");

const readRoot = () => ({
  props: root ? Object.assign({}, root.style.values) : {},
  theme: root ? root.getAttribute("data-theme") : null,
});
const resetRoot = () => {
  if (!root) {
    return;
  }
  Object.keys(root.style.values).forEach((name) => root.style.removeProperty(name));
  root.setAttribute("data-theme", TREE.frame_theme === undefined ? "dark" : TREE.frame_theme);
};

const tiles = section ? section.querySelectorAll("[data-sc-preset-tile]") : [];
const payloadOf = (tile, radio) => (radio && radio.getAttribute("data-sc-preset-sheet")) ||
  tile.getAttribute("data-sc-preset-sheet");
const pressed = tiles.map((tile) => {
  const radio = tile.querySelector('input[type="radio"]');
  resetRoot();
  if (!radio) {
    return {value: null, payload: payloadOf(tile, radio), painted: readRoot()};
  }
  tiles.forEach((other) => {
    const box = other.querySelector('input[type="radio"]');
    if (box) {
      box.checked = box === radio;
    }
  });
  radio.dispatchEvent(new DomEvent("click", {bubbles: true}));
  radio.dispatchEvent(new DomEvent("change", {bubbles: true}));
  return {
    value: radio.getAttribute("value"),
    payload: payloadOf(tile, radio),
    painted: readRoot(),
  };
});

/* the sheet edit: a preset is pressed first, so "kept the last valid state" has something to keep */
const editing = {lines: [], typed: null, after_junk: null, keystroke: null, before: null};
if (sheet) {
  const first = tiles[0];
  const firstRadio = first ? first.querySelector('input[type="radio"]') : null;
  resetRoot();
  if (firstRadio) {
    tiles.forEach((other) => {
      const box = other.querySelector('input[type="radio"]');
      if (box) {
        box.checked = box === firstRadio;
      }
    });
    firstRadio.dispatchEvent(new DomEvent("click", {bubbles: true}));
  }
  editing.before = readRoot();
  editing.lines = SIGNALS.lines || [];
  sheet.value = [].concat(editing.lines, [SIGNALS.junk || ""]).join("\n");
  sheet.dispatchEvent(new DomEvent("input", {bubbles: true}));
  editing.typed = readRoot();
  /* the editor over the textarea reports the same keystroke as its own event */
  resetRoot();
  sheet.value = editing.lines.join("\n");
  sheet.dispatchEvent(new DomEvent("sc-sheet-edited", {bubbles: true}));
  editing.keystroke = readRoot();
  /* nothing the grammar describes: the frame keeps the state it already had */
  sheet.value = SIGNALS.junk || "";
  sheet.dispatchEvent(new DomEvent("input", {bubbles: true}));
  editing.after_junk = readRoot();
}

/* the deprecated `theme.light` switch is a flag, not a palette: its own `change` must leave the
 * frame exactly as it stands */
const ignored = {light: null};
const light = documentNode.querySelector('input[name$=".theme.light"]');
if (light) {
  resetRoot();
  light.checked = true;
  light.dispatchEvent(new DomEvent("change", {bubbles: true}));
  ignored.light = readRoot();
}

process.stdout.write(JSON.stringify({
  framework: true,
  section: section ? {
    present: true,
    className: section.className,
    scheme: section.getAttribute("data-sc-pal-scheme"),
  } : {present: false},
  tiles: pressed,
  editing,
  ignored,
}) + "\n");
