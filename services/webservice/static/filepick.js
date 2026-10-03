/* THE MISSIONS TAB'S FILE CONTROL: the console's own button, and the chosen file's name.
 *
 * WHY THIS EXISTS. A raw `<input type="file">` is drawn by the BROWSER: its button wears the
 * browser's own chrome and the browser's own LANGUAGE ("Datei auswählen" on Frank's German browser),
 * which is exactly what he objected to. The console is English, so browser chrome must never be what
 * the operator reads. The markup therefore keeps the REAL input in the DOM — focusable and announced —
 * but CLIPPED out of sight (never `display:none`, never `aria-hidden`: either would take it away from
 * a keyboard and a screen reader), and puts a `<label for=...>` in front of the operator wearing the
 * tab's own button chip. This script is what the label cannot do on its own: it writes the CHOSEN
 * FILE'S NAME, in the console's own words, into the span beside the control, so the operator can see
 * what they picked before they submit — and it changes (or clears) if they pick again.
 *
 * IT IS AN ENHANCEMENT, AND ONLY THAT. With JavaScript off the input is still a real, operable file
 * field in the tab order — the operator can still choose a file and submit it; only the friendly
 * name readout is missing. Nothing about the form's correctness depends on this file.
 *
 * THE CONTRACT, so the markup and this file cannot drift: a control that opts in carries
 * `data-file-pick` and holds ONE `input[type="file"]` and ONE `[data-file-name]` span; this wires the
 * input's `change` to that span.
 */
(function () {
  "use strict";

  function wire(control) {
    var input = control.querySelector('input[type="file"]');
    var name = control.querySelector("[data-file-name]");
    if (!input || !name) { return; }

    function paint() {
      /* A chosen file's name in the console's own words; nothing at all when nothing is chosen (the
         browser's own "No file chosen" is chrome, and chrome is what this control removes). */
      name.textContent = input.files && input.files.length ? input.files[0].name : "";
    }

    input.addEventListener("change", paint);
    paint();
  }

  function start() {
    var controls = document.querySelectorAll("[data-file-pick]");
    for (var index = 0; index < controls.length; index += 1) { wire(controls[index]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
