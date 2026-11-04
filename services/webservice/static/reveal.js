/* THE CONFIGURATION TAB'S ONE ENHANCEMENT: the password field's eye.
 *
 * A masked field the reader cannot check is a field they cannot proof-read. The eye lets them LOOK at
 * what the field holds, in the field itself, and nothing more.
 *
 * WHAT THE FIELD MAY HOLD. The field ships holding the STORED password when one is set, and nothing
 * when none is. The button is DISABLED while the field is empty — nothing to reveal — and names the
 * reason in its own `aria-label` + `title`; it goes live the moment the field holds something, and
 * inert again when the field is emptied. Both are re-evaluated on every `input`.
 *
 * WHAT IT DELIBERATELY DOES NOT DO. It never moves, copies, mirrors or stores the value: no hidden
 * input, no data attribute, no second node, no console. The ONE mutation it makes is `input.type` —
 * the value never leaves the element the reader is typing into, so there is exactly one copy of it in
 * the document and it is the one the browser masks. `autocomplete="new-password"` on the field (set in
 * the template) keeps the browser from offering a saved login here.
 *
 * IT IS AN ENHANCEMENT, AND ONLY THAT. The page is served with the button `hidden` and the field a
 * plain `type="password"` input, so with JavaScript off the form is complete and correct. That is why
 * the button is unhidden HERE rather than rendered visible.
 *
 * THE CONTRACT, so the markup and this file cannot drift: a button with `data-reveal` names, by id, the
 * field it toggles; it carries `aria-pressed` as its announced state (the visible state is the glyph
 * pair `aria-pressed` swaps, see `.cfg-reveal` in shell.css); `title`/`aria-label` are rewritten with
 * it; and it is `type="button"`, so it can never submit the form it sits in.
 */
(function () {
  "use strict";

  /* THE STRINGS ARE SERVED, NOT WRITTEN HERE. ``window.dcssbMsg`` (the served map at
     ``/i18n/messages.js``, which ``base.html`` includes before this script) looks a key up and swaps
     a value in; this file carries no user-visible string and no translation logic. The ``t`` fallback
     only guards a map that somehow did not load — it never renders a translated string. */
  function t(key) {
    return window.dcssbMsg ? window.dcssbMsg(key) : key;
  }

  var SHOW = t("reveal.show");
  var HIDE = t("reveal.hide");
  /* What the inert state says, so a press that cannot act is announced as one that cannot act. */
  var NOTHING_TO_SHOW = t("reveal.nothing");

  function wire(button) {
    var field = document.getElementById(button.getAttribute("data-reveal"));
    if (!field) { return; }

    /* Revealable means "there is something here to look at". */
    function revealable() {
      return field.value !== "";
    }

    function state() {
      return button.getAttribute("aria-pressed") === "true";
    }

    function paint() {
      var words;
      if (!revealable()) {
        /* A control that goes inert while the field is still unmasked would leave the next thing
           typed into it in the clear: restore the masking with the announced state. */
        field.type = "password";
        button.setAttribute("aria-pressed", "false");
        words = NOTHING_TO_SHOW;
        button.disabled = true;
      } else {
        words = state() ? HIDE : SHOW;
        /* the field DISABLES with the page (a server that cannot take a change) */
        button.disabled = Boolean(field.disabled);
      }
      button.setAttribute("aria-label", words);
      button.setAttribute("title", words);
    }

    button.addEventListener("click", function () {
      /* A disabled button fires no click at all; this is the belt to that brace. */
      if (!revealable()) { return; }
      var shown = !state();
      /* The ONE mutation of the secret: the masking of the field the reader is already typing into. */
      field.type = shown ? "text" : "password";
      button.setAttribute("aria-pressed", shown ? "true" : "false");
      paint();
    });

    field.addEventListener("input", paint);

    /* The field DISABLES with the page (a server that cannot take a change), so the control that
       belongs to it is disabled with it — the disabled attribute is rendered server-side, and this
       only keeps the two in step when the form is drawn enabled. */
    if (field.disabled) {
      button.disabled = true;
    } else {
      button.hidden = false;
    }
    paint();
  }

  function start() {
    var buttons = document.querySelectorAll("[data-reveal]");
    for (var index = 0; index < buttons.length; index += 1) { wire(buttons[index]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
