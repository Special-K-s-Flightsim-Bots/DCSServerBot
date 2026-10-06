/* THE MISSIONS TAB'S SELECTION BAR: the live count and the count-bearing button label.
 *
 * WHY THIS EXISTS. The list IS the selection (card M9): every mission row carries a checkbox that
 * belongs to ONE form (`form="missions-form"`), and the card header carries the bar — a select-all
 * box, the count, a Clear link and TWO actions on the one selection (M11: `Set as start` and
 * `Remove selected…`). What markup alone cannot do is keep the count and the buttons' own state in
 * step with what is ticked, so this script does exactly that and nothing else: the count reads
 * "N of M selected", and — ONCE THIS SCRIPT HAS TAKEN OVER —
 *   * the REMOVAL button is DISABLED while nothing is selected and reads "Remove N missions…"
 *     (singular "Remove 1 mission…") otherwise;
 *   * the START button (`[data-sel-start]`) is DISABLED unless EXACTLY ONE mission is ticked, and,
 *     while the selection is none or several, the COUNT also states why (the sentence is the
 *     SERVER's own, read off the button's `data-sel-why` — never a second copy written here);
 *   * the select-all box is checked, unchecked or indeterminate to match.
 * BOTH BUTTONS SHIP ENABLED in the markup so their paths work with JavaScript off; the disable
 * below is this enhancement's own, applied only after it is running, and each ROUTE refuses an
 * invalid selection itself with the same honest sentence.
 *
 * IT IS AN ENHANCEMENT, AND ONLY THAT. With JavaScript off the boxes and the buttons are a plain
 * form: ticking a row and submitting sends the selection to whichever action the pressed button
 * names (the removal dialog, or the activate route via `formaction`), which still asks the
 * confirmation or serves the start write. Nothing about the form's correctness depends on this file —
 * only the count, the disabled-until-valid state and the removal button's label are missing.
 *
 * THE CONTRACT, so the markup and this file cannot drift: the card header holds ONE `[data-sel-bar]`
 * with a `[data-sel-all]` box, a `[data-sel-count]` element, the removal `[data-sel-go]` button and
 * OPTIONALLY the start `[data-sel-start]` button (present only where the viewer may activate); the
 * rows hold the `[data-sel-box]` checkboxes (the running mission's is `disabled` and is never counted
 * — it cannot be selected, and the bar's total is the number of SELECTABLE rows).
 */
(function () {
  "use strict";

  /* THE STRINGS ARE SERVED, NOT WRITTEN HERE (``/i18n/messages.js``, included by ``base.html``
     before this script). ``window.dcssbMsg`` looks a key up and swaps a ``{name}`` value in; this
     file carries no user-visible string and no translation logic. */
  function t(key, params) {
    return window.dcssbMsg ? window.dcssbMsg(key, params) : key;
  }

  function start() {
    var bar = document.querySelector("[data-sel-bar]");
    if (!bar) { return; }
    var boxes = document.querySelectorAll("[data-sel-box]");
    if (!boxes.length) { return; }
    var all = bar.querySelector("[data-sel-all]");
    var count = bar.querySelector("[data-sel-count]");
    var go = bar.querySelector("[data-sel-go]");
    var startGo = bar.querySelector("[data-sel-start]");

    /* The SELECTABLE rows only: a disabled box (the running mission) is not part of the total, so
       the count can never promise a selection the form would refuse. */
    var selectable = [];
    for (var index = 0; index < boxes.length; index += 1) {
      if (!boxes[index].disabled) { selectable.push(boxes[index]); }
    }
    var total = selectable.length;

    function ticked() {
      var found = 0;
      for (var i = 0; i < selectable.length; i += 1) {
        if (selectable[i].checked) { found += 1; }
      }
      return found;
    }

    function refresh() {
      var n = ticked();
      if (count) {
        var words = t("mission.count", {n: n, total: total});
        /* THE START REASON (M11): `Set as start` acts on the tick and is meaningful with EXACTLY ONE
           mission, so with none or several the count — the one place the selection is described —
           also says why. The wording is the SERVER's own sentence (`data-sel-why` on the button,
           rendered from the route's refusal constant), so the client reason and the server's answer
           cannot drift. */
        if (startGo && n !== 1) {
          var why = startGo.getAttribute("data-sel-why");
          if (why) { words += " \u2014 " + why; }
        }
        count.textContent = words;
      }
      if (startGo) {
        startGo.disabled = n !== 1;
      }
      if (go) {
        go.disabled = n === 0;
        if (n === 0) { go.textContent = t("mission.remove_selected"); }
        else if (n === 1) { go.textContent = t("mission.remove_one"); }
        else { go.textContent = t("mission.remove_many", {n: n}); }
      }
      if (all) {
        all.checked = total > 0 && n === total;
        all.indeterminate = n > 0 && n < total;
      }
    }

    for (var b = 0; b < selectable.length; b += 1) {
      selectable[b].addEventListener("change", refresh);
    }
    if (all) {
      all.addEventListener("change", function () {
        for (var i = 0; i < selectable.length; i += 1) { selectable[i].checked = all.checked; }
        refresh();
      });
    }
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
