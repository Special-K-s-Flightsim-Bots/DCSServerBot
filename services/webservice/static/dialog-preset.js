/* dialog-preset.js — the launch dialog's weather blocks FOLLOW the mode, the preset list is
 * filtered by file, and the ticked presets are listed as chips.
 *
 * The dialog's form POSTS the launch, so a mode change must NEVER submit it. The wizard's step is a
 * GET form and re-renders server-side; here the gate is applied on the client instead, in three
 * parts:
 *
 * 1. THE MODE (`weather_mode`): the three radios are the one weather choice, so the blocks that
 *    belong to a mode are shown only while it is chosen — the preset filter, the preset pick and the
 *    preset-file filter for `file`, the temporary weather fields for `custom`, and nothing extra for
 *    `leave`. Every temporary row additionally carries the wizard's own gate as `data-gate-*`
 *    attributes (`pages/presets.row_gate`), and this script shows/hides them by the CURRENT mode
 *    values, so the visible rows equal what the wizard's step would draw.
 * 2. THE FILE FILTER (`preset_file`): the pick's `<optgroup>`s are the node's preset FILES and every
 *    group is drawn from the start. Choosing a file HIDES the other groups and does nothing else —
 *    it never disables a group and never clears a selection, because a ticked preset must survive a
 *    filter change. The filter is a view, not a scope: the tick's own value carries the file it came
 *    from, so a tick made under a hidden group still posts and still resolves against its group.
 * 3. THE CHOSEN BLOCK: every ticked entry, in the order the select draws it (the files' own config
 *    order, never the click order), as a chip naming its file; a chip's Remove button unticks it.
 *
 * It also fills `clouds.base` with the chosen preset's own base (the picker option's `data-base`),
 * which is the dialog's twin of the wizard's plate link.
 *
 * Progressive enhancement: with JavaScript off every block stays visible, EVERY file's presets stay
 * visible under their own heading, the filter does nothing, and every ticked entry posts its own
 * value (a grouped entry carries its file). A node carrying a single preset file draws no groups and
 * no filter, so there is nothing here to narrow.
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

  //: hide the groups the filter leaves out. A hidden group keeps its ticks: the filter is a view and
  //: the entry's own value carries its file, so nothing about it has to be unselected or disabled.
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
      group.style.display = match ? "" : "none";
    }
  }

  //: the file a pick entry came from: its own `data-file`, else its group's label.
  function fileOfOption(option) {
    var own = option.getAttribute("data-file");
    if (own) { return String(own); }
    var group = option.parentNode;
    if (group && group.tagName === "OPTGROUP") { return String(group.getAttribute("label") || ""); }
    return "";
  }

  function chip(text, className) {
    var span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    return span;
  }

  //: paint the chosen block from the select, in the select's own order (the files' config order).
  function chosen(root) {
    if (!root) { return; }
    var pick = root.querySelector(".dlg-pick select.dlg-pick-select");
    var box = root.querySelector(".dlg-pick [data-chosen]");
    if (!pick || !box) { return; }
    var list = box.querySelector("[data-chosen-chips]");
    var counter = box.querySelector("[data-chosen-count]");
    if (!list) { return; }
    while (list.firstChild) { list.removeChild(list.firstChild); }
    var count = 0;
    for (var index = 0; index < pick.options.length; index += 1) {
      var option = pick.options[index];
      if (!option.selected) { continue; }
      count += 1;
      var item = document.createElement("li");
      item.className = "dlg-chip";
      item.appendChild(chip(String(option.textContent || ""), "dlg-chip-name"));
      var file = fileOfOption(option);
      if (file) { item.appendChild(chip(file, "dlg-chip-file")); }
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "dlg-chip-x";
      remove.setAttribute("data-chosen-remove", String(option.value));
      remove.setAttribute("aria-label", "Remove");
      remove.textContent = "×";
      item.appendChild(remove);
      list.appendChild(item);
    }
    if (counter) { counter.textContent = String(count); }
    box.hidden = count === 0;
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
    if (name === "weather_mode") { reveal(document); narrow(document); chosen(document); }
    if (name === "preset_file") { narrow(document); chosen(document); }
    if (name === "preset") { chosen(document); }
  });

  //: a chip's Remove button unticks the entry it names; the filter's view is left alone.
  document.addEventListener("click", function (event) {
    var target = event && event.target;
    if (!target || typeof target.closest !== "function") { return; }
    var remove = target.closest("[data-chosen-remove]");
    if (!remove) { return; }
    event.preventDefault();
    var pick = document.querySelector(".dlg-pick select.dlg-pick-select");
    if (!pick) { return; }
    var value = remove.getAttribute("data-chosen-remove");
    for (var index = 0; index < pick.options.length; index += 1) {
      if (String(pick.options[index].value) === value) {
        pick.options[index].selected = false;
      }
    }
    chosen(document);
  });

  document.addEventListener("DOMContentLoaded", function () {
    reveal(document);
    narrow(document);
    chosen(document);
    gate(document);
  });
})();
