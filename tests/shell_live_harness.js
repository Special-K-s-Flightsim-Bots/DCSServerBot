/* THE LIVE STREAM'S SUSPEND/RESUME HARNESS — the shipped `/static/shell.js` driven against a minimal
 * DOM, a stub EventSource and a manual event queue.
 *
 * WHY A BROWSER-LESS HARNESS. The behaviour under test is about WHICH CONNECTIONS EXIST over time:
 * a browser keeps an open stream for a page it has parked in its back/forward cache, and the browser
 * allows six connections per origin — so a page that does not close its stream on `pagehide` can
 * starve every later request. "Is the source closed and reopened" cannot be read off the rendered
 * markup, so this loads the REAL shipped file (no copy, no re-implementation) into a DOM stub that
 * implements only what it uses:
 *   document.currentScript / getAttribute, document.getElementById, document.createElement,
 *   window.EventSource, window.addEventListener (pagehide/pageshow),
 *   window.setInterval/clearInterval/setTimeout, window.dcssbMsg.
 *
 * Usage:  node shell_live_harness.js <path to shell.js>
 *         -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SCRIPT = process.argv[2];

const STATUS_EL = { id: "live-status", textContent: "", parentNode: { insertBefore() {} } };

const windowListeners = {};
const timers = {};
let nextTimer = 1;
const created = [];
let closed = 0;

function FakeEventSource(url) {
  this.url = url;
  this.closed = false;
  this.listeners = {};
  created.push(this);
  this.addEventListener = (kind, fn) => { (this.listeners[kind] = this.listeners[kind] || []).push(fn); };
  this.close = () => { this.closed = true; closed += 1; };
}

const scriptEl = {
  getAttribute(name) {
    if (name === "data-stream") { return "/api/dashboard/stream?level=info"; }
    if (name === "data-fragments") { return "/api/dashboard/fragments?level=info"; }
    return null;
  },
};

const document = {
  currentScript: scriptEl,
  hidden: false,
  getElementById: (id) => (id === "live-status" ? STATUS_EL : null),
  createElement: () => ({ style: {}, classList: { add() {}, remove() {} },
                          setAttribute() {}, textContent: "", id: "" }),
  addEventListener: () => {},
  body: { appendChild() {} },
};

const window = {
  location: { pathname: "/", assign() {} },
  EventSource: FakeEventSource,
  setInterval: (fn, ms) => { const id = nextTimer++; timers[id] = { fn, ms }; return id; },
  clearInterval: (id) => { delete timers[id]; },
  setTimeout: (fn) => { fn(); return 0; },
  clearTimeout: () => {},
  addEventListener: (kind, fn) => { (windowListeners[kind] = windowListeners[kind] || []).push(fn); },
};

/* The strings come from the SERVED map (`/i18n/messages.js`, handed in as DCSSB_MESSAGES_JS by the
   test runner), exactly as `base.html` includes it before this script — never a copy in the test. A
   runner that does not hand one in gets a key-echoing stub, which is enough to see that the status
   was set at all. */
const messages = process.env.DCSSB_MESSAGES_JS;
if (messages) {
  new Function("document", "window", fs.readFileSync(messages, "utf8"))(document, window);
}
if (typeof window.dcssbMsg !== "function") {
  window.dcssbMsg = (key, params) => key + ":" + (params && params.status ? params.status : "");
}

globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });

/* The shipped script reads its dependencies from BOTH the window object (`window.EventSource`,
   `window.dcssbMsg`) and the bare globals the browser provides (`new EventSource(...)`,
   `setInterval`, `fetch`). In a browser the two ARE the same object; here they have to be installed
   under both names or the script dies on a ReferenceError. */
globalThis.EventSource = FakeEventSource;
globalThis.window = window;
globalThis.document = document;
globalThis.setInterval = window.setInterval;
globalThis.clearInterval = window.clearInterval;
globalThis.setTimeout = window.setTimeout;
globalThis.clearTimeout = window.clearTimeout;

const source = fs.readFileSync(SCRIPT, "utf8");
if (source.indexOf("EventSource") === -1) {
  process.stdout.write(JSON.stringify({ error: "not the live script: no EventSource in it" }));
  process.exit(0);
}
new Function("document", "window", source)(document, window);

function fire(kind, persisted) {
  (windowListeners[kind] || []).forEach((fn) => fn({ persisted: persisted === true }));
}

function open() { return created.filter((es) => !es.closed).length; }

const report = {
  streamAttributeHonoured: created.length === 1 && created[0].url.indexOf("/api/dashboard/stream") === 0,
  openedOnLoad: created.length,
  openAfterLoad: open(),
  statusAfterLoad: STATUS_EL.textContent,
};

fire("pagehide", true);
report.closedOnPagehide = created[0].closed;
report.openAfterPagehide = open();
report.pollLeftBehindByPagehide = Object.keys(timers).length;

fire("pageshow", true);
report.createdAfterPageshow = created.length;
report.openAfterPageshow = open();
report.reopenedUrl = created.length > 1 ? created[1].url : null;
report.statusAfterPageshow = STATUS_EL.textContent;

// a pageshow with nothing suspended must NOT open a second stream
fire("pageshow", true);
report.createdAfterIdlePageshow = created.length;

// and the cycle repeats
fire("pagehide", true);
report.openAfterSecondPagehide = open();
fire("pageshow", true);
report.openAfterSecondPageshow = open();

/* A `pagehide` THAT IS NOT A PARK. `event.persisted` is the browser's own statement that this
   document STAYS ALIVE (a back/forward-cache park). With it false the page is being DESTROYED: the
   connection goes with the document, so closing it frees nothing — and on a navigation that does not
   complete (a cancelled or refused navigation, a same-document restore) it leaves a SURVIVING page
   with no stream and no `pageshow` to reopen it. */
function current() { return created[created.length - 1]; }
fire("pagehide", false);
report.closedOnNonParkPagehide = current().closed;
report.openAfterNonParkPagehide = open();

/* A RESTORE WITH THE POLLING FALLBACK ARMED. A stream that is aborted by a navigation fires `error`,
   and the shipped `onerror` arms the polling fallback. If that happens while the page is parked, the
   restore must still bring the STREAM back: the fallback is a different transport and it can stop for
   good (an expired session answers 401/403 and `stopPolling` never restarts it). */
fire("pagehide", true);
report.openAfterParkBeforeAbort = open();
current().onerror();                                   // the abort a navigation fires on the stream
report.pollArmedByAbort = Object.keys(timers).length;
fire("pageshow", true);
report.createdAfterRestoreWithPoll = created.length;
report.openAfterRestoreWithPoll = open();
report.pollAfterRestore = Object.keys(timers).length;
report.reopenedAfterRestoreWithPoll = created.length > 2 ? current().url : null;

process.stdout.write(JSON.stringify(report));
