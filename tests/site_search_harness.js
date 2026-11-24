/* THE `data-search` HARNESS — the shipped `/static/site-search.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. What the script does happens while the visitor types: a
 * suggestion list appears under the top bar's box, keys move through it, and a pick NAVIGATES to the
 * pilot's page. None of that is markup, so none of it can be asserted on a rendered page; this loads
 * the REAL shipped file (no copy, no re-implementation) into a DOM stub and fires the events a
 * browser would.
 *
 * THE STUB implements only what the file uses: `document.readyState`,
 * `querySelectorAll("form[data-search]")`, the form's own `getAttribute`/`querySelector`, the
 * field's `aria-label` (the popup's accessible name is the field's own label),
 * `createElement` for the `span`/`ul`/`li` it builds, `insertBefore`/`appendChild`/`removeChild`,
 * `textContent`, `hidden`, `className`, `id`, `setAttribute`/`getAttribute`,
 * `addEventListener`/firing, `window.{setTimeout,clearTimeout,fetch}` and `window.location.assign`.
 *
 * THE DEBOUNCE AND THE REQUESTS ARE THE HARNESS'S TO DRIVE: the timers are queued and run explicitly,
 * and every `fetch` returns a promise the harness answers when it chooses — which is the only way to
 * prove the things that cannot be seen on a page: that ONE request goes out per typed burst, that
 * ONE character asks nothing, that a STALE answer is ignored, and that the same fragment is not
 * asked for twice.
 *
 * `AbortController` is deliberately NOT provided: the browser cancels an in-flight request with it,
 * but the guard that must hold on its own — and that this harness pins — is the script's own read of
 * what the box currently asks for.
 *
 * Usage: node site_search_harness.js <path to site-search.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const URL = "/group1/search.json";

/* ------------------------------------------------------------------ the minimal DOM */

function element(tag) {
  const node = {
    tagName: tag,
    children: [],
    parentNode: null,
    className: "",
    id: "",
    hidden: false,
    value: "",
    _text: "",
    attributes: {},
    handlers: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    insertBefore(child, before) {
      child.parentNode = this;
      const at = this.children.indexOf(before);
      this.children.splice(at < 0 ? this.children.length : at, 0, child);
      return child;
    },
    removeChild(child) {
      const at = this.children.indexOf(child);
      if (at >= 0) { this.children.splice(at, 1); }
      child.parentNode = null;
      return child;
    },
    addEventListener(kind, fn) { (this.handlers[kind] = this.handlers[kind] || []).push(fn); },
    fire(kind, event) { (this.handlers[kind] || []).forEach((fn) => fn(event || {})); },
  };
  Object.defineProperty(node, "textContent", {
    get() {
      if (this._text) { return this._text; }
      return this.children.map((child) => child.textContent).join("");
    },
    set(value) {
      this._text = value === null || value === undefined ? "" : String(value);
      this.children = [];
      this.value = this._text;
    },
  });
  return node;
}

function find(root, className) {
  if (root.className.split(" ").indexOf(className) >= 0) { return root; }
  for (let i = 0; i < root.children.length; i += 1) {
    const hit = find(root.children[i], className);
    if (hit) { return hit; }
  }
  return null;
}

/* ------------------------------------------------------------------ the harness's own clocks */

const timers = [];
const fetches = [];
const answers = [];
const visited = [];

const window = {
  setTimeout(fn) { const entry = {fn, cancelled: false}; timers.push(entry); return entry; },
  clearTimeout(entry) {
    if (!entry) { return; }
    entry.cancelled = true;
    const at = timers.indexOf(entry);
    if (at >= 0) { timers.splice(at, 1); }
  },
  fetch(url) {
    fetches.push(url);
    return new Promise((resolve) => { answers.push(resolve); });
  },
  location: {
    assign(target) { visited.push(target); },
  },
};

function runTimers() {
  const due = timers.splice(0);
  due.forEach((entry) => { if (!entry.cancelled) { entry.fn(); } });
}

/* a request is answered from the queue, in the order it was made */
function reply(body, ok) {
  const resolve = answers.shift();
  resolve({ok: ok === undefined ? true : ok, json: () => Promise.resolve(body)});
}

async function flush() {
  for (let i = 0; i < 8; i += 1) { await Promise.resolve(); }
}

/* ------------------------------------------------------------------ boot + scenarios */

