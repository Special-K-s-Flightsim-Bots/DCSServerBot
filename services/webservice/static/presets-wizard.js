/* presets-wizard.js — a MODE control re-renders its step the moment it changes, and a re-render keeps
 * the operator where they were.
 *
 * The wizard's step is a GET form whose values ride in the URL, so a control that GATES which rows a
 * step draws (?clouds=, ?enable_dust=, ?fog.mode=, ?halo.preset=) only takes effect once the step is
 * re-submitted. This script submits the step's form when such a control changes — that submit IS the
 * whole fix, because the form already carries every value.
 *
 * A submit is a full navigation, which would otherwise land the operator at the top of the page. So
 * the script REMEMBERS where they were and RESTORES it after the reload: the control they just changed
 * (so it stays in view) or, for a Next/Back press, the scroll offset. It is progressive enhancement —
 * with JavaScript off nothing is submitted automatically and the operator's own Next/Back carries the
 * step, exactly as before.
 *
 * The mode controls are marked `data-pw-mode` by the page, so a plain value select (clouds.iprecptns)
 * is left alone and never re-renders the step. Delegation on `document` means the binding survives any
 * number of rows, and the guard on the form's method keeps a POST step (the review) untouched.
 */
(function () {
  "use strict";

  //: the one slot a re-render's position is handed to. A single navigation is in flight, so one slot
  //: is enough; it is consumed (and cleared) on the next load.
  var KEY = "dcssb:presets-scroll";

  function storage() {
    try {
      if (typeof window === "undefined" || !window.sessionStorage) { return null; }
      return window.sessionStorage;
    } catch (error) {
      return null;          // a browser that refuses sessionStorage (private mode) simply opts out
    }
  }

  function remember(form) {
    var store = storage();
    if (!store) { return; }
    var payload = { y: (typeof window !== "undefined" && window.scrollY) || 0, name: "" };
    var active = (typeof document !== "undefined" && document.activeElement) || null;
    if (active && typeof active.getAttribute === "function") {
      var tag = String(active.tagName || "").toLowerCase();
      var type = String(active.getAttribute("type") || "").toLowerCase();
      // a control the operator TYPED INTO (a select, a non-submit input) is the one to keep in view;
      // a Next/Back press stores no name, so the raw offset is restored instead.
      if (tag === "select" || (tag === "input" && type !== "submit" && type !== "image")) {
        payload.name = active.getAttribute("name") || "";
      }
    }
    try { store.setItem(KEY, JSON.stringify(payload)); } catch (error) { /* storage full: skip */ }
  }

  function submitStep(form) {
    if (!form || (form.method || "").toLowerCase() === "post") { return; }
    remember(form);
    form.submit();
  }

  function restore() {
    var store = storage();
    if (!store) { return; }
    var raw = null;
    try { raw = store.getItem(KEY); } catch (error) { return; }
    if (!raw) { return; }
    try { store.removeItem(KEY); } catch (error) { /* ignore */ }
    var payload = {};
    try { payload = JSON.parse(raw) || {}; } catch (error) { return; }
    var target = null;
    if (payload.name && typeof document !== "undefined") {
      target = document.querySelector('[name="' + payload.name + '"]');
    }
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "center" });
    } else if (typeof window !== "undefined" && window.scrollTo) {
      window.scrollTo(0, payload.y || 0);
    }
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || typeof target.matches !== "function") { return; }
    if (target.matches("[data-pw-mode]")) { submitStep(target.form); }
  });

  // a Next/Back (or any submit button) is the same full navigation: remember the offset for it too.
  document.addEventListener("submit", function (event) {
    remember(event && event.target);
  }, true);

  document.addEventListener("DOMContentLoaded", restore);
})();
