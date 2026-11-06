/* dialog-mission-weather.js — the launch dialog READS the mission's own weather on demand and fills
 * the temporary preset's rows with it.
 *
 * THE LAUNCH DIALOG IS A POST FORM: choosing a mode here must NEVER submit it, so the read is a
 * background fetch and nothing else. Nothing is fetched while the dialog renders — the read fires
 * when, and only when, the operator chooses the "Create your own weather" MODE (the third radio row),
 * and the answer only ever WRITES VALUES INTO ALREADY-RENDERED CONTROLS and a line of text.
 *
 * THE LINE is the `[data-mission-line]` element INSIDE the block: the template renders the read's URL,
 * the three sentences and the mission's name on that `<p>` (confirm.html), so every one of the five
 * attributes is read OFF THE LINE, never off the `.dlg-temp` block around it. It is empty as
 * rendered, says "Loading current mission …" the moment the mode is chosen, becomes "Read from
 * <name>.miz" when the answer arrives, and carries the read route's own sentence when the read
 * fails. Leaving the mode clears the line AND every value this script filled.
 *
 * THE ROWS STAY USABLE while the read runs: the fetch never blocks, and a request whose mode has
 * since changed is DISCARDED (a generation counter — an operator who changes their mind mid-read is
 * not fought by the late answer). The clearing restores what each control was RENDERED with, so a
 * value filled for the mission cannot ride along on a mode that did not ask for it.
 *
 * EVERY VALUE IS WRITTEN IN THE SHAPE ITS CONTROL CARRIES, and the control is told it moved: the
 * route answers the shape (the wizard's own dotted-key values), and each filled control gets one
 * `input` event, so the behaviours the page already ships — the time box beside the field, a
 * slider's readout, a dial's needle — follow the new value instead of the rendered one. No shape is
 * decided here.
 *
 * THE FOG TABLE IS FILLED FROM ITS OWN VALUE: the answer carries the mission's keyframes as the
 * single `fog.rows` value, and the table's cells are named per row, so the rows are rebuilt from
 * that value (cloning the row the page drew) and each cell is written by NAME. The table's column
 * order is the render's — this script reads the cell name and never assumes a position.
 *
 * THE PICKERS ARE MARKED, never invented: a control whose value names a cloud preset or an
 * ice-crystal preset the node's OWN list carries is set to that entry; a name the list does NOT carry
 * leaves the control exactly as rendered (free text stays free text), so no control ever claims a
 * value it does not have. The control whose change alters WHICH rows are drawn (`clouds`) gets one
 * `change` event after the fill, so the shipped gate (static/dialog-preset.js) re-runs with the
 * mission's own modes — the same gate the wizard's step applies.
 *
 * Progressive enhancement: with JavaScript off nothing is read and every row stays as it renders;
 * the mode the POST states is what the route reads, and the temporary fields are applied only in the
 * custom mode.
 */
