/* The server's STATUS, kept current while the page is open (L5) — the mark at the TOP of the server
 * page (the page head's dot + word, and the Overview card's copy of the same fact).
 *
 * A PROGRESSIVE ENHANCEMENT. The initial page already contains the status the server rendered, so
 * with JavaScript off nothing is missing — the page simply does not refresh the status on its own.
 *
 * THE DEFECT IT FIXES (Frank's report). On a server page the status moved only when the page was
 * RELOADED (a tab switch), so an operator who started a server and stayed on the DCS Log tab watched
 * it sit on LOADING forever. The page head's status was a server render and nothing refreshed it:
 * the server page is NOT wired to the console's live path (the dashboard's SSE stream), and on the
 * log tabs the only script that ran was the log follower, which owns the log card alone. This script
 * is the status's OWN poll — the same discipline the log follower uses (a normal cadence, a growing
 * BACKOFF on a failure, and an immediate stop when the browser tab is hidden), kept separate from the
 * log on purpose: one fact (the status) gets ONE updater, so the log and the status can never drift.
 *
 * IT UPDATES EVERY `[data-server-status]` ELEMENT with the markup the server returns — the SAME
 * partial (`_server_status.html`) the page rendered from, so a polled status and a rendered status
 * cannot disagree. The URL and the cadence ride on the page head's `data-server-status-url` /
 * `data-server-status-seconds` (the way the log card carries its own window URL and follow seconds).
 *
 * IT RUNS ON EVERY TAB. The page head is outside the tab branches, so the mark is on every tab of the
 * server page and this script keeps it current wherever the operator is reading. While the LOG tab is
 * open both scripts run — the follower polls the log and this one polls the status; neither stops the
 * other (they are separate intervals on separate elements).
 *
 * IT STOPS WHEN THE BROWSER TAB IS HIDDEN (the console's rule: nothing polls in a background tab) and
 * resumes on its own when the tab is shown again.
 *
 * Loaded by server_detail.html on every tab; it does nothing unless a status region is present.
 */
(function () {
  "use strict";

  var urlEl = document.querySelector("[data-server-status-url]");
  var regions = document.querySelectorAll("[data-server-status]");
  if (!urlEl || !regions.length) { return; }

  var url = urlEl.getAttribute("data-server-status-url");
  if (!url) { return; }
  var seconds = parseInt(urlEl.getAttribute("data-server-status-seconds"), 10);
  if (isNaN(seconds) || seconds < 1) { seconds = 10; }

  var RETRY_MS = seconds * 1000;
  /* a failed poll backs off: the wait grows (BACKOFF_MS, doubled, capped) so a console that cannot
     answer is not hammered once per tick, and it drops back the moment a poll succeeds. */
  var BACKOFF_MS = 2000;
  var BACKOFF_MAX_MS = 60000;

  var timer = null;        /* the poll interval, armed while the tab is visible and polls succeed */
  var retryTimer = null;   /* the ONE pending backoff retry, while a poll is failing */
  var inFlight = false;
  var backoff = BACKOFF_MS;

  function paint(html) {
    if (typeof html !== "string") { return; }
    for (var i = 0; i < regions.length; i++) { regions[i].innerHTML = html; }
  }

  function pollOnce() {
    if (inFlight) { return; }
    inFlight = true;
    fetch(url, { headers: { "accept": "application/json" }, credentials: "same-origin" })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        inFlight = false;
        if (!payload || typeof payload.status !== "string") { fail(); return; }
        backoff = BACKOFF_MS;
        paint(payload.status);
      })
      .catch(function () { inFlight = false; fail(); });
  }

  /* A failed poll: drop the normal interval and retry on a growing delay. A hidden tab arms nothing —
     stop() has already cleared the interval and nothing may poll. */
  function fail() {
    if (timer) { window.clearInterval(timer); timer = null; }
    if (document.hidden) { return; }
    var delay = Math.min(backoff, BACKOFF_MAX_MS);
    retryTimer = window.setTimeout(function () {
      retryTimer = null;
      if (document.hidden) { return; }
      pollOnce();
      if (!timer) { timer = window.setInterval(pollOnce, RETRY_MS); }
    }, delay);
    backoff = delay * 2;
  }

  function start() {
    if (timer) { return; }
    if (!regions.length) { return; }
    backoff = BACKOFF_MS;
    if (retryTimer) { window.clearTimeout(retryTimer); retryTimer = null; }
    pollOnce();
    timer = window.setInterval(pollOnce, RETRY_MS);
  }

  function stop() {
    if (timer) { window.clearInterval(timer); timer = null; }
    if (retryTimer) { window.clearTimeout(retryTimer); retryTimer = null; }
  }

  if (typeof document.hidden !== "undefined") {
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { stop(); } else { start(); }
    });
    if (document.hidden) { stop(); } else { start(); }
  } else {
    /* no Page Visibility API: poll for as long as the page is open (the only available notion) */
    start();
  }
  window.addEventListener("pagehide", stop);
})();
