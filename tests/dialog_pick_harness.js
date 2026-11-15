/* Drive the SHIPPED `static/dialog-preset.js` against a minimal DOM and report what the launch
 * dialog's preset PICK does.
 *
 * The script is the shipped asset — the file the dialog's own `<script src>` names — not an inline
 * copy and not a re-spelling, so this exercises the bytes a browser gets. `node` has no DOM, so only
 * the surfaces this script touches are faked: the weather-mode radios, the preset-FILE filter, the
 * preset pick with two `<optgroup>`s, the chosen block and the `.dlg-temp` block.
 *
 * What it checks, case by case:
 *
 * - INITIAL: the file filter stands on its `All files` default (an empty value), every group is
 *   drawn, no group is disabled, nothing is ticked and the chosen block is empty and hidden;
 * - NAME: a ticked entry becomes ONE chip carrying its name and its FILE, and the temporary weather
 *   fields stay hidden — the MODE, never a tick, decides which blocks are shown;
 * - CUSTOM: the temporary fields are revealed, and the tick and its chip survive the mode change;
 * - NAME-FROM-CUSTOM: back in the file mode, ticking the OTHER group's entry leaves both ticks in
 *   place and lists the chips in the SELECT's own order (the files' config order), not the order the
 *   entries were ticked;
 * - FILTERED: a filter value HIDES the other groups and does nothing else — no group and no entry is
 *   disabled, and no tick is cleared, so a tick made under a hidden group still posts;
 * - REMOVED: a chip's Remove button unticks exactly the entry it names and leaves the filter's view
 *   alone.
 *
 * A failed check writes `FAIL <case>: <reason>` to stderr and exits non-zero, so the harness itself
 * refuses a filter that disables a group, a filter that clears a tick, and a chip whose file label or
 * order drifts. The JSON report on stdout is the same observations, for a pytest pin.
 *
 * usage: node dialog_pick_harness.js <path-to-dialog-preset.js>
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");

function element(opts) {
  const node = {
    tag: opts.tag || "div",
    attrs: opts.attrs || {},
    classes: opts.classes || [],
    children: opts.children || [],
    style: {},
    selected: !!opts.selected,
    checked: !!opts.checked,
    disabled: !!opts.disabled,
    value: opts.value === undefined ? "" : opts.value,
    textContent: opts.text === undefined ? "" : opts.text,
    hidden: !!opts.hidden,
    parent: null,
    get firstChild() { return this.children.length ? this.children[0] : null; },
    get options() {
      const found = [];
      const walk = (node) => {
        (node.children || []).forEach((child) => {
          if (String(child.tag).toLowerCase() === "option") { found.push(child); }
          else { walk(child); }
        });
      };
      walk(this);
      return found;
    },
    appendChild(child) { child.parent = this; this.children.push(child); return child; },
    removeChild(child) {
      const at = this.children.indexOf(child);
      if (at !== -1) { this.children.splice(at, 1); child.parent = null; }
      return child;
    },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    closest(selector) {
      let cursor = this;
      while (cursor) {
        if (matchesAll(cursor, selector.split(/\s+/))) { return cursor; }
        cursor = cursor.parent;
      }
      return null;
    },
    querySelector(selector) {
      const found = queryAll(this, selector);
      return found.length ? found[0] : null;
    },
    querySelectorAll(selector) { return queryAll(this, selector); },
    dispatchEvent() {},
  };
  node.children.forEach((child) => { child.parent = node; });
  return node;
}

function matchesPart(node, part) {
  let rest = part;
  if (rest.charAt(0) === ":") {
    if (rest === ":checked") { return !!(node.checked || node.selected); }
    return false;
  }
  const tagMatch = /^[a-zA-Z][a-zA-Z0-9-]*/.exec(rest);
  if (tagMatch) {
    if (String(node.tag).toLowerCase() !== tagMatch[0].toLowerCase()) { return false; }
    rest = rest.slice(tagMatch[0].length);
  }
  if (/:checked/.test(rest) && !(node.checked || node.selected)) { return false; }
  const classRe = /\.([A-Za-z0-9_-]+)/g;
  let found;
  while ((found = classRe.exec(rest))) {
    if (node.classes.indexOf(found[1]) === -1) { return false; }
  }
  const attrRe = /\[([A-Za-z0-9_.-]+)(?:="([^"]*)")?\]/g;
  while ((found = attrRe.exec(rest))) {
    const name = found[1];
    if (!Object.prototype.hasOwnProperty.call(node.attrs, name)) { return false; }
    if (found[2] !== undefined && String(node.attrs[name]) !== found[2]) { return false; }
  }
  return true;
}

