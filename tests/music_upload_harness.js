/* THE `data-upload` HARNESS — the shipped `/static/music-upload.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS. What the script does is what a DROP does: which folder the file
 * goes into, what the request body carries, whether several files are one request each, and where the
 * page lands afterwards. None of that is markup, so none of it can be asserted on a rendered page.
 * This loads the REAL shipped file (no copy, no re-implementation) into a DOM stub and fires the
 * events a browser would.
 *
 * THE STUB implements only what the file uses: `document.querySelectorAll("[data-upload]")`, an
 * element's `getAttribute`/`setAttribute`/`removeAttribute`/`querySelector`/`addEventListener`/
 * `classList`/`closest`, `FormData.append`, `fetch` (answered by the harness), and
 * `window.location.assign`. Nothing is provided that the script does not touch, so a script that
 * grew a dependency on a real browser feature fails here instead of in production.
 *
 * THE FETCH IS THE HARNESS'S TO ANSWER: every request is recorded with the body it carried and
 * answered from the file's own name, which is the only judgement the route makes that matters here.
 *
 * Usage: node music_upload_harness.js <path to music-upload.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");

/* ------------------------------------------------------------------ the minimal DOM */

function element(tag, attributes) {
  const node = {
    tagName: tag,
    attributes: Object.assign({}, attributes || {}),
    children: [],
    parentNode: null,
    classes: {},
    listeners: {},
    note: null,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name] : null;
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); },
    querySelector(selector) { return selector === ".mus-upload-note" ? this.note : null; },
    closest(selector) {
      let current = this;
      while (current) {
        if (current.matches && current.matches(selector)) { return current; }
        current = current.parentNode;
      }
      return null;
    },
    matches(selector) {
      if (selector === "[data-folder]") { return this.getAttribute("data-folder") !== null; }
      return false;
    },
    classList: {
      _owner: null,
      add(name) { this._owner.classes[name] = true; },
      remove(name) { delete this._owner.classes[name]; }
    }
  };
  node.classList._owner = node;
  return node;
}

const CARD_ATTRIBUTES = {
  "data-upload": "",
  "data-upload-url": "/music/Jungle%20Beats/upload",
  "data-upload-folder": "jungle",
  "data-upload-node": "dcs-a-01",
  "data-upload-token": "tok",
  "data-upload-token-field": "_csrf_token",
  "data-upload-folder-field": "folder",
  "data-upload-node-field": "node",
  "data-upload-file-field": "file",
  "data-upload-page": "/music/Jungle Beats",
  "data-upload-refused": "Not uploaded: the file could not be sent."
};

function scene() {
  const card = element("section", CARD_ATTRIBUTES);
  const note = element("p", {});
  note.textContent = "";
  card.note = note;

  const row = element("a", { "data-folder": "jungle/sub" });
  row.parentNode = card;
  const label = element("span", {});
  label.parentNode = row;
  const cell = element("td", {});
  cell.parentNode = card;

  return { card, note, row, label, cell };
}

/* ------------------------------------------------------------------ one scenario */

function run(scenario) {
  const page = scene();
  const state = {
    posts: [],
    landing: "",
    prevented: 0,
    answers: scenario.answers || {}
  };

  class FormData {
    constructor() { this.entries = {}; }
    append(name, value, filename) {
      const entry = filename === undefined ? value : String(filename);
      (this.entries[name] = this.entries[name] || []).push(entry);
    }
  }

  const sandbox = {
    document: {
      querySelectorAll(selector) {
        return selector === "[data-upload]" ? [page.card] : [];
      }
    },
    window: {
      location: { assign(url) { state.landing = url; } }
    },
    FormData,
    fetch(url, options) {
      const body = options.body.entries;
      state.posts.push({ url, body });
      const name = String((body.file || [""])[0]);
      const answer = name.endsWith(".txt")
        ? { ok: false, message: `${name} was not uploaded: a track is a .mp3 or a .ogg file.` }
        : { ok: true, message: `${name} uploaded.` };
      return Promise.resolve({ json: () => Promise.resolve(answer) });
    },
    encodeURIComponent,
    Promise,
    setTimeout,
    console
  };

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);

  function fire(type, target, files, types) {
    const event = {
      type,
      target,
      dataTransfer: files === null ? null : { files, types: types || ["Files"] },
      preventDefault() { state.prevented += 1; }
    };
    const handlers = page.card.listeners[type] || [];
    handlers.forEach(function (handler) { handler(event); });
  }

  return { page, state, fire };
}

async function drain() {
  for (let i = 0; i < 8; i += 1) {
    await new Promise(function (resolve) { setTimeout(resolve, 0); });
  }
}

function file(name) {
  return { name };
}

function report(state, page) {
  return {
    posts: state.posts.map(function (post) { return post.body; }),
    landing: state.landing,
    note: page.note.textContent,
    prevented: state.prevented
  };
}

/* ------------------------------------------------------------------ the scenarios */

async function main() {
  const out = {};

  // 1. a drop on the CARD: the folder on screen
  {
    const run1 = run({});
    run1.fire("dragenter", run1.page.cell, [file("Make It Wit Chu.mp3")]);
    run1.fire("dragover", run1.page.cell, [file("Make It Wit Chu.mp3")]);
    run1.fire("drop", run1.page.cell, [file("Make It Wit Chu.mp3")]);
    await drain();
    out.card = report(run1.state, run1.page);
  }

  // 2. a drop on a RAIL ROW: that row's folder
  {
    const run2 = run({});
    run2.fire("drop", run2.page.label, [file("Nightcall.mp3")]);
    await drain();
    out.row = report(run2.state, run2.page);
  }

  // 3. two files: one request each, in the order they were dropped
  {
    const run3 = run({});
    run3.fire("drop", run3.page.cell, [file("one.mp3"), file("two.ogg")]);
    await drain();
    out.many = report(run3.state, run3.page);
  }

  // 4. a refusal is written into the card and the page does NOT leave
  {
    const run4 = run({});
    run4.fire("drop", run4.page.cell, [file("notes.txt")]);
    await drain();
    out.refused = report(run4.state, run4.page);
  }

  // 5. a drag that carries no file (a selected word, a link) is ignored
  {
    const run5 = run({});
    run5.fire("dragenter", run5.page.cell, [], ["text/plain"]);
    run5.fire("dragover", run5.page.cell, [], ["text/plain"]);
    run5.fire("drop", run5.page.cell, [], ["text/plain"]);
    await drain();
    out.text = report(run5.state, run5.page);
  }

  process.stdout.write(JSON.stringify(out));
}

main().catch(function (error) {
  process.stderr.write(String(error && error.stack ? error.stack : error));
  process.exit(1);
});
