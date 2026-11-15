/* THE SERVER-LOG FOLLOWER'S HARNESS — the shipped `/static/server-log.js` driven against a minimal
 * DOM, a hand-resolved `fetch` and a manual clock.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The two rulings on this feature are about WHEN the script
 * talks to the server: it must follow while the tab is VISIBLE and stop the moment it is hidden, and
 * a running server's new lines must appear without a reload. An assertion on the RENDERED MARKUP cannot
 * say when a request was made or whether the timer was cleared — only the protocol settles it. So this
 * loads the REAL shipped file — no copy, no re-implementation — into a minimal DOM stub and drives it:
 * it implements only what server-log.js uses (getElementById, createElement, appendChild,
 * insertAdjacentHTML, firstElementChild, getAttribute, textContent, disabled, addEventListener, the
 * scroll metrics), a `fetch` resolved by hand from a queue, and a clock whose intervals can be fired
 * on demand.
 *
 * Usage:  node server_log_harness.js <path to server-log.js>     -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const { installMessages } = require("./dcssb_messages_harness.js");

const SCRIPT_PATH = process.argv[2];

function makeElement(id) {
  return {
    id: id || "",
    _html: "",
    scrollTop: 0,
    scrollHeight: 100,
    clientHeight: 50,
    className: "",
    textContent: "",
    disabled: false,
    hidden: false,
    listeners: {},
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get firstElementChild() { return this._html ? { offsetTop: this._html.length } : null; },
    appendChild(node) {
      this._html += (node && typeof node.textContent === "string") ? node.textContent : "";
    },
    insertAdjacentHTML(position, html) {
      if (position === "afterbegin") { this._html = String(html) + this._html; }
      else { this._html += String(html); }
    },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    click() {
      if (this.disabled) { return; }
      (this.listeners.click || []).forEach((fn) => fn({}));
    },
  };
}

/* flush the microtask queue the script's promise chains run on */
function settle() { return new Promise((resolve) => setImmediate(resolve)); }

