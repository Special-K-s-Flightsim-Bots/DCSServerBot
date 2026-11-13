/* ROW DRAG — a pointer reorder for any list that carries the list's own data attributes.
 *
 * The list opts in with data attributes, so the same script serves a table (the music playlist) and
 * a plain row container (the missions list) without a second copy:
 *
 *   <table data-reorder data-reorder-url="…" data-reorder-field="order" data-reorder-token="…"
 *          data-reorder-token-field="_csrf_token" data-reorder-refused="…" data-reorder-offline="…">
 *     <tr data-reorder-row data-reorder-id="…"><td data-reorder-handle tabindex="0">…</td>…</tr>
 *
 * A non-table container carries the same attributes and marks its row holder with `data-reorder-body`
 * (on itself or on a descendant that holds the rows):
 *
 *   <div data-reorder data-reorder-body …>
 *     <div data-reorder-row data-reorder-id="…"><div data-reorder-handle>…</div>…</div>
 *   </div>
 *
 * WHAT IT DOES NOT DO: it draws no control of its own. `data-reorder` alone is the whole opt-in — no
 * other attribute says whether the list also draws up/down buttons, and a list that draws none is
 * enhanced exactly the same. It posts the WHOLE new order once per drop (never one request per moved
 * row). A refusal is shown in the list's own card and the rows are put back, so the DOM never claims
 * an order the server did not save.
 *
 * TWO MODES, DECIDED BY ONE ATTRIBUTE:
 *
 *  * `data-reorder` WITH `data-reorder-url` — the list writes its own order: one POST per drop, the
 *    status line, and the restore when the answer is a refusal or the network is gone.
 *  * `data-reorder` WITHOUT `data-reorder-url` — LOCAL: the rows live inside a form whose Save carries
 *    the order, so a drop (or an Alt+Arrow move) only moves the row inside the container. No request,
 *    no status line, no restore: the DOM state IS the payload. The container is told after each move
 *    with a bubbling `reorder-moved` event (detail: the ids in their new DOM order) so a listener can
 *    repaint whatever it draws from that order. NO SHIPPED PAGE LISTENS TODAY — the lists that run
 *    this mode renumber their rows with a CSS counter — so the event is the hook a page opts into,
 *    not a callback anything depends on. Keep dispatching it: a page that draws row numbers itself
 *    (rather than in CSS) subscribes here instead of re-reading the DOM after the drop.
 *
 * TWO OPTIONAL ATTRIBUTES A LIST MAY CARRY:
 *
 *  * `data-reorder-params` — a url-encoded string of STATIC fields every request must also carry
 *    (`server=Alpha`). The missions route resolves its target from the posted server, so a drop
 *    without it cannot resolve and is answered with a redirect instead of JSON.
 *  * the ANSWER's `song_ids` — the ids the write left behind, in playback order. A list whose write
 *    RENUMBERS its rows (the music playlist: the order IS the id) gets its rows re-stamped from this
 *    list, so the next drop names ids that exist instead of being refused as a non-permutation.
 *
 * THE KEYBOARD ROUTE IS THE SAME HANDLE: a focused `[data-reorder-handle]` moves its row one place on
 * Alt+ArrowUp / Alt+ArrowDown and posts that same whole order, so a list whose only order control is
 * the grip stays reorderable without a pointer. The handle must be focusable (the page gives it a
 * tabindex) — this script neither adds nor removes one.
 *
 * NO STRING IS COMPOSED HERE: the two sentences it can show arrive in data attributes, rendered by
 * the server in the request's language.
 */
