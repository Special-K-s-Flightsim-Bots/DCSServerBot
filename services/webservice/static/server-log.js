/* The server's OWN dcs.log in the Log tab: follow it while the tab is open, page back on demand.
 *
 * A PROGRESSIVE ENHANCEMENT. The initial page already contains the tail the server rendered, so with
 * JavaScript off nothing is missing — the page is simply not live and "Load older" does nothing.
 *
 * FOLLOW ONLY WHILE THE TAB IS OPEN (Frank's ruling): the loop runs while the document is VISIBLE and
 * stops the moment it is hidden or the page is left — there is no background tailing and no server
 * work once nobody is looking.
 *
 * THE VIEW IS A WINDOW, NEVER THE WHOLE FILE. Each tick asks the card's `data-window-url` for the
 * bytes appended since the last `offset` (`?offset=`), and "Load older" asks for the window ending at
 * `before` (`?behind=`). The server returns HTML fragments rendered by the SAME partial the page uses
 * plus the cursors, so no markup lives here and the log content is escaped server-side.
 *
 * NEVER A HALF-WRITTEN LINE: the server holds back the trailing incomplete line, so a fragment only
 * ever carries whole lines — this script never has to guess where a line ends.
 *
 * A NEW LOG (the file was truncated OR rotated): the card carries the file's `data-identity`, which is
 * echoed back on every follow as `?identity=`. The server answers `reset: true` when that identity
 * changed (a REPLACEMENT — even one already BIGGER than the offset we hold) or when the size fell
 * below it (a truncation); the view is cleared and the fresh file's tail shown, with a line saying so
 * — two files are never spliced.
 *
 * THE EMPTY STATE CAN BECOME THE LOG (L3, Frank's report). The card ALWAYS ships `#dcs-log-body`
 * (even while the file does not exist yet) and the honest sentence lives BESIDE it in
 * `#dcs-log-note`. This script therefore never bails out on the empty state: it keeps following, and
 * the moment real lines arrive it hides the note and renders into the body. If the log DISAPPEARS or
 * is rotated away, the body is cleared and the note says so rather than holding a stale tail.
 *
 * "NO LOG FILE YET" IS NOT A FAILURE (L3). The server marks that normal state with `pending: true` on
 * an otherwise empty answer; the follower keeps its NORMAL cadence on a pending answer and only backs
 * off on a genuine failure (a network error, a non-2xx, or an `available: false` answer WITHOUT the
 * pending flag — a node that is down). So a server that is still starting shows its log the moment it
 * boots, with no reload.
 *
 * A FAILED READ BACKS OFF (L1-fix). A network hiccup, a non-2xx, or an "available: false" body (a node
 * that is down) stops the normal interval and retries on a GROWING delay (2s doubling to 60s) — the
 * console's own live discipline (static/shell.js) — so a node that is down is not hammered once per
 * tick by every open tab. The status line says "retrying…" while it waits, and the first success drops
 * back to the normal interval. A hidden tab clears the pending retry too, so nothing polls when hidden.
 *
 * Loaded by server_detail.html on every tab; it does nothing unless the log card is present.
 */
