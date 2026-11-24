/* Drives the wizard's actions-step inline script against a minimal DOM.
 *
 * Usage: node scheduler_picks_harness.js <path to the extracted inline script>
 *
 * The stub implements only what the script uses: querySelectorAll over the three hooks it knows
 * ([data-action-trigger], select[data-action-method], [data-action-picks]), addEventListener,
 * closest('form'), querySelector by pick kind, classList.toggle and the fields' disabled flag.
 * It prints ONE JSON object: whether the method select has a change listener, whether changing it
 * submits the form, and the disabled/off state of both mission picks after the radio is swapped.
 * A browser-less harness exists because a markup assertion cannot see what the script DOES.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const result = { methodListener: false, methodSubmits: 0, pick: {} };

const form = {
  submits: 0,
  requestSubmit() { this.submits += 1; },
  submit() { this.submits += 1; },
};

function listeners() {
  return {
    map: {},
    addEventListener(kind, fn) { (this.map[kind] = this.map[kind] || []).push(fn); },
    fire(kind) { (this.map[kind] || []).forEach((fn) => fn({ target: this })); },
  };
}

function pick(name) {
  const state = { off: true, fields: [{ disabled: name === "mission_file" }] };
  return {
    get off() { return state.off; },
    fields: state.fields,
    classList: {
      toggle(cls, on) { if (cls === "sw-pick-off") { state.off = on; } },
    },
    querySelectorAll() { return state.fields; },
  };
}

const picks = { mission_id: pick("mission_id"), mission_file: pick("mission_file") };

function radio(value, checked) {
  const events = listeners();
  return {
    value,
    checked,
    addEventListener: events.addEventListener.bind(events),
    fire: events.fire.bind(events),
  };
}

const radios = [radio("mission_id", true), radio("mission_file", false)];
const holder = {
  querySelectorAll(selector) {
    return selector.indexOf("action_pick.") === -1 ? [] : radios;
  },
  querySelector(selector) {
    const match = /data-pick="([^"]+)"/.exec(selector);
    return match ? picks[match[1]] : null;
  },
};
const triggerGroup = listeners();
triggerGroup.closest = () => form;
const methodSelect = listeners();
methodSelect.closest = () => form;

const document = {
  querySelectorAll(selector) {
    if (selector === "[data-action-trigger]") { return [triggerGroup]; }
    if (selector === "select[data-action-method]") { return [methodSelect]; }
    if (selector === "[data-action-picks]") { return [holder]; }
    return [];
  },
};

vm.runInNewContext(source, { document });

result.methodListener = (methodSelect.map.change || []).length > 0;
methodSelect.fire("change");
result.methodSubmits = form.submits;

/* the operator picks "by mission file": the id pick goes off and stops posting, the file pick goes
 * live — the swap, not merely a hidden heading */
radios[0].checked = false;
radios[1].checked = true;
radios[1].fire("change");
result.pick = {
  idOff: picks.mission_id.off,
  fileOff: picks.mission_file.off,
  idDisabled: picks.mission_id.fields.every((field) => field.disabled),
  fileDisabled: picks.mission_file.fields.every((field) => field.disabled),
};

process.stdout.write(JSON.stringify(result));
