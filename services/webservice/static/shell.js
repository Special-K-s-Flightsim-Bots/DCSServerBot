/* The dashboard's live updates: swap server-rendered fragments, with a polling fallback.
 *
 * Loaded by base.html only when the page passes a `live` context (the dashboard, with
 * `dashboard.live` on). It is a PROGRESSIVE ENHANCEMENT: the initial page already contains the log
 * entries and the server rows, so with JavaScript disabled nothing is missing — the page is simply
 * not live.
 *
 * The transport is Server-Sent Events. The server sends HTML fragments rendered by the SAME Jinja
 * partials the page uses, as one line of JSON per frame:
 *   snapshot -> {"targets": {id: html, ...}}   (immediately on connect)
 *   log      -> {"targets": {"log-panel": html}}     (only when the log fragment changed)
 *   state    -> {"targets": {"state-panel": html, "status-pills": html}}
 *   the ONE-SHOT notice of a write ("notice") is a target too: a strip control now submits in the
 *   background and hands the page to a fresh render at once, so its outcome is written a
 *   moment AFTER that render — the live path is what carries it to the person. An id absent from a
 *   frame is skipped, so a frame sent with no notice never blanks one the page already showed.
 *
 * If the stream cannot be used (no EventSource, or the connection errors — a proxy stripping
 * `text/event-stream` does this), it falls back to polling the fragment endpoint, so the page still
 * updates. The indicator says which state it is in, IN WORDS.
 *
 * THE STREAM BELONGS TO A PAGE THAT IS SHOWN. A page the reader navigates away from may be parked in
 * the browser's back/forward cache rather than destroyed, and an open connection rides into that
 * cache — one of the six the browser allows per origin, held for a page nobody is looking at. The
 * stream is CLOSED when the page is PARKED (``pagehide`` with ``persisted`` true — the browser's own
 * statement that the document stays alive) and reopened when it comes back, so a console cannot
 * starve its own later requests (a click's navigation, an asset, a fragment poll). A ``pagehide``
 * that is NOT a park closes nothing: that document is being destroyed and its connection goes with
 * it, so the only thing a close could do is leave a surviving page (a navigation that did not
 * complete) with no stream and no ``pageshow`` to reopen it.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) { return; }

  /* THE STRINGS ARE SERVED, NOT WRITTEN HERE (``/i18n/messages.js``, included by ``base.html``
     before this script). ``window.dcssbMsg`` looks a key up and swaps a value in; this file carries
     no user-visible string and no translation logic. */
  function t(key, params) {
    return window.dcssbMsg ? window.dcssbMsg(key, params) : key;
  }

  var streamUrl = script.getAttribute("data-stream");
  var fragmentsUrl = script.getAttribute("data-fragments");
  var statusEl = document.getElementById("live-status");
  var TARGETS = ["log-panel", "state-panel", "status-pills", "notice"];
  var RETRY_MS = 10000;
  /* A 503 means the identity could not be VERIFIED RIGHT NOW (the bot is restarting, it has no
     member list): that passes. Stopping would be wrong — the server KEPT the session — so the
     client waits it out with a growing delay and resumes on its own once the answer is a 200.
     The delay is capped so a long outage is still noticed within a minute or two. */
  var BACKOFF_MS = 2000;
  var BACKOFF_MAX_MS = 60000;

  function setStatus(word) {
    if (statusEl) { statusEl.textContent = t("live.status", { status: word }); }
  }

  /* Only auto-scroll a reader who is already at the bottom: someone who scrolled up to read must
     not be yanked back down by the next log line. */
  function atBottom(el) {
    return el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
  }

  function apply(targets) {
    if (!targets) { return; }
    var before = document.getElementById("log-body");
    var keep = before ? atBottom(before) : false;
    for (var i = 0; i < TARGETS.length; i++) {
      var id = TARGETS[i];
      if (!Object.prototype.hasOwnProperty.call(targets, id)) { continue; }
      var el = document.getElementById(id);
      if (el) { el.innerHTML = targets[id]; }
    }
    var after = document.getElementById("log-body");
    if (keep && after) { after.scrollTop = after.scrollHeight; }
  }

  function handle(event) {
    var payload = null;
    try { payload = JSON.parse(event.data); } catch (err) { return; }
    apply(payload && payload.targets);
  }

  var source = null;
  var poll = null;
  /* the growing wait between retries while the server answers a retryable refusal */
  var backoff = BACKOFF_MS;

  /* The way back in. Rendered as a real link (never HTML built from a URL), pointing at the sign-in
     page and carrying the page the reader is on, so after signing in they land back here. */
  function offerSignIn() {
    if (!statusEl || !statusEl.parentNode) { return; }
    if (document.getElementById("live-signin")) { return; }
    var link = document.createElement("a");
    link.id = "live-signin";
    link.className = "live-signin";
    link.href = "/auth/login?next=" + encodeURIComponent(window.location.pathname);
    link.textContent = t("live.sign_in_again");
    statusEl.parentNode.insertBefore(link, statusEl.nextSibling);
  }

  /* STOP the loop, honestly. An expired session refuses every poll, so continuing would hammer the
     server — and its log — once per interval, forever. Stopping and saying so is the whole point.
     A 503 is NOT that: it is retryable (see pollOnce). */
  function stopPolling(reason) {
    if (poll) { clearInterval(poll); poll = null; }
    setStatus(reason);
    offerSignIn();
  }

  /* Retry a RETRYABLE refusal (503): the identity could not be verified yet. The wait grows
     (BACKOFF_MS, doubled, capped at BACKOFF_MAX_MS) so a long restart does not hammer the server,
     and it drops back to the normal interval as soon as an answer arrives. */
  function backOff() {
    if (poll) { clearInterval(poll); poll = null; }
    var delay = Math.min(backoff, BACKOFF_MAX_MS);
    setStatus(t("live.not_ready", { seconds: Math.round(delay / 1000) }));
    setTimeout(function () {
      pollOnce();
      if (!poll) { poll = setInterval(pollOnce, RETRY_MS); }
    }, delay);
    backoff = delay * 2;
  }

  function pollOnce() {
    fetch(fragmentsUrl, { headers: { "accept": "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 401 || response.status === 403) {
          stopPolling(t("live.not_permitted"));
          return null;
        }
        if (response.status === 503) {
          /* the Retry-After the server sends is honoured as a FLOOR: retrying sooner than the
             server asked would be the hammering this whole path exists to avoid */
          var asked = parseInt(response.headers.get("retry-after"), 10);
          if (!isNaN(asked) && asked * 1000 > backoff) { backoff = asked * 1000; }
          backOff();
          return null;
        }
        backoff = BACKOFF_MS;
        return response.ok ? response.json() : null;
      })
      .then(function (payload) { apply(payload); })
      .catch(function () {});
  }

  function startPolling() {
    if (poll) { return; }
    setStatus(t("live.polling"));
    pollOnce();
    poll = setInterval(pollOnce, RETRY_MS);
  }

  function connect() {
    if (!streamUrl) { return; }
    if (!window.EventSource) { startPolling(); return; }
    setStatus(t("live.connecting"));
    source = new EventSource(streamUrl);
    source.addEventListener("snapshot", function (event) { setStatus(t("live.on")); handle(event); });
    source.addEventListener("log", handle);
    source.addEventListener("state", handle);
    source.onopen = function () { setStatus(t("live.on")); };
    source.onerror = function () {
      // EventSource would retry by itself; we close it and use the polling fallback instead, so a
      // proxy that strips the stream still keeps the page current.
      if (source) { source.close(); source = null; }
      startPolling();
    };
  }

  /* A PAGE THAT IS PARKED KEEPS ITS CONNECTION — and only a PARKED page does. ``pagehide`` fires for a
     page the browser stores in its back/forward cache (``event.persisted`` is true: the document stays
     alive, so the open stream rides into the cache and holds one of the browser's six connections per
     origin) AND for a page that is being DESTROYED (``persisted`` false: the connection goes with the
     document, so closing it frees nothing, while a navigation that does not complete — a cancelled or
     refused one, a same-document restore — would leave a SURVIVING page with no stream and no
     ``pageshow`` to reopen it). So the close is gated on the browser's own statement that this
     document stays alive, and the park is the only case the leak fix needs. */
  function suspend(event) {
    if (event && event.persisted !== true) { return; }
    if (source) { source.close(); source = null; }
    if (poll) { clearInterval(poll); poll = null; }
  }

  window.addEventListener("pagehide", suspend);

  /* A RESTORED PAGE GETS ITS STREAM BACK — whatever ran while it was parked. The polling fallback can
     have been armed in the meantime (a 503 backoff, or the abort ``error`` a navigation fires on the
     stream), and it must not be able to suppress the reconnect: it is a different transport, and it can
     stop for good (an expired session answers 401/403 and ``stopPolling`` never restarts it), which
     would leave the page with no live transport at all until a reload. The stream is the transport
     this page wants, so the fallback is dropped rather than kept, and the two never both run. */
  window.addEventListener("pageshow", function () {
    if (source) { return; }
    if (poll) { clearInterval(poll); poll = null; }
    connect();
  });

  connect();
})();
