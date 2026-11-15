/* THE SELECTION-BAR HARNESS — the shipped `/static/mission-select.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The card asks for a LIVE count and a count-bearing button
 * label, and markup alone cannot show either — both are written by the script as boxes are ticked. So
 * this loads the REAL shipped file (no copy, no re-implementation) into a minimal DOM stub and fires
 * the events a browser would. The stub implements only what mission-select.js uses: `document.
 * readyState`, `querySelector("[data-sel-bar]")`, `querySelectorAll("[data-sel-box]")`, the bar's FOUR
 * `querySelector` lookups (the select-all box, the count, the removal button and — M11 — the start
 * button), and each box's `disabled` / `checked` / `addEventListener`.
 *
 * The fleet is three boxes, ALL tickable (the running mission's included): the bar's total is the
 * number of rows, so the count can never disagree with what the operator can tick.
 *
 * M11 adds the START button (`[data-sel-start]`): its disabled state (exactly one ticked) and the
 * count's own reason (read off the button's `data-sel-why`) are here too. The `why` value stands in
 * for the SERVER's sentence the template renders onto that attribute; the real wording lives in
 * `actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL` and a Python test pins the rendered attribute to
 * it — this harness only proves the script STATES whatever the attribute carries.
 *
 * Usage: node mission_select_harness.js <path to mission-select.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const { installMessages } = require("./dcssb_messages_harness.js");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");
const WHY = "Select exactly one mission to set as the start mission.";

function makeBox(disabled) {
  return {
    disabled: !!disabled,
    checked: false,
    handlers: {},
    addEventListener(kind, fn) { (this.handlers[kind] = this.handlers[kind] || []).push(fn); },
    fire(kind) { (this.handlers[kind] || []).forEach((fn) => fn({})); },
  };
}

function boot() {
  const boxes = [makeBox(false), makeBox(false), makeBox(false)];
  const all = Object.assign(makeBox(false), { indeterminate: false });
  const count = { textContent: "" };
  const go = { disabled: false, textContent: "" };
  const startGo = {
    disabled: false,
    textContent: "",
    getAttribute(name) { return name === "data-sel-why" ? WHY : null; },
  };
  const bar = {
    querySelector(selector) {
      if (selector === "[data-sel-all]") { return all; }
      if (selector === "[data-sel-count]") { return count; }
      if (selector === "[data-sel-go]") { return go; }
      if (selector === "[data-sel-start]") { return startGo; }
      return null;
    },
  };
  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelector(selector) { return selector === "[data-sel-bar]" ? bar : null; },
    querySelectorAll(selector) { return selector === "[data-sel-box]" ? boxes : []; },
  };
 /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  const run = new Function("document", "window", SOURCE);
  const window = {};
  installMessages(window);
  run(document, window);

  function snap() {
    return {
      count: count.textContent,
      disabled: go.disabled,
      label: go.textContent,
      start_disabled: startGo.disabled,
      all: all.checked,
      indeterminate: all.indeterminate,
      boxes: boxes.map((box) => box.checked),
    };
  }

  const states = { initial: snap() };
  boxes[1].checked = true; boxes[1].fire("change");
  states.one = snap();
  boxes[2].checked = true; boxes[2].fire("change");
  states.two = snap();
  all.checked = false; all.fire("change");
  states.cleared = snap();
  all.checked = true; all.fire("change");
  states.all = snap();
  return states;
}

process.stdout.write(JSON.stringify({ states: boot() }));
