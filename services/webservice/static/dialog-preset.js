/* dialog-preset.js — the launch dialog's weather blocks FOLLOW the mode, and a chosen preset file
 * narrows the preset list.
 *
 * The dialog's form POSTS the launch, so a mode change must NEVER submit it. The wizard's step is a
 * GET form and re-renders server-side; here the gate is applied on the client instead, in two parts:
 *
 * 1. THE MODE (`weather_mode`): the three radios are the one weather choice, so the blocks that
 *    belong to a mode are shown only while it is chosen — the preset pick and the preset-file pick for
 *    `file`, the temporary weather fields for `custom`, and nothing extra for `leave`. Every temporary
 *    row additionally carries the wizard's own gate as `data-gate-*` attributes
 *    (`pages/presets.row_gate`), and this script shows/hides them by the CURRENT mode values, so the
 *    visible rows equal what the wizard's step would draw.
 * 2. THE FILE (`preset_file`): the pick's `<optgroup>`s are the node's preset FILES, and each group
 *    carries ITS OWN file's names — a name two files both define is drawn under BOTH headings, its
 *    value staying the bare name. Choosing a file hides and disables the other groups and clears
 *    their selections, so the list shows exactly the presets of the file the operator named. The
 *    scope of both the hiding and the clearing is the GROUP, never the option value: the duplicate
 *    entries are what keep a shared name pickable under whichever file it was ticked in.
 *
 * It also fills `clouds.base` with the chosen preset's own base (the picker option's `data-base`),
 * which is the dialog's twin of the wizard's plate link.
 *
 * Progressive enhancement: with JavaScript off every block stays visible, EVERY file's presets stay
 * visible under their own heading, and the file pick posts its first listed label. The route then
 * resolves a ticked name against THAT posted file ONLY and refuses a name the file does not carry, so
 * a name ticked under a heading other than the posted file is refused rather than taken from the
 * file that heading stands for.
 *
 * A node carrying a single preset file draws no file pick and no group headings, so there is nothing
 * here to narrow.
 */
(function () {
  "use strict";

  //: the mode controls: a change on one of these re-gates the rows. A plain value control is left alone.
  var MODE_NAMES = ["clouds", "enable_dust", "fog.mode", "halo.preset"];

  //: the weather MODE's two non-default values (`pages/actions.WEATHER_MODE_*`).
  var MODE_FILE = "file";
  var MODE_CUSTOM = "custom";

  function valueOf(root, name) {
    var control = root.querySelector('[name="' + name + '"]');
    return control ? String(control.value || "") : "";
  }

  function fieldOf(root, name) {
    var control = root.querySelector('[name="' + name + '"]');
    if (!control) { return null; }
    return control.closest ? (control.closest(".dlg-field") || control) : control;
  }

  function show(element, visible) {
    if (element) { element.style.display = visible ? "" : "none"; }
  }

  //: a real halo preset — not empty, not `off`, not `auto` (`presets.is_real_halo`).
  function isRealHalo(value) {
    var text = String(value || "").trim();
    return text !== "" && text !== "off" && text !== "auto";
  }

  //: whether a space-separated condition list admits `value` (an empty list admits every value).
  function allows(condition, value) {
    var text = String(condition || "").trim();
    if (!text) { return true; }
    return text.split(/\s+/).indexOf(value) !== -1;
  }

  function rowShown(row, state) {
    if (!allows(row.getAttribute("data-gate-cloud"), state.clouds)) { return false; }
    if (!allows(row.getAttribute("data-gate-haze"), state.haze)) { return false; }
    if (!allows(row.getAttribute("data-gate-fog-mode"), state.fogMode)) { return false; }
    if (row.hasAttribute("data-halo-real") && !isRealHalo(state.halo)) { return false; }
    return true;
  }

  function gate(root) {
    if (!root) { return; }
    var rows = root.querySelectorAll(".dlg-wrow");
    if (!rows || !rows.length) { return; }
    var state = {
      clouds: valueOf(root, "clouds"),
      haze: valueOf(root, "enable_dust"),
      fogMode: valueOf(root, "fog.mode"),
      halo: valueOf(root, "halo.preset"),
    };
    for (var index = 0; index < rows.length; index += 1) {
      rows[index].style.display = rowShown(rows[index], state) ? "" : "none";
    }
  }

  //: the weather mode the radios state — "" for a dialog that draws no mode at all.
  function modeOf(root) {
    var checked = root.querySelector('input[name="weather_mode"]:checked');
    return checked ? String(checked.value || "") : "";
  }

  function reveal(root) {
    if (!root) { return; }
    var pick = root.querySelector(".dlg-pick");
    var file = fieldOf(root, "preset_file");
    var temporary = root.querySelector(".dlg-temp");
    var mode = modeOf(root);
    if (pick || file) {
      show(pick, mode === MODE_FILE);
      show(file, mode === MODE_FILE);
    }
    if (temporary) { show(temporary, mode === MODE_CUSTOM); }
  }

  function narrow(root) {
    if (!root) { return; }
    var pick = root.querySelector(".dlg-pick select.dlg-pick-select");
    var file = root.querySelector('select[name="preset_file"]');
    if (!pick || !file) { return; }
    var chosen = String(file.value || "");
    var groups = pick.querySelectorAll("optgroup");
    for (var index = 0; index < groups.length; index += 1) {
      var group = groups[index];
      var match = !chosen || group.getAttribute("label") === chosen;
      group.disabled = !match;
      group.style.display = match ? "" : "none";
      if (match) { continue; }
      // a hidden group that kept a selection would still POST it: clear it as well.
      var options = group.querySelectorAll("option");
      for (var inner = 0; inner < options.length; inner += 1) {
        options[inner].selected = false;
      }
    }
  }

  function prefillBase(root) {
    if (!root) { return; }
    var pick = root.querySelector('select[name="clouds.preset"]');
    var base = root.querySelector('[name="clouds.base"]');
    if (!pick || !base || !pick.options) { return; }
    var option = pick.options[pick.selectedIndex];
    var figure = option && option.getAttribute ? option.getAttribute("data-base") : "";
    //: the base belongs to the chosen preset: clearing the picker back to its empty entry clears the
    //: base too, so a stale editable figure never rides along a selection that asked for nothing.
    base.value = figure || "";
  }

  function isMode(name) {
    return MODE_NAMES.indexOf(name) !== -1;
  }

  document.addEventListener("change", function (event) {
    var target = event && event.target;
    if (!target || typeof target.getAttribute !== "function") { return; }
    var name = target.getAttribute("name");
    if (name === "clouds.preset") { prefillBase(document); }
    if (isMode(name) || name === "clouds.preset") { gate(document); }
    if (name === "weather_mode") { reveal(document); narrow(document); }
    if (name === "preset_file") { narrow(document); }
  });

  document.addEventListener("DOMContentLoaded", function () {
    reveal(document);
    narrow(document);
    gate(document);
  });
})();
