/* THE FLEET POLL'S HARNESS — the shipped `/static/site-live.js` driven against a minimal DOM.
 *
 * The script's whole job is writing an answer into cells that the SERVER already composed, so what
 * needs proving is the WRITE: which element each key lands on, that a state flips the row's class
 * and the live span's visibility, and that the bar widths follow. Rendered-markup assertions cannot
 * see any of that.
 *
 * The stub implements only what the script uses: `document.querySelector`,
 * `document.querySelectorAll`, `document.hidden`, `document.addEventListener`, per-element
 * `querySelector` / `querySelectorAll`, `classList.toggle`, `style` and `textContent`, plus a
 * `fetch` the harness resolves by hand and a `setInterval` it captures instead of scheduling.
 *
 * Usage: node site_live_harness.js <path to site-live.js>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const SOURCE = fs.readFileSync(process.argv[2], "utf8");

function matches(node, selector) {
  let found = null;
  let match = /^\[data-live="([^"]+)"\]$/.exec(selector);
  if (match) { found = node.attrs["data-live"] === match[1]; }
  match = /^\[data-live-board\]$/.exec(selector);
  if (match) { found = Object.prototype.hasOwnProperty.call(node.attrs, "data-live-board"); }
  match = /^\[data-srv\]$/.exec(selector);
  if (match) { found = Object.prototype.hasOwnProperty.call(node.attrs, "data-srv"); }
  match = /^\[data-srv="([^"]+)"\]$/.exec(selector);
  if (match) { found = node.attrs["data-srv"] === match[1]; }
  return Boolean(found);
}

function walk(node, out) {
  (node.children || []).forEach(function (child) {
    out.push(child);
    walk(child, out);
  });
  return out;
}

function el(tag, attrs, classes) {
  const node = {
    tag: tag,
    attrs: attrs || {},
    textContent: "",
    style: {},
    children: [],
    classList: {
      toggle(name, on) {
        const set = new Set((this._value || "").split(" ").filter(Boolean));
        if (on) { set.add(name); } else { set.delete(name); }
        this._value = Array.from(set).join(" ");
      },
      _value: (classes || []).join(" "),
    },
    get className() { return this.classList._value; },
    set className(value) { this.classList._value = value; },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    querySelector(selector) {
      const hit = walk(this, []).find((child) => matches(child, selector));
      return hit || null;
    },
    querySelectorAll(selector) {
      return walk(this, []).filter((child) => matches(child, selector));
    },
  };
  return node;
}

function cell(key, text) {
  const node = el("span", {"data-live": key});
  node.textContent = text || "";
  return node;
}

/* One article exactly as `site_servers.html` renders a running row: every hook the script writes. */
function article(name) {
  const node = el("article", {"data-srv": name}, ["srow"]);
  node.children = [
    cell("dot", ""),
    cell("state", "Running"),
    cell("live", ""),
    cell("players", "0"),
    cell("mission", ""),
    cell("map", ""),
    cell("runtime", ""),
    cell("restart", ""),
    cell("slots-coalitions", ""),
    cell("slots-none", ""),
    cell("slots-sides", ""),
    cell("slots-red", ""),
    cell("blue-text", ""),
    cell("blue-bar", ""),
    cell("red-text", ""),
    cell("red-bar", ""),
  ];
  node.children[0].className = "dot pulse";
  return node;
}

