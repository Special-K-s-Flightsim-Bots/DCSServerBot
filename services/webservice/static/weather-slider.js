/* THE WEATHER CONTROLS' behaviour — one shipped asset for the wizard and the launch dialog.
 *
 * WHAT IT DRIVES. Each container carrying `data-weather-pair` holds ONE `input[type="range"]`, ONE
 * `input[type="number"]` and an `output[data-weather-readout]`. The range carries no `name` and never
 * submits; the number box is the form's field and the ONE value a preset keeps. Dragging the range
 * writes its value into the number box; typing in the number box moves the range to it (the browser
 * clamps the range to its own min/max). The readout mirrors the range's value and is BLANK while the
 * number box is empty, so a pair with no value reads as empty and shows a value the moment either
 * control is used.
 *
 * THE DIRECTION DIAL is the SAME pair, drawn round: a container carrying `data-weather-dial` inside a
 * `data-weather-pair` holds the range as its ring and needs no readout, so nothing but the ANGLE is
 * extra. This file writes `--angle` (0–1 of the track) on that container, which is what the sheet
 * rotates the needle by — the value is still the named number box's, posted unchanged.
 *
 * THE TIME HELPER is an addition beside a text control, never a replacement: an
 * `input[type="time"]` carrying `data-weather-time="<field name>"` writes that field's text box, and
 * a text box holding a plain `HH:MM` fills the helper. With no script the helper does nothing and the
 * text box is the control the form posts, exactly as before.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. It writes no value anywhere but the number box, the readout, the
 * time box and the dial's own angle: no hidden input, no data attribute, no second copy of the
 * submitted field. It never submits, never touches the form, and carries no user-visible string.
 *
 * IT IS AN ENHANCEMENT, AND ONLY THAT. The page is served with the number box (and the time text box)
 * as the submitted control, typed and bounded by the same min/max/step, so with JavaScript off the
 * form is complete and correct and this file simply never runs.
 *
 * THE CONTRACT, so the markup and this file cannot drift: a slider or dial container names itself
 * `data-weather-pair` and holds exactly one range and one number input (the dial additionally carries
 * `data-weather-dial` and the sheet reads its `--angle`); a time helper names itself
 * `data-weather-time` with the field it fills. Nothing else is read.
 */
(function () {
  "use strict";

  /* The readout is the SLIDER's own value, and it is blank while the pair holds none: a range always
     keeps a position, so the number box — not the range — is what says whether a value is set. */
  function paint(range, number, out) {
    if (!out) { return; }
    var empty = number.value === "" || isNaN(Number(number.value));
    out.textContent = empty ? "" : range.value;
  }

  function wire(pair) {
    var range = pair.querySelector('input[type="range"]');
    var number = pair.querySelector('input[type="number"]');
    if (!range || !number) { return; }
    var out = pair.querySelector("[data-weather-readout]");

    function fromRange() {
      number.value = range.value;
      paint(range, number, out);
    }

    function fromNumber() {
      /* An empty box is a pair with no value: the range is left where it is (it always holds one) and
         the readout goes blank. A non-numeric entry leaves the range alone too. */
      if (number.value !== "" && !isNaN(Number(number.value))) {
        range.value = number.value;
      }
      paint(range, number, out);
    }

    range.addEventListener("input", fromRange);
    range.addEventListener("change", fromRange);
    number.addEventListener("input", fromNumber);
    number.addEventListener("change", fromNumber);

    /* The pair the server rendered: the number box already carries the preset's value, so the range is
       placed on it and the readout shows it. An empty box leaves the range at its own default. */
    if (number.value !== "") { range.value = number.value; }
    paint(range, number, out);
  }

  /* The DIAL's own reading: the angle is the number box's position on its own track, as a fraction, so
     the same value drives the needle as the value the form posts. */
  function dial(pair) {
    var range = pair.querySelector('input[type="range"]');
    var number = pair.querySelector('input[type="number"]');
    if (!range || !number) { return; }
    var min = range.min === "" ? 0 : Number(range.min);
    var max = range.max === "" ? 100 : Number(range.max);

    function angle() {
      var value = number.value === "" || isNaN(Number(number.value))
        ? (range.value === "" ? min : Number(range.value))
        : Number(number.value);
      var fraction = max > min ? (value - min) / (max - min) : 0;
      pair.style.setProperty("--angle", String(Math.max(0, Math.min(1, fraction))));
    }

    range.addEventListener("input", angle);
    range.addEventListener("change", angle);
    number.addEventListener("input", angle);
    number.addEventListener("change", angle);
    angle();
  }

  /* The TIME helper: the field it fills is named by its own attribute, and only a plain HH:MM moves
     between the two — a named moment is not a time of day, so the helper goes blank for it. */
  function timeHelper(helper) {
    var name = helper.getAttribute("data-weather-time");
    if (!name) { return; }
    var host = helper.form || helper.parentNode;
    var box = host ? host.querySelector('input[type="text"][name="' + name + '"]') : null;
    if (!box) { return; }
    var clock = /^\d{1,2}:\d{2}$/;

    function copyToBox() {
      if (helper.value !== "") { box.value = helper.value; }
    }

    function copyToHelper() {
      /* A named moment (or an offset) is not a time of day: leaving the typed text alone, the helper
         only blanks itself so the two controls never show two different answers. */
      helper.value = clock.test(box.value) ? box.value : "";
    }

    helper.addEventListener("input", copyToBox);
    helper.addEventListener("change", copyToBox);
    box.addEventListener("input", copyToHelper);
    box.addEventListener("change", copyToHelper);
    if (clock.test(box.value)) { helper.value = box.value; }
  }

  function start() {
    var pairs = document.querySelectorAll("[data-weather-pair]");
    for (var index = 0; index < pairs.length; index += 1) { wire(pairs[index]); }
    var dials = document.querySelectorAll("[data-weather-dial]");
    for (index = 0; index < dials.length; index += 1) { dial(dials[index]); }
    var helpers = document.querySelectorAll("[data-weather-time]");
    for (index = 0; index < helpers.length; index += 1) { timeHelper(helpers[index]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();