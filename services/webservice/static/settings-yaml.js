/* THE SETTINGS PAGE'S EXPERT MODE — the YAML panel becomes an editor, validated on every change.
 *
 * WHAT IT ADDS, AND WHAT IT DOES NOT. The page ships the file re-serialised as YAML in a read-only
 * `<pre>`; with JavaScript off that is exactly what an operator gets, and this script is never the
 * reason a page works. It adds ONE thing: the visible `Expert Mode` toggle turns that panel into an
 * editor (`CodeMirror`, vendored — see `static/vendor/codemirror/SOURCE.txt`) with YAML syntax
 * highlighting, and every change is checked against the BOT'S OWN validator before the document can
 * be saved.
 *
 * THE CHECK IS THE SERVER'S. On each (debounced) change the editor POSTs its text to the validate
 * route and renders the JSON verdict it gets back: the offending LINE and the BLOCK it belongs to
 * are marked in the editor, and the summary line names the FIRST error in words — the SERVER composes
 * that sentence (it knows the line and the block and renders it in the request's language), so the
 * editor and the bot can never disagree about what is wrong. Nothing is validated in the browser.
 *
 * THE SAVE IS THE SERVER'S TOO. The toggle keeps a hidden companion (`_expert`) at "1" and the
 * editor's text is carried by a named control (`_yaml`), so a save posts the edited document and the
 * route writes it through the SAME atomic primitive the form uses — refusing an invalid document with
 * the reason and writing nothing. This script only stops an OBVIOUSLY-unverified submit from leaving
 * early (see the submit guard); the refusal itself is the server's.
 *
 * THE CONTRACT with `settings.html`: the panel is `[data-cfg-yaml]` and carries the validate URL; the
 * toggle is `[data-cfg-expert-toggle]`; the read-only panel is `[data-cfg-yaml-readonly]`; the editor
 * host is `[data-cfg-yaml-editor]`; the status line is `[data-cfg-yaml-status]`; the badge is
 * `[data-cfg-yaml-badge]`; and INSIDE the form live `[data-cfg-expert]` (the companion) and
 * `[data-cfg-yaml-text]` (the text carrier) beside the `_csrf_token` field.
 */
