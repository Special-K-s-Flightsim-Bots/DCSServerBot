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
 *
 * THE TOGGLE-LESS MODE — a page that edits ONE block rather than a whole file (the preset editor) has
 * no read-only view to enhance and no Expert Mode to offer, so it declares its carrier as the source:
 * `data-cfg-yaml-source="textarea"` on the panel. There the `<textarea>[data-cfg-yaml-text]` holds the
 * document, is EDITABLE with JavaScript off, and is replaced by the editor on load (the textarea stays
 * in the form, hidden, so the value still posts). The status line, the validate call and the submit
 * guard are the SAME ones the toggle uses — one editor behaviour in the codebase.
 *
 * THE COMPLETION POPUP — a panel carrying `data-hints-url` gets schema-driven completion: that URL is
 * fetched ONCE per editor and the schema's tree is kept in memory; `Ctrl+Space` opens the popup, and it
 * also opens by itself where a KEY would be written. The rows, their labels, the `required` and `set`
 * marks and the examples all come from the payload — this file carries NO key list, NO option list and
 * NO example text of its own. A panel without the attribute, a refused or failed fetch, or an empty
 * payload leaves the editor exactly as it was: no popup, no message, no console noise. The widget, its
 * keys and its insertion are CodeMirror's `show-hint` (vendored beside the core, see `SOURCE.txt`);
 * the popup's strings are inserted and displayed as TEXT ONLY, so schema prose can never become markup.
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

  /* The panel value that declares the textarea as the SOURCE (see the header): the toggle-less mode. */
  var PLAIN_SOURCE = "textarea";

  /* THE HINT PAYLOAD'S OWN VOCABULARY (see `services/webservice/config_hints.py`): one `*` segment for
   * one element of a sequence, and the kind that names a container whose keys the DOCUMENT invents (a
   * `regex;` block). */
  var ELEMENT_SEGMENT = "*";
  var REGEX_KIND = "regex";

  /* `completeSingle: false` — a list holding ONE row is not inserted on its own; the popup opens and
   * the operator picks. Two ways to pick: Enter/Tab on the highlighted row, or a SINGLE CLICK on it
   * (the addon's own `completeOnSingleClick` default, kept: a click is a pick, not an accident, and a
   * double click is not required). Esc, blur and a new completion close the widget; it binds only its
   * own keys while it is open. */
  var HINT_OPTIONS = { completeSingle: false };

  function start() {
    var aside = document.querySelector("[data-cfg-yaml]");
    if (!aside) { return; }
    var toggle = aside.querySelector("[data-cfg-expert-toggle]");
    var readonly = aside.querySelector("[data-cfg-yaml-readonly]");
    var editorHost = aside.querySelector("[data-cfg-yaml-editor]");
    var status = aside.querySelector("[data-cfg-yaml-status]");
    var badge = aside.querySelector("[data-cfg-yaml-badge]");
    /* THE FORM THE CARRIERS LIVE IN. A page that owns the panel inside its own form (the presets
     * wizard, the preset editor, the Site settings page — whose cards each own a form of their own)
     * answers through the panel's nearest ancestor form; the settings pages carry a marker
     * (`data-async` / `.cfg-form`) and their aside sits OUTSIDE the form, so `closest` is null there
     * and the two markers are the answer. `closest` is tried FIRST because a page with several
     * marked forms would otherwise hand the panel a sibling's carriers. */
    var form = (aside.closest ? aside.closest("form") : null)
               || document.querySelector("form[data-async]") || document.querySelector(".cfg-form");
    if (!editorHost || !status || !form) { return; }
    if (typeof CodeMirror === "undefined") { return; }

    var expertField = form.querySelector("[data-cfg-expert]");
    var textField = form.querySelector("[data-cfg-yaml-text]");
    var csrfField = form.querySelector("[name='_csrf_token']");
    if (!textField) { return; }
    /* THE TWO MODES. With a toggle the page offers a read-only view the operator may turn into an
     * editor; without one the carrier IS the source and the editor is simply the better control for
     * it. A panel that declares neither shape is left alone rather than guessed at. */
    var plain = aside.getAttribute("data-cfg-yaml-source") === PLAIN_SOURCE;
    if (!plain && (!toggle || !readonly || !expertField)) { return; }

    var validateUrl = aside.getAttribute("data-validate-url") || "";
    var hintsUrl = aside.getAttribute("data-hints-url") || "";
    var hintPaths = null;      // the schema tree's map of dotted container path -> entry, or null
    var hintsRequested = false;
    var autoOpenedAt = "";     // the position the popup was last offered at by itself
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
      maybeOpenHint();
    }

    /* ------------------------------------------------------------------ the completion popup
     *
     * ONE PREFETCH. The panel names the payload (`data-hints-url`); the tree is fetched once and kept
     * for the editor's lifetime. Every failure — the attribute absent, a non-200, a body without a
     * `paths` map, no entries, a network error — ends the same way: `hintPaths` stays null and no
     * popup ever opens, with nothing said anywhere (the editor is not a place for error banners).
     */
    function prefetch() {
      if (!hintsUrl || hintsRequested || typeof fetch !== "function") { return; }
      hintsRequested = true;
      fetch(hintsUrl, {
        headers: { "accept": "application/json" },
        credentials: "same-origin",
      }).then(function (response) {
        return response && response.ok ? response.json() : null;
      }).then(function (found) {
        if (!found || !found.paths || typeof found.paths !== "object") { return; }
        if (!Object.keys(found.paths).length) { return; }
        hintPaths = found.paths;
      }).catch(function () {
        /* no payload: completion simply never opens. Nothing is reported and nothing is claimed. */
      });
    }

    function indentOf(text) {
      var found = /^\s*/.exec(text);
      return found ? found[0].length : 0;
    }

    /* A line without its trailing comment — what the DOCUMENT says, for path and presence reads. A
     * `#` inside a quoted scalar is CONTENT, not a comment (`desc: "x # y"`), so the scan tracks the
     * quote it is in: single quotes close on `''`, double quotes honour `\"`. A quote opens a scalar
     * only where a scalar can start (start of line, or after `:` `-` `,` `[` `{`), so an apostrophe
     * inside a plain value (`don't`) is not one. An unterminated quote runs to the line's end, which
     * is what a half-typed value is. */
    function visible(line) {
      var quote = "";
      var previous = "";
      for (var n = 0; n < line.length; n += 1) {
        var ch = line.charAt(n);
        if (quote) {
          if (quote === '"' && ch === "\\") { n += 1; continue; }
          if (ch === quote) {
            if (quote === "'" && line.charAt(n + 1) === "'") { n += 1; continue; }
            quote = "";
          }
          continue;
        }
        if (ch === "#") { return line.slice(0, n); }
        if ((ch === '"' || ch === "'") &&
            (!previous || ":-,{[".indexOf(previous) >= 0)) { quote = ch; continue; }
        if (ch !== " " && ch !== "\t") { previous = ch; }
      }
      return line;
    }

    /* THE ENCLOSING SEGMENTS of the line *line*, in order: every mapping key above it and `*` for a
     * sequence item, each with the column it was written at. A key written after a `- ` belongs to
     * that element, so it sits two columns right of the dash. */
    function segmentsAbove(line) {
      var stack = [];
      for (var n = 0; n < line; n += 1) {
        var text = visible(editor.getLine(n));
        if (!text.trim()) { continue; }
        var indent = indentOf(text);
        while (stack.length && stack[stack.length - 1].indent >= indent) { stack.pop(); }
        var item = /^\s*-(?:\s+(.*))?$/.exec(text);
        if (item) {
          stack.push({ seg: ELEMENT_SEGMENT, indent: indent });
          var inItem = /^([^\s#][^:]*):(\s|$)/.exec((item[1] || "").trim());
          if (inItem) { stack.push({ seg: inItem[1].trim(), indent: indent + 2 }); }
          continue;
        }
        var key = /^\s*([^\s#][^:]*):(\s|$)/.exec(text);
        if (key) { stack.push({ seg: key[1].trim(), indent: indent }); }
      }
      return stack;
    }

    /* The segment names whose column is LEFT of *indent* — the containers a caret at *indent* is in. */
    function segmentsFor(stack, indent) {
      var kept = [];
      for (var n = 0; n < stack.length; n += 1) {
        if (stack[n].indent < indent) { kept.push(stack[n].seg); }
      }
      return kept;
    }

    /* THE PLAIN MAP ACCESS. A dotted path resolves only if the payload carries exactly that key —
     * no name search, no fuzzy match, no case folding, no inference from the document's text. The ONE
     * exception is the schema's `regex;` statement: a segment the payload does not carry descends into
     * the container's `regex;` child (`regexElementPath`), because that child IS the schema's claim
     * that the DOCUMENT invents the key there (a node name, a preset name, an instance). That descent
     * is taken only where the container offers exactly ONE such child; two invented-key containers are
     * an ambiguity and answer nothing. A row of kind `regex` is never a KEY the document writes, so
     * this walk uses those rows without ever offering them (see `keyRows`). */
    function resolvePath(segments) {
      var path = "";
      for (var n = 0; n < segments.length; n += 1) {
        var next = path ? path + "." + segments[n] : segments[n];
        if (Object.prototype.hasOwnProperty.call(hintPaths, next)) { path = next; continue; }
        if (segments[n] === ELEMENT_SEGMENT) { return null; }
        var element = regexElementPath(path);
        if (!element) { return null; }
        path = element;
      }
      return Object.prototype.hasOwnProperty.call(hintPaths, path) ? path : null;
    }

    function regexElementPath(path) {
      var entry = hintPaths[path];
      if (!entry || !entry.children) { return ""; }
      var found = "";
      for (var n = 0; n < entry.children.length; n += 1) {
        var row = entry.children[n];
        if (row.kind !== REGEX_KIND) { continue; }
        if (found) { return ""; }                 // two invented-key containers: no guess between them
        found = row.path + "." + ELEMENT_SEGMENT;
      }
      return found && Object.prototype.hasOwnProperty.call(hintPaths, found) ? found : "";
    }

    /* the child row named *key* in the container *path*, or null. A `regex` row names the schema's
     * invented-key block, never a key the document writes, so it is never the field a typed key
     * resolves to — and its example/enum are therefore never offered either. */
    function fieldRow(path, key) {
      var entry = hintPaths[path];
      if (!entry || !entry.children) { return null; }
      for (var n = 0; n < entry.children.length; n += 1) {
        var row = entry.children[n];
        if (row.kind === REGEX_KIND) { continue; }
        if (row.key === key) { return row; }
      }
      return null;
    }

    /* THE KEYS ALREADY WRITTEN in the block at *indent*, read from the editor's OWN text — or null
     * when the block is not a plain block mapping (flow style, an anchor, an alias), where no mark may
     * be shown at all. A sequence item's inline key (`- name: x`) counts as its element's key. */
    function presentKeys(line, indent) {
      var start = 0, end = editor.lineCount() - 1, parent = -1;
      for (var n = line - 1; n >= 0; n -= 1) {
        var above = visible(editor.getLine(n));
        if (above.trim() && indentOf(above) < indent) { start = n + 1; parent = n; break; }
      }
      for (var n = line + 1; n < editor.lineCount(); n += 1) {
        var below = visible(editor.getLine(n));
        if (below.trim() && indentOf(below) < indent) { end = n - 1; break; }
      }
      if (parent >= 0) {
        var opener = visible(editor.getLine(parent));
        var item = /^\s*-\s+(.*)$/.exec(opener);
        var tail = item ? item[1] : opener;
        var colon = tail.indexOf(":");
        var value = colon >= 0 ? tail.slice(colon + 1).trim() : "";
        if (value) {
          if (!item) { return null; }                    // `key: <something here>` — the block is not plain
          if (/^[[{&*]/.test(value)) { return null; }    // the element itself is flow, an anchor or an alias
        }
      }
      var found = {};
      for (var n = start; n <= end; n += 1) {
        var text = visible(editor.getLine(n));
        if (!text.trim()) { continue; }
        var at = indentOf(text);
        var inline = /^(\s*)-\s+([^\s#][^:]*):(\s|$)/.exec(text);
        if (inline && inline[1].length + 2 === indent) { found[inline[2].trim()] = true; continue; }
        if (at !== indent) { continue; }
        if (/^\s*[[{]/.test(text)) { return null; }      // a flow collection at this level
        var key = /^\s*([^\s#][^:]*):(\s|$)/.exec(text);
        if (key) { found[key[1].trim()] = true; }
      }
      return found;
    }

    /* a schema example is YAML source already: its lines, indented *indent* columns */
    function exampleBody(example, indent) {
      var pad = new Array(indent + 1).join(" ");
      var lines = String(example).replace(/\s+$/, "").split("\n");
      var body = [];
      for (var n = 0; n < lines.length; n += 1) { body.push(pad + lines[n]); }
      return body.join("\n");
    }

    /* THE ROWS OF ONE KEY BLOCK: `key · label · required · set` with the field's prose underneath, and
     * a second row for a block the schema gives an example — that one inserts the example, indented
     * under the key. A row inserts the key and its `: ` separator and NEVER a value; the operator
     * accepts it with Enter, Tab or a single click on the row.
     *
     * A `regex` row is NOT one of these. Its "key" is the SCHEMA's own statement that the document
     * invents the key there (`regex;(.+)`), so offering it would write schema syntax into the file —
     * and both its rows (the key row and its example row) are dropped for that reason. The row keeps
     * its one real job: the path resolver descends through it (`regexElementPath`), so the keys INSIDE
     * such a block are still completed where the document already wrote the invented key. */
    function keyRows(path, line, indent) {
      var entry = hintPaths[path];
      if (!entry || !entry.children) { return []; }
      var present = presentKeys(line, indent);
      var rows = [];
      for (var n = 0; n < entry.children.length; n += 1) {
        var row = entry.children[n];
        if (!row.key || row.kind === REGEX_KIND) { continue; }
        var marks = [];
        if (row.required) { marks.push(t("yaml.hint.required")); }
        if (present && present[row.key]) { marks.push(t("yaml.hint.set")); }
        rows.push({
          text: row.key + ": ",
          displayText: row.key + " · " + (row.label || "")
                       + (marks.length ? " · " + marks.join(" · ") : "")
                       + (row.desc ? "\n" + row.desc : ""),
        });
        if (row.example === null || row.example === undefined) { continue; }
        if (!Object.prototype.hasOwnProperty.call(hintPaths, row.path)) { continue; }
        if (!String(row.example).trim()) { continue; }
        rows.push({
          text: row.key + ":\n" + exampleBody(row.example, indent + 2),
          displayText: row.key + " · " + t("yaml.hint.example")
                       + (row.desc ? "\n" + row.desc : ""),
        });
      }
      return rows;
    }

    /* THE ROWS OF ONE FIELD'S VALUE POSITION: its enum members, then its example as one insertion. */
    function valueRows(row, indent) {
      var rows = [];
      var values = row.enum || [];
      for (var n = 0; n < values.length; n += 1) {
        var value = String(values[n]);
        rows.push({ text: value, displayText: value + (row.desc ? "\n" + row.desc : "") });
      }
      if (row.example === null || row.example === undefined) { return rows; }
      var example = String(row.example).replace(/\s+$/, "");
      if (!example) { return rows; }
      var text = example.indexOf("\n") < 0 ? example : "\n" + exampleBody(example, indent + 2);
      rows.push({
        text: text,
        displayText: t("yaml.hint.example") + (row.desc ? "\n" + row.desc : ""),
      });
      return rows;
    }

    /* THE ROWS OF A SEQUENCE ITEM (`- `): the element's own block when it is one, otherwise the
     * element row's enum and example. */
    function elementRows(path, line, indent) {
      var elementPath = path ? path + "." + ELEMENT_SEGMENT : ELEMENT_SEGMENT;
      if (Object.prototype.hasOwnProperty.call(hintPaths, elementPath)) {
        return keyRows(elementPath, line, indent + 2);
      }
      var entry = hintPaths[path];
      if (!entry || !entry.children) { return []; }
      for (var n = 0; n < entry.children.length; n += 1) {
        if (entry.children[n].path === elementPath) { return valueRows(entry.children[n], indent + 2); }
      }
      return [];
    }

    function rowsHere(cur) {
      var line = editor.getLine(cur.line);
      var before = line.slice(0, cur.ch);
      var after = line.slice(cur.ch);
      var stack = segmentsAbove(cur.line);
      var column = line.trim() ? indentOf(line) : cur.ch;
      var path = resolvePath(segmentsFor(stack, column));
      if (path === null) { return []; }

      var item = /^(\s*)-[ \t]*$/.exec(before);
      if (item && !after.trim()) { return elementRows(path, cur.line, item[1].length); }

      if (/^\s*$/.test(before) && !after.trim()) { return keyRows(path, cur.line, cur.ch); }

      var value = /^(\s*)(-\s+)?([^\s#][^:]*):[ \t]*$/.exec(before);
      if (!value || after.trim()) { return []; }
      var keyIndent = value[1].length + (value[2] ? value[2].length : 0);
      var segments = segmentsFor(stack, keyIndent);
      if (value[2]) { segments.push(ELEMENT_SEGMENT); }
      var container = resolvePath(segments);
      if (container === null) { return []; }
      var row = fieldRow(container, value[3].trim());
      return row ? valueRows(row, keyIndent) : [];
    }

    /* THE POPUP. show-hint owns the widget, the acceptance keys and the insertion; this answers with
     * the rows. Opening a second completion closes the first inside the addon. */
    function openHint() {
      if (!hintPaths || !editor || typeof CodeMirror.showHint !== "function") { return; }
      CodeMirror.showHint(editor, function () {
        var cur = editor.getCursor();
        return { list: rowsHere(cur), from: cur, to: cur };
      }, HINT_OPTIONS);
    }

    /* BY ITSELF, BUT ONLY WHERE A KEY GOES: an empty key position, `- `, or a key's empty value —
     * never on every keystroke, which is the behaviour operators switch off. */
    function maybeOpenHint() {
      if (!hintPaths || !editor) { return; }
      if (editor.state && editor.state.completionActive) { return; }   // the open widget updates itself
      var cur = editor.getCursor();
      var line = editor.getLine(cur.line);
      var before = line.slice(0, cur.ch);
      if (line.slice(cur.ch).trim()) { return; }
      if (!/^\s*(-[ \t]*)?$/.test(before) && !/^(\s*)(-\s+)?[^\s#][^:]*:[ \t]*$/.test(before)) { return; }
      var at = cur.line + ":" + cur.ch;
      if (at === autoOpenedAt) { return; }
      autoOpenedAt = at;
      openHint();
    }

    function build(value) {
      editorHost.hidden = false;
      editor = CodeMirror(editorHost, {
        value: value,
        mode: "yaml",
        lineNumbers: true,
        lineWrapping: false,
        indentUnit: 2,
        tabSize: 2,
        /* Ctrl+Space asks for the popup wherever the caret is (show-hint's own default binding). */
        extraKeys: { "Ctrl-Space": function () { openHint(); } },
      });
      editor.on("change", onChange);
      editor._cfgMarks = [];
      prefetch();
    }

    function enable() {
      if (!editor) {
        readonly.hidden = true;
        build(readonly.textContent);
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

    if (plain) {
      /* THE TOGGLE-LESS MODE: the textarea is the source and the operator's own control. The editor
       * replaces it on screen; the textarea stays in the form (hidden) so the value posts either way.
       * A refused submit then re-reads the SAME value, so with JavaScript off nothing below runs. */
      build(textField.value);
      textField.hidden = true;
      check();
    } else {
      toggle.addEventListener("change", function () {
        if (toggle.checked) { enable(); } else { disable(); }
      });
    }

    /* THE SUBMIT GUARD. The server refuses an invalid document; this only stops an OBVIOUSLY
     * unverified one from leaving early. In the CAPTURE phase, so it runs before the console's own
     * submit interceptor (`submit.js`, a bubble listener) and can veto the submit outright: with the
     * editor open, a submit whose text has changed since the last verdict (or whose last verdict was
     * a refusal) is held back and re-checked instead of posted. A submit with a clean, valid verdict
     * is left entirely alone. A control that does NOT post the edited document (a form with more than
     * one action, e.g. the presets wizard's Remove) opts out with `data-cfg-expert-skip`, so the guard
     * never holds an action the document has nothing to do with. */
    form.addEventListener("submit", function (event) {
      if (!editor) { return; }                  // expert off: the plain form is untouched
      var submitter = event.submitter;
      if (submitter && submitter.hasAttribute && submitter.hasAttribute("data-cfg-expert-skip")) {
        return;                                 // this control carries no edited document
      }
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