(function () {
  "use strict";

  if (!window.fetch || !window.URLSearchParams || !window.PointerEvent) { return; }

  function rowHolder(root) {
    if (root.tBodies) { return root.tBodies[0] || null; }
    return root.hasAttribute("data-reorder-body") ? root : root.querySelector("[data-reorder-body]");
  }

  function rowIds(body) {
    var rows = body.querySelectorAll("[data-reorder-row]");
    var ids = [];
    for (var i = 0; i < rows.length; i += 1) { ids.push(rows[i].getAttribute("data-reorder-id")); }
    return ids;
  }

  function statusLine(table) {
    var card = table.closest(".card") || table.parentNode;
    var line = card.querySelector("[data-reorder-status]");
    if (!line) {
      line = document.createElement("p");
      line.className = "reorder-note";
      line.setAttribute("data-reorder-status", "");
      line.setAttribute("role", "status");
      card.appendChild(line);
    }
    return line;
  }

  function restore(body, order) {
    var rows = body.querySelectorAll("[data-reorder-row]");
    var byId = {};
    for (var i = 0; i < rows.length; i += 1) {
      byId[rows[i].getAttribute("data-reorder-id")] = rows[i];
    }
    for (var j = 0; j < order.length; j += 1) {
      if (byId[order[j]]) { body.appendChild(byId[order[j]]); }
    }
  }

  function post(table, order) {
    var params = new URLSearchParams();
    var tokenField = table.getAttribute("data-reorder-token-field") || "_csrf_token";
    var token = table.getAttribute("data-reorder-token");
    if (token) { params.append(tokenField, token); }
    /* THE LIST'S OWN STATIC PARAMS: a list whose write needs more than the order names them here as a
       url-encoded string (`data-reorder-params="server=Alpha"`), rendered by the server in the
       request's language. The missions route needs the server the list belongs to — without it the
       request cannot resolve its target and the drop is answered with a redirect, not JSON. */
    var extra = table.getAttribute("data-reorder-params");
    if (extra) {
      var pairs = new URLSearchParams(extra);
      pairs.forEach(function (value, key) { params.append(key, value); });
    }
    for (var i = 0; i < order.length; i += 1) {
      params.append(table.getAttribute("data-reorder-field") || "order", order[i]);
    }
    return fetch(table.getAttribute("data-reorder-url"), {
      method: "POST",
      body: params,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "application/json"
      }
    }).then(function (response) {
      return response.json();
    });
  }

  /* A WRITE MAY RENUMBER THE ROWS. The music playlist's write replaces every `song_id` (the order IS
     the id), so the ids this script just posted no longer exist once the answer says ok. When the
     answer carries `song_ids` — the ids AS THE WRITE LEFT THEM, in playback order — the rows are
     re-stamped in their DOM order so the NEXT drop names ids that exist. A list whose write leaves
     its ids alone (the missions list, which orders by name) simply gets no `song_ids` and nothing
     happens. */
  function restamp(body, ids) {
    if (!ids || !ids.length) { return; }
    var rows = body.querySelectorAll("[data-reorder-row]");
    if (rows.length !== ids.length) { return; }
    for (var i = 0; i < rows.length; i += 1) {
      rows[i].setAttribute("data-reorder-id", String(ids[i]));
    }
  }

  /* A LIST WITH NO URL WRITES NOTHING ITSELF: the page carries the order in its own form, so the move
     it just made IS the new state and the container is told about it. `reorder-moved` is the hook for
     a page that repaints from the order; no shipped page subscribes today (their lists renumber with
     a CSS counter), so it stays a pure notification. */
  function local(table) {
    return !table.getAttribute("data-reorder-url");
  }

  function announce(table, body) {
    if (!table.dispatchEvent || !window.CustomEvent) {
      return;
    }
    table.dispatchEvent(new CustomEvent("reorder-moved", {
      bubbles: true,
      detail: { ids: rowIds(body) }
    }));
  }

  /* ONE WRITE PER GESTURE: the whole new order, or nothing. `before` is the order the list read when
     the gesture started, so a move that changed nothing posts nothing and a refusal puts it back. A
     LOCAL list posts nothing at all — it only reports the move. */
  function commit(table, body, before) {
    var after = rowIds(body);
    if (after.join("\u0000") === before.join("\u0000")) { return; }
    if (local(table)) { announce(table, body); return; }
    var line = statusLine(table);
    line.textContent = "";
    post(table, after).then(function (answer) {
      if (answer && answer.ok) {
        restamp(body, answer.song_ids);
        if (answer.message) { line.textContent = answer.message; }
        return;
      }
      restore(body, before);
      line.textContent = (answer && answer.message)
        || table.getAttribute("data-reorder-refused") || "";
    }).catch(function () {
      restore(body, before);
      line.textContent = table.getAttribute("data-reorder-offline") || "";
    });
  }

  function enhance(table) {
    var body = rowHolder(table);
    if (!body || !rowIds(body).length) { return; }

    var drag = null;

    body.addEventListener("pointerdown", function (event) {
      var handle = event.target.closest ? event.target.closest("[data-reorder-handle]") : null;
      if (!handle || event.button !== 0) { return; }
      var row = handle.closest("[data-reorder-row]");
      if (!row) { return; }
      event.preventDefault();
      drag = { row: row, pointerId: event.pointerId, order: rowIds(body) };
      row.classList.add("reorder-dragging");
      document.body.classList.add("reorder-active");
      if (handle.setPointerCapture) { handle.setPointerCapture(event.pointerId); }
    });

    body.addEventListener("pointermove", function (event) {
      if (!drag || event.pointerId !== drag.pointerId) { return; }
      /* ONE RULE FOR BOTH DIRECTIONS: the row is placed before the first OTHER row whose middle is
         BELOW the pointer, and after every row above it. Testing the pointer against a row only when
         that row already sits on one side of the dragged one makes one direction unreachable — a row
         dragged UP never satisfies "pointer below the middle of a row that follows it", so it stayed
         where it was. Read the rows in DOM order and take the first crossing, wherever the pointer is. */
      var rows = body.querySelectorAll("[data-reorder-row]");
      var reference = null;
      for (var i = 0; i < rows.length; i += 1) {
        if (rows[i] === drag.row) { continue; }
        var box = rows[i].getBoundingClientRect();
        if (event.clientY < box.top + box.height / 2) { reference = rows[i]; break; }
      }
      if (reference) { body.insertBefore(drag.row, reference); }
      else { body.appendChild(drag.row); }
    });

    function finish(event) {
      if (!drag || (event && event.pointerId !== drag.pointerId)) { return; }
      var row = drag.row;
      var before = drag.order;
      drag = null;
      row.classList.remove("reorder-dragging");
      document.body.classList.remove("reorder-active");
      commit(table, body, before);
    }

    body.addEventListener("pointerup", finish);
    body.addEventListener("pointercancel", function (event) {
      if (!drag || event.pointerId !== drag.pointerId) { return; }
      var row = drag.row;
      var before = drag.order;
      drag = null;
      row.classList.remove("reorder-dragging");
      document.body.classList.remove("reorder-active");
      restore(body, before);
    });

    /* THE KEYBOARD PATH: one place per press, the same whole order, the same refusal handling. */
    body.addEventListener("keydown", function (event) {
      if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) { return; }
      var handle = event.target.closest ? event.target.closest("[data-reorder-handle]") : null;
      if (!handle) { return; }
      var row = handle.closest("[data-reorder-row]");
      if (!row) { return; }
      var sibling = event.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
      if (!sibling || !sibling.getAttribute || sibling.getAttribute("data-reorder-id") === null) {
        return;
      }
      event.preventDefault();
      var before = rowIds(body);
      if (event.key === "ArrowUp") { body.insertBefore(row, sibling); }
      else { body.insertBefore(row, sibling.nextSibling); }
      if (handle.focus) { handle.focus(); }
      commit(table, body, before);
    });
  }

  var tables = document.querySelectorAll("[data-reorder]");
  for (var i = 0; i < tables.length; i += 1) { enhance(tables[i]); }
})();