function matchesAll(node, parts) {
  return parts.every((part) => matchesPart(node, part));
}

function queryAll(root, selector) {
  const parts = selector.trim().split(/\s+/);
  const hit = [];
  (function walk(node, index) {
    if (!node) { return; }
    const at = matchesPart(node, parts[index]);
    if (at && index === parts.length - 1) { hit.push(node); }
    const next = at ? index + 1 : index;
    if (next >= parts.length) { return; }
    (node.children || []).forEach((child) => walk(child, next));
  })(root, 0);
  return hit;
}

// ---- the dialog's own little tree ------------------------------------------------------------
const FLAT = "config/presets.yaml";
const EXTRA = "config/presets/extra.yaml";
const SEPARATOR = "\n";

function radio(value, checked) {
  return element({tag: "input", attrs: {type: "radio", name: "weather_mode", value: value},
                  value: value, checked: !!checked});
}

const modes = [radio("leave", true), radio("file"), radio("custom")];

//: a grouped entry's value carries its own file and its name, split by the page's separator.
const optionSummer = element({tag: "option", value: FLAT + SEPARATOR + "Summer",
                              attrs: {value: FLAT + SEPARATOR + "Summer", "data-file": FLAT},
                              text: "Summer"});
const optionWinter = element({tag: "option", value: EXTRA + SEPARATOR + "Winter",
                              attrs: {value: EXTRA + SEPARATOR + "Winter", "data-file": EXTRA},
                              text: "Winter"});
const groupFlat = element({tag: "optgroup", attrs: {label: FLAT}, children: [optionSummer]});
const groupExtra = element({tag: "optgroup", attrs: {label: EXTRA}, children: [optionWinter]});
const groups = [groupFlat, groupExtra];
const pickSelect = element({tag: "select", attrs: {name: "preset", multiple: ""},
                            classes: ["dlg-pick-select"], children: [groupFlat, groupExtra]});
const chosenCount = element({tag: "span", attrs: {"data-chosen-count": ""}, text: "0"});
const chosenList = element({tag: "ul", attrs: {"data-chosen-chips": ""}});
const chosenBox = element({tag: "div", classes: ["dlg-chosen"], attrs: {"data-chosen": ""},
                           children: [chosenCount, chosenList], hidden: true});
const pickField = element({tag: "fieldset", classes: ["dlg-field", "dlg-pick"],
                           children: [pickSelect, chosenBox]});
//: the FILE control is a FILTER whose default value is empty — "All files".
const fileSelect = element({tag: "select", attrs: {name: "preset_file"}, value: ""});
const fileField = element({tag: "p", classes: ["dlg-field"], children: [fileSelect]});
const temp = element({tag: "div", classes: ["dlg-temp"]});
const modeField = element({tag: "fieldset", classes: ["dlg-field", "dlg-radio"], children: modes});

const dialog = element({tag: "div", children: [modeField, fileField, pickField, temp]});

const listeners = {};
const document = {
  querySelector(selector) {
    if (selector === '[role="dialog"]') { return dialog; }
    return dialog.querySelector(selector);
  },
  querySelectorAll(selector) { return dialog.querySelectorAll(selector); },
  createElement(tag) { return element({tag}); },
  addEventListener(event, handler) { (listeners[event] = listeners[event] || []).push(handler); },
};

function fire(target, event) {
  (listeners[event] || []).forEach((handler) => handler({target, preventDefault() {}}));
}

vm.runInNewContext(source, {document, Event: function (name) { this.name = name; }});
(listeners.DOMContentLoaded || []).forEach((handler) => handler({}));

// ---- what the pick looks like, read off the DOM ----------------------------------------------
const failures = [];

function check(step, condition, reason) {
  if (condition) { return; }
  failures.push(step + ": " + reason);
  process.stderr.write("FAIL " + step + ": " + reason + "\n");
}

function groupHidden(group) { return group.style.display === "none"; }

function chipViews() {
  return chosenList.children.map((item) => {
    const remove = item.children.find((child) => child.hasAttribute("data-chosen-remove"));
    return {
      name: (item.children[0] || {}).textContent,
      file: (item.children[1] || {}).textContent,
      remove: remove ? remove.getAttribute("data-chosen-remove") : null,
    };
  });
}

function state(step) {
  return {
    step: step,
    mode: modes.filter((row) => row.checked).map((row) => row.value),
    fileValue: fileSelect.value,
    pickDisplay: pickField.style.display,
    fileDisplay: fileField.style.display,
    tempDisplay: temp.style.display,
    groupsShown: groups.filter((group) => !groupHidden(group)).map((g) => g.getAttribute("label")),
    groupsDisabled: groups.filter((group) => group.disabled === true).map((g) => g.getAttribute("label")),
    optionsDisabled: pickSelect.options.filter((o) => o.disabled === true).map((o) => o.value),
    selected: pickSelect.options.filter((o) => o.selected).map((o) => o.value),
    chosenHidden: chosenBox.hidden === true,
    chosenCount: chosenCount.textContent,
    chips: chipViews(),
  };
}

