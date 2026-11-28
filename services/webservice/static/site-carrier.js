/* The carrier board's day columns run OLDEST on the left, today on the right, so the band that
 * holds them (`[data-cband-end]`, `overflow-x: auto`) is wider than the page and would open on its
 * left edge — three weeks of empty days, with last night's passes off-screen to the right.
 *
 * This file scrolls the band to its RIGHT edge once the page has laid out, so the visitor lands on
 * today.
 *
 * WITHOUT JAVASCRIPT the page is unchanged and still usable: the band keeps its own horizontal
 * scroll and simply opens on the oldest day, one drag away from today. The column order is never
 * touched — nothing here reorders, reverses or duplicates the table.
 *
 * Scoped to the board's own band; any page without the hook returns at once. */
(function () {
  var band = document.querySelector("[data-cband-end]");
  if (!band) return;

  function toEnd() {
    /* scrollWidth is the full column run, clientWidth the visible part: the difference is the
       rightmost position, where today's column sits */
    band.scrollLeft = band.scrollWidth - band.clientWidth;
  }

  /* defer runs this after the document is parsed, so the table is measured already; the load pass
     catches a late layout (a webfont or a scrollbar changing the band's width) */
  toEnd();
  window.addEventListener("load", toEnd);
})();
