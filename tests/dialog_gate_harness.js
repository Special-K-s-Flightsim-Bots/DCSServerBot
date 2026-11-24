/* Drive the launch dialog's shipped `/static/dialog-preset.js` against a minimal DOM and report which
 * temporary rows it SHOWS, per mode state — at FIELD level (one entry per row), plus the cloud-base
 * prefill.
 *
 * The rows come from the RENDERED dialog (pytest extracts each `.dlg-wrow`'s gate attributes into a
 * JSON file), so this exercises the shipped bytes against the real gate data, not a re-spelling.
 *
 * usage: node dialog_gate_harness.js <dialog-preset.js> <rows.json>
 * prints one JSON object per state, then the base prefill.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const rowsData = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

function makeRow(entry) {
  const attrs = {};
  if (entry.cloud) { attrs["data-gate-cloud"] = entry.cloud; }
  if (entry.haze) { attrs["data-gate-haze"] = entry.haze; }
  if (entry.fogMode) { attrs["data-gate-fog-mode"] = entry.fogMode; }
  if (entry.haloReal) { attrs["data-halo-real"] = ""; }
  return {
    key: entry.key,
    style: {},
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(attrs, name); },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
  };
}

const rows = rowsData.map(makeRow);

const controls = {};
function control(name) {
  if (!controls[name]) { controls[name] = { value: "", style: {} }; }
  return controls[name];
}
["clouds", "enable_dust", "fog.mode", "halo.preset", "clouds.base"].forEach(control);

//: a picker whose selected option carries the prefill base, and whose SECOND option (the empty one)
//: carries none — choosing it back must CLEAR the base.
const pick = {
  value: "",
  selectedIndex: 0,
  options: [
    { getAttribute(name) { return name === "data-base" ? "2520" : null; } },
    { getAttribute() { return null; } },
  ],
};

const listeners = {};
const document = {
  addEventListener(kind, handler) { (listeners[kind] = listeners[kind] || []).push(handler); },
  querySelector(selector) {
    const match = /^\[name="([^"]+)"\]$/.exec(selector);
    if (match) { return control(match[1]); }
    if (selector === 'select[name="clouds.preset"]') { return pick; }
    return null;
  },
  querySelectorAll(selector) { return selector === ".dlg-wrow" ? rows : []; },
};
const window = {};

vm.runInNewContext(source, { document, window });

function fire(kind, target) {
  (listeners[kind] || []).forEach((handler) => handler({ target: target }));
}

function setState(state) {
  control("clouds").value = state.clouds || "";
  control("enable_dust").value = state.haze || "";
  control("fog.mode").value = state.fogMode || "";
  control("halo.preset").value = state.halo || "";
}

const states = [
  { step: "none", clouds: "", haze: "", fogMode: "", halo: "" },
  { step: "clouds-preset", clouds: "preset", haze: "", fogMode: "", halo: "" },
  { step: "clouds-dynamic", clouds: "dynamic", haze: "", fogMode: "", halo: "" },
  { step: "clouds-clear", clouds: "clear", haze: "", fogMode: "", halo: "" },
  { step: "dust", clouds: "", haze: "dust", fogMode: "", halo: "" },
  { step: "fog-auto", clouds: "", haze: "fog", fogMode: "auto", halo: "" },
  { step: "fog-manual", clouds: "", haze: "fog", fogMode: "manual", halo: "" },
  { step: "halo-off", clouds: "preset", haze: "", fogMode: "", halo: "off" },
  { step: "halo-real", clouds: "preset", haze: "", fogMode: "", halo: "Preset1" },
];

const report = [];
states.forEach((state) => {
  setState(state);
  fire("DOMContentLoaded", {});
  report.push({
    step: state.step,
    visible: rows.filter((row) => row.style.display !== "none").map((row) => row.key),
  });
});

// the cloud-base prefill: choosing a preset fills the base from the option's data-base
control("clouds.base").value = "";
fire("change", { getAttribute(name) { return name === "name" ? "clouds.preset" : null; } });
report.push({ step: "base-prefill", base: control("clouds.base").value });

// ...and clearing the picker back to the option that carries NO base clears the base with it
control("clouds.base").value = "2520";
pick.selectedIndex = 1;
fire("change", { getAttribute(name) { return name === "name" ? "clouds.preset" : null; } });
report.push({ step: "base-cleared", base: control("clouds.base").value });

process.stdout.write(JSON.stringify(report, null, 2));