(function () {
  "use strict";

  //: the MODE radios (`pages/actions.WEATHER_MODE_FIELD`) — the trigger is the third row, "custom".
  var MODE = 'input[name="weather_mode"]';
  var CUSTOM = "custom";
  //: the block the temporary rows live in, with the line the mission's name is shown on.
  var BLOCK = ".dlg-temp";
  var LINE = "[data-mission-line]";
  //: the control whose change re-gates the rows. It is one of the mode controls the shipped gate
  //: reads, and it is the cloud mode — never `clouds.preset`, whose change also pre-fills the base.
  var GATE_CONTROL = "clouds";
  //: the ONE placeholder the "read from" sentence carries, filled with the mission's file name.
  var MISSION_PLACEHOLDER = "{mission}";
  //: THE FOG TABLE — the rows live in the rendered dialog and the answer carries them as ONE
  //: ``fog.rows`` value, so the rows are read from that value and written into the cells by NAME
  //: (``fog_row_<index>_<cell>``). Neither the column order nor the cell spelling is decided here.
  var FOG_TABLE = ".dlg-ftable";
  var FOG_ROW = ".dlg-frow:not(.hd)";
  var FOG_CELL = /^fog_row_\d+_(.+)$/;
  //: the preset key the table's rows travel under (``weather_fields.FOG_ROWS_KEY``).
  var FOG_ROWS_KEY = "fog.rows";
  //: THE LAUNCH'S "apply them to the original mission" BOX (``actions.MODIFY_MISSION_OPTION``'s
  //: neighbour ``use_orig``): the read follows it, because the launch rewrites the mission it names.
  var ORIGINAL_FIELD = "use_orig";

  //: what each control was RENDERED with, so clearing restores it rather than emptying it.
  var filled = [];
  //: bumped on every mode change; an answer whose generation is stale is discarded unanswered.
  var generation = 0;

  function customChosen(document_) {
    var checked = document_.querySelector(MODE + ":checked");
    return !!checked && String(checked.value || "") === CUSTOM;
  }

  function block(document_) {
    return document_.querySelector(BLOCK);
  }

  function line(document_) {
    return document_.querySelector(LINE);
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

  //: the table's data rows, as an array (a live NodeList would change under the loop that grows it).
  function fogRows(table) {
    var nodes = table.querySelectorAll(FOG_ROW);
    var rows = [];
    for (var index = 0; index < nodes.length; index += 1) { rows.push(nodes[index]); }
    return rows;
  }

  //: the answer's ``fog.rows`` value — the preset's own spelling: one row per keyframe as
  //: ``time:thickness:visibility``, rows separated by ``,``, an omitted cell left empty.
  function parseFogRows(text) {
    var rows = [];
    var chunks = String(text || "").split(",");
    for (var index = 0; index < chunks.length; index += 1) {
      var cells = chunks[index].split(":");
      var row = {time: (cells[0] || "").trim(), thickness: (cells[1] || "").trim(),
                 visibility: (cells[2] || "").trim()};
      if (!row.time && !row.thickness && !row.visibility) { continue; }
      rows.push(row);
    }
    return rows;
  }

  //: every cell keeps the name the form posts (``fog_row_<index>_<cell>``), so a row added here is
  //: routed with the rows the page drew.
  function renumberRows(table) {
    var rows = fogRows(table);
    for (var index = 0; index < rows.length; index += 1) {
      var cells = rows[index].querySelectorAll('input[type="number"]');
      for (var cell = 0; cell < cells.length; cell += 1) {
        var match = FOG_CELL.exec(cells[cell].name || "");
        if (match) { cells[cell].name = "fog_row_" + index + "_" + match[1]; }
      }
      var remove = rows[index].querySelector("[data-fog-remove]");
      if (remove) { remove.setAttribute("data-fog-remove", String(index)); }
    }
  }

  //: THE KEYFRAMES the mission carries: one row per keyframe, added by cloning the row the page
  //: drew (a POST form has no GET reload to spawn one), then every cell written by its own name.
  function fillFogRows(root, text) {
    var table = root && root.querySelector ? root.querySelector(FOG_TABLE) : null;
    if (!table) { return; }
    var wanted = parseFogRows(text);
    if (!wanted.length) { return; }
    var rows = fogRows(table);
    if (!rows.length) { return; }
    while (rows.length < wanted.length) {
      var last = rows[rows.length - 1];
      var clone = last.cloneNode(true);
      var inputs = clone.querySelectorAll("input");
      for (var input = 0; input < inputs.length; input += 1) { inputs[input].value = ""; }
      last.parentNode.insertBefore(clone, last.nextSibling);
      var grown = fogRows(table);
      if (grown.length <= rows.length) { return; }   // a table that cannot grow: fill what is there
      rows = grown;
    }
    renumberRows(table);
    rows = fogRows(table);
    for (var index = 0; index < wanted.length && index < rows.length; index += 1) {
      var cells = rows[index].querySelectorAll('input[type="number"]');
      for (var cell = 0; cell < cells.length; cell += 1) {
        var match = FOG_CELL.exec(cells[cell].name || "");
        if (!match || wanted[index][match[1]] === undefined) { continue; }
        if (write(cells[cell], wanted[index][match[1]])) { announce(cells[cell]); }
      }
    }
  }

  //: leaving the mode drops the rows this fill added; the row the page rendered stays, blank.
  function clearFogRows(root) {
    var table = root && root.querySelector ? root.querySelector(FOG_TABLE) : null;
    if (!table) { return; }
    var rows = fogRows(table);
    for (var index = rows.length - 1; index > 0; index -= 1) {
      if (rows[index].parentNode) { rows[index].parentNode.removeChild(rows[index]); }
    }
    renumberRows(table);
  }

  //: restore every control this script filled and empty the line — leaving the mode, and the failure's
  //: own "nothing is prefilled" (a failure fills nothing, but a previous success may be cleared by it).
  function clear(document_) {
    clearFogRows(block(document_));
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
  function readUrl(node, mission, original) {
    var url = String((node && node.getAttribute("data-mission-url")) || "");
    if (!url) { return url; }
    var query = [];
    if (mission) { query.push("mission=" + encodeURIComponent(mission)); }
    //: ALWAYS stated: the launch applies its weather to the ORIGINAL mission file by default, so a
    //: request that says nothing would read a different file than the one being rewritten.
    query.push("original=" + (original ? "1" : "0"));
    return url + (url.indexOf("?") === -1 ? "?" : "&") + query.join("&");
  }

  //: THE LAUNCH'S MISSION PICK (`pages/actions.MISSION_FIELD`) — Startup draws it, and the weather
  //: this block shows is the weather of the mission it names. A `<select>`, never the dialog's hidden
  //: `name="mission"` field (the Load dialog carries the picked row's index there), so the two shapes
  //: cannot be confused.
  var MISSION_PICK = 'select[name="mission"]';

  //: the mission the read names: the pick's own entry when the dialog draws one, else the name the
  //: page rendered onto the line (the Load dialog, whose mission is the row that opened it).
  function missionOf(document_, node) {
    var pick = document_.querySelector(MISSION_PICK);
    if (pick) { return String(pick.value || ""); }
    return String((node && node.getAttribute("data-mission-name")) || "");
  }

  //: whether the launch will rewrite the ORIGINAL mission file — the box the dialog offers, and the
  //: launch's own default when it offers none (the Load dialog has no such box).
  function originalRequested(document_) {
    var box = document_.querySelector('input[type="checkbox"][name="' + ORIGINAL_FIELD + '"]');
    if (!box) { return true; }
    return !!box.checked;
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
    fillFogRows(block_, (response.values || {})[FOG_ROWS_KEY]);
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
    var mission = missionOf(document_, node);
    var failed = String(node.getAttribute("data-mission-error") || "");
    fetch(readUrl(node, mission, originalRequested(document_)),
          {headers: {"Accept": "application/json"}, credentials: "same-origin"})
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
    var name = target.getAttribute("name");
    if (name === ORIGINAL_FIELD) {
      // THE MISSION THE CUSTOM WEATHER READS FOLLOWS THE BOX: the launch rewrites the original file
      // or the one the server is on, so a changed box re-reads instead of keeping the other file's
      // values in controls the launch will apply.
      if (!customChosen(document)) { return; }
      start(document);
      return;
    }
    if (target.matches && target.matches(MISSION_PICK)) {
      // …and it follows the MISSION pick for the same reason: another mission is another file with
      // another sky, so the rows are filled from the mission this launch will actually load.
      if (!customChosen(document)) { return; }
      start(document);
      return;
    }
    if (name !== "weather_mode") { return; }
    generation += 1;
    if (!customChosen(document)) { clear(document); return; }
    start(document);
  });
})();
