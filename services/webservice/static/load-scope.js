/* THE LOAD TAB'S SCOPE SELECTOR APPLIES ON CHANGE.
 *
 * The panel is a plain GET form, so it works fully without this file: the reader picks a scope and
 * presses Show. This is the enhancement — picking a scope navigates straight away — and it is a
 * delegated listener because the live stream re-renders the panel, so a listener bound at load
 * would be lost with the markup it was attached to.
 *
 * Only the scope <select> is covered: the scale is a link chip and the graph is picked by a card,
 * so the selector is the one control that otherwise needs a second click. A change event on a
 * <select> fires only for a real value change, and the form is a GET that reads nothing and writes
 * nothing, so firing it early costs a reload, never state.
 */
(() => {
  document.addEventListener('change', (event) => {
    const field = event.target;
    if (!field || typeof field.closest !== 'function') return;
    if (!field.closest('.ld-scope')) return;
    const form = field.form;
    if (!form || typeof form.requestSubmit !== 'function') return;
    form.requestSubmit();
  });
})();
