/* THE LIBRARY CARD'S DROP TARGET — a file dropped on the card is uploaded into a folder.
 *
 * WHERE IT LANDS. A drop anywhere on the card goes into the folder the browser is SHOWING
 * (`data-upload-folder`); a drop on a row of the folder rail goes into THAT row's folder
 * (`data-folder`). One rule, one door: both land in a folder of the node's music directory, through
 * the same route the card's own file input posts to.
 *
 * WHAT IT DOES NOT CHANGE. The card header carries a real `multipart` form with the CSRF token, the
 * node and the folder, so with JavaScript off a file picked there uploads exactly the same way. This
 * is a progressive enhancement: when a drop happens it builds the SAME body and posts it, one request
 * per file, sequentially — a second file never races the first on the same folder.
 *
 * THE ANSWER IS THE ROUTE'S OWN SENTENCE. The POST asks for `application/json`, so a refusal (a
 * wrong extension, a name the folder already holds, the size cap) arrives as `ok: false` and one
 * sentence, which is written into the card's status line and NOT navigated away from. A success
 * reloads the folder it landed in, so the new tracks are listed without anyone pressing Rescan —
 * the service bumped that node's scan generation, and the render reads it.
 *
 * NOTHING IS REFUSED HERE. Every judgement (the name, the extension, the folder, the cap, the
 * overwrite) belongs to the route and the action behind it: a script that refused first would be a
 * second, silently different rule.
 */
(function () {
  "use strict";

  var CARD = "[data-upload]";
  var ROW = "[data-folder]";
  var MARK = "mus-drop";

  function text(card, name) {
    return card.getAttribute("data-upload-" + name) || "";
  }

  function note(card) {
    return card.querySelector(".mus-upload-note");
  }

  function say(card, message) {
    var line = note(card);
    if (!line) { return; }
    line.textContent = message;
    line.hidden = !message;
  }

  /* The folder a drop lands in: the rail row under the pointer, or the folder on screen. */
  function folderOf(card, target) {
    var row = target && target.closest ? target.closest(ROW) : null;
    return row ? row.getAttribute("data-folder") || "" : text(card, "folder");
  }

  function filesOf(event) {
    var transfer = event.dataTransfer;
    var found = [];
    if (!transfer || !transfer.files) { return found; }
    for (var i = 0; i < transfer.files.length; i += 1) {
      found.push(transfer.files[i]);
    }
    return found;
  }

  function carriesFiles(event) {
    var transfer = event.dataTransfer;
    if (!transfer) { return false; }
    var kinds = transfer.types;
    if (kinds) {
      for (var i = 0; i < kinds.length; i += 1) {
        if (kinds[i] === "Files") { return true; }
      }
    }
    return false;
  }

  function post(card, file, folder) {
    var body = new FormData();
    body.append(text(card, "token-field"), text(card, "token"));
    body.append(text(card, "node-field"), text(card, "node"));
    body.append(text(card, "folder-field"), folder);
    body.append(text(card, "file-field"), file, file.name);
    return fetch(text(card, "url"), {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      return response.json().catch(function () {
        return { ok: false, message: text(card, "refused") };
      });
    }, function () {
      return { ok: false, message: text(card, "refused") };
    });
  }

  /* ONE FILE AFTER THE OTHER, then ONE navigation: the answers are collected, so a refusal from the
     second file is still read on a page that did not leave for the first. */
  function send(card, files, folder, done) {
    var answer = { ok: false, message: "", landing: folder };
    var index = 0;

    function next() {
      if (index >= files.length) {
        done(answer);
        return;
      }
      var file = files[index];
      index += 1;
      post(card, file, folder).then(function (result) {
        answer.message = result && result.message ? result.message : "";
        answer.ok = answer.ok || Boolean(result && result.ok);
        if (result && result.ok) { answer.landing = folder; }
        next();
      });
    }

    next();
  }

  function land(card, answer) {
    if (!answer.ok) {
      say(card, answer.message || text(card, "refused"));
      return;
    }
    var page = text(card, "page");
    if (!page) { return; }
    window.location.assign(page + "?folder=" + encodeURIComponent(answer.landing || ""));
  }

  function bind(card) {
    var depth = 0;
    var marked = null;

    /* the rail ROW a drop would land in is marked, so the folder is visible before the button is
       released — cleared whenever the pointer leaves it */
    function markRow(row) {
      if (marked === row) { return; }
      if (marked) { marked.classList.remove(MARK); }
      marked = row;
      if (marked) { marked.classList.add(MARK); }
    }

    card.addEventListener("dragenter", function (event) {
      if (!carriesFiles(event)) { return; }
      event.preventDefault();
      depth += 1;
      card.classList.add(MARK);
    });

    card.addEventListener("dragover", function (event) {
      if (!carriesFiles(event)) { return; }
      /* the default is "no drop here", which is what a browser shows without this line */
      event.preventDefault();
      if (event.dataTransfer) { event.dataTransfer.dropEffect = "copy"; }
      markRow(event.target && event.target.closest ? event.target.closest(ROW) : null);
    });

    card.addEventListener("dragleave", function (event) {
      if (!carriesFiles(event)) { return; }
      depth = Math.max(0, depth - 1);
      if (!depth) { card.classList.remove(MARK); }
      markRow(null);
    });

    card.addEventListener("drop", function (event) {
      if (!carriesFiles(event)) { return; }
      event.preventDefault();
      depth = 0;
      card.classList.remove(MARK);
      markRow(null);
      var files = filesOf(event);
      if (!files.length) { return; }
      var folder = folderOf(card, event.target);
      say(card, "");
      card.setAttribute("aria-busy", "true");
      send(card, files, folder, function (answer) {
        card.removeAttribute("aria-busy");
        land(card, answer);
      });
    });
  }

  var cards = document.querySelectorAll(CARD);
  for (var i = 0; i < cards.length; i += 1) {
    bind(cards[i]);
  }
})();
