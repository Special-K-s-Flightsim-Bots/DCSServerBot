/* THE `data-lookup` HARNESS — the shipped `/static/player-lookup.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. What the script does is what the FIELD does while it is
 * typed in — a suggestion list appears, keys move through it, and a pick writes an UCID into the box.
 * None of that is markup, so none of it can be asserted on a rendered page; this loads the REAL
 * shipped file (no copy, no re-implementation) into a DOM stub and fires the events a browser would.
 *
 * THE STUB implements only what the file uses: `document.readyState`, `querySelectorAll("input[
 * data-lookup]")`, `querySelector('label[for=…]')` (the popup's accessible name is the field's own
 * label), `createElement` for the `span`/`ul`/`li` it builds, `insertBefore`/`appendChild`/
 * `removeChild`, `textContent`, `hidden`, `className`, `id`, `setAttribute`/`getAttribute`,
 * `addEventListener`/firing, and `window.{setTimeout,clearTimeout,fetch}`.
 *
 * THE DEBOUNCE AND THE REQUESTS ARE THE HARNESS'S TO DRIVE: the timers are queued and run explicitly,
 * and every `fetch` returns a promise the harness answers when it chooses — which is the only way to
 * prove the two things that matter and cannot be seen on a page: that ONE request goes out per typed
 * burst, and that a STALE answer (one whose query the box has moved on from) is ignored.
 *
 * `AbortController` is deliberately NOT provided: the browser cancels an in-flight request with it,
 * but the guard that must hold on its own — and that this harness pins — is the script's own read of
 * what the box currently asks for.
 *
 * Usage: node player_lookup_harness.js <path to player-lookup.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const URL = "/actions/ban/players";

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
    },
  });
  return node;
}

function find(root, className) {
  if (root.className === className) { return root; }
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
  field.id = "ucid-dialog";
  field.setAttribute("data-lookup", URL);
  const label = element("label");
  label.textContent = "Player (UCID)";
  const para = element("p");
  para.appendChild(label);
  para.appendChild(field);

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector(selector) {
      return selector === 'label[for="ucid-dialog"]' ? label : null;
    },
    querySelectorAll(selector) {
      return selector === "input[data-lookup]" ? [field] : [];
    },
    createElement(tag) { return element(tag); },
  };

  /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  new Function("document", "window", SOURCE)(document, window);

  const states = {};

  function snapshot() {
    const list = find(para, "lk-list");
    const rows = list
      ? list.children.map((option) => {
          const name = find(option, "lk-name");
          const ucid = find(option, "lk-ucid");
          return {
            name: name ? name.textContent : null,
            ucid: ucid ? ucid.textContent : null,
            role: option.getAttribute("role"),
            selected: option.getAttribute("aria-selected"),
            selected_look: option.className.indexOf("sel") >= 0,
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
      combobox: {role: field.getAttribute("role"),
                 autocomplete: field.getAttribute("aria-autocomplete")},
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
    const list = find(para, "lk-list");
    const event = {prevented: false, preventDefault() { this.prevented = true; }};
    list.children[optionIndex].fire("mousedown", event);
    return event;
  }

  const VIPER = "4b1f9c22" + "4b1f9c22" + "4b1f9c22" + "4b1f9c22";

  states.initial = snapshot();

  /* 1. ONE request per typed burst, and the popup's contract */
  type("vip");
  states.typed = snapshot();
  runTimers();
  states.asked = snapshot();
  reply({players: [{name: "Viper", ucid: VIPER}, {name: "", ucid: "d" + "e".repeat(31)}]});
  await flush();
  states.offered = snapshot();

  /* 2. a press takes the row under the pointer */
  states.press_event = press(0);
  states.picked = snapshot();

  /* 3. Down + Enter takes the SECOND row */
  type("vip");
  runTimers();
  reply({players: [{name: "Viper", ucid: VIPER}, {name: "Viper Two", ucid: "e" + "f".repeat(31)}]});
  await flush();
  states.down = key("ArrowDown");
  states.after_down = snapshot();
  states.up = key("ArrowUp");
  states.after_up = snapshot();
  states.enter = key("Enter");
  states.enter_picked = snapshot();

  /* 4. Escape puts the list away WITHOUT changing what was typed */
  type("vip");
  runTimers();
  reply({players: [{name: "Viper", ucid: VIPER}]});
  await flush();
  states.escape = key("Escape");
  states.after_escape = snapshot();

  /* 5. an answer matching nobody leaves the box as it was */
  type("nobody");
  runTimers();
  reply({players: []});
  await flush();
  states.empty = snapshot();

  /* 6. a refused answer is not an error: no popup, the box unchanged */
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
  reply({players: [{name: "Answer To vi", ucid: VIPER}]});
  await flush();
  states.stale_ignored = snapshot();
  reply({players: [{name: "Answer To viper", ucid: "a".repeat(32)},
                   {name: "Another", ucid: "b".repeat(32)}]});
  await flush();
  states.answer_taken = snapshot();

  /* 8. Enter with nothing offered is the dialog's own submit */
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