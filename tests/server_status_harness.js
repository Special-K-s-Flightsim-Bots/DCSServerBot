/* THE STATUS POLL'S HARNESS — the shipped `/static/server-status.js` (optionally beside the shipped
 * `/static/server-log.js`) driven against a minimal DOM, a hand-resolved `fetch` and a manual clock.
 *
 * WHY A BROWSER-LESS HARNESS. Observed behaviour is about WHEN a request is made and WHETHER the status
 * moved on its own: the mark at the top must change while the operator sits on the DCS Log tab, and it
 * must stop when the browser tab is hidden. An assertion on the rendered markup cannot say either.
 * So this loads the REAL shipped files — no copy, no re-implementation — into a DOM stub that
 * implements only what they use, and drives them:
 *   * server-status.js: querySelector("[data-server-status-url]"), querySelectorAll("[data-server-status]"),
 *     getAttribute, innerHTML, document.hidden, visibilitychange;
 *   * server-log.js (when a second argument is given): getElementById, createElement, appendChild,
 *     insertAdjacentHTML, firstElementChild, textContent, disabled, click, the scroll metrics.
 *
 * The FIRST request each script makes happens synchronously while the file is loaded, so the queue is
 * PRIMED BEFORE the script runs (the same discipline the log harness uses).
 *
 * Usage:  node server_status_harness.js <path to server-status.js> [<path to server-log.js>]
 *         -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const STATUS_SCRIPT = process.argv[2];
const LOG_SCRIPT = process.argv[3] || null;

function makeElement(id, attrs) {
  return {
    id: id || "",
    attrs: attrs || {},
    _html: "",
    scrollTop: 0,
    scrollHeight: 100,
    clientHeight: 50,
    className: "",
    textContent: "",
    disabled: false,
    hidden: false,
    listeners: {},
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get firstElementChild() { return this._html ? { offsetTop: this._html.length } : null; },
    appendChild(node) { this._html += (node && typeof node.textContent === "string") ? node.textContent : ""; },
    insertAdjacentHTML(position, html) {
      if (position === "afterbegin") { this._html = String(html) + this._html; }
      else { this._html += String(html); }
    },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    click() { if (!this.disabled) { (this.listeners.click || []).forEach((fn) => fn({})); } },
  };
}

function settle() { return new Promise((resolve) => setImmediate(resolve)); }

function boot(options) {
  const opts = options || {};
  const statusUrlEl = makeElement("", { "data-server-status-url": "/fleet/SRS-1/status",
                                        "data-server-status-seconds": "10" });
  const headRegion = makeElement("", { "data-server-status": "" });
  const cardRegion = makeElement("", { "data-server-status": "" });
  const regions = [headRegion, cardRegion];

  const ids = {};
  if (opts.withLog) {
    ids["dcs-log-card"] = makeElement("dcs-log-card",
      { "data-window-url": "/fleet/SRS-1/log/window", "data-offset": "10",
        "data-follow-seconds": "3" });
    ids["dcs-log-body"] = makeElement("dcs-log-body");
    ids["dcs-log-note"] = makeElement("dcs-log-note");
    ids["dcs-log-note-text"] = makeElement("dcs-log-note-text");
    ids["dcs-log-status"] = makeElement("dcs-log-status");
    ids["dcs-log-older"] = makeElement("dcs-log-older");
  }

  const documentListeners = {};
  const windowListeners = {};
  const document = {
    hidden: false,
    getElementById: (id) => ids[id] || null,
    querySelector: (sel) => (sel === "[data-server-status-url]" ? statusUrlEl : null),
    querySelectorAll: (sel) => (sel === "[data-server-status]" ? regions : []),
    createElement: () => makeElement(),
    addEventListener: (kind, fn) => { (documentListeners[kind] = documentListeners[kind] || []).push(fn); },
  };
  const timers = {};
  let nextTimer = 1;
  const timeouts = {};
  let nextTimeout = 1;
  const window = {
    setInterval: (fn, ms) => { const id = nextTimer++; timers[id] = { fn, ms }; return id; },
    clearInterval: (id) => { delete timers[id]; },
    setTimeout: (fn, ms) => { const id = nextTimeout++; timeouts[id] = { fn, ms }; return id; },
    clearTimeout: (id) => { delete timeouts[id]; },
    addEventListener: (kind, fn) => { (windowListeners[kind] = windowListeners[kind] || []).push(fn); },
  };

  /* ONE fetch, routed BY URL: a status poll answers the status payload, a log follow the log payload.
     The queues are primed BEFORE the scripts load, so each script's first (synchronous) request gets
     its own answer and neither script's payload is handed to the other. */
  const fetches = [];
  const statusQueue = [];
  const logQueue = [];
  if ("status" in opts) { statusQueue.push({ status: opts.status }); }
  if ("log" in opts) { logQueue.push({ available: true, lines: opts.log, offset: 44 }); }
  globalThis.fetch = (url) => {
    fetches.push(url);
    if (url.indexOf("/status") !== -1) {
      const payload = statusQueue.length ? statusQueue.shift() : { status: "" };
      if (payload && payload.networkError) { return Promise.reject(new Error("down")); }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    }
    const payload = logQueue.length ? logQueue.shift() : { available: true, lines: "", offset: 10 };
    if (payload && payload.networkError) { return Promise.reject(new Error("down")); }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  };

  new Function("document", "window", fs.readFileSync(STATUS_SCRIPT, "utf8"))(document, window);
  if (opts.withLog) {
    new Function("document", "window", fs.readFileSync(LOG_SCRIPT, "utf8"))(document, window);
  }

  return {
    headRegion: headRegion,
    cardRegion: cardRegion,
    document: document,
    timers: timers,
    timeouts: timeouts,
    fetches: fetches,
    statusQueue: statusQueue,
    logQueue: logQueue,
    fireVisibility(state) {
      document.hidden = (state === "hidden");
      (documentListeners.visibilitychange || []).forEach((fn) => fn({}));
    },
    tick() { Object.keys(timers).forEach((id) => timers[id].fn()); },
    fireTimeouts() { Object.keys(timeouts).forEach((id) => { const t = timeouts[id]; delete timeouts[id]; t.fn(); }); },
    timerCount() { return Object.keys(timers).length; },
    timeoutCount() { return Object.keys(timeouts).length; },
  };
}

