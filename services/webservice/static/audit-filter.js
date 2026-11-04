/* THE AUDIT PAGE'S FILTER APPLIES ITSELF — the dropdowns and the date pickers.
 *
 * A `<select>` or a date picker carries a whole decision in one gesture: once the person lets go of
 * it, there is nothing left to confirm, so waiting for the Filter button is a step that decides
 * nothing. This submits the form on `change` of those two control kinds only.
 *
 * IT DOES NOT REPLACE THE FORM. The form stays a real `method="get"` GET that the Filter button
 * submits, so with JavaScript off — or on a browser that refuses this file — the band behaves exactly
 * as it always did. The two TEXT boxes keep needing the button on purpose: a filter applied on every
 * keystroke would cut the trail the reader is still typing at.
 *
 * Loaded by `templates/audit.html` through `{% block scripts %}`, the same way the Access page loads
 * its own script and the dashboard loads the load-scope picker.
 */
(function () {
  "use strict";

  var form = document.querySelector("form[data-autofilter]");
  if (!form) { return; }

  form.addEventListener("change", function (event) {
    var control = event.target;
    if (!control || !control.matches) { return; }
    if (control.matches("select, input[type=date]")) { form.submit(); }
  });
})();
