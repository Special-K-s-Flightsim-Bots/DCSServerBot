/* The server picker submits on change, so the live band needs no visible submit button. Without
 * JavaScript the form keeps its `noscript` button and works the same way. */
document.querySelectorAll("form.pickbar select[data-autosubmit]").forEach(function (select) {
  select.addEventListener("change", function () { select.form.submit(); });
});