function useMode(name) {
  modes.forEach((row) => { row.checked = row.value === name; });
  fire(modes.filter((row) => row.checked)[0], "change");
}

function tick(option) { option.selected = true; fire(pickSelect, "change"); }

function expectChips(step, expected) {
  const seen = chipViews().map((chip) => [chip.name, chip.file]);
  check(step, JSON.stringify(seen) === JSON.stringify(expected),
        "chips are " + JSON.stringify(seen) + ", not " + JSON.stringify(expected));
  check(step, chosenCount.textContent === String(expected.length),
        "the count is " + chosenCount.textContent + ", not " + expected.length);
  check(step, chosenBox.hidden === (expected.length === 0),
        "the chosen block's visibility does not follow the tick count");
}

function expectNoDisabled(step) {
  check(step, groups.every((group) => group.disabled !== true), "the filter disabled a group");
  check(step, pickSelect.options.every((option) => option.disabled !== true),
        "the filter disabled an entry");
}

const report = [];
report.push(state("initial"));
check("initial", fileSelect.value === "", "the file filter is not on its All files default");
check("initial", groups.every((group) => !groupHidden(group)), "a group is hidden by default");
check("initial", groups.every((group) => group.disabled !== true), "a group is disabled by default");
check("initial", pickSelect.options.every((option) => option.selected === false),
      "an entry is ticked by default");
check("initial", chosenBox.hidden === true && chosenCount.textContent === "0",
      "the chosen block is not empty by default");

// a tick in the FILE mode: one chip naming the preset and its file, the fields still hidden
useMode("file");
tick(optionWinter);
report.push(state("name"));
expectChips("name", [["Winter", EXTRA]]);
check("name", temp.style.display === "none", "a tick revealed the temporary weather fields");
check("name", optionSummer.selected === false, "a tick ticked another entry");

// "Create your own": the fields are revealed, and the tick and its chip survive the mode change
useMode("custom");
report.push(state("custom"));
check("custom", temp.style.display === "", "the custom mode did not reveal the temporary fields");
check("custom", optionWinter.selected === true, "a mode change cleared a tick");
expectChips("custom", [["Winter", EXTRA]]);

// back in the file mode, ticking the OTHER group's entry: the chips follow the SELECT's order
useMode("file");
tick(optionSummer);
report.push(state("name-from-custom"));
check("name-from-custom", temp.style.display === "none",
      "the temporary fields stayed visible in the file mode");
check("name-from-custom", optionWinter.selected === true, "a second tick cleared the first");
check("name-from-custom", optionSummer.selected === true, "the second tick did not land");
expectChips("name-from-custom", [["Summer", FLAT], ["Winter", EXTRA]]);

// the filter hides the other groups and does NOTHING else: no disabled group, no cleared tick
fileSelect.value = EXTRA;
fire(fileSelect, "change");
report.push(state("filtered"));
check("filtered", groupHidden(groupFlat), "the filter did not hide the other file's group");
check("filtered", !groupHidden(groupExtra), "the filter hid the chosen file's own group");
expectNoDisabled("filtered");
check("filtered", optionSummer.selected === true && optionWinter.selected === true,
      "the filter cleared a tick");
expectChips("filtered", [["Summer", FLAT], ["Winter", EXTRA]]);

// a chip's Remove button unticks exactly the entry it names; the filter's view is left alone
const summerRemove = chosenList.children[0].children
  .find((child) => child.hasAttribute("data-chosen-remove"));
check("removed", summerRemove !== undefined, "the first chip carries no Remove button");
check("removed", summerRemove !== undefined &&
      summerRemove.getAttribute("data-chosen-remove") === FLAT + SEPARATOR + "Summer",
      "the Remove button does not name the entry it stands for");
if (summerRemove !== undefined) { fire(summerRemove, "click"); }
report.push(state("removed"));
check("removed", optionSummer.selected === false, "Remove did not untick its own entry");
check("removed", optionWinter.selected === true, "Remove unticked another entry");
check("removed", groupHidden(groupFlat) && !groupHidden(groupExtra),
      "Remove disturbed the filter's view");
expectChips("removed", [["Winter", EXTRA]]);

process.stdout.write(JSON.stringify(report, null, 2) + "\n");
if (failures.length) {
  process.stderr.write(failures.length + " check(s) failed\n");
  process.exit(1);
}
