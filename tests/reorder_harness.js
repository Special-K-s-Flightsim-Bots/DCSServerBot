/* THE `data-reorder` HARNESS — the shipped `/static/reorder.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS. What the script does is a WRITE: which order the body carries,
 * how many requests one gesture makes, and whether a refusal puts the rows back. None of that is
 * markup, so none of it can be asserted on a rendered page. This loads the REAL shipped file (no
 * copy, no re-implementation) into a DOM stub and fires the events a browser would.
 *
 * THE TABLE IS BUILT WITHOUT `data-reorder-glyphs`: the opt-in is `data-reorder` alone, so a script
 * that still required the glyph attribute would enhance nothing and every scenario below would see
 * zero requests.
 *
 * TWO SHAPES, ONE SCRIPT: the default list is a table (its rows in a `tbody`). A caller that wants
 * the OTHER shape — a plain row container carrying `data-reorder-body`, which is what the missions
 * list renders — passes the attributes and row ids a page produced as a second argument, and the
 * container scenarios run on exactly that markup.
 *
 * THE STUB implements only what the file uses: `document.querySelectorAll("[data-reorder]")`,
 * `createElement`, an element's `getAttribute`/`setAttribute`/`hasAttribute`/`closest`/`matches`/
 * `querySelector`/`querySelectorAll`/`addEventListener`/`appendChild`/`insertBefore`/`removeChild`/
 * `classList`/`compareDocumentPosition`/`getBoundingClientRect`/`nextSibling`/`previousElementSibling`/
 * `focus`, plus `fetch` and `URLSearchParams`, which the harness answers and records. Nothing is
 * provided that the script does not touch, so a script that grew a dependency on a real browser
 * feature fails here instead of in production.
 *
 * Usage: node reorder_harness.js <path to reorder.js> [<rendered container JSON>]
 *        -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const CONTAINER = process.argv[3]
  ? JSON.parse(fs.readFileSync(process.argv[3], "utf8")) : null;

const ROW_HEIGHT = 30;
const FOLLOWING = 4;
const PRECEDING = 2;

/* ------------------------------------------------------------------ the minimal DOM */

function element(tag, attributes) {
  const node = {
    tagName: String(tag).toUpperCase(),
    attributes: Object.assign({}, attributes || {}),
    children: [],
    parentNode: null,
    classes: {},
    listeners: {},
    textContent: "",
    focused: false,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name] : null;
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    appendChild(child) {
      if (child.parentNode) { child.parentNode.removeChild(child); }
      this.children.push(child);
      child.parentNode = this;
      return child;
    },
    insertBefore(child, reference) {
      if (child.parentNode) { child.parentNode.removeChild(child); }
      const index = reference ? this.children.indexOf(reference) : -1;
      this.children.splice(index < 0 ? this.children.length : index, 0, child);
      child.parentNode = this;
      return child;
    },
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index >= 0) { this.children.splice(index, 1); }
      child.parentNode = null;
      return child;
    },
    focus() { this.focused = true; },
    setPointerCapture() {},
    matches(selector) { return matches(this, selector); },
    closest(selector) {
      let current = this;
      while (current) {
        if (matches(current, selector)) { return current; }
        current = current.parentNode;
      }
      return null;
    },
    querySelector(selector) {
      const found = descendants(this).filter((entry) => matches(entry, selector));
      return found.length ? found[0] : null;
    },
    querySelectorAll(selector) {
      return descendants(this).filter((entry) => matches(entry, selector));
    },
    getBoundingClientRect() {
      const index = this.parentNode ? this.parentNode.children.indexOf(this) : 0;
      return { top: index * ROW_HEIGHT, height: ROW_HEIGHT,
               bottom: index * ROW_HEIGHT + ROW_HEIGHT, left: 0, width: 100 };
    },
    get nextSibling() {
      if (!this.parentNode) { return null; }
      const siblings = this.parentNode.children;
      const index = siblings.indexOf(this);
      return index < 0 || index + 1 >= siblings.length ? null : siblings[index + 1];
    },
    get previousElementSibling() {
      if (!this.parentNode) { return null; }
      const siblings = this.parentNode.children;
      const index = siblings.indexOf(this);
      return index <= 0 ? null : siblings[index - 1];
    },
    get nextElementSibling() {
      return this.nextSibling;
    },
    compareDocumentPosition(other) {
      const order = documentOrder(this);
      const mine = order.indexOf(this);
      const theirs = order.indexOf(other);
      if (mine < 0 || theirs < 0) { return 1; }
      if (theirs > mine) { return FOLLOWING; }
      if (theirs < mine) { return PRECEDING; }
      return 0;
    }
  };
  node.classList = {
    add(name) { node.classes[name] = true; },
    remove(name) { delete node.classes[name]; },
    contains(name) { return Boolean(node.classes[name]); }
  };
  return node;
}

function descendants(node) {
  const out = [];
  node.children.forEach((child) => { out.push(child); out.push(...descendants(child)); });
  return out;
}

function documentOrder(node) {
  let root = node;
  while (root.parentNode) { root = root.parentNode; }
  const all = [root].concat(descendants(root));
  return all;
}

