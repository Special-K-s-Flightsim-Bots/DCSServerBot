/* THE SUBMIT INTERCEPTOR'S HARNESS — the shipped `/static/submit.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The behaviour the operator reported ("the delete button does not
 * refresh the mission list") is a RACE, and only the protocol the script speaks settles it: does the
 * navigation happen BEFORE the write's answer, or AFTER it? An assertion on the RENDERED MARKUP cannot
 * say. So this loads the REAL shipped file — no copy, no re-implementation — into a minimal DOM stub
 * and drives a submit. The stub implements only what submit.js uses: `document.addEventListener`,
 * `window.location` (pathname / search / assign) and a `fetch` the harness resolves by hand, plus the
 * two constructors the script calls (`FormData`) and the pressed control's `classList`/attributes.
 * Every navigation and every request is recorded, in order, so the script's own sequencing is the
 * thing under test.
 *
 * Usage:  node submit_harness.js <path to submit.js>     -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");

/* A FormData stand-in: submit.js builds one off the form and appends a named submitter. */
function FormDataStub(form) { this.form = form; this.entries = []; }
FormDataStub.prototype.has = function (name) {
  return this.entries.some((entry) => entry[0] === name);
};
FormDataStub.prototype.append = function (name, value) { this.entries.push([name, value]); };

function makeButton(name, value) {
  const attrs = {};
  const classes = {};
  return {
    name: name || "",
    value: value || "",
    classList: {
      add(c) { classes[c] = true; },
      remove(c) { delete classes[c]; },
      contains(c) { return Boolean(classes[c]); },
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    removeAttribute(k) { delete attrs[k]; },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    _classes: classes,
    _attrs: attrs,
  };
}

/* A form exactly as the browser hands it to the script. `data-back` carries a value; `data-await` is a
   BARE attribute, so `getAttribute` returns the EMPTY STRING for it — a falsy value that a truthiness
   test reads as "absent". The harness models that, so a script that branches with `!getAttribute(...)`
   fails here instead of passing while production navigates at once. */
function makeForm(options) {
  const opts = options || {};
  const attrs = {};
  if (opts.dataBack !== undefined) { attrs["data-back"] = opts.dataBack; }
  if (opts.dataAwait !== undefined) { attrs["data-await"] = ""; }
  return {
    action: opts.action || "/actions/server/missions/delete",
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(attrs, name); },
    matches(selector) { return selector === "form[data-async]"; },
  };
}

