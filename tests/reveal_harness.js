/* THE REVEAL TOGGLE'S HARNESS — the shipped `/static/reveal.js` driven against a minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. The eye's flip must be proven on the STATE the page is
 * IN on arrival — with a password stored the field holds the real value — and an assertion on the
 * RENDERED MARKUP cannot prove what the script does with that markup. So this loads the REAL shipped
 * file — no copy, no re-implementation — into a minimal DOM stub and presses the button. The stub
 * implements only what reveal.js uses (getElementById, querySelectorAll, addEventListener, the three
 * attributes, `type`, `value`, `disabled`, `hidden`), and `click` refuses to fire on a disabled
 * button, exactly as a browser does. The contract asserted by the python test that drives this file is
 * the one the script's own header declares.
 *
 * Usage: node reveal_harness.js <path to reveal.js> -> prints one JSON object on stdout
 */
"use strict";

const fs = require("fs");
const { installMessages } = require("./dcssb_messages_harness.js");

const SCRIPT_PATH = process.argv[2];

/** One field + one button, booted as the page delivers them, and the shipped script evaluated once. */
function boot(options) {
  const settings = options || {};
  const field = {
    id: "cfg-password",
    value: settings.value === undefined ? "" : settings.value,
    disabled: Boolean(settings.disabled),
    type: "password",
    listeners: {},
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
 /* an edit the browser would report as an `input` event */
    input(newValue) { this.value = newValue; (this.listeners.input || []).forEach((fn) => fn({})); },
  };

  const attributes = {
    "data-reveal": "cfg-password",
    "aria-pressed": "false",
    "aria-label": "Show password",
    "title": "Show password",
  };
  const button = {
    hidden: true,
    disabled: false,
    listeners: {},
    getAttribute(name) { return name in attributes ? attributes[name] : null; },
    setAttribute(name, value) { attributes[name] = String(value); },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
 /* a real browser fires no click on a disabled control — the harness must not either */
    click() {
      if (this.disabled) { return; }
      (this.listeners.click || []).forEach((fn) => fn({}));
    },
  };

  const document = {
    readyState: "complete",
    addEventListener() {},
    getElementById(id) { return id === field.id ? field : null; },
    querySelectorAll(selector) { return selector === "[data-reveal]" ? [button] : []; },
  };

 /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  const source = fs.readFileSync(SCRIPT_PATH, "utf8");
  const run = new Function("document", "window", source);
  const window = {};
  installMessages(window);
  run(document, window);

  return { field, button, attributes };
}

function observe(pair) {
  return {
    hidden: pair.button.hidden,
    disabled: pair.button.disabled,
    label: pair.attributes["aria-label"],
    title: pair.attributes["title"],
    pressed: pair.attributes["aria-pressed"],
    fieldType: pair.field.type,
    fieldValue: pair.field.value,
  };
}

const STORED = "S3cretPass";
const TYPED = "N3wPass";

const result = {};

/* 1. a password is stored: the field ships holding the REAL value, so the eye is live */
{
  const pair = boot({ value: STORED });
  const before = observe(pair);
  pair.button.click();
  const revealed = observe(pair);
  pair.button.click();
  result.stored = { before, revealed, hiddenAgain: observe(pair) };
}

/* 2. no password is stored: the field is empty, so there is nothing to reveal */
{
  const pair = boot({ value: "" });
  const before = observe(pair);
  pair.button.click();
  result.empty = { before, afterClick: observe(pair) };
}

/* 3. the reader has TYPED into an empty field: the same state the stored case is in */
{
  const pair = boot({ value: "" });
  pair.field.input(TYPED);
  const armed = observe(pair);
  pair.button.click();
  const revealed = observe(pair);
  pair.button.click();
  result.typed = { armed, revealed, hiddenAgain: observe(pair) };
}

/* 4. the field is emptied after being revealed: nothing left to show, and the button knows it */
{
  const pair = boot({ value: STORED });
  pair.button.click();
  pair.field.input("");
  result.emptiedAgain = observe(pair);
}

/* 5. a server that cannot take a change: the field is disabled, so the eye stays out of the way */
{
  const pair = boot({ value: STORED, disabled: true });
  pair.button.click();
  result.disabledForm = observe(pair);
}

/* 6. the press must never touch the VALUE of the field (it reveals; it never writes) */
{
  const pair = boot({ value: STORED });
  pair.button.click();
  pair.button.click();
  result.valueUntouched = pair.field.value === STORED;
}

process.stdout.write(JSON.stringify(result));
