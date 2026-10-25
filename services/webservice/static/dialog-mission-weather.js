/* dialog-mission-weather.js — the launch dialog READS the mission's own weather on demand and fills
 * the temporary preset's rows with it.
 *
 * THE LAUNCH DIALOG IS A POST FORM: selecting an entry here must NEVER submit it, so the read is a
 * background fetch and nothing else. Nothing is fetched while the dialog renders — the read fires
 * when, and only when, the operator selects the pick's "Create your own weather" entry, and the
 * answer only ever WRITES VALUES INTO ALREADY-RENDERED CONTROLS and a line of text.
 *
 * THE LINE is the `[data-mission-line]` element INSIDE the block: the template renders the read's URL,
 * the three sentences and the mission's name on that `<p>` (confirm.html), so every one of the five
 * attributes is read OFF THE LINE, never off the `.dlg-temp` block around it. It is empty as
 * rendered, says "Loading current mission …" the moment the entry is selected, becomes "Read from
 * <name>.miz" when the answer arrives, and carries the read route's own sentence when the read
 * fails. Selecting anything else clears the line AND every value this script filled.
 *
 * THE ROWS STAY USABLE while the read runs: the fetch never blocks, and a request whose selection has
 * since changed is DISCARDED (a generation counter — an operator who changes their mind mid-read is
 * not fought by the late answer). The clearing restores what each control was RENDERED with, so a
 * value filled for the mission cannot ride along on a selection that did not ask for it.
 *
 * EVERY VALUE IS WRITTEN IN THE SHAPE ITS CONTROL CARRIES, and the control is told it moved: the
 * route answers the shape (the wizard's own dotted-key values), and each filled control gets one
 * `input` event, so the behaviours the page already ships — the time box beside the field, a
 * slider's readout, a dial's needle — follow the new value instead of the rendered one. No shape is
 * decided here.
 *
 * THE PICKERS ARE MARKED, never invented: a control whose value names a cloud preset or an
 * ice-crystal preset the node's OWN list carries is set to that entry; a name the list does NOT carry
 * leaves the control exactly as rendered (free text stays free text), so no control ever claims a
 * value it does not have. The control whose change alters WHICH rows are drawn (`clouds`) gets one
 * `change` event after the fill, so the shipped gate (static/dialog-preset.js) re-runs with the
 * mission's own modes — the same gate the wizard's step applies.
 *
 * Progressive enhancement: with JavaScript off nothing is read and every row stays as it renders;
 * the route's own conflict refusal stays the backstop.
 */
