/* THE CONSOLE'S ONE SUBMIT INTERCEPTOR — the confirmation dialog and the row strip.
 *
 * WHY ONE COPY, AND WHERE IT LIVES. The submit interception was inline in `confirm.html` (defect 2) and
 * reached THAT page only. The strip's controls are on EVERY page that renders a row — and the strip is
 * the control actually pressed on the live dashboard, where a plain `<form method="post">` still held
 * the browser until the action had already finished, so no rendered row existed to pulse (the signal
 * only exists on a render). The behaviour the two need is the same, so it is ONE script here, loaded by
 * `base.html` on EVERY page (the dialog extends base.html too), rather than a second copy in the strip's
 * markup: two copies would be two places for the same defect to reappear, and the dialog's own inline
 * block was the first one. This file is served by the shell's asset path (`/static/submit.js`).
 *
 * WHAT IT DOES NOT CHANGE. The form stays a real `method="post"` form: with JavaScript off the browser
 * posts it and follows the 303 exactly as before. This is progressive enhancement only. The request is
 * byte-for-byte the form's own — the SAME body (every hidden field, the CSRF field, the target and the
 * origin), the SAME method and the SAME destination — sent with `fetch` instead of the page's own
 * navigation. `redirect: "manual"` keeps fetch from following the 303 itself (the outcome belongs to the
 * page the person is on, not to a promise).
 *
 * TWO KINDS OF FORM, TWO WAYS BACK — and the form itself says which, by whether it STATES where it
 * returns to (`data-back`):
 *
 *  * THE DIALOG (the form carries `data-back`) CLOSES THE MOMENT THE BUTTON IS PRESSED. The confirmation
 *    is its own page (`confirm.html`), so "closing the dialog" is leaving it: the navigation to the page
 *    the dialog states happens AT ONCE, on the press, and the write keeps running in the background
 *    (`keepalive` lets an in-flight request outlive the document, so abandoning the page does not
 *    abandon the write). The modal is gone the instant the person pressed the button — the behaviour the
 *    earlier design had and the one Frank asks for. Its outcome is NOT swallowed by the early close:
 *    every refusal is decided by a ROUTE and reaches the person through the console's own ONE-SHOT
 *    NOTICE, rendered where they land and on the LIVE path (`pages/actions._remember_notice` /
 *    `pages/live.py`'s `notice` target) — the same mechanism every other write already uses. (A NETWORK
 *    failure is an honest no-op: the write was attempted once and never retried.)
 *  * A FORM THAT STAYS ON ITS PAGE (the config/revert/channels/coalitions forms and the strip's DIRECT
 *    controls) does not state a `data-back`, so it is NOT a dialog being dismissed. It navigates back to
 *    the page it is on — path and query, no fragment — but only ONCE THE WRITE HAS ANSWERED
 *    (`redirect: "manual"` resolves when the server's answer is on the wire, i.e. AFTER the route has run
 *    the action and committed it). That is the delete-button rule: the fresh render such a write returns
 *    to reads the state the action CHANGED, not the pre-write state it was rendered from.
 *
 * A REFUSAL IS NEVER LOST. Every refusal is decided by a ROUTE (or refused by the console's access gate)
 * and reaches the person through the ONE-SHOT NOTICE, rendered where they land and on the live path —
 * the SAME mechanism the strip's outcome has always used. The routes that refused without leaving a
 * notice (no identity, a spent/absent confirm token, an out-of-scope target) store one before refusing,
 * so a refusal made in the background is seen rather than swallowed.
 *
 * IT NEVER DISABLES THE PRESSED CONTROL (the W4g rule): the server says what is running, and the action
 * seam's guard refusing a second press — with its own typed refusal — is already the answer to a double
 * click. A disabled button would be a second, silent refusal path. WHAT IT DOES INSTEAD, while a write
 * that STAYS ON ITS PAGE is in flight, is wear the console's own BUSY IDIOM: the `busy` class the
 * stylesheet animates (so the wait is visible) and the `aria-busy` a screen reader reads, on the control
 * that was pressed. And a SECOND press while one is pending is IGNORED — a confirm token is SPENT by the
 * first POST, so a second could only be refused; swallowing it is not a second, silent refusal path
 * (nothing runs), it is one press, one request.
 */
