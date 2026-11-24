/* THE SHIPPED SETTINGS SCRIPTS' HARNESS — each driven against a minimal DOM, browser-less.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The blocker was "the + Add button does nothing on an
 * EMPTY container": a container with zero rows has nothing to clone and `settings.html` used to ship
 * no `<template>`, so `rowSeed` returned null and the press was a silent no-op. That behaviour lives
 * in the SHIPPED script, so it is asserted over the REAL file — no copy, no re-implementation — by
 * loading it into a DOM stub and dispatching the click the browser would. The stub implements only
 * what the script uses: `document.addEventListener`, an element's `matches`/`closest`/
 * `querySelector(All)`/`cloneNode`/`insertBefore`/`remove`/attribute accessor, and a `<template>`
 * whose `content.firstElementChild` is the seed row (kept OUT of the live tree, exactly as a real
 * template's content is inert).
 *
 * IT DRIVES TWO SHIPPED FILES. The same technique is what `static/settings-yaml.js` needs: its whole
 * contract — the Expert Mode toggle, the `_expert`/`_yaml` carriers, the server verdict rendered in
 * the status line, and the submit guard — lives in the browser, so it is asserted over the REAL file
 * by loading it into a DOM stub too. The source given on the command line decides which scenario set
 * runs: a file carrying the `[data-cfg-yaml]` contract is `settings-yaml.js`, anything else is
 * `configgen.js`. No second harness, no copy of either script.
 *
 * Usage: node configgen_harness.js <path to configgen.js | settings-yaml.js> -> one JSON object
 *
 * `settings-yaml.js` reads its user-visible strings from the SERVED map (`window.dcssbMsg`,
 * `/i18n/messages.js`); the harness installs that map from `DCSSB_MESSAGES_JS` (see
 * `conftest.run_node_harness`) before the script runs, exactly as a browser does.
 */
"use strict";

const fs = require("fs");
const { installMessages } = require("./dcssb_messages_harness.js");

const SCRIPT_PATH = process.argv[2];
const SOURCE = fs.readFileSync(SCRIPT_PATH, "utf8");

/* ---- a very small DOM ---------------------------------------------------------------- */

