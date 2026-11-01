/* ACCESS MATRIX — a live change bar and a live filter, ON TOP of a page that already works.
 *
 * Everything this script does is optional: the form posts the whole matrix and the server computes
 * the delta, so with scripting off a save stores the same thing. What it adds is the operator's
 * feedback — how many cells they have moved, which ones, and a filter that narrows the table as they
 * type.
 *
 * NO STRING IS COMPOSED HERE. The four sentences a change can read as (`%(role)s added`,
 * `%(role)s removed`, `managed_by granted`, `managed_by removed`) and the two bar states arrive in the
 * form's data attributes, rendered by the server in the reader's language. The line format
 * ("<capability> — <sentence>") is markup, not copy.
 *
 * The baseline of every checkbox is `data-was`, rendered from the DECLARED value — never from a
 * script-side copy of the matrix, so this file cannot disagree with what a save would compute.
 */
(function () {
  "use strict";

  var form = document.querySelector(".acc-form");
  if (!form) { return; }

  var countEl = document.getElementById("acc-count");
  var metaEl = document.getElementById("acc-meta");
  var applyEl = document.getElementById("acc-apply");
  var searchEl = document.querySelector('.acc-search input[type="search"]');
  var changesEl = document.querySelector('.acc-toggle input[type="checkbox"]');
  var rows = Array.prototype.slice.call(form.querySelectorAll("tr.acc-row[data-cap]"));
  var groups = Array.prototype.slice.call(form.querySelectorAll("tr.grp"));

  var ADDED = form.getAttribute("data-added") || "%(role)s";
  var REMOVED = form.getAttribute("data-removed") || "%(role)s";
  var MANAGER_ON = form.getAttribute("data-manager-on") || "";
  var MANAGER_OFF = form.getAttribute("data-manager-off") || "";
  var PENDING = form.getAttribute("data-pending") || "%(count)s";
  var NO_CHANGES = form.getAttribute("data-no-changes") || "";

  function sentence(template, role) {
    return template.split("%(role)s").join(role || "");
  }

  function roleOf(input) {
    var label = input.getAttribute("aria-label") || "";
    var index = label.lastIndexOf(" \u2014 ");
    return index >= 0 ? label.slice(index + 3) : "";
  }

  function isChecked(input) { return input.checked; }
  function wasChecked(input) { return input.getAttribute("data-was") === "1"; }

  function rowChanges(row) {
    var changes = [];
    var boxes = row.querySelectorAll('input[type="checkbox"]');
    for (var i = 0; i < boxes.length; i += 1) {
      var box = boxes[i];
      if (isChecked(box) === wasChecked(box)) { continue; }
      if (box.getAttribute("data-cap-kind") === "manager") {
        changes.push(isChecked(box) ? MANAGER_ON : MANAGER_OFF);
      } else {
        changes.push(sentence(isChecked(box) ? ADDED : REMOVED, roleOf(box)));
      }
    }
    return changes;
  }

  function line(capability, text) {
    var wrapper = document.createElement("span");
    wrapper.className = "acc-line";
    var cap = document.createElement("span");
    cap.className = "mono";
    cap.textContent = capability;
    wrapper.appendChild(cap);
    wrapper.appendChild(document.createTextNode(" \u2014 " + text));
    return wrapper;
  }

  function pending() {
    var out = [];
    rows.forEach(function (row) {
      rowChanges(row).forEach(function (text) { out.push([row.getAttribute("data-cap"), text]); });
    });
    return out;
  }

  function applyFilter() {
    var needle = searchEl ? searchEl.value.trim().toLowerCase() : "";
    var onlyChanged = changesEl ? changesEl.checked : false;
    rows.forEach(function (row) {
      var haystack = (row.getAttribute("data-search") || "").toLowerCase();
      var matches = (!needle || haystack.indexOf(needle) >= 0);
      if (matches && onlyChanged) { matches = rowChanges(row).length > 0; }
      row.hidden = !matches;
    });
    // a group header stays only while one of its own rows is visible
    groups.forEach(function (group) {
      var any = false;
      var node = group.nextElementSibling;
      while (node && !node.classList.contains("grp")) {
        if (node.classList.contains("acc-row") && !node.hidden) { any = true; break; }
        node = node.nextElementSibling;
      }
      group.hidden = !any;
    });
  }

  function render() {
    var changes = pending();
    if (countEl) {
      countEl.textContent = changes.length ? changes.length : "0";
      if (changes.length) { countEl.classList.add("a"); } else { countEl.classList.remove("a"); }
    }
    if (metaEl) {
      metaEl.textContent = "";
      if (!changes.length) {
        metaEl.textContent = NO_CHANGES;
      } else {
        var title = document.createElement("span");
        title.className = "acc-line";
        title.textContent = PENDING.split("%(count)s").join(String(changes.length));
        metaEl.appendChild(title);
        changes.forEach(function (pair) { metaEl.appendChild(line(pair[0], pair[1])); });
      }
    }
    if (applyEl) { applyEl.disabled = false; }
    applyFilter();
  }

  form.addEventListener("change", function (event) {
    if (event.target && event.target.tagName === "INPUT") { render(); }
  });
  if (searchEl) { searchEl.addEventListener("input", applyFilter); }
  if (changesEl) { changesEl.addEventListener("change", applyFilter); }
})();
