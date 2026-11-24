/* THE SETUP TOUR'S BUSY-SUBMIT HARNESS — the shipped inline script driven against a minimal DOM.
 *
 * The busy state is a client-side enhancement the step pages carry INLINE (there is no static file
 * for it), so this loads the EXACT script text the page serves (the test asserts it is in the page
 * and hands the same string here) and drives submits against a DOM stub. The stub implements only
 * what the script uses: `document.querySelectorAll("form")`, `document.createElement`,
 * a form's `addEventListener`/`querySelector`/`querySelectorAll`/`appendChild`/`dataset`, a button's
 * `dataset`/`textContent`/`setAttribute`/`disabled`, and a submit event carrying `submitter`.
 *
 * Usage:  node busy_submit_harness.js <path to the busy script body>   -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SOURCE = fs.readFileSync(process.argv[2], "utf8");

function makeButton(name, value, busy) {
  const attrs = {};
  return {
    tagName: "BUTTON",
    name: name || "",
    value: value || "",
    disabled: false,
    textContent: value || "",
    dataset: busy ? { busy: busy } : {},
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    _attrs: attrs,
  };
}

function makeForm(buttons) {
  const listeners = [];
  const form = {
    dataset: {},
    appended: [],
    buttons,
    addEventListener(kind, fn) { if (kind === "submit") { listeners.push(fn); } },
    querySelector(sel) {
      if (sel === "button[data-busy]") {
        return buttons.find((b) => b.dataset && b.dataset.busy) || null;
      }
      return null;
    },
    querySelectorAll(sel) {
      if (sel.indexOf("button") >= 0) { return buttons; }
      return [];
    },
    appendChild(node) { form.appended.push(node); },
    fire(submitter) {
      const event = {
        submitter: submitter || null, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
      };
      listeners.forEach((fn) => fn(event));
      return event;
    },
  };
  return form;
}

/* Boot the shipped script once, with a fresh DOM: one form that HOLDS a data-busy button and one
 * that does not (the control — the enhancement must not touch it). */
function boot() {
  const button = makeButton("action", "test", "Testing the connection…");
  const continueButton = makeButton("action", "continue", "Continuing…");
  const busyForm = makeForm([button, continueButton]);
  const otherButton = makeButton("action", "switch", null);
  const otherForm = makeForm([otherButton]);
  const document = {
    querySelectorAll(sel) { return sel === "form" ? [busyForm, otherForm] : []; },
    createElement() { return { type: "", name: "", value: "" }; },
  };
  const run = new Function("document", SOURCE);
  run(document);
  return { busyForm, otherForm, button, continueButton, otherButton };
}

const result = {};

/* 1. A PRESS: the pressed button shows its label, is marked busy, the form locks, the value is
 *    mirrored into a hidden input BEFORE the control is disabled. */
{
  const dom = boot();
  const event = dom.busyForm.fire(dom.button);
  result.press = {
    prevented: event.defaultPrevented,
    label: dom.button.textContent,
    ariaBusy: dom.button.getAttribute("aria-busy"),
    disabled: dom.busyForm.buttons.map((b) => b.disabled),
    locked: dom.busyForm.dataset.busy === "1",
    mirrored: dom.busyForm.appended.map((h) => ({ name: h.name, value: h.value, type: h.type })),
  };
}

/* 2. A SECOND SUBMIT while the first is in flight is REFUSED (defaultPrevented, no extra lock). */
{
  const dom = boot();
  dom.busyForm.fire(dom.button);
  const again = dom.busyForm.fire(dom.continueButton);
  result.double = {
    prevented: again.defaultPrevented,
    appended: dom.busyForm.appended.length,
    continueLabel: dom.continueButton.textContent,
  };
}

/* 3. A FORM WITHOUT A data-busy BUTTON is left alone: no lock, no mirror, nothing disabled. */
{
  const dom = boot();
  const event = dom.otherForm.fire(dom.otherButton);
  result.untouched = {
    prevented: event.defaultPrevented,
    disabled: dom.otherForm.buttons.map((b) => b.disabled),
    locked: Object.prototype.hasOwnProperty.call(dom.otherForm.dataset, "busy"),
    appended: dom.otherForm.appended.length,
  };
}

/* 4. A PRESS WITH NO SUBMITTER (implicit submission) still locks the form and mirrors nothing. */
{
  const dom = boot();
  dom.busyForm.fire(null);
  result.implicit = {
    disabled: dom.busyForm.buttons.map((b) => b.disabled),
    appended: dom.busyForm.appended.map((h) => ({ name: h.name, value: h.value })),
  };
}

process.stdout.write(JSON.stringify(result));