function matchesSelector(elem, selector) {
  return String(selector).split(",").map((s) => s.trim()).filter(Boolean).some(function (token) {
    let rest = token, attr = null, val = null, cls = null;
    const attrM = rest.match(/\[([^\]=]+)(?:=(["']?)([^"']*)\2)?\]/);
    if (attrM) {
      attr = attrM[1];
      if (attrM[3] !== undefined) { val = attrM[3]; }
      rest = rest.replace(attrM[0], "");
    }
    const clsM = rest.match(/\.([\w-]+)/);
    if (clsM) { cls = clsM[1]; rest = rest.replace(clsM[0], ""); }
    const tag = rest.trim();
    if (tag && elem.tagName.toLowerCase() !== tag.toLowerCase()) { return false; }
    if (cls && (elem.getAttribute("class") || "").split(/\s+/).indexOf(cls) === -1) { return false; }
    if (attr) {
      if (!elem.hasAttribute(attr)) { return false; }
      if (val !== null && String(elem.getAttribute(attr)) !== val) { return false; }
    }
    return true;
  });
}

function el(tag, attrs, children) {
  const a = Object.assign({}, attrs || {});
  const node = {
    tagName: String(tag).toUpperCase(),
    _attrs: a,
    _listeners: {},
    children: [],
    parentNode: null,
    name: Object.prototype.hasOwnProperty.call(a, "name") ? a.name : "",
    value: Object.prototype.hasOwnProperty.call(a, "value") ? a.value : "",
    type: Object.prototype.hasOwnProperty.call(a, "type") ? a.type : "",
    checked: false,
    selectedIndex: 0,
    textContent: "",
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); },
    getAttribute(k) { return this.hasAttribute(k) ? String(this._attrs[k]) : null; },
    setAttribute(k, v) { this._attrs[k] = v; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener(kind, fn) { (this._listeners[kind] = this._listeners[kind] || []).push(fn); },
    dispatchEvent(kind, event) { (this._listeners[kind] || []).forEach((fn) => fn(event)); },
    matches(sel) { return matchesSelector(this, sel); },
    closest(sel) { let n = this; while (n) { if (n.matches && n.matches(sel)) { return n; } n = n.parentNode; } return null; },
    querySelectorAll(sel) {
      const out = [];
      (function walk(n) { n.children.forEach(function (c) { if (matchesSelector(c, sel)) { out.push(c); } walk(c); }); })(this);
      return out;
    },
    querySelector(sel) { const found = this.querySelectorAll(sel); return found.length ? found[0] : null; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    insertBefore(c, ref) {
      c.parentNode = this;
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) { this.children.push(c); } else { this.children.splice(i, 0, c); }
      return c;
    },
    remove() { if (this.parentNode) { const i = this.parentNode.children.indexOf(this); if (i >= 0) { this.parentNode.children.splice(i, 1); } this.parentNode = null; } },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; } return c; },
 /* `<select>.options` is the OPTION children — the sections branch reads `.value` off them and drops
 * the used one. Modelled as a getter so a `removeChild` is immediately visible to it. */
    get options() { return this.children.filter(function (c) { return c.tagName === "OPTION"; }); },
    cloneNode(deep) {
      const copy = el(this.tagName, Object.assign({}, this._attrs));
      copy.name = this.name; copy.value = this.value; copy.type = this.type;
      copy.checked = this.checked; copy.selectedIndex = this.selectedIndex;
      if (deep) { this.children.forEach(function (c) { copy.appendChild(c.cloneNode(true)); }); }
      return copy;
    },
    focus() {},
  };
 /* `hidden` is a REFLECTED property in a real DOM: reading it answers the attribute, and
 * `element.hidden = false` removes the attribute. The Expert Mode script toggles the two panels
 * through `.hidden`, so the stub must reflect it rather than leave a plain expando. */
  Object.defineProperty(node, "hidden", {
    get() { return this.hasAttribute("hidden"); },
    set(v) { if (v) { this.setAttribute("hidden", ""); } else { this.removeAttribute("hidden"); } },
    configurable: true,
  });
  (children || []).forEach(function (c) { node.appendChild(c); });
  return node;
}

function template(row) {
  const t = el("template", {});
  t.content = { firstElementChild: row };   // inert: not in the live tree
  return t;
}

function namesOfInputs(container) {
  return container.querySelectorAll("input, textarea, select").map(function (f) { return f.name; });
}

/* ---- boot the SHIPPED configgen.js with a document that records its handlers ----------- */

function boot() {
  const handlers = {};
  const document = {
    addEventListener(kind, fn) { (handlers[kind] = handlers[kind] || []).push(fn); },
    // the sections branch CREATES an `<option>` when it offers a removed name again
    createElement(tag) { return el(tag, {}); },
  };
  const run = new Function("document", SOURCE);
  run(document);
  return {
    fire(kind, target) {
      (handlers[kind] || []).forEach(function (fn) {
        fn({ target: target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
      });
    },
  };
}

/* ---- boot the SHIPPED settings-yaml.js with the page's real structure ------------------ */

/* The markup `settings.html` ships, reduced to what `settings-yaml.js` reads: the form with its
 * `_csrf_token`, `_expert` companion and `_yaml` carrier; the aside with its validate URL, toggle,
 * status line, badge, the read-only `<pre>` and the empty (hidden) editor host. The editor is the
 * vendored CodeMirror, stubbed to the five calls the script makes; `fetch` is stubbed so a check can
 * answer with a chosen verdict (or stay pending, for the submit-guard scenarios). */
function yamlBoot(opts) {
  const settings = opts || {};
  const manual = Boolean(settings.manual);
  const root = el("div", {});

  const form = el("form", { class: "cfg-form", "data-async": "" });
  const csrf = el("input", { type: "hidden", name: "_csrf_token", value: "CSRFTOKEN" });
  const expert = el("input", { type: "hidden", name: "_expert", value: "0", "data-cfg-expert": "" });
  const textField = el("textarea", { name: "_yaml", "data-cfg-yaml-text": "" });
  form.appendChild(csrf); form.appendChild(expert); form.appendChild(textField);

  const aside = el("aside", { "data-cfg-yaml": "", "data-validate-url": "/actions/settings/validate" });
  const hintsUrl = settings.hintsUrl || "";
  if (hintsUrl) { aside.setAttribute("data-hints-url", hintsUrl); }
  const toggle = el("input", { type: "checkbox", role: "switch", "data-cfg-expert-toggle": "" });
  const status = el("div", { "data-cfg-yaml-status": "" });
  const badge = el("span", { "data-cfg-yaml-badge": "" });
  badge.textContent = "read-only";
  const readonly = el("pre", { "data-cfg-yaml-readonly": "" });
  readonly.textContent = ("text" in settings) ? settings.text : "guild_id: 1\nguild_name: X\n";
  const editorHost = el("div", { "data-cfg-yaml-editor": "", hidden: "" });
  aside.appendChild(toggle); aside.appendChild(status); aside.appendChild(badge);
  aside.appendChild(readonly); aside.appendChild(editorHost);
  root.appendChild(form); root.appendChild(aside);

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll(sel) { return root.querySelectorAll(sel); },
  };

  let editor = null;
  let editorOptions = null;
 /* THE HINT ADDON'S CALL SHAPE. The script asks `CodeMirror.showHint(cm, getHints, options)`; this
 * records the call, so a scenario can ask for the rows the popup would show and then accept one the
 * way the addon's Enter does (replace `from`..`to` with the row's text). */
  const hintCalls = [];
  function CodeMirror(host, options) {
    editorOptions = options;
    const api = {
 /* THE STUB MODELS A LINE BUFFER AND A CARET, not just a string. That is what lets the harness
 * see the change defect: a check that calls `setCursor` between keystrokes moves the caret,
 * and the next `typeChar` then lands at the moved position — so the document is no longer what
 * was typed. A plain "set the whole value" stub would hide it. */
      _lines: options.value.split("\n"),
      _cursor: { line: options.value.split("\n").length - 1, ch: 0 },
      _onChange: null,
      _cursorMoves: 0,
      getValue() { return this._lines.join("\n"); },
      setValue(v) { this._lines = v.split("\n"); if (this._onChange) { this._onChange(); } },
      on(kind, fn) { if (kind === "change") { this._onChange = fn; } },
      lineCount() { return this._lines.length; },
      addLineClass() {},
      removeLineClass() {},
      getCursor() { return { line: this._cursor.line, ch: this._cursor.ch }; },
      setCursor(pos) { this._cursorMoves += 1; this._cursor = { line: pos.line, ch: pos.ch }; },
      getLine(n) { return this._lines[n] === undefined ? "" : this._lines[n]; },
      refresh() {},
 /* THE ADDON'S INSERTION, for a scenario that ACCEPTS a row: `from`..`to` replaced by the row's text,
 * the caret left at the end of what was inserted, and the resulting edit reported as ONE `change`. */
      replaceRange(text, from, to) {
        const start = from || this._cursor;
        const end = to || start;
        const head = this._lines[start.line].slice(0, start.ch);
        const tail = this._lines[end.line].slice(end.ch);
        const chunk = String(text).split("\n");
        const rows = [head + chunk[0]];
        for (let i = 1; i < chunk.length; i += 1) { rows.push(chunk[i]); }
        const last = chunk[chunk.length - 1].length;
        rows[rows.length - 1] = rows[rows.length - 1] + tail;
        this._lines.splice(start.line, end.line - start.line + 1, ...rows);
        this._cursor = { line: start.line + chunk.length - 1, ch: last };
        if (this._onChange) { this._onChange(); }
      },
      closeHint() { hintCalls.length = 0; },
 /* a test driver: an edit the browser would report as ONE `change` event (a paste / replace) */
      type(value) {
        this._lines = value.split("\n");
        this._cursor = { line: this._lines.length - 1, ch: this._lines[this._lines.length - 1].length };
        if (this._onChange) { this._onChange(); }
      },
 /* a test driver: insert ONE character at the CARET and fire a `change` — the browser's keystroke */
      typeChar(ch) {
        const line = this._lines[this._cursor.line];
        if (ch === "\n") {
          this._lines[this._cursor.line] = line.slice(0, this._cursor.ch);
          this._lines.splice(this._cursor.line + 1, 0, line.slice(this._cursor.ch));
          this._cursor = { line: this._cursor.line + 1, ch: 0 };
        } else {
          this._lines[this._cursor.line] = line.slice(0, this._cursor.ch) + ch + line.slice(this._cursor.ch);
          this._cursor = { line: this._cursor.line, ch: this._cursor.ch + 1 };
        }
        if (this._onChange) { this._onChange(); }
      },
    };
    editor = api;
    return api;
  }

  CodeMirror.showHint = function (cm, getHints, options) {
    hintCalls.push({ cm: cm, getHints: getHints, options: options });
    return { close() {} };
  };

  const fetches = [];
  const pending = [];
  let verdict = settings.verdict || { ok: true };
  function fetch(url, init) {
    fetches.push({ url: url, init: init });
    if (hintsUrl && url === hintsUrl) {
 /* THE HINT PAYLOAD, answered on its own route: a good body, a refused status or a dead network —
 * the three failures the editor must survive without a popup and without a word. */
      if (settings.hintsNetwork) { return Promise.reject(new Error("offline")); }
      if (settings.hintsStatus) {
        return Promise.resolve({ ok: false, status: settings.hintsStatus,
                                 json: function () { return Promise.resolve({}); } });
      }
      return Promise.resolve({ ok: true, status: 200,
                               json: function () { return Promise.resolve(settings.hints || { paths: {} }); } });
    }
    if (manual) {
      return new Promise(function (resolve) {
        pending.push(function (v) { resolve({ ok: true, json: function () { return Promise.resolve(v); } }); });
      });
    }
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve(verdict); } });
  }

  const window = {};
  installMessages(window);

 /* Immediate timers so a debounced check runs within the scenario; the real 350 ms debounce is a
 * timing detail, not the contract. */
  const run = new Function("document", "window", "CodeMirror", "fetch", "setTimeout", "clearTimeout",
                           SOURCE);
  run(document, window, CodeMirror, fetch, function (fn) { fn(); }, function () {});

  return {
    form: form, expert: expert, textField: textField, toggle: toggle, status: status,
    badge: badge, readonly: readonly, editorHost: editorHost, fetches: fetches,
    document: document,
    editor() { return editor; },
    editorOptions() { return editorOptions; },
 /* THE POPUP, asked the way the page asks it. `ctrlSpace()` presses the binding the editor was built
 * with; `hintData()` calls the RECORDED hint function, so the rows are the shipped script's own;
 * `accept(i)` inserts row i as the addon's Enter does. `popupOpen()` answers whether a completion was
 * asked for at all — nothing else can open one. */
    ctrlSpace() {
      const keys = editorOptions && editorOptions.extraKeys;
      if (keys && keys["Ctrl-Space"]) { keys["Ctrl-Space"](editor); }
    },
    popupOpen() { return hintCalls.length > 0; },
    hintOptions() { return hintCalls.length ? hintCalls[hintCalls.length - 1].options : null; },
    hintData() {
      if (!hintCalls.length) { return null; }
      const call = hintCalls[hintCalls.length - 1];
      return call.getHints(call.cm, call.options);
    },
    hintRows() { const data = this.hintData(); return data ? data.list : null; },
    hintTexts() { return (this.hintRows() || []).map(function (row) {
      return { text: typeof row === "string" ? row : row.text,
               displayText: typeof row === "string" ? row : row.displayText };
    }); },
    accept(index) {
      const data = this.hintData();
      const row = data.list[index];
      const text = typeof row === "string" ? row : row.text;
      editor.replaceRange(text, data.from, data.to);
      return text;
    },
    pendingChecks() { return pending.length; },
    resolveVerdict(v) { const fn = pending.shift(); if (fn) { fn(v); } },
    open() { toggle.checked = true; toggle.dispatchEvent("change", {}); },
    close() { toggle.checked = false; toggle.dispatchEvent("change", {}); },
    submit() {
      const event = {
        defaultPrevented: false,
        _stopped: false,
        _immediate: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this._stopped = true; },
        stopImmediatePropagation() { this._immediate = true; },
      };
      form.dispatchEvent("submit", event);
      return event;
    },
  };
}

/* The PRESETS WIZARD's own markup, reduced to what `settings-yaml.js` reads. It differs from the
 * settings pages in the two ways the shared script must tolerate: the `[data-cfg-yaml]` card sits
 * INSIDE the POST form (so the form is found through `closest`, not a page marker), and the toggle is
 * a NAMED `_expert` checkbox with a hidden companion before it — the no-script pair — beside a Remove
 * control that opts out of the submit guard (`data-cfg-expert-skip`). */