function boot(overrides) {
  const card = {
    getAttribute(name) {
      const attrs = {
        "data-window-url": "/fleet/SRS-1/log/window",
        "data-offset": "10",
        "data-before": "4",
        "data-follow-seconds": "3",
      };
      if (overrides && name in overrides) { return overrides[name]; }
      return name in attrs ? attrs[name] : null;
    },
  };
  const body = makeElement("dcs-log-body");
  const noteEl = makeElement("dcs-log-note");
  const noteText = makeElement("dcs-log-note-text");
  const statusEl = makeElement("dcs-log-status");
  const olderBtn = makeElement("dcs-log-older");
  const ids = { "dcs-log-card": card, "dcs-log-body": body, "dcs-log-note": noteEl,
                "dcs-log-note-text": noteText, "dcs-log-status": statusEl,
                "dcs-log-older": olderBtn };

  const documentListeners = {};
  const windowListeners = {};
  const document = {
    hidden: false,
    getElementById: (id) => ids[id] || null,
    createElement: () => makeElement(),
    addEventListener: (kind, fn) => { (documentListeners[kind] = documentListeners[kind] || []).push(fn); },
  };
  const timers = {};
  let nextTimer = 1;
  const timeouts = {};
  let nextTimeout = 1;
  const window = {
    setInterval: (fn, ms) => { const id = nextTimer++; timers[id] = { fn: fn, ms: ms }; return id; },
    clearInterval: (id) => { delete timers[id]; },
    setTimeout: (fn, ms) => { const id = nextTimeout++; timeouts[id] = { fn: fn, ms: ms }; return id; },
    clearTimeout: (id) => { delete timeouts[id]; },
    addEventListener: (kind, fn) => { (windowListeners[kind] = windowListeners[kind] || []).push(fn); },
  };

  const fetches = [];
  const queue = [];
  globalThis.fetch = (url) => {
    fetches.push(url);
    const payload = queue.length ? queue.shift() : { available: true, lines: "", offset: 10 };
    if (payload && payload.networkError) { return Promise.reject(new Error("network down")); }
    if (payload && payload.transportError) { return Promise.resolve({ ok: false, status: 500 }); }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  };

  const source = fs.readFileSync(SCRIPT_PATH, "utf8");
  installMessages(window);
  new Function("document", "window", source)(document, window);

  return {
    body: body,
    noteEl: noteEl,
    noteText: noteText,
    statusEl: statusEl,
    olderBtn: olderBtn,
    document: document,
    timers: timers,
    timeouts: timeouts,
    fetches: fetches,
    queue: queue,
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

  /* 1. boot VISIBLE: one immediate follow, a timer armed, the appended lines land, the offset advances */
  {
    const h = boot();
    await settle();
    result.bootFetches = h.fetches.slice();
    const beforeHtml = h.body._html;
    h.queue.push({ available: true, lines: "<div>NEW-1</div>", offset: 42 });
    h.tick();
    await settle();
    result.visible = {
      timerCount: h.timerCount(),
      appended: h.body._html.indexOf("NEW-1") !== -1,
      grewFromBoot: h.body._html.length > beforeHtml.length,
      status: h.statusEl.textContent,
    };
  }

  /* 2. the tab is HIDDEN: the timer is cleared and NO further request is made, however many ticks pass */
  {
    const h = boot();
    await settle();
    const before = h.fetches.length;
    h.fireVisibility("hidden");
    const timersAfterHide = h.timerCount();
    h.tick();                      // would fire a follow if the timer were still armed
    await settle();
    result.hidden = {
      timerCount: timersAfterHide,
      requestsWhileHidden: h.fetches.length - before,
      status: h.statusEl.textContent,
    };

    /* 3. VISIBLE again: it resumes on its own */
    h.fireVisibility("visible");
    await settle();
    result.resumed = {
      timerCount: h.timerCount(),
      requestOnResume: h.fetches.length > before,
      status: h.statusEl.textContent,
    };
  }

  /* 4. a NEW log (reset): the view is cleared and the notice + the fresh lines are shown */
  {
    const h = boot();
    await settle();
    h.queue.push({ available: true, reset: true, lines: "<div>AFTER-ROTATION</div>", offset: 7,
                  before: 0 });
    h.tick();
    await settle();
    result.reset = {
      showsNotice: h.body._html.indexOf("rotated") !== -1,
      showsNewLine: h.body._html.indexOf("AFTER-ROTATION") !== -1,
    };
  }

  /* 5. LOAD OLDER: it prepends to the window and disables itself at the start of the file */
  {
    const h = boot();
    await settle();
    h.body._html = "<div>CURRENT</div>";
    h.queue.push({ available: true, lines: "<div>OLDER</div>", before: 0 });
    h.olderBtn.click();
    await settle();
    result.older = {
      prepended: h.body._html.indexOf("OLDER") !== -1 && h.body._html.indexOf("CURRENT") !== -1,
      orderKept: h.body._html.indexOf("OLDER") < h.body._html.indexOf("CURRENT"),
      buttonDisabled: h.olderBtn.disabled === true,
      buttonLabel: h.olderBtn.textContent,
      requestedBehind: h.fetches.some((u) => u.indexOf("?behind=4") !== -1),
    };
  }

  /* 6. THE IDENTITY IS ECHOED: the card's data-identity rides on every follow, so the server can tell
        a rotation (changed identity) from an append. */
  {
    const h = boot({ "data-identity": "4242" });
    await settle();
    result.identity = {
      bootFetches: h.fetches.slice(),
      echoed: h.fetches.length > 0 && h.fetches[0].indexOf("&identity=4242") !== -1,
    };
  }

  /* 7. A FAILED READ BACKS OFF: the interval is dropped and ONE retry is armed on a growing delay, the
        status says "retrying…", and firing the retry makes a fresh request and re-arms the interval. */
  {
    const h = boot();
    await settle();
    h.queue.push({ networkError: true });
    h.tick();
    await settle();
    result.backoff = {
      timerCount: h.timerCount(),                 // the normal interval is cleared
      timeoutCount: h.timeoutCount(),             // one pending retry
      status: h.statusEl.textContent,             // says so
    };
    const beforeRetry = h.fetches.length;
    h.fireTimeouts();
    await settle();
    result.backoff.retried = h.fetches.length > beforeRetry;
    result.backoff.timerCountAfterRetry = h.timerCount();
    result.backoff.statusAfterRetry = h.statusEl.textContent;
  }

  /* 8. A NODE THAT IS DOWN (available: false) backs off TOO — the same discipline, and the node's own
        sentence is kept in the status alongside "retrying…". */
  {
    const h = boot();
    await settle();
    h.queue.push({ available: false, message: "Node 'X' is offline or unknown." });
    h.tick();
    await settle();
    result.downNode = {
      status: h.statusEl.textContent,
      timerCount: h.timerCount(),
      timeoutCount: h.timeoutCount(),
    };
  }

  /* 9. A HIDDEN TAB STOPS CLEANLY even mid-backoff: the pending retry is cleared, so nothing polls. */
  {
    const h = boot();
    await settle();
    h.queue.push({ networkError: true });
    h.tick();
    await settle();
    h.fireVisibility("hidden");
    await settle();
    result.backoffHidden = {
      timerCount: h.timerCount(),
      timeoutCount: h.timeoutCount(),
      status: h.statusEl.textContent,
    };
  }

  /* 10. THE LEVEL AND THE FILE SELECTOR ride on every request, so the server filters the window the way
         the page rendered it and (on the Events tab) reads events.log. */
  {
    const h = boot({ "data-level": "warning", "data-which": "events" });
    await settle();
    result.levelWhich = {
      bootFetches: h.fetches.slice(),
      hasLevel: h.fetches.length > 0 && h.fetches[0].indexOf("&level=warning") !== -1,
      hasWhich: h.fetches.length > 0 && h.fetches[0].indexOf("&which=events") !== -1,
    };
    h.queue.push({ available: true, lines: "<div>OLDER</div>", before: 0, at_start: true });
    h.olderBtn.click();
    await settle();
    result.levelWhich.olderFetch = h.fetches[h.fetches.length - 1];
  }

  /* 11. THE FILTER NEVER LIES: an "at_start" answer disables the button as "Start of log". */
  {
    const h = boot();
    await settle();
    h.queue.push({ available: true, lines: "", before: 0, at_start: true,
                   message: "No log lines match the selected level." });
    h.olderBtn.click();
    await settle();
    result.atStart = {
      buttonDisabled: h.olderBtn.disabled === true,
      buttonLabel: h.olderBtn.textContent,
      status: h.statusEl.textContent,
    };
  }

  /* 12. A ROTATION DURING THE WALK (page back): the view RESETS and says the log restarted, the page
         the walk gathered (the previous file's alone) is shown, and the button is spent — two files
         are never spliced. */
  {
    const h = boot();
    await settle();
    h.body._html = "<div>CURRENT</div>";
    h.queue.push({ available: true, reset: true, lines: "<div>OLD-PAGE</div>", before: 0,
                   identity: "9007199254740995",
                   message: "The log restarted while loading older lines." });
    h.olderBtn.click();
    await settle();
    result.walkRotation = {
      showsNotice: h.body._html.indexOf("rotated") !== -1,
      showsOldPage: h.body._html.indexOf("OLD-PAGE") !== -1,
      clearedCurrent: h.body._html.indexOf("CURRENT") === -1,
      buttonDisabled: h.olderBtn.disabled === true,
      status: h.statusEl.textContent,
    };
  }

  /* 13. "NO LOG FILE YET" IS A NORMAL STATE, NOT A FAILURE (L3): a ``pending`` answer keeps the
         NORMAL interval running (no backoff timeout), shows the sentence in the note, and leaves the
         body empty. The follower must NOT drop its cadence for a file that simply is not there yet. */
  {
    const h = boot();
    await settle();
    h.queue.push({ available: false, pending: true,
                   message: "No log file yet for this server (looked for 'dcs.log')." });
    h.tick();
    await settle();
    result.pending = {
      timerCount: h.timerCount(),            // the normal interval is KEPT (not dropped)
      timeoutCount: h.timeoutCount(),        // and NO backoff retry is armed
      bodyEmpty: h.body._html === "",
      noteShown: h.noteEl.hidden === false && h.noteText.textContent.indexOf("No log file yet") !== -1,
      status: h.statusEl.textContent,
    };
  }

  /* 14. THE EMPTY STATE BECOMES THE LOG. Boot with the body empty and the note
         showing "no log file yet"; the first follow answers ``pending`` (still no file), the second
         brings REAL lines — the note must give way to the log, with no reload. */
  {
    const h = boot();
    h.noteEl.hidden = false;
    h.noteText.textContent = "No log file yet for this server.";
    await settle();
    h.queue.push({ available: false, pending: true,
                   message: "No log file yet for this server." });
    h.tick();
    await settle();
    const afterPending = {
      bodyEmpty: h.body._html === "",
      noteShown: h.noteEl.hidden === false,
      timerCount: h.timerCount(),
    };
    h.queue.push({ available: true, lines: "<div>FIRST-BOOT-LINE</div>", offset: 33 });
    h.tick();
    await settle();
    result.emptyBecomesLog = {
      afterPending: afterPending,
      rendered: h.body._html.indexOf("FIRST-BOOT-LINE") !== -1,
      noteHidden: h.noteEl.hidden === true,
      status: h.statusEl.textContent,
    };
  }

  process.stdout.write(JSON.stringify(result));
}

main();