(function () {
  "use strict";

  //: the pick (a `<select multiple>` — its entries are read, never gated by CSS) and the block the
  //: temporary rows live in, with the line the mission's name is shown on.
  var PICK = ".dlg-pick select.dlg-pick-select";
  var BLOCK = ".dlg-temp";
  var LINE = "[data-mission-line]";
  //: the control whose change re-gates the rows. It is one of the mode controls the shipped gate
  //: reads, and it is the cloud mode — never `clouds.preset`, whose change also pre-fills the base.
  var GATE_CONTROL = "clouds";
  //: the ONE placeholder the "read from" sentence carries, filled with the mission's file name.
  var MISSION_PLACEHOLDER = "{mission}";

  //: what each control was RENDERED with, so clearing restores it rather than emptying it.
  var filled = [];
  //: bumped on every pick change; an answer whose generation is stale is discarded unanswered.
  var generation = 0;

  function pick(document_) {
    return document_.querySelector(PICK);
  }

  function block(document_) {
    return document_.querySelector(BLOCK);
  }

  function line(document_) {
    return document_.querySelector(LINE);
  }

  function isCustom(pick_) {
    var options = (pick_ && pick_.options) || [];
    for (var index = 0; index < options.length; index += 1) {
      if (options[index].selected && options[index].hasAttribute
          && options[index].hasAttribute("data-custom")) {
        return true;
      }
    }
    return false;
  }

  function textOf(node, value) {
    if (node) { node.textContent = value; }
  }

  function isSelect(control) {
    return String((control && control.tagName) || "").toUpperCase() === "SELECT";
  }

  //: the option of *control* whose value is *value*, or null — a name the list does NOT carry.
  function optionFor(control, value) {
    var options = control.options || [];
    for (var index = 0; index < options.length; index += 1) {
      if (String(options[index].value) === String(value)) { return options[index]; }
    }
    return null;
  }

  //: write *value* into *control*, recording what it held; a picker name the list does not carry is
  //: LEFT AS RENDERED and recorded as nothing, so it is never claimed and never "cleared".
  function write(control, value) {
    if (isSelect(control)) {
      if (!optionFor(control, value)) { return false; }
      filled.push({control: control, previous: String(control.value)});
      control.value = value;
      return true;
    }
    filled.push({control: control, previous: String(control.value)});
    control.value = value;
    return true;
  }

  //: tell the control's OWN behaviour that the value moved: the shipped `static/weather-slider.js`
  //: wires the time box beside the start-time field, the slider's readout and the dial's needle to
  //: `input` events on these controls, so an assignment alone would leave them showing the value
  //: they were RENDERED with. Nothing here decides a shape — the control's own rule does.
  function announce(control) {
    if (typeof control.dispatchEvent !== "function") { return; }
    control.dispatchEvent(new Event("input", {bubbles: true}));
  }

  function fill(root, values) {
    var keys = Object.keys(values || {});
    for (var index = 0; index < keys.length; index += 1) {
      var key = keys[index];
      var value = values[key];
      if (value === undefined || value === null || String(value) === "") { continue; }
      var control = root.querySelector('[name="' + key + '"]');
      if (!control) { continue; }
      if (write(control, value)) { announce(control); }
    }
  }

  //: restore every control this script filled and empty the line — the deselect, and the failure's
  //: own "nothing is prefilled" (a failure fills nothing, but a previous success may be cleared by it).
  function clear(document_) {
    for (var index = 0; index < filled.length; index += 1) {
      filled[index].control.value = filled[index].previous;
    }
    filled = [];
    textOf(line(document_), "");
  }

  //: one `change` on the mode control, so the shipped gate re-runs with the mission's own modes.
  function regate(document_) {
    var control = document_.querySelector('[name="' + GATE_CONTROL + '"]');
    if (!control || typeof control.dispatchEvent !== "function") { return; }
    control.dispatchEvent(new Event("change", {bubbles: true}));
  }

  //: THE READ'S OWN ATTRIBUTES live on the line element (`[data-mission-line]`), never on the block:
  //: the template renders all five there, so both this helper and ``start``/``apply`` read them off
  //: ``node``. ``fill`` still targets the BLOCK, because the rows are the block's controls.
  function readUrl(node, mission) {
    var url = String((node && node.getAttribute("data-mission-url")) || "");
    if (!url || !mission) { return url; }
    return url + (url.indexOf("?") === -1 ? "?" : "&") + "mission=" + encodeURIComponent(mission);
  }

  function apply(document_, response) {
    var block_ = block(document_);
    var node = line(document_);
    if (response.failed !== undefined) {
      clear(document_);
      textOf(node, response.failed);
      return;
    }
    fill(block_, response.values);
    regate(document_);
    var template = String(node.getAttribute("data-mission-read") || "");
    var name = String(response.name === undefined || response.name === null ? "" : response.name);
    textOf(node, template.split(MISSION_PLACEHOLDER).join(name));
  }

  function start(document_) {
    var block_ = block(document_);
    var node = line(document_);
    if (!block_ || !node) { return; }
    generation += 1;
    var token = generation;
    clear(document_);
    textOf(node, String(node.getAttribute("data-mission-loading") || ""));
    var mission = String(node.getAttribute("data-mission-name") || "");
    var failed = String(node.getAttribute("data-mission-error") || "");
    fetch(readUrl(node, mission), {headers: {"Accept": "application/json"},
                                   credentials: "same-origin"})
      .then(function (response) {
        if (token !== generation) { return null; }
        if (!response.ok) {
          return response.text().then(function (body) { return {failed: String(body || "").trim()}; });
        }
        return response.json();
      })
      .then(function (answer) {
        if (answer === null || token !== generation) { return; }
        apply(document_, answer);
      })
      .catch(function () {
        if (token !== generation) { return; }
        clear(document_);
        textOf(node, failed);
      });
  }

  document.addEventListener("change", function (event) {
    var target = event && event.target;
    if (!target || typeof target.getAttribute !== "function") { return; }
    if (target !== pick(document)) { return; }
    generation += 1;
    if (!isCustom(target)) { clear(document); return; }
    start(document);
  });
})();