function yamlBootWizard(opts) {
  const settings = opts || {};
  const validateUrl = "/presets/preset/Spring/wizard/validate";
  const text = ("text" in settings) ? settings.text : "Spring:\n  temperature: 8\n";
  const root = el("div", {});

  const form = el("form", { class: "pw-panel", method: "post", action: "/actions/presets/save" });
  const csrf = el("input", { type: "hidden", name: "_csrf_token", value: "CSRFTOKEN" });
  const expert = el("input", { type: "hidden", name: "_expert", value: "0", "data-cfg-expert": "" });
  form.appendChild(csrf); form.appendChild(expert);

  const card = el("section", { class: "pw-card pw-yaml-card", "data-cfg-yaml": "",
                               "data-validate-url": validateUrl });
  const toggle = el("input", { type: "checkbox", role: "switch", name: "_expert", value: "1",
                               "data-cfg-expert-toggle": "" });
  const status = el("div", { "data-cfg-yaml-status": "" });
  const badge = el("span", { "data-cfg-yaml-badge": "" });
  badge.textContent = "read-only";
  const readonly = el("pre", { "data-cfg-yaml-readonly": "" });
  readonly.textContent = text;
  const textField = el("textarea", { name: "_yaml", "data-cfg-yaml-text": "" });
  textField.textContent = text;
  textField.value = text;
  const editorHost = el("div", { class: "cfg-editor", "data-cfg-yaml-editor": "", hidden: "" });
  card.appendChild(toggle); card.appendChild(status); card.appendChild(badge);
  card.appendChild(readonly); card.appendChild(textField); card.appendChild(editorHost);
  form.appendChild(card);

  const remove = el("button", { type: "submit", name: "_action", value: "remove",
                                "data-cfg-expert-skip": "" });
  const save = el("button", { type: "submit", name: "_action", value: "save" });
  form.appendChild(remove); form.appendChild(save);
  root.appendChild(form);

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll(sel) { return root.querySelectorAll(sel); },
  };

  let editor = null;
  let editorOptions = null;
  function CodeMirror(host, options) {
    editorOptions = options;
    const api = {
      _lines: options.value.split("\n"),
      _onChange: null,
      getValue() { return this._lines.join("\n"); },
      on(kind, fn) { if (kind === "change") { this._onChange = fn; } },
      lineCount() { return this._lines.length; },
      addLineClass() {},
      removeLineClass() {},
      type(value) { this._lines = value.split("\n"); if (this._onChange) { this._onChange(); } },
    };
    editor = api;
    return api;
  }

  const fetches = [];
  let verdict = settings.verdict || { ok: true };
  function fetch(url, init) {
    fetches.push({ url: url, init: init });
    return Promise.resolve({ json: function () { return Promise.resolve(verdict); } });
  }

  const window = {};
  installMessages(window);

  const run = new Function("document", "window", "CodeMirror", "fetch", "setTimeout", "clearTimeout",
                           SOURCE);
  run(document, window, CodeMirror, fetch, function (fn) { fn(); }, function () {});

  return {
    form: form, expert: expert, textField: textField, toggle: toggle, status: status,
    badge: badge, readonly: readonly, editorHost: editorHost, fetches: fetches, validateUrl: validateUrl,
    editor() { return editor; },
    editorOptions() { return editorOptions; },
    open() { toggle.checked = true; toggle.dispatchEvent("change", {}); },
    close() { toggle.checked = false; toggle.dispatchEvent("change", {}); },
    submit(submitter) {
      const event = {
        submitter: submitter || null,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
        stopImmediatePropagation() {},
      };
      form.dispatchEvent("submit", event);
      return event;
    },
    remove: remove, save: save,
  };
}

/* The SCHEDULER WIZARD's review step, reduced to what `settings-yaml.js` reads. It differs from both
 * pages above in the three ways the shared script must tolerate there: the `<aside>` sits INSIDE the
 * save form and the page carries NEITHER form marker (so the form is found through `closest`), the
 * plain `<textarea>` SHIPS THE BLOCK (it is the editor with the script off) beside the read-only
 * `<pre>`, and the ONE Expert control is a NAMED `_expert` checkbox that carries `data-cfg-expert`
 * itself — so the flag a browser posts is that box's own checked state, with no hidden companion. */
function yamlBootReview(opts) {
  const settings = opts || {};
  const validateUrl = "/actions/settings/scheduler/validate?_scope=SRS-1";
  const text = ("text" in settings) ? settings.text
                                    : "timezone: Europe/Berlin\nschedule:\n  00-12: NNNNNNN\n";
  const root = el("div", {});

  const form = el("form", { method: "post", action: "/actions/settings/scheduler", class: "sw-form" });
  const csrf = el("input", { type: "hidden", name: "_csrf_token", value: "CSRFTOKEN" });
  const scope = el("input", { type: "hidden", name: "_scope", value: "SRS-1" });
  form.appendChild(csrf); form.appendChild(scope);

  const aside = el("aside", { class: "st-aside sw-yaml", "data-cfg-yaml": "",
                              "data-validate-url": validateUrl });
  const toggle = el("input", { type: "checkbox", name: "_expert", value: "1",
                               "data-cfg-expert": "", "data-cfg-expert-toggle": "" });
  const status = el("div", { "data-cfg-yaml-status": "" });
  const badge = el("span", { "data-cfg-yaml-badge": "" });
  badge.textContent = "read-only";
  const readonly = el("pre", { "data-cfg-yaml-readonly": "" });
  readonly.textContent = text;
  const textField = el("textarea", { name: "block_yaml", "data-cfg-yaml-text": "" });
  textField.textContent = text;
  textField.value = text;
  const editorHost = el("div", { class: "cfg-editor", "data-cfg-yaml-editor": "", hidden: "" });
  aside.appendChild(toggle); aside.appendChild(status); aside.appendChild(badge);
  aside.appendChild(readonly); aside.appendChild(editorHost); aside.appendChild(textField);
  form.appendChild(aside);
  const save = el("button", { type: "submit" });
  form.appendChild(save);
  root.appendChild(form);

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll(sel) { return root.querySelectorAll(sel); },
  };

  let editor = null;
  function CodeMirror(host, options) {
    const api = {
      _lines: options.value.split("\n"),
      _onChange: null,
      getValue() { return this._lines.join("\n"); },
      on(kind, fn) { if (kind === "change") { this._onChange = fn; } },
      lineCount() { return this._lines.length; },
      addLineClass() {},
      removeLineClass() {},
      type(value) { this._lines = value.split("\n"); if (this._onChange) { this._onChange(); } },
    };
    editor = api;
    return api;
  }

  const fetches = [];
  const verdict = settings.verdict || { ok: true };
  function fetch(url, init) {
    fetches.push({ url: url, init: init });
    return Promise.resolve({ json: function () { return Promise.resolve(verdict); } });
  }

  const window = {};
  installMessages(window);

  const run = new Function("document", "window", "CodeMirror", "fetch", "setTimeout", "clearTimeout",
                           SOURCE);
  run(document, window, CodeMirror, fetch, function (fn) { fn(); }, function () {});

  return {
    form: form, toggle: toggle, textField: textField, status: status, badge: badge,
    readonly: readonly, editorHost: editorHost, fetches: fetches, validateUrl: validateUrl,
    editor() { return editor; },
 /* what the browser submits for the Expert flag: an unchecked box posts nothing at all */
    postedFlag() { return toggle.checked ? toggle.value : ""; },
    open() { toggle.checked = true; toggle.dispatchEvent("change", {}); },
    close() { toggle.checked = false; toggle.dispatchEvent("change", {}); },
    submit(submitter) {
      const event = {
        submitter: submitter || null,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
        stopImmediatePropagation() {},
      };
      form.dispatchEvent("submit", event);
      return event;
    },
    save: save,
  };
}

/* ---- scenarios ----------------------------------------------------------------------- */

