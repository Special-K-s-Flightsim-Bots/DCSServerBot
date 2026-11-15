/* THE WEATHER CONTROLS' HARNESS — the shipped `/static/weather-slider.js` driven against a
 * minimal DOM.
 *
 * WHY A BROWSER-LESS HARNESS EXISTS AT ALL. A control's contract is a BEHAVIOUR — dragging the range
 * writes the number box, typing in the number box moves the range, the direction dial's angle follows
 * the value, and the time helper writes the field it names — and no assertion on the rendered markup
 * can prove what a script does with that markup. So this loads the REAL shipped file (no copy, no
 * re-implementation) into a minimal DOM stub and drives every control. The stub models only what the
 * file uses (querySelector, addEventListener, the controls' `value`, the output's `textContent`, the
 * dial container's `style.setProperty`, the helper's attribute and its host), and its range `value`
 * SETTER sanitizes exactly as a browser does: an out-of-range number clamps to min/max and a
 * non-number (including the empty string the template renders when no value is set) falls back to the
 * midpoint of min/max.
 *
 * Usage: node weather_slider_harness.js <path to weather-slider.js> -> one JSON object on stdout
 */
"use strict";

const fs = require("fs");

const SCRIPT_PATH = process.argv[2];

/** One pair — optionally a DIAL (a container the sheet rotates by `--angle`) — booted as the page
 * delivers it, plus an optional TIME helper, with the shipped script evaluated once. */
function boot(options) {
  const settings = options || {};
  const min = settings.min === undefined ? 0 : settings.min;
  const max = settings.max === undefined ? 10 : settings.max;
  const step = settings.step === undefined ? 1 : settings.step;

  const number = {
    value: settings.value === undefined ? "" : settings.value,
    listeners: {},
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    /* an edit the browser would report as an `input` event */
    input(newValue) { this.value = newValue; (this.listeners.input || []).forEach((fn) => fn({})); },
  };

  const range = {
    min: min,
    max: max,
    step: step,
    _value: settings.value === undefined ? "" : settings.value,
    listeners: {},
    get value() {
      if (this._value === "" || isNaN(Number(this._value))) { return String((min + max) / 2); }
      return this._value;
    },
    set value(next) {
      if (next === "" || isNaN(Number(next))) { this._value = ""; return; }
      let n = Number(next);
      if (n < min) { n = min; }
      if (n > max) { n = max; }
      this._value = String(n);
    },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    /* a drag the browser would report as an `input` event */
    slide(newValue) { this.value = newValue; (this.listeners.input || []).forEach((fn) => fn({})); },
  };

  const out = { textContent: "" };

  /* the dial container: what the sheet reads is the `--angle` the file writes here */
  const properties = {};
  const style = {
    setProperty(name, value) { properties[name] = value; },
  };

  const pair = {
    style: style,
    querySelector(selector) {
      if (selector === 'input[type="range"]') { return range; }
      if (selector === 'input[type="number"]') { return number; }
      if (selector === "[data-weather-readout]") { return settings.readout === false ? null : out; }
      return null;
    },
  };

  /* THE TIME HELPER: an input[type=time] naming the field it fills, beside that field's text box. */
  const box = {
    value: settings.startTime === undefined ? "" : settings.startTime,
    listeners: {},
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    input(newValue) { this.value = newValue; (this.listeners.input || []).forEach((fn) => fn({})); },
    change(newValue) { this.value = newValue; (this.listeners.change || []).forEach((fn) => fn({})); },
  };
  const helper = {
    value: "",
    listeners: {},
    getAttribute(name) { return name === "data-weather-time" ? settings.timeField : null; },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    input(newValue) { this.value = newValue; (this.listeners.input || []).forEach((fn) => fn({})); },
    change(newValue) { this.value = newValue; (this.listeners.change || []).forEach((fn) => fn({})); },
    form: null,
    parentNode: {
      querySelector(selector) {
        return selector === 'input[type="text"][name="' + settings.timeField + '"]' ? box : null;
      },
    },
  };

  const document = {
    readyState: "complete",
    addEventListener() {},
    querySelectorAll(selector) {
      if (selector === "[data-weather-pair]") { return [pair]; }
      if (selector === "[data-weather-dial]") { return settings.dial ? [pair] : []; }
      if (selector === "[data-weather-time]") { return settings.timeField ? [helper] : []; }
      return [];
    },
  };

  /* the SHIPPED file, evaluated once with a DOM in place — the same call the browser makes */
  const source = fs.readFileSync(SCRIPT_PATH, "utf8");
  const run = new Function("document", "window", source);
  run(document, {});

  return { range, number, out, properties, helper, box };
}