function matches(node, selector) {
  if (selector.charAt(0) === ".") { return Boolean(node.classes[selector.slice(1)]); }
  const attribute = /^\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(selector);
  if (!attribute) { return false; }
  if (!node.hasAttribute(attribute[1])) { return false; }
  return attribute.length < 3 || attribute[2] === undefined
    || node.getAttribute(attribute[1]) === attribute[2];
}

/* ------------------------------------------------------------------ the list under test */

/* ONE builder for both shapes the script serves: a TABLE (its `tbody` holds the rows) and a plain
   CONTAINER (it holds them itself and says so with `data-reorder-body`). A container's shape is
   supplied from OUTSIDE — `argv[3]` carries the attributes and row ids a rendered page produced —
   so the missions pin drives the markup the template actually emits, not a re-spelling of it. */
function build(options) {
  const settings = Object.assign({
    answer: { ok: true, message: "" },
    shape: "table",
    attributes: null,
    ids: ["1", "2", "3"]
  }, options || {});
  const table = settings.shape === "table";
  const root = element(table ? "table" : "div", settings.attributes || {
    "data-reorder": "",
    "data-reorder-url": "/music/Jungle%20Beats/reorder",
    "data-reorder-field": "order",
    "data-reorder-token": "tok",
    "data-reorder-token-field": "_csrf_token",
    "data-reorder-refused": "Not saved: the order was refused. Reload the page.",
    "data-reorder-offline": "Not saved: the new order could not be sent."
  });
  const card = element("section", { class: "card" });
  card.className = "card";
  const body = table ? element("tbody", {}, []) : root;
  const handles = {};
  settings.ids.forEach((id) => {
    const row = element(table ? "tr" : "div", { "data-reorder-row": "", "data-reorder-id": id });
    const handle = element(table ? "td" : "div", { "data-reorder-handle": "", tabindex: "0" });
    handle.textContent = "⠿";
    row.appendChild(handle);
    row.appendChild(element("div", { class: "nm" }));
    body.appendChild(row);
    handles[id] = handle;
  });
  if (table) {
    root.appendChild(body);
    root.tBodies = [body];
  }
  card.appendChild(root);

  const document = {
    body: element("body", {}),
    querySelectorAll(selector) {
      return [document.body, card].concat(descendants(document.body), descendants(card))
        .filter((node) => matches(node, selector));
    },
    createElement(tag) { return element(tag, {}); }
  };

  const requests = [];
  const fetch = function (url, init) {
    requests.push({ url: url, method: init.method, body: String(init.body) });
    return Promise.resolve({ json: () => Promise.resolve(settings.answer) });
  };
  fetch.requests = requests;

  const context = {
    document, fetch, URLSearchParams, console,
    Node: { DOCUMENT_POSITION_FOLLOWING: FOLLOWING, DOCUMENT_POSITION_PRECEDING: PRECEDING },
    window: { fetch, URLSearchParams, PointerEvent: function PointerEvent() {} }
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);

  return {
    root, table: root, card, body, handles, requests,
    ids() { return body.children.map((row) => row.getAttribute("data-reorder-id")); },
    fired(type, event) {
      const handlers = body.listeners[type] || [];
      const full = Object.assign({ preventDefault() {}, button: 0 }, event);
      handlers.forEach((handler) => handler(full));
    },
    settle() { return new Promise((resolve) => setTimeout(resolve, 10)); },
    status() {
      const line = card.querySelector("[data-reorder-status]");
      return line ? line.textContent : null;
    }
  };
}

/* ------------------------------------------------------------------ the scenarios */

function orderOf(request) {
  return new URLSearchParams(request.body).getAll("order");
}

