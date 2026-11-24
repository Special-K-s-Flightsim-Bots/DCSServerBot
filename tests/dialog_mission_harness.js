/* Drive the launch dialog's shipped `/static/dialog-mission-weather.js` against a minimal DOM and
 * report, FIELD level, what it does to the temporary block's controls.
 *
 * The controls, the block's data attributes and the response all come from the RENDERED dialog and the
 * REAL route response (pytest extracts them into the JSON file this harness reads), so the shipped bytes
 * meet the real rendered markup and the real values, not a re-spelling of either.
 *
 * An OPTIONAL fourth argument is the page's OTHER shipped control asset
 * (`/static/weather-slider.js`): it is loaded first, over the same controls, so a scenario can report
 * what the time box beside a field shows after the fill — through the page's own rule, not a copy.
 *
 * usage: node dialog_mission_harness.js <dialog-mission-weather.js> <data.json> [<weather-slider.js>]
 * prints one JSON object of the observed state after each interaction.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sliderSource = process.argv[4] ? fs.readFileSync(process.argv[4], "utf8") : "";

const CUSTOM = data.custom;

//: a form the controls belong to — its submit/requestSubmit count every attempt to submit the launch,
//: and its `querySelector` is what the shipped time box resolves its field through.
const form = {
  submits: 0,
  submit() { this.submits += 1; },
  requestSubmit() { this.submits += 1; },
  querySelector(selector) {
    const match = /^input\[type="text"\]\[name="([^"]+)"\]$/.exec(selector);
    return match ? (controlsByName[match[1]] || null) : null;
  },
};

let controls = {};
let controlsByName = {};

function buildControl(spec) {
  const listeners = {};
  const control = {
    tagName: spec.kind === "select" ? "SELECT" : "INPUT",
    value: spec.value || "",
    form: form,
    options: (spec.options || []).map((value) => ({
      value: value,
      selected: false,
      hasAttribute() { return false; },
    })),
    getAttribute(name) { return name === "name" ? spec.name : null; },
    addEventListener(kind, handler) { (listeners[kind] = listeners[kind] || []).push(handler); },
    dispatchEvent(event) {
      (listeners[event.type] || []).forEach((handler) => handler({type: event.type, target: control}));
      if (event.bubbles) { document_.fire(event.type, control); }
      return true;
    },
    querySelector() { return null; },
  };
  return control;
}

function buildControls() {
  controls = {};
  controlsByName = {};
  data.controls.forEach((spec) => {
    const control = buildControl(spec);
    controls[spec.name] = control;
    controlsByName[spec.name] = control;
  });
  fogTable = buildFogTable();
}

//: THE FOG TABLE — the markup `templates/confirm.html` draws: a header row (which the asset's own
//: selector skips) plus one row per keyframe, each cell an `input[type="number"]` named
//: `fog_row_<index>_<cell>`, and one remove button per row. This models the STRUCTURE the asset
//: grows and fills; the page's own add/remove script is not part of the asset under test.
let fogTable = null;

function buildFogTable() {
  if (!data.fog_columns) { return null; }
  const parent = {
    insertBefore(node, reference) {
      const index = reference === null ? -1 : parent.rows.indexOf(reference);
      parent.rows.splice(index < 0 ? parent.rows.length : index, 0, node);
      node.parentNode = parent;
    },
    removeChild(node) {
      const index = parent.rows.indexOf(node);
      if (index >= 0) { parent.rows.splice(index, 1); }
    },
    rows: [],
  };
  const table = {
    querySelectorAll(selector) {
      if (selector === ".dlg-frow:not(.hd)") { return parent.rows.slice(); }
      return [];
    },
  };
  (data.fog_rows || [{}]).forEach((cells) => {
    const row = buildFogRow(cells || {});
    row.parentNode = parent;
    parent.rows.push(row);
  });
  parent.rows.forEach((row) => {
    Object.defineProperty(row, "nextSibling", {
      get() {
        const index = parent.rows.indexOf(row);
        return index < 0 || index === parent.rows.length - 1 ? null : parent.rows[index + 1];
      },
    });
  });
  return table;
}

function buildFogRow(cells) {
  const remove = {
    getAttribute() { return "0"; },
    setAttribute() {},
  };
  const inputs = (data.fog_columns || []).map((key) => ({
    name: "fog_row_0_" + key,
    value: String(cells[key] === undefined ? "" : cells[key]),
    dispatchEvent() { return true; },
  }));
  return {
    parentNode: null,
    querySelectorAll(selector) {
      return selector === 'input[type="number"]' ? inputs : [];
    },
    querySelector(selector) {
      return selector === "[data-fog-remove]" ? remove : null;
    },
    cloneNode() {
      return buildFogRow({});
    },
  };
}

//: what the fog table SHOWS: every row's cells by the names the form would post.
function fogSnapshot() {
  if (!fogTable) { return null; }
  return fogTable.querySelectorAll(".dlg-frow:not(.hd)").map((row) => {
    const out = {};
    row.querySelectorAll('input[type="number"]').forEach((cell) => { out[cell.name] = String(cell.value); });
    return out;
  });
}

//: reset every control to the value it was RENDERED with, keeping the objects (and their listeners)
function resetControls() {
  data.controls.forEach((spec) => {
    const control = controlsByName[spec.name];
    if (control) { control.value = spec.value || ""; }
  });
  if (helper) { helper.value = data.helper_value || ""; }
  fogTable = buildFogTable();
}

//: THE BLOCK carries NONE of the read's attributes — it only owns the rows. The rendered template
//: puts every `data-mission-*` attribute on the `[data-mission-line]` element, and this harness models
//: that markup EXACTLY: a fake block that carried the attributes would "correct" a real page whose
//: asset reads them off the wrong element, which is precisely the bug this shape must catch.
const block = {
  getAttribute() { return null; },
  querySelector(selector) {
    if (selector === ".dlg-ftable") { return fogTable; }
    const match = /^\[name="([^"]+)"\]$/.exec(selector);
    return match ? (controlsByName[match[1]] || null) : null;
  },
};

//: THE LINE — the rendered `[data-mission-line]` element — carries the URL, the mission's name and
//: the three sentences. This is where the shipped asset reads them.
const line = {
  textContent: "",
  getAttribute(name) {
    if (name === "data-mission-url") { return data.url; }
    if (name === "data-mission-loading") { return data.loading; }
    if (name === "data-mission-read") { return data.read; }
    if (name === "data-mission-error") { return data.error; }
    if (name === "data-mission-name") { return data.mission || null; }
    return null;
  },
};

//: THE WEATHER MODE radios (`pages/actions.WEATHER_MODE_FIELD`) — the trigger is the "custom" row.
const modes = ["leave", "file", "custom"].map((value) => ({value: value, checked: value === "leave"}));
const modeTarget = {getAttribute(name) { return name === "name" ? "weather_mode" : null; }};

//: THE LAUNCH'S MISSION PICK (`pages/actions.MISSION_FIELD`, Startup only): the weather read follows
//: it, because another mission is another file with another sky. Modelled when the data says the
//: dialog draws one; the Load dialog draws none and names its mission on the line instead.
const missionPick = {
  value: data.mission_pick_value || "",
  matches(selector) { return selector === 'select[name="mission"]'; },
  getAttribute(name) { return name === "name" ? "mission" : null; },
};

//: THE LAUNCH'S "apply them to the original mission" BOX (``use_orig``). The real dialog renders it as
//: a CHECKBOX beside a hidden ``0`` fallback, so the asset MUST ask for the checkbox and read its
//: ``checked`` — reading ``[name="use_orig"]`` alone would find the hidden field and never follow the
//: box. ``has_use_orig`` false models the Load dialog, which offers no such box.
const originalBox = data.has_use_orig ? {
  checked: !!data.use_orig_checked,
  getAttribute(name) { return name === "name" ? "use_orig" : null; },
  dispatchEvent() { return true; },
  addEventListener() {},
} : null;

//: THE TIME BOX beside a field — the markup `templates/confirm.html` draws: an
//: `input[type="time"]` carrying `data-weather-time="<field name>"` and no name of its own.
const helper = data.helper ? {
  tagName: "INPUT",
  value: data.helper_value || "",
  form: form,
  listeners: {},
  getAttribute(name) { return name === "data-weather-time" ? data.helper : null; },
  addEventListener(kind, handler) { (this.listeners[kind] = this.listeners[kind] || []).push(handler); },
  dispatchEvent() { return true; },
} : null;

const document_ = {
  readyState: "complete",
  listeners: {},
  addEventListener(kind, handler) {
    (this.listeners[kind] = this.listeners[kind] || []).push(handler);
  },
  fire(kind, target) {
    (this.listeners[kind] || []).forEach((handler) => handler({ type: kind, target: target }));
  },
  querySelector(selector) {
    if (selector === 'input[name="weather_mode"]:checked') {
      return modes.filter((mode) => mode.checked)[0] || null;
    }
    if (selector === 'input[type="checkbox"][name="use_orig"]') { return originalBox; }
    if (selector === 'select[name="mission"]') { return data.mission_pick ? missionPick : null; }
    if (selector === ".dlg-temp") { return block; }
    if (selector === "[data-mission-line]") { return line; }
    const match = /^\[name="([^"]+)"\]$/.exec(selector);
    return match ? (controlsByName[match[1]] || null) : null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-weather-time]") { return helper ? [helper] : []; }
    return [];
  },
};

class Event_ {
  constructor(type, options) { this.type = type; this.bubbles = !!(options && options.bubbles); }
}

let fetched = [];
function fetch_(url, options) {
  fetched.push({ url: url, options: options });
  if (data.ok) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ values: data.values, name: data.name }),
      text: () => Promise.resolve(""),
    });
  }
  return Promise.resolve({
    ok: false,
    json: () => Promise.reject(new Error("a refusal is not JSON")),
    text: () => Promise.resolve(data.failure || ""),
  });
}

const globals = {
  document: document_, window: {}, Array: Array, Event: Event_, fetch: fetch_,
  encodeURIComponent: encodeURIComponent,
};

buildControls();
if (sliderSource) { vm.runInNewContext(sliderSource, globals); }
vm.runInNewContext(source, globals);

function snapshot() {
  const out = {};
  Object.keys(controls).forEach((name) => { out[name] = String(controls[name].value); });
  return out;
}

//: what the PAGE shows: the named controls' values, plus the time box beside a field when one is drawn
function shown() {
  return { controls: snapshot(), helper: helper ? String(helper.value) : null, fog: fogSnapshot() };
}

function chooseMode(value) {
  modes.forEach((mode) => { mode.checked = mode.value === value; });
  document_.fire("change", modeTarget);
}

function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

const realValues = data.values;
const realOk = data.ok;

(async function main() {
  const report = {};

  // 1. ON RENDER: nothing is fetched and the line is empty
  report.render = { line: line.textContent, fetches: fetched.length, controls: snapshot() };

  // 2. SELECTING "Create your own weather": the line says the read is running and the read fires
  chooseMode("custom");
  report.select = { line: line.textContent, fetches: fetched.length,
                    url: fetched.length ? fetched[fetched.length - 1].url : "" };
  await settle();
  report.answered = Object.assign({ line: line.textContent }, shown());

  // 2b. THE "apply them to the original mission" BOX: flipping it RE-READS, because the launch then
  //     rewrites the OTHER file and the values already filled belong to the file it no longer uses.
  if (originalBox) {
    const before = fetched.length;
    originalBox.checked = false;
    document_.fire("change", originalBox);
    await settle();
    report.original = { fetches: fetched.slice(before).map((entry) => entry.url),
                        line: line.textContent };
    originalBox.checked = data.use_orig_checked;
  }

  // 2c. THE MISSION PICK: choosing another mission RE-READS, because the launch will load THAT file
  //     and the values already filled belong to the one it no longer names.
  if (data.mission_pick) {
    const before = fetched.length;
    missionPick.value = data.mission_pick_change;
    document_.fire("change", missionPick);
    await settle();
    report.mission = { fetches: fetched.slice(before).map((entry) => entry.url),
                       line: line.textContent };
  }

  // 3. the picker a name the list does NOT carry is LEFT AS RENDERED
  resetControls();
  data.values = data.unknown_values;
  chooseMode("custom");
  await settle();
  report.unknown = Object.assign({ line: line.textContent }, shown());
  data.values = realValues;

  // 4. DESELECTING clears every filled value and the line
  resetControls();
  chooseMode("custom");
  await settle();
  const filled = snapshot();
  chooseMode("leave");
  report.deselect = Object.assign({ filled: filled, line: line.textContent }, shown());

  // 5. A FAILED READ fills nothing and carries the route's own sentence
  resetControls();
  data.ok = false;
  chooseMode("custom");
  const failureLoading = line.textContent;
  await settle();
  report.failure = Object.assign({ loading: failureLoading, line: line.textContent }, shown());
  data.ok = realOk;

  // 6. A CHANGE OF MIND MID-READ: the late answer is discarded, the block stays cleared
  resetControls();
  chooseMode("custom");
  chooseMode("leave");
  await settle();
  report.midfetch = Object.assign({ line: line.textContent }, shown());

  report.submits = form.submits;

  process.stdout.write(JSON.stringify(report, null, 2));
})();