(function () {
  "use strict";

  /* How long after the last keystroke the check runs. Long enough that ordinary typing is one
   * request, short enough that the mark appears while the operator is still looking at the line. */
  var DEBOUNCE_MS = 350;

  /* The strings are SERVED, not written here (``/i18n/messages.js``): this file looks a key up and
   * swaps. The ONE sentence that names an error comes from the server's JSON, verbatim. */
  function t(key) {
    return window.dcssbMsg ? window.dcssbMsg(key) : key;
  }

  function start() {
    var aside = document.querySelector("[data-cfg-yaml]");
    if (!aside) { return; }
    var toggle = aside.querySelector("[data-cfg-expert-toggle]");
    var readonly = aside.querySelector("[data-cfg-yaml-readonly]");
    var editorHost = aside.querySelector("[data-cfg-yaml-editor]");
    var status = aside.querySelector("[data-cfg-yaml-status]");
    var badge = aside.querySelector("[data-cfg-yaml-badge]");
    var form = document.querySelector("form[data-async]") || document.querySelector(".cfg-form");
    if (!toggle || !readonly || !editorHost || !status || !form) { return; }
    if (typeof CodeMirror === "undefined") { return; }

    var expertField = form.querySelector("[data-cfg-expert]");
    var textField = form.querySelector("[data-cfg-yaml-text]");
    var csrfField = form.querySelector("[name='_csrf_token']");
    if (!expertField || !textField) { return; }

    var validateUrl = aside.getAttribute("data-validate-url") || "";
    var readonlyBadge = badge ? badge.textContent : "";
    var editor = null;
    var timer = null;
    var dirty = false;         // the editor changed since the last verdict came back
    var verdict = null;        // the last verdict the server returned, or null

    function setStatus(text, state) {
      status.textContent = text || "";
      if (state) { status.setAttribute("data-state", state); }
      else { status.removeAttribute("data-state"); }
    }

    function clearMarks() {
      if (!editor) { return; }
      (editor._cfgMarks || []).forEach(function (mark) {
        editor.removeLineClass(mark.line, "background", mark.cls);
      });
      editor._cfgMarks = [];
    }

    /* Mark the offending LINE and the BLOCK it belongs to, both as background line classes the
     * stylesheet colours. The block span is the server's (`block_line`..`block_end_line`). */
    function mark(found) {
      clearMarks();
      if (!editor || !found) { return; }
      var marks = [];
      function add(line, cls) {
        if (!line || line < 1 || line > editor.lineCount()) { return; }
        editor.addLineClass(line - 1, "background", cls);
        marks.push({ line: line - 1, cls: cls });
      }
      if (found.block_line && found.block_end_line) {
        for (var n = found.block_line; n <= found.block_end_line; n += 1) { add(n, "cm-yaml-block"); }
      }
      if (found.line) { add(found.line, "cm-yaml-error"); }
      editor._cfgMarks = marks;
    }

    function apply(found) {
      verdict = found;
      dirty = false;
      if (found && found.ok) {
        setStatus(t("yaml.ok"), "ok");
      } else {
        setStatus((found && found.message) || "", "bad");
      }
      mark(found);
      /* THE CARET IS NEVER MOVED BY A CHECK. A verdict arrives while the operator
       * is still typing — that is the whole point of the debounce — so calling `editor.setCursor()`
       * here (the old behaviour, to "jump to the error") hijacked the caret mid-word: the rest of the
       * line then landed at the offending column and the YAML was corrupted by the act of fixing it.
       * The error is NAMED in the status line and MARKED in the gutter (`.cm-yaml-error` /
       * `.cm-yaml-block`); the operator moves the caret, never a background check. */
    }

    function check() {
      if (!editor) { return; }
      setStatus(t("yaml.checking"), "wait");
      var body = new URLSearchParams();
      if (csrfField) { body.append(csrfField.name, csrfField.value); }
      body.append(textField.name, editor.getValue());
      fetch(validateUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "accept": "application/json" },
        credentials: "same-origin",
        body: body.toString(),
      }).then(function (response) {
        return response.json();
      }).then(function (found) {
        apply(found);
      }).catch(function () {
        /* a network hiccup: no verdict, and no claim. The save still validates server-side. */
        setStatus("", "");
      });
    }

    function onChange() {
      dirty = true;
      textField.value = editor.getValue();     // the carrier is current even before the check lands
      if (timer) { clearTimeout(timer); }
      timer = setTimeout(check, DEBOUNCE_MS);
    }

    function enable() {
      if (!editor) {
        editorHost.hidden = false;
        readonly.hidden = true;
        editor = CodeMirror(editorHost, {
          value: readonly.textContent,
          mode: "yaml",
          lineNumbers: true,
          lineWrapping: false,
          indentUnit: 2,
          tabSize: 2,
        });
        editor.on("change", onChange);
        editor._cfgMarks = [];
      }
      textField.value = editor.getValue();
      expertField.value = "1";
      if (badge) { badge.textContent = t("yaml.editing"); }
      check();
    }

    function disable() {
      if (editor) {
        textField.value = editor.getValue();
        readonly.textContent = editor.getValue();
        clearMarks();
        editorHost.hidden = true;
        readonly.hidden = false;
        /* DROP THE EDITOR. Expert Mode OFF must leave the panel EXACTLY as it arrived: the submit
         * guard below keys off `editor` being non-null, so keeping it here would keep intercepting
         * the plain form's submit (holding an ordinary save and firing a spurious check) after the
         * operator had merely looked at Expert Mode and turned it back off. Re-enabling builds a
         * fresh editor from the read-only panel, which now holds the last text. */
        editor = null;
      }
      textField.value = "";
      expertField.value = "0";
      if (badge) { badge.textContent = readonlyBadge; }
      setStatus("", "");
      dirty = false;
      verdict = null;
    }

    toggle.addEventListener("change", function () {
      if (toggle.checked) { enable(); } else { disable(); }
    });

    /* THE SUBMIT GUARD. The server refuses an invalid document; this only stops an OBVIOUSLY
     * unverified one from leaving early. In the CAPTURE phase, so it runs before the console's own
     * submit interceptor (`submit.js`, a bubble listener) and can veto the submit outright: with the
     * editor open, a submit whose text has changed since the last verdict (or whose last verdict was
     * a refusal) is held back and re-checked instead of posted. A submit with a clean, valid verdict
     * is left entirely alone. */
    form.addEventListener("submit", function (event) {
      if (!editor) { return; }                  // expert off: the plain form is untouched
      if (dirty || !verdict) {
        event.preventDefault();
        event.stopPropagation();
        if (event.stopImmediatePropagation) { event.stopImmediatePropagation(); }
        check();
        return;
      }
      if (!verdict.ok) {
        event.preventDefault();
        event.stopPropagation();
        if (event.stopImmediatePropagation) { event.stopImmediatePropagation(); }
        setStatus(verdict.message || "", "bad");
      }
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
