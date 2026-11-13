/* THE SITE'S SEARCH SUGGESTIONS — the top bar's "Find a pilot" box, offering pilots as it is typed.
 *
 * WHAT IT DOES. As the visitor types, it asks the FORM's own URL (`<data-search>?q=<typed>`) for the
 * pilots whose name contains what was typed and shows them under the box. Picking a row takes the
 * visitor to that pilot's profile page — this box NAVIGATES, it never writes into the field.
 *
 * A PROGRESSIVE ENHANCEMENT, AND ONLY THAT. The markup it finds is the plain search form the template
 * already ships: with JavaScript off it submits `?q=` to the search page exactly as yesterday, and
 * nothing here may change that. A refused, failed, empty or stale answer simply leaves the form as
 * it is, so Enter with no suggestion highlighted is always the form's own submit.
 *
 * THE CONTRACT, so the markup, the route and this file cannot drift: the form carries
 * `data-search="<url>"`; the answer is `{"pilots": [{"name", "href"}, …]}`, at most 25 rows, most
 * recently seen first; the popup is a `role="listbox"` whose rows are `role="option"`; and the
 * popup's accessible name is the FIELD's own label, read off the markup — this file carries no
 * user-visible string of its own.
 *
 * KEYBOARD: Down/Up move through the suggestions (wrapping), Enter takes the highlighted one and is
 * SWALLOWED so it cannot submit the form first, Escape closes the list without touching the box. A
 * press on a row takes it (`mousedown`, so the box keeps focus and the list does not close under the
 * pointer before the pick).
 *
 * REQUESTS: typing is a burst, so one request goes out when the burst stops; an answer whose query
 * the box has moved on from is dropped; and the same fragment is not asked for twice.
 */