(function () {
  "use strict";

  /* Every write form the console wants submitted in the background carries this attribute. It is
     opt-in on purpose: a form that NAVIGATES rather than writes — the row's confirm-required control
     posts to its dialog's path and the answer IS the dialog — must stay a plain form, or the dialog
     would never be shown. */
  var ASYNC = "form[data-async]";

  /* The forms whose write is currently in flight — one request per press. A WeakSet so nothing
     leaks and no DOM attribute is invented to remember it. */
  var pending = new WeakSet();

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
    /* Where a write returns. The dialog states it (`data-back`); a form that stays on its page returns
       to the page it is on — the path and query the bar carries (tab, level, sort), WITHOUT the
       fragment, which is no part of a server render and would make the navigation a no-op when the bar
       still has one. The route resolves its own destination server-side for the no-JS path; this is
       only where the ENHANCED path lands. */
    var stated = form.getAttribute("data-back");
    return stated ? stated : window.location.pathname + window.location.search;
  }

  /* THE WAIT IS VISIBLE, for a write that STAYS on its page: the control the person pressed wears the
     console's BUSY IDIOM (the `busy` class the stylesheet already animates + the `aria-busy` a screen
     reader reads) while the write is in flight. It is added HERE, on the real control, and removed when
     the answer lands (just before the navigation) so the fresh page is never painted with a stale
     pulse. A form submitted with Enter has no submitter: nothing to mark, and the write still runs and
     navigates. A DIALOG never wears it: it leaves on the press, so there is no wait to show. */
  function busy(control) {
    if (!control || !control.classList) { return; }
    control.classList.add("busy");
    control.setAttribute("aria-busy", "true");
  }

  function settled(control) {
    if (!control || !control.classList) { return; }
    control.classList.remove("busy");
    control.removeAttribute("aria-busy");
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches(ASYNC)) { return; }
    event.preventDefault();
    /* ONE press, ONE request: a second press while this form's write is still pending is ignored
       (nothing runs, so it is not a second refusal path). */
    if (pending.has(form)) { return; }
    pending.add(form);

    var to = destination(form);
    var control = event.submitter;
    /* THE DIALOG'S OWN DESTINATION is `data-back` (see `destination`). Its presence is what says this
       form IS a dialog being dismissed — a page that must close on the press — as opposed to a form
       that stays and waits for a fresh render. */
    var goingBack = form.getAttribute("data-back");

    /* The SAME body, method and destination the browser would have sent — with `redirect: "manual"`
       so fetch does not follow the 303 itself. `keepalive` lets a write the person abandons by closing
       the dialog finish server-side. */
    var reply = fetch(form.action, {
      method: "POST",
      body: payload(form, event.submitter),
      credentials: "same-origin",
      redirect: "manual",
      keepalive: true,
      headers: { "Accept": "application/json" }
    });

    /* THE DIALOG CLOSES THE MOMENT THE BUTTON IS PRESSED: leave for the stated page AT ONCE, with the
       write still running in the background. No busy idiom is worn — there is no visible wait to show
       — and the outcome reaches the person through the one-shot notice on the page they land on (and on
       the live path). */
    if (goingBack) {
      /* the write was attempted once and the page is leaving: a network failure has nowhere to be
         shown, and it must not surface as an unhandled promise rejection (nothing is retried) */
      reply.catch(function () {});
      window.location.assign(to);
      return;
    }

    /* A FORM THAT STAYS ON ITS PAGE: the wait is shown on the pressed control, and the navigation
       happens ONCE THE WRITE HAS ANSWERED — not alongside it. This is what makes the delete button
       refresh its list: the answer (303 or refusal) is only sent after the route has run and committed
       the action, so the page this lands on is rendered from the state the action CHANGED. Both
       outcomes navigate: an accepted write lands on the fresh render, a refusal lands on the page whose
       one-shot notice explains it, and a NETWORK failure is an honest no-op (the write was attempted
       once and never retried). */
    busy(control);
    function leave() {
      settled(control);
      window.location.assign(to);
    }
    reply.then(leave, leave);
  });
})();
