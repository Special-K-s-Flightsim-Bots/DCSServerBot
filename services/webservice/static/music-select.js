/* THE SELECTION COUNT of the playlist editor's library browser.
 *
 * It writes how many rows are ticked into `[data-selected-count]`, using the label template the
 * server rendered into that element (`%(count)s …`), so no sentence is composed here. It is a
 * progressive enhancement: with JavaScript off the count keeps its rendered value and the boxes
 * still post their `file` field to the editor's add form.
 *
 * The boxes belong to `#mus-add-many` by their `form` attribute, so they are found by that attribute
 * rather than by being descendants of the form.
 */
(function () {
  "use strict";

  var form = document.getElementById("mus-add-many");
  var target = document.querySelector("[data-selected-count]");
  if (!form || !target) { return; }

  var template = target.getAttribute("data-selected-template") || "%(count)s";
  var boxes = document.querySelectorAll('input[form="mus-add-many"][type="checkbox"]');

  function render() {
    var chosen = 0;
    for (var i = 0; i < boxes.length; i += 1) {
      if (boxes[i].checked) { chosen += 1; }
    }
    target.textContent = template.replace("%(count)s", String(chosen));
  }

  for (var i = 0; i < boxes.length; i += 1) {
    boxes[i].addEventListener("change", render);
  }
  render();
})();