(function () {
  "use strict";

  /* one character is not a search: it names almost every pilot, and the route answers nothing for
     it either. The popup waits for the second character. */
  var MIN_CHARS = 2;
  /* typing is a burst: one request goes out when the burst stops, so a five-letter name is ONE
     lookup rather than five. */
  var DEBOUNCE_MS = 180;

  function start() {
    var forms = document.querySelectorAll("form[data-search]");
    for (var index = 0; index < forms.length; index += 1) {
      enhance(forms[index]);
    }
  }

  function enhance(form) {
    var url = form.getAttribute("data-search");
    var field = form.querySelector ? form.querySelector('input[type="search"]') : null;
    if (!url || !field || !field.parentNode) { return; }

    var list = document.createElement("ul");
    list.className = "sr-list";
    list.id = (field.id ? field.id : "sr") + "-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    /* THE POPUP'S NAME IS THE FIELD'S OWN LABEL: one wording, authored once in the template, and
       none written here. The site's field declares it as `aria-label`; a `label[for=…]` is honoured
       where a page authors one instead. */
    var label = null;
    if (field.id) { label = document.querySelector('label[for="' + field.id + '"]'); }
    var named = (field.getAttribute && field.getAttribute("aria-label")) ||
                (label && label.textContent ? label.textContent.trim() : "");
    if (named) { list.setAttribute("aria-label", named); }

    var wrap = document.createElement("span");
    wrap.className = "sr-wrap";
    field.parentNode.insertBefore(wrap, field);
    wrap.appendChild(field);
    wrap.appendChild(list);

    /* THE COMBOBOX CONTRACT, set only once this file is running: with JavaScript off the input keeps
       the plain attributes the template wrote, and a popup that does not exist must not be
       announced. */
    field.setAttribute("role", "combobox");
    field.setAttribute("aria-autocomplete", "list");
    field.setAttribute("aria-controls", list.id);
    field.setAttribute("aria-expanded", "false");

    var rows = [];       /* the suggestions on screen: {name, href}, in the order the route sent them */
    var options = [];    /* their rendered elements, so the highlight needs no second lookup */
    var active = -1;     /* the highlighted row, -1 for none */
    var asked = "";      /* the query the in-flight (or last) request belongs to */
    var timer = null;    /* the debounce timer */
    var pending = null;  /* the in-flight request's controller, so a stale answer is dropped */

    function close() {
      rows = [];
      options = [];
      active = -1;
      list.hidden = true;
      list.textContent = "";
      field.setAttribute("aria-expanded", "false");
    }

    function highlight(index) {
      if (active >= 0 && active < options.length) {
        options[active].className = "sr-opt";
        options[active].setAttribute("aria-selected", "false");
      }
      active = index;
      if (index < 0 || index >= options.length) { return; }
      options[index].className = "sr-opt sel";
      options[index].setAttribute("aria-selected", "true");
      if (options[index].scrollIntoView) { options[index].scrollIntoView({block: "nearest"}); }
    }

    function choose(index) {
      if (index < 0 || index >= rows.length) { return false; }
      /* THE PICK: the visitor leaves for the pilot's page — the row's own href, never a rewrite. */
      var target = rows[index].href;
      close();
      if (target) { window.location.assign(target); }
      return true;
    }

    function paint(found) {
      close();
      rows = found;
      list.hidden = false;
      field.setAttribute("aria-expanded", "true");
      for (var index = 0; index < rows.length; index += 1) {
        var row = rows[index];
        var option = document.createElement("li");
        option.className = "sr-opt";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        var name = document.createElement("span");
        name.className = "sr-name";
        name.textContent = row.name || "";
        option.appendChild(name);
        option.addEventListener("mousedown", picker(index, choose));
        list.appendChild(option);
        options.push(option);
      }
      if (rows.length) { highlight(0); }
    }

    function accept(payload) {
      var found = [];
      var pilots = payload && payload.pilots;
      if (pilots && pilots.length) {
        for (var index = 0; index < pilots.length; index += 1) {
          var row = pilots[index];
          if (row && row.href) { found.push({name: row.name || "", href: row.href}); }
        }
      }
      return found;
    }

    function look(term) {
      asked = term;
      if (pending) { pending.abort(); }
      pending = window.AbortController ? new window.AbortController() : null;
      var request = {headers: {accept: "application/json"}, credentials: "same-origin"};
      if (pending) { request.signal = pending.signal; }
      window.fetch(url + "?q=" + encodeURIComponent(term), request)
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (payload) {
          /* THE STALE ANSWER: the visitor typed on while this was in flight, so the answer belongs
             to a fragment the box no longer asks for. */
          if (term !== asked || field.value.trim() !== term) { return; }
          var found = accept(payload);
          if (found.length) { paint(found); } else { close(); }
        })
        .catch(function () { /* refused, offline or aborted: the plain form is still the answer */ });
    }

    function onInput() {
      var term = field.value.trim();
      if (timer) { window.clearTimeout(timer); timer = null; }
      if (term.length < MIN_CHARS) {
        asked = "";
        if (pending) { pending.abort(); pending = null; }
        close();
        return;
      }
      /* THE SAME FRAGMENT IS NOT ASKED TWICE: a keystroke that leaves the term where it was (a
         trailing space, a caret move) must not cost a request. */
      if (term === asked) { return; }
      timer = window.setTimeout(function () { timer = null; look(term); }, DEBOUNCE_MS);
    }

    function onKey(event) {
      if (event.key === "ArrowDown" && rows.length) {
        event.preventDefault();
        highlight(active + 1 < rows.length ? active + 1 : 0);
        return;
      }
      if (event.key === "ArrowUp" && rows.length) {
        event.preventDefault();
        highlight(active - 1 >= 0 ? active - 1 : rows.length - 1);
        return;
      }
      if (event.key === "Enter") {
        /* swallowed ONLY while a suggestion is highlighted: with the list closed (or nothing
           offered) Enter is the form's own submit, exactly as it is with JavaScript off. */
        if (choose(active)) { event.preventDefault(); }
        return;
      }
      if (event.key === "Escape" && rows.length) {
        event.preventDefault();
        close();
      }
    }

    field.addEventListener("input", onInput);
    field.addEventListener("keydown", onKey);
    /* leaving the box puts the list away WITHOUT changing what was typed. */
    field.addEventListener("blur", close);
  }

  /* ONE picker per row, built where the row is: the closure carries the row's own index. */
  function picker(index, choose) {
    return function (event) {
      event.preventDefault();
      choose(index);
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