function boot() {
  const board = el("div", {"data-live-board": "/fleet.json"}, ["board"]);
  board.children = [article("Root-1"), article("Root-2")];
  const up = cell("up", "1 of 2 up");
  const flying = cell("flying", "11 pilots in the air");
  const timers = [];
  const fetches = [];
  const document = {
    hidden: false,
    documentElement: el("html", {}, []),
    addEventListener() {},
    querySelector(selector) {
      if (matches(board, selector)) { return board; }
      if (matches(up, selector)) { return up; }
      if (matches(flying, selector)) { return flying; }
      return null;
    },
    querySelectorAll() { return []; },
  };
  const window = {
    fetch: undefined,
    setInterval(fn, every) { timers.push({fn, every}); return timers.length; },
  };
  function fetchStub(url, opts) {
    let settle;
    const promise = new Promise((resolve) => { settle = resolve; });
    fetches.push({url, opts, resolve: settle});
    return promise;
  }
  window.fetch = fetchStub;
  const run = new Function("document", "window", "fetch", "setInterval", SOURCE);
  run(document, window, fetchStub, window.setInterval);
  return {board, up, flying, timers, fetches, document};
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const ANSWER = {
  up: "2 of 2 up",
  flying: "3 pilots in the air",
  labels: {no_mission: "No mission", no_map: "no map",
           coalitions: "Coalitions", no_slots: "No slots"},
  rows: [
    {name: "Root-1", state: "Paused", dot: "st-paused", online: true, players: "3",
     in_mission: true, mission: "Northern Shield D3", map: "Syria", runtime: "2:14:36",
     restart: "0:01:12", restart_hot: true, slots: "sides",
     blue_text: "2 / 16", blue_share: 12.5, red_text: "1 / 8", red_share: 12.5},
    {name: "Root-2", state: "Running", dot: "pulse", online: true, players: "0",
     in_mission: false, mission: "No mission", map: "no map", runtime: "—",
     restart: "—", restart_hot: false, slots: "none",
     blue_text: "", blue_share: 0, red_text: "", red_share: 0},
  ],
};

function snapshot(board, up, flying) {
  const rows = board.querySelectorAll("[data-srv]").map(function (node) {
    const read = function (key) {
      const found = node.querySelector('[data-live="' + key + '"]');
      return found ? {text: found.textContent, display: found.style.display,
                      className: found.className, width: found.style.width} : null;
    };
    return {
      name: node.getAttribute("data-srv"),
      rowClass: node.className,
      dot: read("dot"),
      state: read("state"),
      live: read("live"),
      players: read("players"),
      mission: read("mission"),
      map: read("map"),
      runtime: read("runtime"),
      restart: read("restart"),
      coalitions: read("slots-coalitions"),
      none: read("slots-none"),
      sides: read("slots-sides"),
      red: read("slots-red"),
      blueText: read("blue-text"),
      blueBar: read("blue-bar"),
      redText: read("red-text"),
      redBar: read("red-bar"),
    };
  });
  return {up: up.textContent, flying: flying.textContent, rows};
}

function bootSingle(rendered) {
  const marker = el("span", {"data-live-board": "/fleet.json", "data-live-server": "Root-1",
                             "data-live-state": rendered}, ["live"]);
  marker.children = [el("span", {"data-live": "dot"}, ["dot"]),
                     cell("state", "Running")];
  const navigations = [];
  const timers = [];
  const fetches = [];
  const document = {
    hidden: false,
    addEventListener() {},
    querySelector(selector) { return matches(marker, selector) ? marker : null; },
    querySelectorAll() { return []; },
  };
  const window = {
    location: {
      pathname: "/fleet/Root-1", search: "",
      replace(to) { navigations.push(to); },
    },
  };
  function fetchStub(url, opts) {
    let settle;
    const promise = new Promise((resolve) => { settle = resolve; });
    fetches.push({url, opts, resolve: settle});
    return promise;
  }
  window.fetch = fetchStub;
  window.setInterval = function (fn, every) { timers.push({fn, every}); return timers.length; };
  const run = new Function("document", "window", "fetch", "setInterval", SOURCE);
  run(document, window, fetchStub, function (fn, every) { timers.push({fn, every}); });
  return {marker, timers, fetches, navigations};
}

const SINGLE = {
  found: true,
  live_state: "ready",
  live_entries: 2,
  row: {name: "Root-1", state: "Paused", dot: "st-paused", online: true, players: "4",
        in_mission: true, mission: "Northern Shield D3", map: "Syria", runtime: "0:40:00",
        restart: "—", restart_hot: false, slots: "sides",
        blue_text: "3 / 16", blue_share: 18.8, red_text: "1 / 8", red_share: 12.5},
};

async function main() {
  const result = {};

  /* 1. THE WRITE: every cell the answer names is written, and nothing else. */
  {
    const dom = boot();
    result.interval = dom.timers.length === 1 ? dom.timers[0].every : null;
    dom.timers[0].fn();
    result.requested = dom.fetches.length ? dom.fetches[0].url : null;
    dom.fetches[0].resolve({ok: true, json: () => Promise.resolve(ANSWER)});
    await tick(); await tick();
    result.painted = snapshot(dom.board, dom.up, dom.flying);
  }

  /* 2. A HIDDEN TAB DOES NOT POLL: the interval tick is a no-op while the document is hidden. */
  {
    const dom = boot();
    dom.document.hidden = true;
    dom.timers[0].fn();
    result.hiddenRequests = dom.fetches.length;
  }

  /* 3. A FAILED POLL LEAVES THE RENDERED FIGURES IN PLACE. */
  {
    const dom = boot();
    dom.timers[0].fn();
    dom.fetches[0].resolve({ok: false, json: () => Promise.resolve({})});
    await tick(); await tick();
    result.afterRefusal = snapshot(dom.board, dom.up, dom.flying);
  }

  /* 4. A PAGE WITH NO BOARD NEVER POLLS (every other site page loads this script too). */
  {
    const timers = [];
    const document = {
      hidden: false,
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
    const run = new Function("document", "window", "fetch", "setInterval", SOURCE);
    run(document, {fetch: () => { throw new Error("polled a page with no board"); }},
        function () { throw new Error("polled a page with no board"); },
        function (fn, every) { timers.push(every); });
    result.otherPagesPoll = timers.length;
  }

  /* 5. ONE SERVER'S PAGE: the chip is repainted, and a page drawn before the live read landed is
        taken again ONCE — never in a loop, and never when it was drawn from a settled answer. */
  {
    const dom = bootSingle("pending");
    dom.timers[0].fn();
    result.singleRequested = dom.fetches.length ? dom.fetches[0].url : null;
    dom.fetches[0].resolve({ok: true, json: () => Promise.resolve(SINGLE) });
    await tick(); await tick();
    const afterFirst = {navigations: dom.navigations.slice(),
                        state: dom.marker.querySelector('[data-live="state"]').textContent};
    dom.timers[0].fn();
    dom.fetches[1].resolve({ok: true, json: () => Promise.resolve(SINGLE) });
    await tick(); await tick();
    result.single = {afterFirst, navigations: dom.navigations.slice()};
  }

  /* 6. A PAGE DRAWN FROM A SETTLED ANSWER never reloads. */
  {
    const dom = bootSingle("ready");
    dom.timers[0].fn();
    dom.fetches[0].resolve({ok: true, json: () => Promise.resolve(SINGLE)});
    await tick(); await tick();
    result.singleSettled = dom.navigations.slice();
  }

  process.stdout.write(JSON.stringify(result));
}

main();
