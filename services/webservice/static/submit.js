/* THE CONSOLE'S ONE SUBMIT INTERCEPTOR (the DIALOG path was corrected separately) — the
 * confirmation dialog and the row strip.
 *
 * WHY ONE COPY, AND WHERE IT LIVES. The submit interception was inline in `confirm.html` (* Defect 2) and reached THAT page only. The strip's controls are on EVERY page that renders a row —
 * and the strip is the control actually pressed on the live dashboard, where a plain
 * `<form method="post">` still held the browser until the action had already finished, so no
 * rendered row existed to pulse ('s signal only exists on a render). The behaviour the two
 * need is the same one behaviour, so it is ONE script here, loaded by `base.html` on EVERY page
 * (the dialog extends base.html too), rather than a second copy in the strip's markup: two copies
 * would be two places for the same defect to reappear, and the dialog's own inline block was the
 * first one. This file is served by the shell's asset path (`/static/submit.js`) like every other
 * asset.
 *
 * WHAT IT DOES NOT CHANGE. The form stays a real `method="post"` form: with JavaScript off the
 * browser posts it and follows the 303 exactly as before. This is progressive enhancement only.
 * The request is byte-for-byte the form's own — the SAME body (every hidden field, the CSRF field,
 * the target and the origin), the SAME method and the SAME destination — sent with `fetch` instead
 * of the page's own navigation. `redirect: "manual"` keeps fetch from following the 303 itself (the
 * outcome belongs to the page the person is on, not to a promise), and `keepalive: true` lets the
 * request OUTLIVE the navigation below: uvicorn does not cancel an abandoned handler (measured, see
 * `~/.hermes/cache/scratch/probe-disconnect.py`), and a request whose client has gone away still
 * runs — which is what makes handing the page over to a fresh render safe.
 *
 * NO CONTROL WAITS FOR THE ANSWER (the maintainer's third report). W4e/W4h gave the DIALOG a
 * different path from the strip on purpose — the dialog stayed put and showed a refusal in its own
 * notice line — and that is exactly the behaviour he rejected twice ("the modals ... still do not
 * vanish, the moment I press the button that triggers the action"). A shutdown is the one write
 * where holding the person on a frozen page is worst, and the wait bought nothing the page's own
 * route-level mechanism does not already deliver: on press, BOTH controls now fire the POST and
 * leave for their destination at once. The dialog carries `data-back` (the page it was opened from)
 * and a strip control returns to the page it is on; the fresh render lands on the row while the
 * action is still in flight, which is the whole point of the pulse.
 *
 * A REFUSAL IS NOT LOST, and it is no longer the dialog's job to show one. Every refusal is decided
 * by a ROUTE (or refused by the console's access gate) and reaches the person through the console's
 * existing route-level mechanism: the ONE-SHOT NOTICE, rendered where they land and on the live path
 * (`pages/actions._remember_notice` / `pages/live.py`'s `notice` target) — the SAME mechanism the
 * strip's outcome has always used. The routes that refused without leaving a notice (no identity, a
 * spent/absent confirm token, an out-of-scope target) now store one before refusing, so
 * a refusal made in the background is seen rather than swallowed.
 *
 * IT NEVER DISABLES THE PRESSED CONTROL (item 3, W4g's rule): the server says what is
 * running, and the action seam's guard refusing a second press — with its own typed refusal — is
 * already the answer to a double click. A disabled button would be a second, silent refusal path.
 */
(function () {
  "use strict";

  /* Every write form the console wants submitted in the background carries this attribute. It is
     opt-in on purpose: a form that NAVIGATES rather than writes — the row's confirm-required control
     posts to its dialog's path and the answer IS the dialog — must stay a plain form, or the dialog
     would never be shown. */
  var ASYNC = "form[data-async]";

  function payload(form, submitter) {
    /* The EXACT body the browser would send. `new FormData(form)` carries every hidden field; a
       named submit button (the message control's two modes are one form with `name=\"mode\"`) is the
       SUBMITTER and is not part of that list, so it is appended here — the mode a person clicked is
       part of the request and must not be lost to the interception. */
    var data = new FormData(form);
    if (submitter && submitter.name && !data.has(submitter.name)) {
      data.append(submitter.name, submitter.value);
    }
    return data;
  }

  function destination(form) {
    /* Where a write returns. The dialog states it (`data-back`); a strip control returns to the page
       it is on — the path and query the bar carries (tab, level, sort), WITHOUT the fragment, which
       is no part of a server render and would make the navigation a no-op when the bar still has one.
       The route resolves its own destination server-side for the no-JS path; this is only where the
       ENHANCED path lands. */
    var stated = form.getAttribute("data-back");
    return stated ? stated : window.location.pathname + window.location.search;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches(ASYNC)) { return; }
    event.preventDefault();

    var to = destination(form);
    /* ONE request per press, and nothing retried: a confirm token is SPENT by the POST, so a retry
       would only be refused a second time. The request keeps running (uvicorn does not cancel an
       abandoned handler; the probe above) and `keepalive` keeps the browser sending it, while the
       page below is handed over to the fresh render of the state the action is changing. */
    var reply = fetch(form.action, {
      method: "POST",
      body: payload(form, event.submitter),
      credentials: "same-origin",
      redirect: "manual",
      keepalive: true,
      headers: { "Accept": "application/json" }
    });
    /* The answer is not awaited: the refusal it may carry reaches the person through the
       one-shot notice on the page they land on, and the accepted write's effect reaches them through
       the row's own server-rendered signal. We only silence an unhandled rejection — a network
       failure is a rare, honest no-op, and nothing here can be retried. */
    reply.catch(function () {});
    window.location.assign(to);
  });
})();
