/* preset-thumb.js — the cloud preset's thumbnail, hidden when there is no picture to show.
 *
 * The Overview card's <img data-preset-thumb> points at this console's own read route
 * (/fleet/<server>/weather/preset.png), which resolves the mission's own thumbnailName inside the DCS
 * installation ON the server's node. A mission whose installation does not carry that file gets a
 * plain-text refusal from the route, and the browser's default for a failed image is a broken-image
 * mark where the picture should be — a rendering of a missing file as damage.
 *
 * So the picture is a PROGRESSIVE ENHANCEMENT: mark the failed image hidden and the card reads as a
 * weather block without a picture, which is what a mission with no thumbnail is. With this script off
 * the page loses nothing it had — the <img> is still requested, still alt-labelled, and the browser's
 * own failure mark is the fallback.
 *
 * Both orders are covered, because the failure may land before or after this file runs: a LATER
 * failure arrives as a capture-phase `error` event, and an EARLIER one has already settled by the
 * time the document is parsed (a settled image reports `complete`, with a natural width when it
 * loaded and none when it did not).
 */
(function () {
  "use strict";

  function hideIfPresetThumb(image) {
    if (image && image.tagName === "IMG" && image.hasAttribute("data-preset-thumb")) {
      image.hidden = true;
    }
  }

  window.addEventListener("error", function (event) {
    hideIfPresetThumb(event.target);
  }, true);

  document.addEventListener("DOMContentLoaded", function () {
    var images = document.querySelectorAll("img[data-preset-thumb]");
    for (var i = 0; i < images.length; i += 1) {
      if (images[i].complete && !images[i].naturalWidth) {
        hideIfPresetThumb(images[i]);
      }
    }
  });
}());
