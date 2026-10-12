/* The generated config page's Add / Remove controls — progressive enhancement only.
 *
 * The page is a real `method="post"` form without this script: a scalar list is a set of inputs all
 * named the same (`f.<path>`), and a sequence of maps names its inputs `f.<path>.<index>.<rel>` with
 * a companion `f.<path>.__n`. The script only adds and removes rows; the SERVER reads the result
 * back through the same tree (`configgen.apply_form`), so no field name is invented here.
 *
 * A LIST row removed to zero leaves ONE empty input on purpose: an empty scalar list posts an empty
 * value, which the server reads as "the list is now empty" — removing the input entirely would post
 * nothing and read as "unchanged".
 *
 * SEEDING AN EMPTY CONTAINER. Every container ships a `<template>` holding ONE empty row
 * (`settings.html`); a container with zero rows (an empty `plugins:` list is schema-legal) seeds its
 * first row from that template, so "+ Add" is never a control that does nothing. A REGEX container is
 * ALWAYS seeded from its template, even when it already has rows: a regex entry's fields are keyed by
 * a free-text key the operator types per row, so cloning a filled row would carry the old entry's key
 * into a row meant to get a new one.
 *
 * A REGEX row's key. An EXISTING entry's key input is `readonly` — its fields are already named by
 * that key, and the page does not implement a rename, so the control does not pretend to accept one.
 * A NEWLY added row's key is editable: as the operator types it, this script rewrites the row's
 * field names from the `__KEY__` placeholder template to the typed key (`f.<base>.__KEY__.<rel>` ->
 * `f.<base>.<key>.<rel>`), so the posted names are the ones the server reads back.
 *
 * A key may contain a `.` (or a `%`), which is the path separator (or its escape) in a field name, so
 * the typed key is ESCAPED into the name with `encodeToken` — the EXACT inverse of the server's
 * `configgen.decode_token` (`%` -> `%25` first, then `.` -> `%2E`). Only the key token is escaped: the
 * base path and the relative sub-field names are schema keys and carry neither character. The rename
 * matches the PREVIOUS (already escaped) key and joins in the escaped one, so a second keystroke
 * renames from the current name, not from a stale key that would leave a half-renamed row.
 *
 * THE `+ ADD` PICKER. The editor shows only what matters — a schema's MANDATORY
 * fields and the ones the file already carries — so every OPTIONAL, ABSENT field or block is offered
 * by a `+ Add` picker instead of being rendered. Picking a choice CLONES the empty control the
 * container's `<template data-cfg-opt>` ships, inserts it just above the picker, and hides the used
 * choice. The added control posts its own `f.<path>` field like any other: NOTHING is invented here.
 * Its own trashcan removes it instantly (nothing is in the file yet) and shows the choice again.
 *
 * A SHOWN, optional field that CARRIES A VALUE has a different trashcan: the first press only opens an
 * inline warning, and CONFIRMING it appends the field's ENCODED path (`configgen.encode_path`) to the
 * form's removal holder (`f.<configgen.REMOVED_COMPANION>`) and hides the control. The SERVER still
 * refuses the write unless it also presents the acknowledgement the render minted
 * (`configgen.REMOVE_ACK_COMPANION`, checked in `pages/settings`), so the warning is a CONTROL, not a
 * screen. The removal is applied LAST by `configgen.apply_form`, so a removal always wins over the
 * same save's posted leaves.
 */
