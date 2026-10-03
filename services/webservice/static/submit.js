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
 * outcome belongs to the page the person is on, not to a promise).
 *
 * THE PAGE COMES BACK ONCE THE WRITE HAS ANSWERED (the maintainer's delete-button report). W4e/W4g
 * fired the POST and left for the destination AT ONCE, on the assumption that the fresh render the
 * navigation landed on would read the state the action was changing. It does not: the fresh page is
 * rendered from the pre-write state whenever the write has not committed yet, so a DELETE that takes
 * a moment still listed the removed row until a manual refresh. So the navigation now happens ONCE
 * THE ANSWER HAS ARRIVED — `redirect: "manual"` resolves when the server's answer (the 303, or a
 * refusal) is on the wire, i.e. AFTER the route has run the action and committed it. `keepalive`
 * stays on the request, though it is no longer what holds the write open (the wait does): it still
 * lets a write the person abandons by closing the tab finish server-side.
 *
 * A REFUSAL IS NOT LOST, and it is no longer the dialog's job to show one. Every refusal is decided
 * by a ROUTE (or refused by the console's access gate) and reaches the person through the console's
 * existing route-level mechanism: the ONE-SHOT NOTICE, rendered where they land and on the live path
 * (`pages/actions._remember_notice` / `pages/live.py`'s `notice` target) — the SAME mechanism the
 * strip's outcome has always used. The routes that refused without leaving a notice (no identity, a
 * spent/absent confirm token, an out-of-scope target) now store one before refusing, so
 * a refusal made in the background is seen rather than swallowed. A refusal ANSWERS, so it navigates
 * exactly like an accepted write: the outcome is the page they land on.
 *
 * IT NEVER DISABLES THE PRESSED CONTROL (item 3, W4g's rule): the server says what is
 * running, and the action seam's guard refusing a second press — with its own typed refusal — is
 * already the answer to a double click. A disabled button would be a second, silent refusal path.
 * WHAT IT DOES INSTEAD is wear the console's own BUSY IDIOM while the wait is on: the `busy` class
 * the stylesheet animates (so the wait is visible) and the `aria-busy` a screen reader reads, on the
 * control that was pressed. And a SECOND press while one is pending is IGNORED — a confirm token is
 * SPENT by the first POST, so a second could only be refused; swallowing it is not a second, silent
 * refusal path (nothing runs), it is one press, one request.
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
    /* Where a write returns. The dialog states it (`data-back`); a strip control returns to the page
       it is on — the path and query the bar carries (tab, level, sort), WITHOUT the fragment, which
       is no part of a server render and would make the navigation a no-op when the bar still has one.
       The route resolves its own destination server-side for the no-JS path; this is only where the
       ENHANCED path lands. */
    var stated = form.getAttribute("data-back");
    return stated ? stated : window.location.pathname + window.location.search;
  }

  /* THE WAIT IS VISIBLE: the control the person pressed wears the console's BUSY IDIOM (the `busy`
     class the stylesheet already animates + the `aria-busy` a screen reader reads) while the write is
     in flight. It is added HERE, on the real control, and removed when the answer lands (just before
     the navigation) so the fresh page is never painted with a stale pulse. A form submitted with
     Enter has no submitter: nothing to mark, and the write still runs and navigates. */
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
    busy(control);
    /* The SAME body, method and destination the browser would have sent — with `redirect: "manual"`
       so fetch does not follow the 303 itself. */
    var reply = fetch(form.action, {
      method: "POST",
      body: payload(form, event.submitter),
      credentials: "same-origin",
      redirect: "manual",
      keepalive: true,
      headers: { "Accept": "application/json" }
    });
    /* THE NAVIGATION HAPPENS ONCE THE WRITE HAS ANSWERED — not alongside it. This is what makes the
       delete button refresh its list: the answer (303 or refusal) is only sent after the route has
       run and committed the action, so the page this lands on is rendered from the state the action
       CHANGED. Both outcomes navigate: an accepted write lands on the fresh render, a refusal lands
       on the page whose one-shot notice explains it, and a NETWORK failure is an honest no-op (the
       write was attempted once and never retried). */
    function leave() {
      settled(control);
      window.location.assign(to);
    }
    reply.then(leave, leave);
  });
})();