function observe(pair) {
  return {
    rangeValue: pair.range.value,
    numberValue: pair.number.value,
    readout: pair.out.textContent,
  };
}

const result = {};

/* 1. a pair the server rendered WITH a value: both controls carry it, the readout shows it */
{
  const pair = boot({ value: "2000", min: 1260, max: 2520, step: 10 });
  const rendered = observe(pair);
  pair.range.slide("1500");                 /* drag the slider */
  const afterSlide = observe(pair);
  pair.number.input("1800");                /* type in the box */
  const afterType = observe(pair);
  result.filled = { rendered, afterSlide, afterType };
}

/* 2. a pair the server rendered with NO value: both blank, and a drag gives it a value at once */
{
  const pair = boot({ value: "", min: 0, max: 10, step: 1 });
  const rendered = observe(pair);
  pair.range.slide("4");
  const afterSlide = observe(pair);
  result.blank = { rendered, afterSlide };
}

/* 3. typing a number ABOVE the range's max: the range clamps to max (the browser's own rule) */
{
  const pair = boot({ value: "10", min: 0, max: 359, step: 1 });
  pair.number.input("999");
  result.clamped = observe(pair);
}

/* 4. typing a NON-NUMBER: the range is left alone and the readout goes blank */
{
  const pair = boot({ value: "10", min: 0, max: 359, step: 1 });
  pair.number.input("abc");
  result.nonNumeric = observe(pair);
}

/* 5. the box emptied after a value: the readout goes blank (the pair holds no value again) */
{
  const pair = boot({ value: "7", min: 0, max: 10, step: 1 });
  pair.number.input("");
  result.emptied = observe(pair);
}

/* 6. the DIRECTION DIAL: the same pair drawn round — the container's `--angle` follows the value */
{
  const pair = boot({ value: "220", min: 0, max: 359, step: 1, dial: true, readout: false });
  const rendered = pair.properties["--angle"];
  pair.range.slide("90");
  const afterSlide = pair.properties["--angle"];
  pair.number.input("999");                 /* the range clamps to max, so the angle reaches 1 */
  const afterClamp = pair.properties["--angle"];
  pair.number.input("");                    /* an empty box falls back to the range's own place */
  const afterEmpty = pair.properties["--angle"];
  result.dial = { rendered, afterSlide, afterClamp, afterEmpty };
}

/* 7. the TIME HELPER: it fills the field it names, and a named moment leaves the helper blank */
{
  const named = boot({ timeField: "start_time", startTime: "dawn +00:00" });
  const renderedNamed = named.helper.value;
  named.helper.input("06:30");              /* typing in the helper fills the text box */
  const afterHelper = named.box.value;
  const clock = boot({ timeField: "start_time", startTime: "06:30" });
  const renderedClock = clock.helper.value;
  clock.box.change("noon");                 /* a named moment is not a time of day */
  const afterMoment = clock.helper.value;
  clock.box.change("08:15");                /* a plain HH:MM moves the helper back */
  const afterBack = clock.helper.value;
  result.time = { renderedNamed, afterHelper, renderedClock, afterMoment, afterBack };
}

process.stdout.write(JSON.stringify(result));