(function () {
  "use strict";

  var KEY_PLACEHOLDER = "__KEY__";

  /* A page-wide counter that gives each picker choice a stable id, so a control the picker added can
   * name the choice it came from and show it again when the control is removed. Not a field name and
   * not a write path — only a handle between the two elements. */
  var pickerCounter = 0;

  /* The path-token escapes: the SAME two the server's `configgen.encode_token` applies, in the same
   * order (`%` before `.`). Kept here verbatim so the JS and the Python cannot drift. */
  function encodeToken(value) {
    return String(value).replace(/%/g, "%25").replace(/\./g, "%2E");
  }

  function rowSeed(container) {
    var template = container.querySelector("template");
    if (container.hasAttribute("data-cfg-regex") && template) {
      return template.content.firstElementChild.cloneNode(true);
    }
    var rows = container.querySelectorAll("[data-cfg-row]");
    if (rows.length) { return rows[rows.length - 1].cloneNode(true); }
    return template ? template.content.firstElementChild.cloneNode(true) : null;
  }

  function clearRow(row) {
    row.querySelectorAll("input, textarea, select").forEach(function (field) {
      if (field.type === "checkbox" || field.type === "radio") { field.checked = false; }
      else if (field.tagName === "SELECT") { field.selectedIndex = 0; }
      else { field.value = ""; }
    });
    row.removeAttribute("data-cfg-prevkey");
  }

  function renumber(container, prefix) {
    var rows = container.querySelectorAll("[data-cfg-row]");
    rows.forEach(function (row, index) {
      row.setAttribute("data-index", index);
      row.querySelectorAll("input, textarea, select").forEach(function (field) {
        if (!field.name) { return; }
        // f.<prefix>.<number>.<rest>  ->  f.<prefix>.<index>.<rest>
        field.name = field.name.replace(
          new RegExp("^f\\." + prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\.\\d+\\."),
          "f." + prefix + "." + index + ".");
      });
    });
    var counter = container.querySelector("[data-cfg-count]");
    if (counter) { counter.value = String(rows.length); }
  }

  /* Rewrite a NEW regex row's field names from the placeholder to the key just typed. The previous
   * key is remembered on the row so a second keystroke renames from the current key, not from the
   * placeholder again. An empty key leaves the names on the placeholder: the row then posts nothing
   * the server recognises, which is the same as not having typed a key yet. The key is ESCAPED into
   * the name (see `encodeToken`): the stored `data-cfg-prevkey` stays the operator's RAW key, but the
   * NAME is renamed from its escaped form to the newly escaped one. */
  function renameRegexRow(row, key) {
    var prev = row.getAttribute("data-cfg-prevkey");
    if (prev === key) { return; }
    var prevToken = prev === null ? KEY_PLACEHOLDER : encodeToken(prev);
    var nextToken = encodeToken(key);
    row.querySelectorAll("input, textarea, select").forEach(function (field) {
      if (!field.name) { return; }
      field.name = field.name.split("." + prevToken + ".").join("." + nextToken + ".");
    });
    row.setAttribute("data-cfg-prevkey", key);
  }

  document.addEventListener("input", function (event) {
    var target = event.target;
    if (!target || !target.matches || !target.matches("[data-cfg-regexkey]")) { return; }
    var row = target.closest("[data-cfg-row]");
    if (!row) { return; }
    var key = String(target.value || "").trim();
    if (!key) { return; }
    renameRegexRow(row, key);
  });

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) { return; }

    /* THE "+ ADD" PICKER. Picking a choice clones the control the container's template ships EMPTY,
     * inserts it just ABOVE the picker (so the form reads: the fields that are there, the ones just
     * added, then the door to add more), hides the used choice (it cannot be added twice) and tags the
     * added control with the choice's own id so removing it can bring the choice back. NOTHING is
     * written here: the added control simply posts its own `f.<path>` field like any other, and the
     * SERVER resolves it against the same schema tree. No field name is invented. */
    var pick = target.closest("[data-cfg-pick]");
    if (pick) {
      event.preventDefault();
      var item = pick.closest("[data-cfg-pitem]");
      var picker = pick.closest("[data-cfg-picker]");
      if (!item || !picker) { return; }
      var optionTemplate = item.querySelector("template[data-cfg-opt]");
      if (!optionTemplate || !optionTemplate.content || !optionTemplate.content.firstElementChild) { return; }
      var added = optionTemplate.content.firstElementChild.cloneNode(true);
      if (!item.getAttribute("data-cfg-pid")) {
        pickerCounter += 1;
        item.setAttribute("data-cfg-pid", "p" + pickerCounter);
      }
      added.setAttribute("data-cfg-from", item.getAttribute("data-cfg-pid"));
      picker.parentNode.insertBefore(added, picker);
      item.hidden = true;
      var focusable = added.querySelector("input, textarea, select");
      if (focusable) { focusable.focus(); }
      return;
    }

    /* REMOVING A FIELD THE PICKER JUST ADDED. Nothing is in the file yet, so this is INSTANT and
     * needs no acknowledgement: drop the control and show the choice again. */
    var removeAdded = target.closest("[data-cfg-remove-added]");
    if (removeAdded) {
      event.preventDefault();
      var addedNode = removeAdded.closest("[data-cfg-added]");
      if (!addedNode) { return; }
      var form = addedNode.closest("form");
      var from = addedNode.getAttribute("data-cfg-from");
      if (form && from) {
        var choice = form.querySelector('[data-cfg-pid="' + from + '"]');
        if (choice) { choice.hidden = false; }
      }
      addedNode.remove();
      return;
    }

    /* REMOVING A FIELD THAT CARRIES A VALUE. The name and the (already written) value would be
     * DISCARDED, so the trashcan never removes on the first press: it opens the inline warning. Only
     * confirming it names the field's path in the form's removal holder and hides the field. The
     * SERVER still refuses the write unless it carries the acknowledgement the render minted, so the
     * warning is a CONTROL and not a screen. */
    var removeStart = target.closest("[data-cfg-rm-start]");
    if (removeStart) {
      event.preventDefault();
      var box = removeStart.closest("[data-cfg-setrm]");
      var warning = box && box.querySelector("[data-cfg-rmwarn]");
      if (!box || !warning) { return; }
      warning.hidden = false;
      removeStart.hidden = true;
      return;
    }
    var removeCancel = target.closest("[data-cfg-rm-cancel]");
    if (removeCancel) {
      event.preventDefault();
      var box2 = removeCancel.closest("[data-cfg-setrm]");
      var warning2 = box2 && box2.querySelector("[data-cfg-rmwarn]");
      if (!box2 || !warning2) { return; }
      warning2.hidden = true;
      var start2 = box2.querySelector("[data-cfg-rm-start]");
      if (start2) { start2.hidden = false; }
      return;
    }
    var removeConfirm = target.closest("[data-cfg-rm-confirm]");
    if (removeConfirm) {
      event.preventDefault();
      var box3 = removeConfirm.closest("[data-cfg-setrm]");
      var field = box3 && box3.closest("[data-cfg-field]");
      var owner = box3 && box3.closest("form");
      if (!box3 || !field || !owner) { return; }
      var holder = owner.querySelector("[data-cfg-rmval]");
      if (!holder) { return; }
      var path = box3.getAttribute("data-cfg-rpath");
      holder.value = holder.value ? holder.value + "\n" + path : path;
      field.remove();
      return;
    }

    var add = target.closest("[data-cfg-add]");
    if (add) {
      event.preventDefault();
      var container = add.closest("[data-cfg-list], [data-cfg-seq], [data-cfg-regex]");
      if (!container) { return; }
      if (container.hasAttribute("data-cfg-sections")) {
        // A FIXED-ALTERNATIVE container: the section NAME is chosen from the schema's own list (the
        // picker), never typed. Clone the empty section, stamp the chosen name onto its display and
        // its `__key` control, rename its sub-fields from the `__KEY__` placeholder to the name, and
        // drop the used option so a second section can only be a DIFFERENT allowed name.
        var pick = container.querySelector("[data-cfg-sectionpick]");
        var name = pick ? String(pick.value || "").trim() : "";
        if (!name) { return; }
        var template = container.querySelector("template");
        if (!template) { return; }
        var seed = template.content.firstElementChild.cloneNode(true);
        var sectionName = seed.querySelector("[data-cfg-sectionname]");
        if (sectionName) { sectionName.textContent = name; }
        var sectionKey = seed.querySelector("[data-cfg-sectionkey]");
        if (sectionKey) { sectionKey.value = name; }
        renameRegexRow(seed, name);
        var bar = container.querySelector(".cfg-addbar");
        container.insertBefore(seed, bar || null);
        if (pick) {
          var used = null;
          for (var index = 0; index < pick.options.length; index += 1) {
            if (pick.options[index].value === name) { used = pick.options[index]; break; }
          }
          if (used) { pick.removeChild(used); }
          if (!pick.options.length && bar) { bar.hidden = true; }
        }
        return;
      }
      var seed = rowSeed(container);
      if (!seed) { return; }
      clearRow(seed);
      var prefix = container.getAttribute("data-cfg-list") || container.getAttribute("data-cfg-seq");
      if (container.hasAttribute("data-cfg-seq") && prefix) {
        seed.setAttribute("data-index", container.querySelectorAll("[data-cfg-row]").length);
        container.insertBefore(seed, add);
        renumber(container, prefix);
      } else if (container.hasAttribute("data-cfg-list") && prefix) {
        container.insertBefore(seed, add);
      } else {
        // a regex entry: its key is a free-text field of its own — the row's names stay as typed
        container.insertBefore(seed, add);
      }
      var first = seed.querySelector("input, textarea, select");
      if (first) { first.focus(); }
      return;
    }

    var remove = target.closest("[data-cfg-remove]");
    if (remove) {
      event.preventDefault();
      var box = remove.closest("[data-cfg-row]");
      var owner = remove.closest("[data-cfg-list], [data-cfg-seq], [data-cfg-regex]");
      if (!box || !owner) { return; }
      if (owner.hasAttribute("data-cfg-list")) {
        // keep one empty row: removing the LAST input would post nothing ("unchanged")
        var rows = owner.querySelectorAll("[data-cfg-row]");
        if (rows.length <= 1) { clearRow(box); return; }
        box.remove();
        return;
      }
      box.remove();
      var seqPrefix = owner.getAttribute("data-cfg-seq");
      if (seqPrefix) { renumber(owner, seqPrefix); }
    }
  });
})();