async function main() {
  const report = {};

  /* 1. A POINTER DROP: no glyph attribute anywhere, one POST carrying the whole new order. */
  const drop = build();
  report.glyph_attribute_absent = !drop.table.hasAttribute("data-reorder-glyphs");
  drop.fired("pointerdown", { target: drop.handles["1"], pointerId: 7, button: 0 });
  drop.fired("pointermove", { pointerId: 7, clientY: 50 });
  drop.fired("pointerup", { pointerId: 7, clientY: 50 });
  await drop.settle();
  report.drop = {
    requests: drop.requests.length,
    url: drop.requests.length ? drop.requests[0].url : null,
    method: drop.requests.length ? drop.requests[0].method : null,
    order: drop.requests.length ? orderOf(drop.requests[0]) : [],
    dom: drop.ids()
  };

  /* 2. THE KEYBOARD: Alt+ArrowDown on the first row moves it one place and posts the same order. */
  const key = build();
  key.fired("keydown", { target: key.handles["1"], altKey: true, key: "ArrowDown" });
  await key.settle();
  report.keyboard_down = {
    requests: key.requests.length,
    order: key.requests.length ? orderOf(key.requests[0]) : [],
    dom: key.ids()
  };

  /* 2b. UPWARD: dragging the SECOND row above the first must swap them. The downward case above
        passes even when the up branch is unreachable, so the gesture needs its own scenario. */
  const up = build();
  up.fired("pointerdown", { target: up.handles["2"], pointerId: 11, button: 0 });
  up.fired("pointermove", { pointerId: 11, clientY: 5 });
  up.fired("pointerup", { pointerId: 11, clientY: 5 });
  await up.settle();
  report.drop_up = {
    requests: up.requests.length,
    order: up.requests.length ? orderOf(up.requests[0]) : [],
    dom: up.ids()
  };

  /* 3. THE EDGE: Alt+ArrowUp on the first row writes nothing. */
  const edge = build();
  edge.fired("keydown", { target: edge.handles["1"], altKey: true, key: "ArrowUp" });
  await edge.settle();
  report.keyboard_edge = { requests: edge.requests.length, dom: edge.ids() };

  /* 4. A REFUSAL: the rows go back to the order the gesture started from. */
  const refused = build({ answer: { ok: false, message: "Not the current track list." } });
  refused.fired("keydown", { target: refused.handles["1"], altKey: true, key: "ArrowDown" });
  await refused.settle();
  report.refusal = {
    requests: refused.requests.length,
    dom: refused.ids(),
    status: refused.status()
  };

  /* 5. A PRESS ON A GLYPH, NOT ON THE HANDLE, starts no drag (the row's other controls stay usable) */
  const other = build();
  other.fired("pointerdown", { target: element("button", {}), pointerId: 9, button: 0 });
  other.fired("pointerup", { pointerId: 9 });
  await other.settle();
  report.no_handle = { requests: other.requests.length, dom: other.ids() };

  /* 5b. THE LIST'S OWN STATIC PARAMS: a write that needs more than the order (the missions route needs
        the server) names them in `data-reorder-params`, and every request carries them. */
  const params = build({
    attributes: {
      "data-reorder": "",
      "data-reorder-url": "/actions/server/missions/reorder",
      "data-reorder-field": "order",
      "data-reorder-params": "server=Alpha",
      "data-reorder-token": "tok",
      "data-reorder-token-field": "_csrf_token"
    }
  });
  params.fired("keydown", { target: params.handles["1"], altKey: true, key: "ArrowDown" });
  await params.settle();
  const paramsBody = params.requests.length
    ? new URLSearchParams(params.requests[0].body) : null;
  report.params = {
    requests: params.requests.length,
    server: paramsBody ? paramsBody.getAll("server") : [],
    order: paramsBody ? paramsBody.getAll("order") : []
  };

  /* 5c. A WRITE MAY RENUMBER THE ROWS: the answer's `song_ids` re-stamp the rows in DOM order so the
        NEXT drop names ids that exist. A list whose write leaves ids alone gets no `song_ids`. */
  const restamped = build({ answer: { ok: true, message: "", song_ids: ["9", "8", "7"] } });
  restamped.fired("keydown", { target: restamped.handles["1"], altKey: true, key: "ArrowDown" });
  await restamped.settle();
  report.restamp = { dom: restamped.ids() };

  /* 6. A ROW CONTAINER (the missions list) — only when the caller supplied the shape a page rendered:
        a drop on its grip posts ONE request to ITS url, carrying the WHOLE order under ITS field. */
  if (CONTAINER) {
    const container = build({ shape: "container", attributes: CONTAINER.attributes,
                              ids: CONTAINER.ids });
    const first = CONTAINER.ids[0];
    const field = CONTAINER.attributes["data-reorder-field"] || "order";
    const tokenField = CONTAINER.attributes["data-reorder-token-field"] || "_csrf_token";
    report.container = {
      attributes_rendered: CONTAINER.attributes,
      handled_by_the_script: container.root.listeners.pointerdown !== undefined,
      before: container.ids()
    };
    container.fired("keydown", { target: container.handles[first], altKey: true, key: "ArrowDown" });
    await container.settle();
    const body = container.requests.length ? new URLSearchParams(container.requests[0].body) : null;
    report.container.keyboard = {
      requests: container.requests.length,
      url: container.requests.length ? container.requests[0].url : null,
      method: container.requests.length ? container.requests[0].method : null,
      field: field,
      order: body ? body.getAll(field) : [],
      token: body ? body.getAll(tokenField) : [],
      dom: container.ids()
    };

    /* the pointer route, on the same rendered attributes */
    const drag = build({ shape: "container", attributes: CONTAINER.attributes,
                         ids: CONTAINER.ids });
    drag.fired("pointerdown", { target: drag.handles[first], pointerId: 3, button: 0 });
    drag.fired("pointermove", { pointerId: 3, clientY: 50 });
    drag.fired("pointerup", { pointerId: 3, clientY: 50 });
    await drag.settle();
    report.container.drop = {
      requests: drag.requests.length,
      order: drag.requests.length
        ? new URLSearchParams(drag.requests[0].body).getAll(field) : [],
      dom: drag.ids()
    };
  }

  process.stdout.write(JSON.stringify(report, null, 2) + "\n");
}

main().catch((error) => {
  process.stderr.write(String(error && error.stack ? error.stack : error) + "\n");
  process.exit(1);
});