async function main() {
  const result = {};

  /* 1. BOOT: one immediate poll, and the mark is painted into BOTH regions from the answer. */
  {
    const h = boot({ status: "<span class=\"dot pause\"></span> LOADING" });
    await settle();
    result.boot = {
      fetches: h.fetches.slice(),
      painted: h.headRegion.innerHTML.indexOf("LOADING") !== -1,
      paintedBoth: h.cardRegion.innerHTML.indexOf("LOADING") !== -1,
      timerCount: h.timerCount(),
    };
    /* 2. A TICK: a changed status is painted on its own — no reload. */
    h.statusQueue.push({ status: "<span class=\"dot run\"></span> RUNNING" });
    h.tick();
    await settle();
    result.tick = {
      head: h.headRegion.innerHTML,
      moved: h.headRegion.innerHTML.indexOf("RUNNING") !== -1,
      cardMoved: h.cardRegion.innerHTML.indexOf("RUNNING") !== -1,
    };
  }

  /* 3. WHILE THE LOG TAB IS OPEN (the headline): the log follower AND the status poll both run, and a
        status change still lands on the mark. Fails if one script stops the other. */
  {
    const h = boot({ withLog: true, status: "<span class=\"dot pause\"></span> LOADING",
                     log: "<div>LOG-LINE</div>" });
    await settle();
    const bootTimers = h.timerCount();
    h.statusQueue.push({ status: "<span class=\"dot run\"></span> RUNNING" });
    h.tick();
    await settle();
    result.withLogTab = {
      bootTimers: bootTimers,                    // both intervals armed
      logFollowed: h.fetches.some((u) => u.indexOf("/log/window") !== -1),
      statusPolled: h.fetches.filter((u) => u.indexOf("/status") !== -1).length >= 2,
      statusMoved: h.headRegion.innerHTML.indexOf("RUNNING") !== -1,
    };
  }

  /* 4. HIDDEN: the interval is cleared and NO further poll is made; VISIBLE again resumes. */
  {
    const h = boot({ status: "<span class=\"dot pause\"></span> LOADING" });
    await settle();
    const before = h.fetches.length;
    h.fireVisibility("hidden");
    const timersAfterHide = h.timerCount();
    h.tick();
    await settle();
    result.hidden = {
      timerCount: timersAfterHide,
      requestsWhileHidden: h.fetches.length - before,
    };
    h.fireVisibility("visible");
    await settle();
    result.hidden.resumedTimers = h.timerCount();
    result.hidden.requestOnResume = h.fetches.length > before;
  }

  /* 5. A FAILED POLL BACKS OFF: the interval is dropped, ONE retry is armed, and firing it recovers. */
  {
    const h = boot({ status: "<span class=\"dot pause\"></span> LOADING" });
    await settle();
    h.statusQueue.push({ networkError: true });
    h.tick();
    await settle();
    result.backoff = { timerCount: h.timerCount(), timeoutCount: h.timeoutCount() };
    const beforeRetry = h.fetches.length;
    h.statusQueue.push({ status: "<span class=\"dot run\"></span> RUNNING" });
    h.fireTimeouts();
    await settle();
    result.backoff.retried = h.fetches.length > beforeRetry;
    result.backoff.recovered = h.headRegion.innerHTML.indexOf("RUNNING") !== -1;
    result.backoff.timerCountAfterRetry = h.timerCount();
  }

  /* 6. NO REGION / NO URL: the script does nothing (a page without the status mark is untouched). */
  {
    const documentListeners = {};
    const document = {
      hidden: false,
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => makeElement(),
      addEventListener: (kind, fn) => { (documentListeners[kind] = documentListeners[kind] || []).push(fn); },
    };
    const timers = {};
    const window = {
      setInterval: (fn, ms) => { timers[fn] = ms; return 1; },
      clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
      addEventListener: () => {},
    };
    let fetched = 0;
    globalThis.fetch = () => { fetched++; return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
    new Function("document", "window", fs.readFileSync(STATUS_SCRIPT, "utf8"))(document, window);
    await settle();
    result.noRegion = { timers: Object.keys(timers).length, fetched: fetched };
  }

  process.stdout.write(JSON.stringify(result));
}

main();
