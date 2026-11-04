/* A FIELD'S OWN SUGGESTIONS — the console's one autocomplete, on any text box that carries
 * `data-lookup` (the ADD ban dialog's UCID field is its only user so far).
 *
 * WHAT IT DOES. As the operator types, it asks the field's own URL (`<data-lookup>?q=<typed>`) for
 * what could go in the box and shows the answer under it. Picking a row writes that row's UCID into
 * the field: an operator knows a player by NAME and the ban needs the UCID.
 *
 * A PROGRESSIVE ENHANCEMENT, AND ONLY THAT. The markup it finds is a plain, required text input: with
 * JavaScript off the dialog renders in full and the UCID is typed by hand, which the write validates
 * exactly as it validates one filled from a suggestion. Nothing about the form's correctness depends
 * on this file — a refused, failed, empty or stale answer simply leaves the box as it is.
 *
 * THE CONTRACT, so the markup, the route and this file cannot drift: the field carries
 * `data-lookup="<url>"` (the declaration's own string, `pages/actions.BAN_ADD_UCID_LOOKUP`); the
 * answer is `{"players": [{"name", "ucid"}, …]}`, at most 25 rows, most recently seen first; the
 * popup is a `role="listbox"` whose rows are `role="option"`; and the popup's accessible name is the
 * FIELD's own label, read off the markup — this file carries no user-visible string of its own.
 *
 * KEYBOARD: Down/Up move through the suggestions (wrapping), Enter takes the highlighted one and is
 * SWALLOWED so it cannot submit the dialog before a choice was made, Escape closes the list without
 * touching the box. The first suggestion is highlighted as soon as the list appears, so a name typed
 * in full is one Enter away from its UCID. A press on a row takes it (`mousedown`, so the box keeps
 * focus and the list does not close under the pointer first).
 */
(function () {
  "use strict";

  /* one character is enough to ask: the query is a name FRAGMENT or a UCID fragment, and the route
     answers a name that matches nobody with an empty list. */
  var MIN_CHARS = 1;
  /* typing is a burst: one request goes out when the burst stops, so a five-letter name is ONE
     lookup rather than five. */
  var DEBOUNCE_MS = 180;

  function start() {
    var fields = document.querySelectorAll("input[data-lookup]");
    for (var index = 0; index < fields.length; index += 1) {
      enhance(fields[index]);
    }
  }

  function enhance(field) {
    var url = field.getAttribute("data-lookup");
    if (!url || !field.parentNode) { return; }

    var list = document.createElement("ul");
    list.className = "lk-list";
    list.id = (field.id ? field.id : "lk") + "-lookup";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    /* THE POPUP'S NAME IS THE FIELD'S OWN LABEL: one wording, authored once in the template, and
       none written here. */
    var label = field.id ? document.querySelector('label[for="' + field.id + '"]') : null;
    if (label && label.textContent) { list.setAttribute("aria-label", label.textContent.trim()); }

    var wrap = document.createElement("span");
    wrap.className = "lk-wrap";
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

    var rows = [];       /* the suggestions on screen: {name, ucid}, in the order the route sent them */
    var options = [];    /* their rendered elements, so the highlight needs no second lookup */
    var active = -1;     /* the highlighted row, -1 for none */
    var asked = "";      /* the query the in-flight request belongs to */
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
        options[active].className = "lk-opt";
        options[active].setAttribute("aria-selected", "false");
      }
      active = index;
      if (index < 0 || index >= options.length) { return; }
      options[index].className = "lk-opt sel";
      options[index].setAttribute("aria-selected", "true");
      if (options[index].scrollIntoView) { options[index].scrollIntoView({block: "nearest"}); }
    }

    function choose(index) {
      if (index < 0 || index >= rows.length) { return false; }
      /* THE PICK: the box takes the UCID, never the name it was found by. */
      field.value = rows[index].ucid;
      close();
      if (field.focus) { field.focus(); }
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
        option.className = "lk-opt";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        /* the name the operator searched for, then the UCID they have to type — the pair `/info`'s
           own autocomplete shows. A player the table holds no name for shows the UCID alone instead
           of an empty first half. */
        if (row.name) {
          var name = document.createElement("span");
          name.className = "lk-name";
          name.textContent = row.name;
          option.appendChild(name);
        }
        var ucid = document.createElement("span");
        ucid.className = "lk-ucid";
        ucid.textContent = "(" + row.ucid + ")";
        option.appendChild(ucid);
        option.addEventListener("mousedown", picker(index, choose));
        list.appendChild(option);
        options.push(option);
      }
      if (rows.length) { highlight(0); }
    }

    function accept(payload) {
      var found = [];
      var players = payload && payload.players;
      if (players && players.length) {
        for (var index = 0; index < players.length; index += 1) {
          var row = players[index];
          if (row && row.ucid) { found.push({name: row.name || "", ucid: row.ucid}); }
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
          /* THE STALE ANSWER: the operator typed on while this was in flight, so the answer belongs
             to a query the box no longer asks. */
          if (term !== asked || field.value.trim() !== term) { return; }
          var found = accept(payload);
          if (found.length) { paint(found); } else { close(); }
        })
        .catch(function () { /* refused, offline or aborted: the plain text box is still the answer */ });
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
        /* swallowed ONLY while a suggestion is highlighted: with the list closed (or nothing offered)
           Enter is the dialog's own submit, exactly as it is with JavaScript off. */
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