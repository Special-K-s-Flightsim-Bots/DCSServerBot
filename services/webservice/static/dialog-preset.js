/* dialog-preset.js — the launch dialog's temporary rows FOLLOW the mode controls, and a chosen cloud
 * preset pre-fills the base.
 *
 * The dialog's temporary preset is a POST form (it launches the server), so a mode change must NEVER
 * submit it. The wizard's step is a GET form and re-renders server-side; here the gate is applied on
 * the client instead: every temporary row carries the wizard's own gate as `data-gate-*` attributes
 * (`pages/presets.row_gate`), and this script shows/hides by the CURRENT mode values, so the visible
 * rows equal what the wizard's step would draw. It never calls `submit`.
 *
 * It also fills `clouds.base` with the chosen preset's own base (the picker option's `data-base`),
 * which is the dialog's twin of the wizard's plate link.
 *
 * Progressive enhancement: with JavaScript off every row stays visible and the route's own resolution
 * (one choice derives the pair; a name and the fields together are refused) is the backstop.
 */
(function () {
  "use strict";

  //: the mode controls: a change on one of these re-gates the rows. A plain value control is left alone.
  var MODE_NAMES = ["clouds", "enable_dust", "fog.mode", "halo.preset"];

  function valueOf(root, name) {
    var control = root.querySelector('[name="' + name + '"]');
    return control ? String(control.value || "") : "";
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
  });

  document.addEventListener("DOMContentLoaded", function () { gate(document); });
})();