async function boot() {
  const field = element("input");
  field.id = "q";
  field.setAttribute("type", "search");
  field.setAttribute("aria-label", "Find a pilot");

  const form = element("form");
  form.setAttribute("data-search", URL);
  form.appendChild(field);
  form.querySelector = function (selector) {
    return selector === 'input[type="search"]' ? field : null;
  };

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll(selector) {
      return selector === "form[data-search]" ? [form] : [];
    },
    createElement(tag) { return element(tag); },
  };

  /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  new Function("document", "window", SOURCE)(document, window);

  const states = {};

  function snapshot() {
    const list = find(form, "sr-list");
    const rows = list
      ? list.children.map((option) => {
          const name = find(option, "sr-name");
          return {
            name: name ? name.textContent : null,
            role: option.getAttribute("role"),
            selected: option.getAttribute("aria-selected"),
            selected_look: option.className.split(" ").indexOf("sel") >= 0,
          };
        })
      : [];
    return {
      value: field.value,
      list: list ? {hidden: list.hidden, role: list.getAttribute("role"),
                    label: list.getAttribute("aria-label"), id: list.id,
                    expanded: field.getAttribute("aria-expanded"),
                    controls: field.getAttribute("aria-controls")} : null,
      rows,
      fetches: fetches.slice(),
      visited: visited.slice(),
      combobox: {role: field.getAttribute("role"),
                 autocomplete: field.getAttribute("aria-autocomplete")},
      wrapped: !!(field.parentNode && field.parentNode.className === "sr-wrap"),
    };
  }

  function type(text) {
    field.value = text;
    field.fire("input");
  }

  function key(name) {
    const event = {key: name, prevented: false, preventDefault() { this.prevented = true; }};
    field.fire("keydown", event);
    return event;
  }

  function press(optionIndex) {
    const list = find(form, "sr-list");
    const event = {prevented: false, preventDefault() { this.prevented = true; }};
    list.children[optionIndex].fire("mousedown", event);
    return event;
  }

  states.initial = snapshot();

  /* 1. ONE request per typed burst; ONE character asks nothing; the popup's contract */
  type("v");
  runTimers();
  states.one_char = snapshot();
  type("vi");
  states.typed = snapshot();
  runTimers();
  states.asked = snapshot();
  reply({pilots: [{name: "Viper", href: "/player/u-1"},
                  {name: "Viper Two", href: "/player/u-2"}]});
  await flush();
  states.offered = snapshot();

  /* 2. a press takes the row under the pointer and navigates to its href */
  states.press_event = press(0);
  states.picked = snapshot();

  /* 3. Down + Enter takes the SECOND row, and Enter does not submit the form */
  type("vip");
  runTimers();
  reply({pilots: [{name: "Viper", href: "/player/u-1"},
                  {name: "Viper Two", href: "/player/u-2"}]});
  await flush();
  states.down = key("ArrowDown");
  states.after_down = snapshot();
  states.up = key("ArrowUp");
  states.after_up = snapshot();
  states.enter = key("Enter");
  states.enter_picked = snapshot();

  /* 4. Escape puts the list away WITHOUT changing what was typed */
  type("vipe");
  runTimers();
  reply({pilots: [{name: "Viper", href: "/player/u-1"}]});
  await flush();
  states.escape = key("Escape");
  states.after_escape = snapshot();

  /* 5. an answer matching nobody leaves the form as it was */
  type("nobody");
  runTimers();
  reply({pilots: []});
  await flush();
  states.empty = snapshot();

  /* 6. a refused answer is not an error: no popup, the form unchanged */
  type("refused");
  runTimers();
  reply(null, false);
  await flush();
  states.refused = snapshot();

  /* 7. THE STALE ANSWER: two bursts, the FIRST one ("vi") answered LAST — after the box has already
     moved on to "viper". The transport is FIFO, so the answer to "vi" resolves first here. */
  type("vi");
  runTimers();
  type("viper");
  runTimers();
  reply({pilots: [{name: "Answer To vi", href: "/player/stale"}]});
  await flush();
  states.stale_ignored = snapshot();
  reply({pilots: [{name: "Answer To viper", href: "/player/u-1"},
                  {name: "Another", href: "/player/u-2"}]});
  await flush();
  states.answer_taken = snapshot();

  /* 8. THE SAME FRAGMENT IS NOT ASKED TWICE: a trailing space leaves the term where it was */
  type("viper ");
  runTimers();
  states.same_fragment = snapshot();

  /* 9. Enter with nothing offered is the form's own submit */
  type("");
  states.cleared = snapshot();
  states.enter_alone = key("Enter");
  states.tail = snapshot();
  return states;
}

boot().then((states) => {
  process.stdout.write(JSON.stringify({states}));
}).catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
