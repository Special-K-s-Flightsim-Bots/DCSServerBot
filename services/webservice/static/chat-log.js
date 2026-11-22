/* The Chat tab's live view: follow the server's in-game chat on an event stream.
 *
 * A PROGRESSIVE ENHANCEMENT. The initial page already contains the lines the server rendered, so
 * with JavaScript off nothing is missing — the page is simply not live.
 *
 * ONE EventSource PER OPEN TAB. The stream is opened while the document is VISIBLE and closed the
 * moment it is hidden or the page is left — no background connection, no server work once nobody is
 * looking. EventSource reconnects on its own; the server prefers the browser's `Last-Event-ID` over
 * the URL's cursor, so a reconnection resumes exactly where the last frame ended and no line is
 * repeated or skipped.
 *
 * THE FRAME IS THE DELTA. The server sends only the lines newer than the cursor, already rendered by
 * the same partial the page used; a `reset: true` frame means the process restarted and its ring is
 * empty — the view is cleared first so two histories are never spliced.
 *
 * THE FILTER IS THE SERVER'S. `data-who` / `data-q` ride on the stream URL, so the live view keeps
 * the reader's filter instead of growing past it; the server also renders the `N of M` sentence.
 *
 * THE COMPOSER's two niceties live here too: the character counter beside the note, and Enter sending
 * the line (Shift+Enter breaks it) — both are skipped when the server is not running, because the
 * card then ships no composer at all.
 */
(function () {
  "use strict";

  /* THE STRINGS ARE SERVED, NOT WRITTEN HERE (``/i18n/messages.js``, included by ``base.html``
     before this script). The chat pane reuses the log follower's wording — the sentences are the
     same ones, so they are the same msgids. */
  function t(key, params) {
    return window.dcssbMsg ? window.dcssbMsg(key, params) : key;
  }

  var card = document.getElementById("chat-card");
  if (!card) { return; }

  var body = document.getElementById("chat-body");
  var noteEl = document.getElementById("chat-note");
  var noteText = document.getElementById("chat-note-text");
  var statusEl = document.getElementById("chat-status");
  var countEl = document.getElementById("chat-count");
  var streamUrl = card.getAttribute("data-stream-url");
  var lastSeq = parseInt(card.getAttribute("data-after"), 10);
  var who = card.getAttribute("data-who") || "";
  var q = card.getAttribute("data-q") || "";

  if (!streamUrl) { return; }
  if (isNaN(lastSeq) || lastSeq < 0) { lastSeq = 0; }
  if (!body) { return; }

  var source = null;       /* the open EventSource, or null while the tab is hidden */

  function setStatus(text) { if (statusEl) { statusEl.textContent = text; } }

  function showNote(sentence) {
    if (noteText) { noteText.textContent = sentence || ""; }
    if (noteEl) { noteEl.hidden = !sentence; }
  }

  function hideNote() { if (noteEl) { noteEl.hidden = true; } }

  function clearBody() { body.innerHTML = ""; }

  function atBottom(el) { return el.scrollTop + el.clientHeight >= el.scrollHeight - 4; }

  function append(html) {
    if (!html) { return; }
    var keep = atBottom(body);
    body.insertAdjacentHTML("beforeend", html);
    if (keep) { body.scrollTop = body.scrollHeight; }
  }

  /* The page's own filter values ride on the stream URL, so the live view keeps the reader's filter. */
  function streamUrlWithCursor() {
    var query = "?after=" + encodeURIComponent(lastSeq);
    if (who) { query += "&who=" + encodeURIComponent(who); }
    if (q) { query += "&q=" + encodeURIComponent(q); }
    return streamUrl + query;
  }

  /* The ``N of M lines`` count beside the status: EMPTY when no filter is in force (the whole window
     is shown), the server's own rendered sentence otherwise. */
  function applyCount(payload) {
    if (!countEl) { return; }
    countEl.textContent = payload && payload.count_label ? payload.count_label : "";
  }

  function applyFrame(payload) {
    if (!payload) { return; }
    if (payload.reset) {
      /* the ring went backwards: the process restarted. Clear the view and say so — never splice */
      clearBody();
      var note = document.createElement("div");
      note.className = "ln";
      note.textContent = t("chat.restarted");
      body.appendChild(note);
    }
    append(payload.lines);
    if (payload.lines) { hideNote(); }
    if (typeof payload.last_seq === "number" && payload.last_seq >= 0) { lastSeq = payload.last_seq; }
    applyCount(payload);
    setStatus(payload.message ? payload.message : t("log.following"));
  }

  function open() {
    if (source || typeof window.EventSource === "undefined") { return; }
    setStatus(t("log.following"));
    source = new window.EventSource(streamUrlWithCursor());
    source.addEventListener("chat", function (event) {
      var payload = null;
      try { payload = JSON.parse(event.data); } catch (err) { return; }
      applyFrame(payload);
    });
    source.addEventListener("error", function () {
      /* EventSource reconnects by itself; the status says so while it is down. */
      setStatus(t("log.retrying"));
    });
  }

  function close() {
    if (source) { source.close(); source = null; }
    setStatus(t("log.paused"));
  }

  if (typeof document.hidden !== "undefined") {
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { close(); } else { open(); }
    });
    if (document.hidden) { close(); } else { open(); }
  } else {
    /* no Page Visibility API: follow for as long as the page is open (the only available notion) */
    open();
  }
  window.addEventListener("pagehide", close);

  /* THE COMPOSER: the counter and Enter-to-send. The card ships no composer while the server cannot
     take a message, so both are skipped by their own absence. */
  var form = card.querySelector("form.chat-send");
  if (!form) { return; }
  var field = form.querySelector("textarea.msg");
  var counter = document.getElementById("chat-cnt");
  var limit = field ? parseInt(field.getAttribute("maxlength"), 10) : NaN;

  function renderCount() {
    if (!counter || !field) { return; }
    var max = isNaN(limit) ? "" : String(limit);
    counter.textContent = field.value.length + (max ? " / " + max : "");
  }

  if (field) {
    field.addEventListener("input", renderCount);
    field.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) { return; }
      event.preventDefault();
      if (typeof form.requestSubmit === "function") { form.requestSubmit(); }
      else { form.submit(); }
    });
    renderCount();
  }
})();
