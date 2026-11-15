/* Drive the shipped `static/dialog-preset.js` against a minimal DOM and report what the weather MODE
 * control DOES — which blocks each mode shows, and how the preset-file pick narrows the preset list.
 *
 * The script is the SHIPPED asset (not an inline copy), so this exercises the bytes a browser gets.
 * `node` has no DOM, so only the surfaces this script touches are faked: the mode radios, the file
 * pick, the preset pick with two `<optgroup>`s, the `.dlg-temp` block and the two cloud controls.
 *
 * usage: node dialog_mode_harness.js <path-to-dialog-preset.js>
 * prints one JSON object of the observed state after each interaction.
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
function radio(value, checked) {
  return element({tag: "input", attrs: {type: "radio", name: "weather_mode", value: value},
                  value: value, checked: !!checked});
}

const modes = [radio("leave", true), radio("file"), radio("custom")];

//: a grouped entry's value carries its own file and its name, split by the page's separator.
const optionFlat = element({tag: "option", attrs: {value: "config/presets.yaml\nSummer",
                                                   "data-file": "config/presets.yaml"},
                            text: "Summer"});
const optionExtra = element({tag: "option", attrs: {value: "config/presets/extra.yaml\nWinter",
                                                    "data-file": "config/presets/extra.yaml"},
                             text: "Winter", selected: false});
const groupFlat = element({tag: "optgroup", attrs: {label: "config/presets.yaml"},
                           children: [optionFlat]});
const groupExtra = element({tag: "optgroup", attrs: {label: "config/presets/extra.yaml"},
                            children: [optionExtra]});
const pickSelect = element({tag: "select", attrs: {name: "preset", multiple: ""},
                            classes: ["dlg-pick-select"], children: [groupFlat, groupExtra]});
const chosenCount = element({tag: "span", attrs: {"data-chosen-count": ""}, text: "0"});
const chosenList = element({tag: "ul", attrs: {"data-chosen-chips": ""}});
const chosenBox = element({tag: "div", classes: ["dlg-chosen"], attrs: {"data-chosen": ""},
                           children: [chosenCount, chosenList], hidden: true});
const pickField = element({tag: "fieldset", classes: ["dlg-field", "dlg-pick"],
                           children: [pickSelect, chosenBox]});
//: the FILE control is a FILTER whose default value is empty, "All files".
const fileSelect = element({tag: "select", attrs: {name: "preset_file"}, value: ""});
const fileField = element({tag: "p", classes: ["dlg-field"], children: [fileSelect]});
const temp = element({tag: "div", classes: ["dlg-temp"]});
const modeField = element({tag: "fieldset", classes: ["dlg-field", "dlg-radio"], children: modes});
const clouds = element({tag: "select", attrs: {name: "clouds"}, value: "dynamic"});
const base = element({tag: "input", attrs: {name: "clouds.base"}, value: ""});

const dialog = element({tag: "div", children: [modeField, fileField, pickField, temp, clouds, base]});

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
  (listeners[event] || []).forEach((handler) => handler({target}));
}

vm.runInNewContext(source, {document, Event: function (name) { this.name = name; }});
(listeners.DOMContentLoaded || []).forEach((handler) => handler({}));

function state(label) {
  return {
    step: label,
    pickDisplay: pickField.style.display,
    fileDisplay: fileField.style.display,
    tempDisplay: temp.style.display,
    flatGroupHidden: groupFlat.style.display === "none" || groupFlat.disabled === true,
    extraGroupHidden: groupExtra.style.display === "none" || groupExtra.disabled === true,
    selected: [optionFlat, optionExtra].filter((o) => o.selected).map((o) => o.attrs.value),
    chosenHidden: chosenBox.hidden === true,
    chosenCount: chosenCount.textContent,
    chips: chosenList.children.map((chip) => ({
      name: (chip.children[0] || {}).textContent,
      file: (chip.children[1] || {}).textContent,
    })),
  };
}

const report = [];
report.push(state("initial"));

modes[0].checked = false; modes[1].checked = true;
fire(modes[1], "change");
report.push(state("file-mode"));

fileSelect.value = "config/presets.yaml";
optionFlat.selected = true;
fire(fileSelect, "change");
report.push(state("file-chosen"));

fileSelect.value = "config/presets/extra.yaml";
optionExtra.selected = true;
fire(fileSelect, "change");
report.push(state("other-file-chosen"));

modes[1].checked = false; modes[2].checked = true;
fire(modes[2], "change");
report.push(state("custom-mode"));

modes[2].checked = false; modes[0].checked = true;
fire(modes[0], "change");
report.push(state("leave-mode"));

process.stdout.write(JSON.stringify(report, null, 2));