(function () {
  "use strict";

  var card = document.getElementById("dcs-log-card");
  if (!card) { return; }

  var body = document.getElementById("dcs-log-body");
  var noteEl = document.getElementById("dcs-log-note");
  var noteText = document.getElementById("dcs-log-note-text");
  var statusEl = document.getElementById("dcs-log-status");
  var olderBtn = document.getElementById("dcs-log-older");
  var windowUrl = card.getAttribute("data-window-url");
  var offset = parseInt(card.getAttribute("data-offset"), 10);
  var before = parseInt(card.getAttribute("data-before"), 10);
  var identity = card.getAttribute("data-identity");
  var level = card.getAttribute("data-level");
  var which = card.getAttribute("data-which");
  var followSeconds = parseInt(card.getAttribute("data-follow-seconds"), 10);

  if (!windowUrl) { return; }
  if (isNaN(offset) || offset < 0) { offset = 0; }
  if (isNaN(before) || before < 0) { before = 0; }
  if (isNaN(followSeconds) || followSeconds < 1) { followSeconds = 3; }

  /* No body means the card is not the one this script renders into: nothing to follow. The server
     ALWAYS ships the body now (even in the empty state), so this is a safety net, not the old bail. */
  if (!body) { return; }

  var RETRY_MS = followSeconds * 1000;
  /* A failed read backs off: the wait grows (BACKOFF_MS, doubled, capped at BACKOFF_MAX_MS) so a node
     that is down is not hammered once per tick by every open tab, and it drops back to the normal
     interval the moment a read succeeds. */
  var BACKOFF_MS = 2000;
  var BACKOFF_MAX_MS = 60000;

  var timer = null;        /* the follow interval, armed while the tab is visible and reads succeed */
  var retryTimer = null;   /* the ONE pending backoff retry, while a read is failing */
  var inFlight = false;
  var backoff = BACKOFF_MS;

  function setStatus(text) { if (statusEl) { statusEl.textContent = text; } }

  /* THE EMPTY SENTENCE BESIDE THE BODY (L3). `showNote` reveals the honest sentence ("no log file
     yet…", a node failure, a filtered-empty page); `hideNote` is called the moment real lines render.
     The note lives in its own element, so revealing it never touches the lines already shown. */
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

  /* Prepend older lines while keeping the reader's place: anchor on the line that was first before the
     insert and restore its distance from the top afterwards. */
  function prepend(html) {
    if (!html) { return; }
    hideNote();
    var anchor = body.firstElementChild;
    var prior = anchor ? anchor.offsetTop : 0;
    body.insertAdjacentHTML("afterbegin", html);
    if (anchor) { body.scrollTop += (anchor.offsetTop - prior); }
  }

  /* The identity we hold, echoed so the server can tell a ROTATION from an append. Absent when the
     page shipped none (the card rendered no identity) — then the server falls back to the size check. */
  function identityParam() {
    return (identity === null || identity === "") ? "" : "&identity=" + encodeURIComponent(identity);
  }

  /* The level and the file selector the card shipped ride on EVERY request, so the server filters the
     window the same way the page rendered it and (on the Events tab) reads events.log. Absent when the
     page shipped none — the server then applies its own default. */
  function levelParam() {
    return (level === null || level === "") ? "" : "&level=" + encodeURIComponent(level);
  }

  function whichParam() {
    return (which === null || which === "") ? "" : "&which=" + encodeURIComponent(which);
  }

  /* request(query, handlers): handlers.ok is called ONLY for a successful, AVAILABLE answer;
     handlers.pending for a NORMAL "no log file yet" answer (``pending: true`` — the follower keeps its
     normal cadence); handlers.fail for anything else — a network failure, a non-2xx response, or an
     "available: false" body WITHOUT the pending flag (a node that is down), which drives the backoff. */
  function request(query, handlers) {
    fetch(windowUrl + query, { headers: { "accept": "application/json" }, credentials: "same-origin" })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        if (payload && payload.available) { handlers.ok(payload); return; }
        if (payload && payload.pending) { handlers.pending(payload); return; }
        handlers.fail(payload);
      })
      .catch(function () { handlers.fail(null); });
  }

  function showUnavailable(payload) {
    setStatus(payload && payload.message ? payload.message : "not available");
  }

  /* A NORMAL "no log file yet" answer (L3): keep the body clear and show the honest sentence, but do
     NOT back off — the file may appear on the very next tick. The body is cleared so a log that
     DISAPPEARED leaves no stale tail behind. */
  function applyPending(payload) {
    clearBody();
    showNote(payload && payload.message ? payload.message : "The log file is not there yet.");
    setStatus("waiting for the log file to appear");
    backoff = BACKOFF_MS;
  }

  /* A failed read: drop the normal interval and retry on a growing delay, saying so. When the tab is
     hidden the retry is NOT armed — stop() already cleared the interval and nothing may poll. */
  function scheduleRetry(payload) {
    if (timer) { window.clearInterval(timer); timer = null; }
    if (document.hidden) { return; }
    var message = payload && payload.message ? payload.message : "";
    var delay = Math.min(backoff, BACKOFF_MAX_MS);
    setStatus((message ? message + " \u2014 " : "") + "retrying\u2026");
    retryTimer = window.setTimeout(function () {
      retryTimer = null;
      if (document.hidden) { return; }
      followOnce();
      if (!timer) { timer = window.setInterval(followOnce, RETRY_MS); }
    }, delay);
    backoff = delay * 2;
  }

  function applyFollow(payload) {
    if (payload.reset) {
      /* A NEW log (truncated or rotated): clear the view and say so — never splice two files. */
      clearBody();
      var note = document.createElement("div");
      note.className = "ln";
      note.textContent = "\u2014 the log was rotated; showing the new file \u2014";
      body.appendChild(note);
    }
    append(payload.lines);
    /* REAL LINES ARE HERE: the empty sentence (if any) gives way to the log — the empty state BECOMES
       the log, which is the whole point of L3. */
    if (payload.lines) { hideNote(); }
    if (typeof payload.offset === "number") { offset = payload.offset; }
    if (payload.reset && typeof payload.before === "number") { before = payload.before; }
    /* keep the identity we hold in step with the file the server actually read this tick — it is an
       opaque STRING token (a big inode must not be parsed as a number and lose its low bits) */
    if (payload.identity !== undefined && payload.identity !== null && payload.identity !== "") {
      identity = String(payload.identity);
    }
    if (olderBtn && olderBtn.disabled && typeof payload.before === "number" && payload.before > 0) {
      olderBtn.disabled = false;
      olderBtn.textContent = "Load older";
    }
    setStatus(payload.message ? payload.message : "following while this tab is open");
  }

  function followOnce() {
    if (inFlight) { return; }
    inFlight = true;
    request("?offset=" + encodeURIComponent(offset) + identityParam() + levelParam() + whichParam(),
            {
      ok: function (payload) {
        inFlight = false;
        backoff = BACKOFF_MS;               /* a good read: the next failure starts the backoff over */
        applyFollow(payload);
      },
      pending: function (payload) {
        inFlight = false;
        /* the file is not there yet (a server still starting): a NORMAL state — keep the interval
           running and show the sentence; the log appears on its own the moment it exists */
        applyPending(payload);
      },
      fail: function (payload) {
        inFlight = false;
        scheduleRetry(payload);
      },
    });
  }

  function start() {
    if (timer) { return; }
    backoff = BACKOFF_MS;
    if (retryTimer) { window.clearTimeout(retryTimer); retryTimer = null; }
    setStatus("following while this tab is open");
    followOnce();
    timer = window.setInterval(followOnce, RETRY_MS);
  }

  function stop() {
    if (timer) { window.clearInterval(timer); timer = null; }
    if (retryTimer) { window.clearTimeout(retryTimer); retryTimer = null; }
    setStatus("paused \u2014 this tab is hidden");
  }

  function loadOlder() {
    if (olderBtn) { olderBtn.disabled = true; }
    var priorBefore = before;
    request("?behind=" + encodeURIComponent(before) + levelParam() + whichParam(),
            {
      ok: function (payload) {
        if (payload.reset) {
          /* A ROTATION LANDED DURING THE WALK: the server ended the page there, so the lines it
             returned are the PREVIOUS file's alone (two files are never spliced). The view resets and
             says the log restarted, and there is no older page to offer — the file at this path is now
             a different one. */
          clearBody();
          var note = document.createElement("div");
          note.className = "ln";
          note.textContent = "\u2014 the log was rotated while loading older lines \u2014";
          body.appendChild(note);
          body.insertAdjacentHTML("beforeend", payload.lines);
          if (payload.lines) { hideNote(); }
          if (payload.identity !== undefined && payload.identity !== null && payload.identity !== "") {
            identity = String(payload.identity);
          }
          before = 0;
          if (olderBtn) { olderBtn.disabled = true; olderBtn.textContent = "Log restarted"; }
          setStatus(payload.message ? payload.message : "the log was rotated");
          return;
        }
        prepend(payload.lines);
        var next = (typeof payload.before === "number") ? payload.before : before;
        var moved = next < priorBefore;
        before = next;
        /* THE FILTER NEVER LIES: ``at_start`` says the walk reached the beginning of the file (there is
           no older matching line), so the button says so rather than offer a page that would be empty. */
        if (payload.at_start || !payload.lines || !moved || before <= 0) {
          if (olderBtn) { olderBtn.disabled = true; olderBtn.textContent = "Start of log"; }
        } else if (olderBtn) {
          olderBtn.disabled = false;
        }
        if (payload.message) { setStatus(payload.message); }
      },
      pending: function (payload) {
        /* the file is not there yet: a page-back is not the follow loop's failure — re-enable the
           button and say why, leaving the follow cadence untouched */
        if (olderBtn) { olderBtn.disabled = false; }
        showUnavailable(payload);
      },
      fail: function (payload) {
        /* A failed page-back is NOT the follow loop's failure: re-enable the button and say why,
           leaving the follow cadence untouched (the reader can ask again). */
        if (olderBtn) { olderBtn.disabled = false; }
        showUnavailable(payload);
      },
    });
  }

  if (olderBtn) { olderBtn.addEventListener("click", loadOlder); }

  if (typeof document.hidden !== "undefined") {
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { stop(); } else { start(); }
    });
    if (document.hidden) { stop(); } else { start(); }
  } else {
    /* no Page Visibility API: follow for as long as the page is open (the only available notion) */
    start();
  }
  window.addEventListener("pagehide", stop);
})();