/* Boot the shipped script once, with a fresh DOM and a fresh fetch the harness controls. */
function boot() {
  const submitHandlers = [];
  const order = [];
  const navigations = [];
  const fetches = [];
  const document = {
    readyState: "complete",
    addEventListener(kind, fn) { if (kind === "submit") { submitHandlers.push(fn); } },
    querySelectorAll() { return []; },
  };
  const window = {
    location: {
      pathname: "/fleet/SRS-1",
      search: "?tab=missions",
      assign(to) { order.push("navigate"); navigations.push(to); },
    },
  };
  function fetchStub(url, opts) {
    order.push("write");
    let settle;
    let fail;
    const promise = new Promise((resolve, reject) => { settle = resolve; fail = reject; });
    fetches.push({ url, opts, resolve: settle, reject: fail });
    return promise;
  }
  /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  const run = new Function("document", "window", "fetch", "FormData", SOURCE);
  run(document, window, fetchStub, FormDataStub);
  return {
    order, navigations, fetches,
    submit(form, submitter) {
      const event = {
        target: form, submitter: submitter || null, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
      };
      submitHandlers.forEach((fn) => fn(event));
      return event;
    },
  };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function main() {
  const result = {};

  /* 1. THE ORDER: the navigation waits for the write's answer. */
  {
    const dom = boot();
    const button = makeButton("", "");
    const event = dom.submit(makeForm({}), button);
    const before = {
      prevented: event.defaultPrevented,
      requests: dom.fetches.length,
      navigations: dom.navigations.slice(),
      busyClass: button.classList.contains("busy"),
      busyAria: button.getAttribute("aria-busy") === "true",
      order: dom.order.slice(),
    };
    await tick();
    const stillWaiting = dom.navigations.slice();
    dom.fetches[0].resolve({ status: 0, type: "opaqueredirect" });
    await tick();
    const after = {
      navigations: dom.navigations.slice(),
      busyClass: button.classList.contains("busy"),
      busyAria: button.getAttribute("aria-busy"),
      order: dom.order.slice(),
    };
    result.waits = { before, stillWaiting, after };
  }

  /* 2. A SLOW WRITE: the navigation is still after the answer, however long it takes. */
  {
    const dom = boot();
    dom.submit(makeForm({}), makeButton("", ""));
    const beforeResolve = dom.navigations.slice();
    await new Promise((resolve) => setTimeout(resolve, 25));   // the write is still running
    const stillBefore = dom.navigations.slice();
    dom.fetches[0].resolve({ status: 0 });
    await tick();
    result.slow = { beforeResolve, stillBefore, after: dom.navigations.slice() };
  }

  /* 3. A REFUSAL answers too: it navigates like an accepted write. */
  {
    const dom = boot();
    dom.submit(makeForm({}), makeButton("", ""));
    dom.fetches[0].resolve({ status: 403 });
    await tick();
    result.refusal = { navigations: dom.navigations.slice() };
  }

  /* 4. A NETWORK FAILURE is an honest no-op — and it still navigates (nothing retried). */
  {
    const dom = boot();
    dom.submit(makeForm({}), makeButton("", ""));
    dom.fetches[0].reject(new Error("network down"));
    await tick();
    result.network = { navigations: dom.navigations.slice() };
  }

  /* 5. THE DIALOG'S data-back is where it returns. */
  {
    const dom = boot();
    dom.submit(makeForm({ dataBack: "/logs" }), makeButton("", ""));
    dom.fetches[0].resolve({ status: 0 });
    await tick();
    result.dataBack = { navigations: dom.navigations.slice() };
  }

  /* 6. ONE PRESS, ONE REQUEST: a second press while the write is pending is ignored. */
  {
    const dom = boot();
    const form = makeForm({});
    dom.submit(form, makeButton("", ""));
    dom.submit(form, makeButton("", ""));
    const requests = dom.fetches.length;
    dom.fetches[0].resolve({ status: 0 });
    await tick();
    result.doublePress = { requests, navigations: dom.navigations.slice() };
  }

  /* 7. A DIALOG CLOSES ON THE PRESS. A form that STATES where it goes back to (``data-back``) is the
     dialog being dismissed: it leaves AT ONCE — the navigation is on the recording BEFORE the write's
     answer is ever resolved — and the write still runs (one request on the wire). No busy idiom is
     worn, because there is no visible wait to show. */
  {
    const dom = boot();
    const button = makeButton("", "");
    dom.submit(makeForm({ dataBack: "/logs" }), button);
    const beforeResolve = {
      requests: dom.fetches.length,
      navigations: dom.navigations.slice(),
      busyClass: button.classList.contains("busy"),
      order: dom.order.slice(),
    };
    await tick();
    const stillBefore = dom.navigations.slice();
    dom.fetches[0].resolve({ status: 0 });
    await tick();
    result.dialog = { beforeResolve, stillBefore, after: dom.navigations.slice() };
  }

  /* 8. A WAITING DIALOG (``data-await``) KEEPS its stated destination but hands the page over only
     ONCE the write has answered — the Bans lift/add/edit, whose returned list IS the thing the write
     changed. It wears the busy idiom (there is a real wait to show) and the navigation lands on the
     `data-back` it stated. */
  {
    const dom = boot();
    const button = makeButton("", "");
    dom.submit(makeForm({ dataBack: "/bans?dgsa=1", dataAwait: true }), button);
    const beforeResolve = {
      requests: dom.fetches.length,
      navigations: dom.navigations.slice(),
      busyClass: button.classList.contains("busy"),
      order: dom.order.slice(),
    };
    await tick();
    const stillBefore = dom.navigations.slice();
    dom.fetches[0].resolve({ status: 0 });
    await tick();
    result.awaitDialog = { beforeResolve, stillBefore, after: dom.navigations.slice() };
  }

  /* 9. THE SERVER'S OWN DESTINATION. A write can MOVE the page it was pressed on (a rename changes
     the server's address), and a 303's Location is invisible to `fetch(redirect:"manual")`. The route
     therefore answers a JSON request with where the write landed, and the script must go THERE rather
     than back to the URL the form was on. */
  {
    const dom = boot();
    dom.submit(makeForm({}), makeButton("", ""));
    dom.fetches[0].resolve({
      ok: true, status: 200,
      headers: { get() { return "application/json"; } },
      json() { return Promise.resolve({ redirect: "/fleet/RENAMED?tab=configuration" }); },
    });
    await tick(); await tick();
    result.serverRedirect = { navigations: dom.navigations.slice() };
  }

  process.stdout.write(JSON.stringify(result));
}

main();