if (/data-cfg-yaml/.test(SOURCE)) {
 /* The SHIPPED `settings-yaml.js` — the Expert Mode contract. */
  const flush = function () { return new Promise(function (r) { setImmediate(r); }); };

  if (process.argv[3] === "wizard") {
 /* The WIZARD's panel: the same script, the wizard's own DOM shape. */
    (async function () {
      const result = {};

      /* 1. Expert Mode OFF on arrival: no editor, the read-only panel, a plain submit untouched. */
      {
        const page = yamlBootWizard({});
        const event = page.submit(page.save);
        result.off = {
          editorBuilt: page.editor() !== null,
          expert: page.expert.value,
          readonlyShown: !page.readonly.hidden,
          submitPrevented: event.defaultPrevented,
        };
      }

      /* 2. Expert Mode ON: the editor is built from the review's own YAML, both carriers are set, and
       * the first check reaches the WIZARD's own validate URL with the CSRF token and the document. */
      {
        const page = yamlBootWizard({ verdict: { ok: true } });
        page.open();
        await flush();
        const body = page.fetches.length ? page.fetches[0].init.body : "";
        result.validateUrl = page.validateUrl;
        result.on = {
          editorBuilt: page.editor() !== null,
          seededFromPanel: page.editor() && page.editor().getValue() === page.readonly.textContent,
          expert: page.expert.value,
          carrierMatchesEditor: page.textField.value === page.editor().getValue(),
          editorShown: page.editorHost.hidden === false,
          readonlyHidden: page.readonly.hidden,
          badge: page.badge.textContent,
          fetchMethod: page.fetches.length ? page.fetches[0].init.method : "",
          fetchUrl: page.fetches.length ? page.fetches[0].url : "",
          bodyCarriesCsrf: body.indexOf("CSRFTOKEN") !== -1,
          bodyCarriesDocument: body.indexOf("_yaml=") !== -1,
        };
      }

      /* 3. A change in the editor updates the carrier at once and the check posts the edited text. */
      {
        const page = yamlBootWizard({ verdict: { ok: true } });
        page.open();
        await flush();
        page.editor().type("Spring:\n  temperature: 21\n");
        await flush();
        const body = page.fetches.length ? page.fetches[page.fetches.length - 1].init.body : "";
        result.change = {
          carrierUpdated: page.textField.value === "Spring:\n  temperature: 21\n",
          bodyCarriesEdited: body.indexOf("21") !== -1,
        };
      }

      /* 4. The submit guard: a DIRTY save is held back; the Remove control — which posts no document
       * and opts out — is left entirely alone. */
      {
        const page = yamlBootWizard({ verdict: { ok: true } });
        page.open();
        await flush();
        page.editor().type("Spring:\n  temperature: 21\n");   // dirty: text moved since the verdict
        const dirtySave = page.submit(page.save);
        const skipRemove = page.submit(page.remove);
        result.guard = {
          dirtySavePrevented: dirtySave.defaultPrevented,
          skipSubmitterPrevented: skipRemove.defaultPrevented,
        };
      }

      /* 5. Expert Mode OFF again: the read-only panel returns, the carrier keeps the edit and the
       * companion reads "0" — the plain form is the one that posts. */
      {
        const page = yamlBootWizard({ verdict: { ok: true } });
        page.open();
        await flush();
        page.editor().type("Spring:\n  temperature: 9\n");
        await flush();
        page.close();
        const event = page.submit(page.save);
        result.offAgain = {
          expert: page.expert.value,
          readonlyShown: !page.readonly.hidden,
          readonlyHoldsEdit: page.readonly.textContent === "Spring:\n  temperature: 9\n",
          submitPrevented: event.defaultPrevented,
        };
      }

      process.stdout.write(JSON.stringify(result));
    })();
  } else if (process.argv[3] === "review") {
 /* The SCHEDULER REVIEW step's panel: the same script, the wizard's own no-page-marker shape. */
    (async function () {
      const result = {};
      const BLOCK = "timezone: Europe/Berlin\nschedule:\n  00-12: NNNNNNN\n";

      /* 1. Arrival with the script on and Expert Mode OFF: no editor, the read-only text shown, and a
       * plain submit neither held back nor carrying the flag — the block does not decide anything. */
      {
        const page = yamlBootReview({});
        const event = page.submit(page.save);
        result.off = {
          editorBuilt: page.editor() !== null,
          readonlyShown: !page.readonly.hidden,
          carrierHoldsBlock: page.textField.value === BLOCK,
          postedFlag: page.postedFlag(),
          submitPrevented: event.defaultPrevented,
          fetches: page.fetches.length,
        };
      }

      /* 2. Expert Mode ON: the editor is seeded from the block the step printed, the carrier follows
       * it, the box posts "1", and the first check reaches the wizard's OWN validate URL. */
      {
        const page = yamlBootReview({ verdict: { ok: true } });
        page.open();
        await flush();
        const body = page.fetches.length ? page.fetches[0].init.body : "";
        result.on = {
          editorBuilt: page.editor() !== null,
          seededFromPanel: page.editor() && page.editor().getValue() === page.readonly.textContent,
          postedFlag: page.postedFlag(),
          carrierMatchesEditor: page.textField.value === page.editor().getValue(),
          editorShown: page.editorHost.hidden === false,
          readonlyHidden: page.readonly.hidden,
          badge: page.badge.textContent,
          fetchMethod: page.fetches.length ? page.fetches[0].init.method : "",
          fetchUrl: page.fetches.length ? page.fetches[0].url : "",
          bodyCarriesCsrf: body.indexOf("CSRFTOKEN") !== -1,
          bodyCarriesBlock: body.indexOf("block_yaml=") !== -1,
          status: page.status.textContent,
        };
      }

      /* 3. An edit reaches the carrier at once and the check posts the edited text — so a green
       * verdict is a verdict on what the save would write. */
      {
        const page = yamlBootReview({ verdict: { ok: true } });
        page.open();
        await flush();
        page.editor().type("timezone: UTC\n");
        await flush();
        const body = page.fetches.length ? page.fetches[page.fetches.length - 1].init.body : "";
        result.change = {
          carrierUpdated: page.textField.value === "timezone: UTC\n",
          bodyCarriesEdited: body.indexOf("UTC") !== -1,
        };
      }

      /* 4. A refused verdict holds the save back and says so in the status line. */
      {
        const page = yamlBootReview({ verdict: { ok: false, message: "refused" } });
        page.open();
        await flush();
        const event = page.submit(page.save);
        result.refused = {
          submitPrevented: event.defaultPrevented,
          statusState: page.status.getAttribute("data-state"),
        };
      }

      /* 5. OFF again: the read-only text returns with the edit, the carrier empties and the box posts
       * nothing — the plain save is the plan's. */
      {
        const page = yamlBootReview({ verdict: { ok: true } });
        page.open();
        await flush();
        page.editor().type("timezone: UTC\n");
        await flush();
        page.close();
        const event = page.submit(page.save);
        result.offAgain = {
          postedFlag: page.postedFlag(),
          carrier: page.textField.value,
          readonlyShown: !page.readonly.hidden,
          readonlyHoldsEdit: page.readonly.textContent === "timezone: UTC\n",
          submitPrevented: event.defaultPrevented,
        };
      }

      process.stdout.write(JSON.stringify(result));
    })();
  } else if (process.argv[3] === "hints") {
 /* THE COMPLETION POPUP — a synthetic payload of the SHAPE `config_hints.payload` serves, and the
  * shipped script's own answers about it. Every row, label, mark and example below comes from that
  * payload; the scenario only asks the questions. */
    const HINTS_URL = "/actions/settings/hints";
    const PAYLOAD = {
      paths: {
        "": { desc: "", kind: "map", example: null, children: [
          { key: "guild_id", path: "guild_id", kind: "number", label: "Number", required: true,
            desc: "Discord guild/server ID.", example: "112233445566778899", enum: [] },
          { key: "language", path: "language", kind: "select", label: "Select", required: false,
            desc: "Schema prose with <b>x</b> in it.", example: null, enum: ["en", "de"] },
          { key: "logging", path: "logging", kind: "map", label: "Group", required: false,
            desc: "Logging configuration.", example: "loglevel: DEBUG\nlogfile: bot.log", enum: [] },
          { key: "plugins", path: "plugins.*", kind: "select", label: "Select", required: false,
            desc: "Plugin name.", example: null, enum: [] },
        ] },
        "logging": { desc: "Logging configuration.", kind: "map", example: "loglevel: DEBUG",
                     children: [
          { key: "loglevel", path: "logging.loglevel", kind: "select", label: "Select",
            required: false, desc: "Minimum level.", example: null, enum: ["DEBUG", "INFO"] },
          { key: "utc", path: "logging.utc", kind: "bool", label: "Switch", required: false,
            desc: "UTC timestamps.", example: null, enum: [] },
        ] },
        "plugins": { desc: "", kind: "list", example: null, children: [
          { key: "plugins", path: "plugins.*", kind: "select", label: "Select", required: false,
            desc: "Plugin name.", example: null, enum: ["admin", "mission"] },
        ] },
      },
    };
    function rowFor(page, text) {
      const rows = page.hintTexts();
      for (let i = 0; i < rows.length; i += 1) { if (rows[i].text === text) { return { index: i, row: rows[i] }; } }
      return null;
    }

    (async function () {
      const result = {};

      /* 1. Ctrl+Space on an EMPTY document: the root's keys, one row per key, never auto-accepted. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "" });
        page.open();
        await flush();
        page.ctrlSpace();
        const rows = page.hintTexts() || [];
        result.ctrlSpace = {
          open: page.popupOpen(),
          completeSingle: page.hintOptions() ? page.hintOptions().completeSingle : null,
          keys: rows.map(function (row) { return row.text; }),
          firstRow: rows.length ? rows[0].displayText : null,
        };
      }

      /* 2. THE `set` MARK: two keys are in the document, two are not. The marks come from the
       * editor's OWN text, so the two present ones are marked and the two absent ones are not. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD,
                                text: "guild_id: 1\nlanguage: en\n" });
        page.open();
        await flush();
        page.ctrlSpace();
        result.marks = {
          set: (page.hintTexts() || []).filter(function (row) {
            return row.displayText && row.displayText.split("\n")[0].indexOf("set") !== -1;
          }).map(function (row) { return row.text; }),
          required: (page.hintTexts() || []).filter(function (row) {
            return row.displayText && row.displayText.split("\n")[0].indexOf("required") !== -1;
          }).map(function (row) { return row.text; }),
        };
      }

      /* 3. ACCEPT: the key and its separator, no value, and no other line touched. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD,
                                text: "guild_id: 1\nlanguage: en\n" });
        page.open();
        await flush();
        page.ctrlSpace();
        const found = rowFor(page, "logging: ");
        const inserted = found ? page.accept(found.index) : null;
        result.accept = {
          inserted: inserted,
          value: page.editor().getValue(),
          lines: page.editor().getValue().split("\n"),
        };
      }

      /* 4. A REFUSED FETCH: no popup, nothing said, and the editor is exactly as usable as before. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hintsStatus: 500, verdict: { ok: true } });
        page.open();
        await flush();
        page.ctrlSpace();
        page.editor().type("guild_id: 7\n");
        await flush();
        result.refused = {
          open: page.popupOpen(),
          status: page.status.textContent,
          carrier: page.textField.value,
        };
      }

      /* 4b. A DEAD NETWORK: the same silence. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hintsNetwork: true });
        page.open();
        await flush();
        page.ctrlSpace();
        result.offline = { open: page.popupOpen(), status: page.status.textContent };
      }

      /* 4c. NO `data-hints-url` AT ALL: the editor is the one it always was. */
      {
        const page = yamlBoot({ verdict: { ok: true } });
        page.open();
        await flush();
        page.ctrlSpace();
        result.noPayload = { open: page.popupOpen(), fetches: page.fetches.length };
      }

      /* 5. A SEQUENCE ITEM (`- `): the element's enum values. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "plugins:\n  - " });
        page.open();
        await flush();
        page.editor().setCursor({ line: 1, ch: 4 });
        page.ctrlSpace();
        const rows = page.hintTexts();
        result.sequence = {
          open: page.popupOpen(),
          values: rows ? rows.map(function (row) { return row.text; }) : null,
        };
      }

      /* 6. A NESTED BLOCK: the caret inside `logging:` gets THAT block's keys, by plain path. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "logging:\n  " });
        page.open();
        await flush();
        page.editor().setCursor({ line: 1, ch: 2 });
        page.ctrlSpace();
        const rows = page.hintTexts();
        result.nested = { keys: rows ? rows.map(function (row) { return row.text; }) : null };
      }

      /* 7. THE DESC IS TEXT: a description carrying markup arrives verbatim in the row. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "" });
        page.open();
        await flush();
        page.ctrlSpace();
        const found = rowFor(page, "language: ");
        result.desc = { displayText: found ? found.row.displayText : null };
      }

      /* 8. BY ITSELF at an empty key position — and NOT on a keystroke that is not one. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "guild_id: 1\n" });
        page.open();
        await flush();
        page.editor().type("guild_id: 1\n");
        await flush();
        const opened = page.popupOpen();
        const typed = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "" });
        typed.open();
        await flush();
        typed.editor().type("guild_id: 1");
        await flush();
        result.auto = { atEmptyKey: opened, atTypedValue: typed.popupOpen() };
      }

      /* 9. A BLOCK'S OWN EXAMPLE ROW: its row inserts the example under the key, indented. */
      {
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: PAYLOAD, text: "" });
        page.open();
        await flush();
        page.ctrlSpace();
        const rows = page.hintTexts() || [];
        const example = rows.filter(function (row) { return row.text.indexOf("logging:") === 0 && row.text.indexOf("\n") !== -1; });
        result.exampleRow = { found: example.length, text: example.length ? example[0].text : null };
      }

      /* 10. A `#` INSIDE A QUOTED SCALAR IS CONTENT, NOT A COMMENT. The line before the caret carries
       * `"x # y"`; the block's keys must still be read, and a QUOTED KEY carrying a `#` must still be
       * recognised as the key it is (that one is what the strip used to cut in half). */
      {
        const QUOTED = {
          paths: {
            "": { desc: "", kind: "map", example: null, children: [
              { key: "guild_id", path: "guild_id", kind: "number", label: "Number", required: true,
                desc: "", example: null, enum: [] },
              { key: "language", path: "language", kind: "select", label: "Select", required: false,
                desc: "", example: null, enum: [] },
              { key: "\"a # b\"", path: "\"a # b\"", kind: "text", label: "Text", required: false,
                desc: "", example: null, enum: [] },
              { key: "logging", path: "logging", kind: "map", label: "Group", required: false,
                desc: "", example: null, enum: [] },
            ] },
            "logging": { desc: "", kind: "map", example: null, children: [
              { key: "loglevel", path: "logging.loglevel", kind: "select", label: "Select",
                required: false, desc: "", example: null, enum: ["DEBUG"] },
              { key: "utc", path: "logging.utc", kind: "bool", label: "Switch", required: false,
                desc: "", example: null, enum: [] },
            ] },
          },
        };
        const page = yamlBoot({ hintsUrl: HINTS_URL, hints: QUOTED,
                                text: "guild_id: 1\n\"a # b\": 2\nlogging:\n  desc: \"x # y\"\n"
                                      + "  loglevel: DEBUG\n  " });
        page.open();
        await flush();
        page.editor().setCursor({ line: 5, ch: 2 });
        page.ctrlSpace();
        const rows = page.hintTexts() || [];
        const marked = rows.filter(function (row) {
          return row.displayText && row.displayText.split("\n")[0].indexOf("set") !== -1;
        }).map(function (row) { return row.text; });
        const root = yamlBoot({ hintsUrl: HINTS_URL, hints: QUOTED,
                                text: "guild_id: 1\n\"a # b\": 2\n" });
        root.open();
        await flush();
        root.editor().setCursor({ line: 2, ch: 0 });
        root.ctrlSpace();
        const rootMarked = (root.hintTexts() || []).filter(function (row) {
          return row.displayText && row.displayText.split("\n")[0].indexOf("set") !== -1;
        }).map(function (row) { return row.text; });
        result.quotedHash = {
          insideBlock: rows.map(function (row) { return row.text; }),
          insideBlockMarked: marked,
          rootMarked: rootMarked,
        };
      }

      process.stdout.write(JSON.stringify(result));
    })();
  } else if (process.argv[3] === "slice") {
 /* A ONE-KEY-SLICE PAYLOAD: the document's root is ONE key the schema INVENTS (a preset name, a node
  * name), so the payload's root carries the schema's `regex;` row and whatever else the schema declares
  * at its top level. argv[4] is a file the test wrote from `config_hints.payload(...)`. The questions:
  * what does the popup OFFER where the document's root key is written, and what would accepting a row
  * actually insert? Then, with the invented key written, are the block's own keys still completed —
  * the path resolver must keep descending through the `regex;` row. */
    const slicePayload = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
    (async function () {
      const result = {};

      /* 1. An EMPTY document: the popup at the document root. */
      const page = yamlBoot({ hintsUrl: "/actions/settings/hints", hints: slicePayload, text: "" });
      page.open();
      await flush();
      page.editor().setCursor({ line: 0, ch: 0 });
      page.ctrlSpace();
      const rows = page.hintTexts() || [];
      result.root = {
        open: page.popupOpen(),
        texts: rows.map(function (row) { return row.text; }),
        displays: rows.map(function (row) { return row.displayText; }),
      };

      /* 2. ACCEPT EVERY ROW, one per fresh document, and keep the resulting text. */
      result.accepted = [];
      for (let i = 0; i < rows.length; i += 1) {
        const fresh = yamlBoot({ hintsUrl: "/actions/settings/hints", hints: slicePayload, text: "" });
        fresh.open();
        await flush();
        fresh.editor().setCursor({ line: 0, ch: 0 });
        fresh.ctrlSpace();
        const inserted = fresh.accept(i);
        result.accepted.push({ inserted: inserted, value: fresh.editor().getValue() });
      }

      /* 3. THE INVENTED KEY WRITTEN: the block's own keys are still resolved through the `regex;` row,
       *    and none of them is the schema's `regex;` row. */
      const firstKey = rows.length ? String(rows[0].text).replace(/:\s*$/, "") : "Spring";
      const blockName = firstKey.indexOf("regex;") === 0 ? "Spring" : firstKey;
      const block = yamlBoot({ hintsUrl: "/actions/settings/hints", hints: slicePayload,
                               text: blockName + ":\n  " });
      block.open();
      await flush();
      block.editor().setCursor({ line: 1, ch: 2 });
      block.ctrlSpace();
      const blockRows = block.hintTexts() || [];
      result.insideBlock = {
        open: block.popupOpen(),
        texts: blockRows.map(function (row) { return row.text; }),
      };

      process.stdout.write(JSON.stringify(result));
    })();
  } else if (process.argv[3] === "real") {
 /* THE PRODUCTION PAYLOAD, NOT A FIXTURE. argv[4] is a file the test wrote from
  * `services/webservice/config_hints.payload(...)`; the document below is a REAL scoped file whose
  * server key the schema's `regex;` container invents (`servers_schema.yaml`). The caret inside it
  * must resolve through that container to the node block's own keys. */
    const realPayload = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
    (async function () {
      const result = {};
      const page = yamlBoot({ hintsUrl: "/actions/settings/hints", hints: realPayload,
                              text: "default:\n  \n" });
      page.open();
      await flush();
      page.editor().setCursor({ line: 1, ch: 2 });
      page.ctrlSpace();
      result.insideNode = {
        open: page.popupOpen(),
        keys: (page.hintTexts() || []).map(function (row) { return row.text; }),
      };
      const value = yamlBoot({ hintsUrl: "/actions/settings/hints", hints: realPayload,
                               text: "default:\n  messages: \n" });
      value.open();
      await flush();
      value.editor().setCursor({ line: 1, ch: 12 });
      value.ctrlSpace();
      result.nodeValues = {
        open: value.popupOpen(),
        values: (value.hintTexts() || []).map(function (row) { return row.text; }),
      };
      process.stdout.write(JSON.stringify(result));
    })();
  } else (async function () {
    const result = {};

 /* 1. Expert Mode OFF on arrival: the panel is the read-only view it always was, no editor is
 * built, and the `_yaml` carrier stays empty — the plain form cannot smuggle a raw write. */
    {
      const page = yamlBoot({});
      const event = page.submit();
      result.off = {
        editorBuilt: page.editor() !== null,
        expert: page.expert.value,
        carrier: page.textField.value,
        status: page.status.textContent,
        editorHidden: page.editorHost.hidden,
        readonlyShown: !page.readonly.hidden,
        submitPrevented: event.defaultPrevented,
        fetches: page.fetches.length,
      };
    }

 /* 2. Expert Mode ON: the editor is built from the read-only text, both carriers are set, the
 * badge flips, and the first check reaches the SERVER's validate route with the CSRF token. */
    {
      const page = yamlBoot({ verdict: { ok: true } });
      page.open();
      await flush();
      const body = page.fetches.length ? page.fetches[0].init.body : "";
      result.on = {
        editorBuilt: page.editor() !== null,
        seededFromPanel: page.editor() && page.editor().getValue() === page.readonly.textContent,
        expert: page.expert.value,
        carrierMatchesEditor: page.textField.value === page.editor().getValue(),
        editorShown: page.editorHost.hidden === false,
        readonlyHidden: page.readonly.hidden,
        badge: page.badge.textContent,
        status: page.status.textContent,
        state: page.status.getAttribute("data-state"),
        fetchMethod: page.fetches.length ? page.fetches[0].init.method : "",
        fetchUrl: page.fetches.length ? page.fetches[0].url : "",
        bodyCarriesCsrf: body.indexOf("CSRFTOKEN") !== -1,
        bodyCarriesDocument: body.indexOf("_yaml=") !== -1,
      };
    }

 /* 3. A change in the editor: the carrier is current immediately and the check that follows
 * posts the EDITED document (not the one it was seeded with). */
    {
      const page = yamlBoot({ verdict: { ok: true } });
      page.open();
      await flush();
      const before = page.fetches.length;
      page.editor().type("guild_id: 42\nguild_name: Renamed\n");
      await flush();
      const body = page.fetches.length > before ? page.fetches[page.fetches.length - 1].init.body : "";
      result.change = {
        carrierUpdated: page.textField.value === "guild_id: 42\nguild_name: Renamed\n",
        checksIssued: page.fetches.length - before,
        bodyCarriesEdited: body.indexOf("42") !== -1,
      };
    }

 /* 3b. THE REFLOW / CARET DEFECT. A full line typed ONE KEYSTROKE AT A TIME while
 * the debounced check keeps returning a verdict that carries an offending LINE and COLUMN (the
 * server names the half-typed line — column 1 here). A check that moves the caret would scatter
 * the remaining characters and split the row; the document must end EXACTLY as typed, with the
 * caret at the end and NO caret move attributed to a check. */
    {
      const page = yamlBoot({ text: "",
        verdict: { ok: false, message: "Not valid against the schema — line 1.", line: 1, column: 1 } });
      page.open();
      await flush();
      const editor = page.editor();
      const typed = "guild_name: My Server\n";
      for (let i = 0; i < typed.length; i += 1) { editor.typeChar(typed[i]); await flush(); }
      result.keystrokes = {
        typed: typed,
        value: editor.getValue(),
        intact: editor.getValue() === typed,
        cursorMoves: editor._cursorMoves,
        cursor: editor.getCursor(),
      };
    }

 /* 4. The SERVER's verdict is what the status line and the submit guard act on. A refusal is
 * shown verbatim and holds the submit; a valid verdict lets it through; a submit while a
 * check is still pending (or the text moved since the last verdict) is held back too. */
    {
      const page = yamlBoot({ verdict: { ok: false, message: "Not valid against the schema — line 1." } });
      page.open();
      await flush();
      const refused = page.submit();
      result.submitRefused = {
        status: page.status.textContent,
        state: page.status.getAttribute("data-state"),
        submitPrevented: refused.defaultPrevented,
      };
    }
    {
      const page = yamlBoot({ verdict: { ok: true } });
      page.open();
      await flush();
      const clean = page.submit();
      result.submitClean = { submitPrevented: clean.defaultPrevented };
    }
    {
      const page = yamlBoot({ manual: true });
      page.open();                       // a check is now in flight, no verdict yet
      const pending = page.submit();
      result.submitPending = {
        submitPrevented: pending.defaultPrevented,
        checksInFlight: page.pendingChecks(),
      };
    }
    {
      const page = yamlBoot({ verdict: { ok: true } });
      page.open();
      await flush();
      const page2 = yamlBoot({ manual: true });
      page2.open();
      page2.resolveVerdict({ ok: true });
      await flush();
      page2.editor().type("guild_id: 7\n");   // dirty: the text moved since the last verdict
      const moved = page2.submit();
      result.submitDirty = {
        submitPrevented: moved.defaultPrevented,
        checksInFlight: page2.pendingChecks(),
      };
    }

 /* 5. Expert Mode turned OFF again: the panel RETURNS to the read-only view, both carriers are
 * cleared, and — the promise "OFF leaves the panel exactly as it is today" — a plain form
 * submit is NOT intercepted, exactly as on arrival. */
    {
      const page = yamlBoot({ verdict: { ok: true } });
      page.open();
      await flush();
      page.editor().type("guild_id: 9\n");
      await flush();
      page.close();
      const event = page.submit();
      result.offAgain = {
        expert: page.expert.value,
        carrier: page.textField.value,
        editorHidden: page.editorHost.hidden,
        readonlyShown: !page.readonly.hidden,
        readonlyHoldsEdit: page.readonly.textContent === "guild_id: 9\n",
        status: page.status.textContent,
        badge: page.badge.textContent,
        submitPrevented: event.defaultPrevented,
      };
    }

    process.stdout.write(JSON.stringify(result));
  })();
} else {
 /* The SHIPPED `configgen.js` — the Add/Remove controls. */
  const result = {};

 /* 1. EMPTY LIST + template -> Add seeds one row (the blocker). */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-list": "plugins" });
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.plugins", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    result.emptyList = { rows: c.querySelectorAll("[data-cfg-row]").length, names: namesOfInputs(c) };
  }

 /* 2. EMPTY SEQ + template -> Add seeds one row and the row counter is updated. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-seq": "rows" });
    c.appendChild(el("input", { name: "f.rows.__n", value: "0", "data-cfg-count": "" }));
    c.appendChild(template(el("div", { "data-cfg-row": "", "data-index": "0" }, [
      el("input", { name: "f.rows.0.n", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    const count = c.querySelector("[data-cfg-count]");
    result.emptySeq = { rows: c.querySelectorAll("[data-cfg-row]").length, names: namesOfInputs(c), count: count.value };
  }

 /* 3. EMPTY REGEX + template -> Add seeds one row whose fields are on the __KEY__ placeholder. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "custom" });
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.custom.__key", value: "", "data-cfg-regexkey": "" }),
      el("input", { name: "f.custom.__KEY__.v", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    result.emptyRegex = { rows: c.querySelectorAll("[data-cfg-row]").length, names: namesOfInputs(c) };
  }

 /* 4. A NEW REGEX ROW: typing a key rewrites the row's field names off the placeholder. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "custom" });
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.custom.__key", value: "", "data-cfg-regexkey": "" }),
      el("input", { name: "f.custom.__KEY__.v", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    const row = c.querySelectorAll("[data-cfg-row]")[0];
    const keyField = row.querySelector("[data-cfg-regexkey]");
    keyField.value = "alpha";
    dom.fire("input", keyField);
    const afterFirst = namesOfInputs(c);
    keyField.value = "beta";           // typing again renames from the CURRENT key, not the placeholder
    dom.fire("input", keyField);
    result.regexTyped = { afterFirst: afterFirst, afterSecond: namesOfInputs(c) };
  }

 /* 5. A NON-EMPTY LIST still seeds by cloning the last row (the original behaviour is kept). */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-list": "plugins" });
    c.appendChild(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.plugins", value: "mission" }),
      el("button", { "data-cfg-remove": "" }),
    ]));
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.plugins", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    const inputs = c.querySelectorAll("input");
    result.existingList = {
      rows: c.querySelectorAll("[data-cfg-row]").length,
      names: namesOfInputs(c),
      lastValue: inputs[inputs.length - 1].value,      // the clone is CLEARED
    };
  }

 /* 6. A NEW REGEX ROW with a DOTTED key: the typed key is ESCAPED into the row's field names, so the
 * name survives the page's own `.` path separator. A second keystroke renames off the escaped key.
 * The container/field mirror the Settings page's own `commands:` regex so a page test can post the
 * exact name this produces. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "commands" });
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.commands.__key", value: "", "data-cfg-regexkey": "" }),
      el("input", { name: "f.commands.__KEY__.name", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    const row = c.querySelectorAll("[data-cfg-row]")[0];
    const keyField = row.querySelector("[data-cfg-regexkey]");
    keyField.value = "greet.user";
    dom.fire("input", keyField);
    const afterFirst = namesOfInputs(c);
    keyField.value = "greet.me";
    dom.fire("input", keyField);
    result.regexTypedDotted = { afterFirst: afterFirst, afterSecond: namesOfInputs(c) };
  }

 /* 7. A NEW REGEX ROW whose key carries a space and brackets: those are NOT path-token specials, so
 * they pass through verbatim (the server's decode leaves them be). */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "custom" });
    c.appendChild(template(el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.custom.__key", value: "", "data-cfg-regexkey": "" }),
      el("input", { name: "f.custom.__KEY__.v", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const add = el("button", { "data-cfg-add": "" });
    c.appendChild(add);
    dom.fire("click", add);
    const row = c.querySelectorAll("[data-cfg-row]")[0];
    const keyField = row.querySelector("[data-cfg-regexkey]");
    keyField.value = "a b[c]";
    dom.fire("input", keyField);
    result.regexTypedSpecial = { afterFirst: namesOfInputs(c) };
  }

 /* 8. A SEQ OF MAPS can be EMPTIED: removing every row leaves zero rows and posts __n = 0 (a
 * genuine "now empty", not a kept-empty-row). The hidden length counter is the only row-list
 * control that authorises the truncation, so a seq that must be emptied depends on it hitting 0. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-seq": "rows" });
    c.appendChild(el("input", { name: "f.rows.__n", value: "2", "data-cfg-count": "" }));
    const r0 = el("div", { "data-cfg-row": "", "data-index": "0" }, [
      el("input", { name: "f.rows.0.n", value: "3" }),
      el("button", { "data-cfg-remove": "" }),
    ]);
    const r1 = el("div", { "data-cfg-row": "", "data-index": "1" }, [
      el("input", { name: "f.rows.1.n", value: "4" }),
      el("button", { "data-cfg-remove": "" }),
    ]);
    c.appendChild(r0);
    c.appendChild(r1);
    c.appendChild(template(el("div", { "data-cfg-row": "", "data-index": "0" }, [
      el("input", { name: "f.rows.0.n", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    c.appendChild(el("button", { "data-cfg-add": "" }));
    dom.fire("click", r1.querySelector("[data-cfg-remove]"));
    dom.fire("click", r0.querySelector("[data-cfg-remove]"));
    const count = c.querySelector("[data-cfg-count]");
    result.seqEmptied = { rows: c.querySelectorAll("[data-cfg-row]").length, count: count.value };
  }

 /* 9. A FIXED-ALTERNATIVE SECTION: the picker names the schema's own sections, so
 * the ``+`` CLONES the empty section, stamps the CHOSEN name onto its display and its ``__key``
 * control, renames its sub-fields off the ``__KEY__`` placeholder, and DROPS the used option so a
 * second section can only be a DIFFERENT name. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "DEFAULT", "data-cfg-sections": "" });
    c.appendChild(template(el("fieldset", { "data-cfg-row": "", "data-cfg-section": "" }, [
      el("span", { "data-cfg-sectionname": "" }),
      el("input", { name: "f.DEFAULT.__key", value: "", "data-cfg-regexkey": "",
                    "data-cfg-sectionkey": "" }),
      el("input", { name: "f.DEFAULT.__KEY__.port", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const pick = el("select", { "data-cfg-sectionpick": "" });
    pick.appendChild(el("option", { value: "frontend" }));
    pick.appendChild(el("option", { value: "client" }));
    const bar = el("div", { class: "cfg-addbar" });
    const add = el("button", { "data-cfg-add": "" });
    bar.appendChild(add);
    c.appendChild(pick); c.appendChild(bar);
    pick.value = "client";                 // the operator picks the second allowed name
    dom.fire("click", add);
    const row = c.querySelectorAll("[data-cfg-row]")[0];
    result.sectionAdded = {
      rows: c.querySelectorAll("[data-cfg-row]").length,
      names: namesOfInputs(c),
      nameSpan: row.querySelector("[data-cfg-sectionname]").textContent,
      keyValue: row.querySelector("[data-cfg-sectionkey]").value,
      remaining: pick.options.map(function (o) { return o.value; }),
    };
  }

 /* 9b. REMOVING a section the picker added puts its NAME BACK into the picker at once, in the schema's
 * own order — so it can be added again without reloading the page, and the ADD BAR comes back when it
 * was the last option. The reported case: ``frontend`` is in the file, so only ``client`` is on offer. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "DEFAULT", "data-cfg-sections": "" });
    c.appendChild(template(el("fieldset", { "data-cfg-row": "", "data-cfg-section": "" }, [
      el("span", { "data-cfg-sectionname": "" }),
      el("input", { name: "f.DEFAULT.__key", value: "", "data-cfg-regexkey": "",
                    "data-cfg-sectionkey": "" }),
      el("input", { name: "f.DEFAULT.__KEY__.port", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ])));
    const pick = el("select", { "data-cfg-sectionpick": "", "data-cfg-all": "frontend,client" });
    pick.appendChild(el("option", { value: "client" }));
    const bar = el("div", { class: "cfg-addbar" });   // the shipped markup NESTS the picker in the bar
    const add = el("button", { "data-cfg-add": "" });
    bar.appendChild(pick); bar.appendChild(add);
    c.appendChild(bar);
    pick.value = "client";
    dom.fire("click", add);
    const row = c.querySelectorAll("[data-cfg-row]")[0];
    const afterAdd = { remaining: pick.options.map(function (o) { return o.value; }), barHidden: bar.hidden };
    dom.fire("click", row.querySelector("[data-cfg-remove]"));
    result.sectionRemoved = {
      rows: c.querySelectorAll("[data-cfg-row]").length,
      offered: pick.options.map(function (o) { return o.value; }),
      barHidden: bar.hidden,
      afterAdd: afterAdd,
    };
  }

 /* 9c. A section the FILE carried, removed by the X, comes back in its SCHEMA place — BEFORE the
 * spelling still on offer, never appended out of turn. */
  {
    const dom = boot();
    const c = el("div", { "data-cfg-regex": "DEFAULT", "data-cfg-sections": "" });
    const rendered = el("fieldset", { "data-cfg-row": "", "data-cfg-section": "frontend" }, [
      el("input", { name: "f.DEFAULT.frontend.port", value: "1" }),
      el("button", { "data-cfg-remove": "" }),
    ]);
    c.appendChild(rendered);
    const pick = el("select", { "data-cfg-sectionpick": "", "data-cfg-all": "frontend,client" });
    pick.appendChild(el("option", { value: "client" }));
    const bar = el("div", { class: "cfg-addbar" });
    bar.appendChild(pick);
    c.appendChild(bar);
    dom.fire("click", rendered.querySelector("[data-cfg-remove]"));
    result.sectionRemovedInOrder = {
      rows: c.querySelectorAll("[data-cfg-row]").length,
      offered: pick.options.map(function (o) { return o.value; }),
    };
  }

 /* 10. THE "+ ADD" PICKER: picking a choice CLONES the container's EMPTY control
 * into the form (just above the picker), HIDES the used choice, and posts the field like any
 * other — no name is invented by the script. */
  {
    const dom = boot();
    const form = el("form", {});
    const holder = el("input", { name: "f.__remove", value: "", "data-cfg-rmval": "" });
    const body = el("div", {});
    const picker = el("details", { "data-cfg-picker": "" });
    const list = el("ul", { class: "cfg-plist" });
    const item = el("li", { "data-cfg-pitem": "" });
    const pickBtn = el("button", { "data-cfg-pick": "" });
    const tpl = el("template", { "data-cfg-opt": "" });
    tpl.content = { firstElementChild: el("div", { "data-cfg-added": "" }, [
      el("input", { name: "f.logging.utc", value: "", type: "checkbox" }),
      el("button", { "data-cfg-remove-added": "" }),
    ]) };
    item.appendChild(pickBtn); item.appendChild(tpl);
    list.appendChild(item); picker.appendChild(list);
    body.appendChild(picker);
    form.appendChild(holder); form.appendChild(body);
    dom.fire("click", pickBtn);
    const chosen = item;
    result.pickerAdd = {
      added: body.querySelectorAll("[data-cfg-added]").length,
      names: namesOfInputs(body),
      choiceHidden: chosen.hidden === true,
      holderUntouched: holder.value,
    };
  }

 /* 11. REMOVING a control the picker added is INSTANT (nothing is in the file): the control goes and
 * the choice comes back. */
  {
    const dom = boot();
    const form = el("form", {});
    const body = el("div", {});
    const picker = el("details", { "data-cfg-picker": "" });
    const item = el("li", { "data-cfg-pitem": "" });
    const pickBtn = el("button", { "data-cfg-pick": "" });
    const tpl = el("template", { "data-cfg-opt": "" });
    tpl.content = { firstElementChild: el("div", { "data-cfg-added": "" }, [
      el("input", { name: "f.logging.utc", value: "" }),
      el("button", { "data-cfg-remove-added": "" }),
    ]) };
    item.appendChild(pickBtn); item.appendChild(tpl);
    picker.appendChild(item);
    body.appendChild(picker);
    form.appendChild(body);
    dom.fire("click", pickBtn);
    const added = body.querySelectorAll("[data-cfg-added]")[0];
    dom.fire("click", added.querySelector("[data-cfg-remove-added]"));
    result.pickerRemoveAdded = {
      added: body.querySelectorAll("[data-cfg-added]").length,
      names: namesOfInputs(body),
      choiceShown: item.hidden === false,
    };
  }

 /* 12. THE TRASHCAN on a value-carrying field: the first press only OPENS the warning; CONFIRMING it
 * names the field's ENCODED path in the form's removal holder and removes the control. */
  {
    const dom = boot();
    const form = el("form", {});
    const holder = el("input", { name: "f.__remove", value: "", "data-cfg-rmval": "" });
    const field = el("div", { "data-cfg-field": "" });
    const rm = el("span", { "data-cfg-setrm": "", "data-cfg-rpath": "logging.utc" });
    const start = el("button", { "data-cfg-rm-start": "" });
    const warn = el("span", { "data-cfg-rmwarn": "", hidden: "" });
    const confirm = el("button", { "data-cfg-rm-confirm": "" });
    const cancel = el("button", { "data-cfg-rm-cancel": "" });
    warn.appendChild(confirm); warn.appendChild(cancel);
    rm.appendChild(start); rm.appendChild(warn);
    field.appendChild(rm);
    form.appendChild(holder); form.appendChild(field);
    dom.fire("click", start);
    const opened = { warningShown: warn.hidden === false, startHidden: start.hidden === true,
                     removedYet: form.querySelectorAll("[data-cfg-field]").length };
    dom.fire("click", cancel);
    const afterCancel = { warningShown: warn.hidden, startShown: start.hidden };
    dom.fire("click", start);
    dom.fire("click", confirm);
    result.rowRemoveSet = {
      opened: opened, afterCancel: afterCancel,
      holder: holder.value,
      fieldsLeft: form.querySelectorAll("[data-cfg-field]").length,
    };
  }

 /* 13. TURNING A SCALAR INTO THE LIST the schema also allows (`*_or_list`). The convert control
 * REMOVES the scalar's own input — a hidden one would still post, so the old value would arrive as an
 * extra row — clones ONE ROW PER VALUE (a CSV scalar splitting on its commas) and flips the shape
 * marker to `list`, which is what the save reads to write a list rather than a scalar. */
  {
    const dom = boot();
    const wrap = el("div", { "data-cfg-orlist": "patterns", "data-cfg-csv": "" });
    const marker = el("input", { name: "f.patterns.__form", value: "scalar", "data-cfg-form": "" });
    const box = el("div", { "data-cfg-orvalue": "" }, [
      el("input", { name: "f.patterns", value: "a, b ,c" }),
      el("button", { "data-cfg-tolist": "" }),
    ]);
    const rows = el("div", { "data-cfg-list": "patterns", "data-cfg-orrows": "", hidden: "" });
    const tpl = el("template", { "data-cfg-template": "" });   // the markup the page really ships
    tpl.content = { firstElementChild: el("div", { "data-cfg-row": "" }, [
      el("input", { name: "f.patterns", value: "" }),
      el("button", { "data-cfg-remove": "" }),
    ]) };
    rows.appendChild(tpl);
    rows.appendChild(el("button", { "data-cfg-add": "" }));
    wrap.appendChild(marker);
    wrap.appendChild(box);
    wrap.appendChild(rows);
    dom.fire("click", box.querySelector("[data-cfg-tolist]"));
    result.orListConverted = {
      marker: marker.value,
      rowsHidden: rows.hidden,
      scalarBoxesLeft: wrap.querySelectorAll("[data-cfg-orvalue]").length,
      rowValues: rows.querySelectorAll("[data-cfg-row]").map(function (row) {
        const fields = row.querySelectorAll("input");
        return fields.length ? fields[0].value : null;
      }),
      names: namesOfInputs(wrap),
    };
  }

  process.stdout.write(JSON.stringify(result));
}
