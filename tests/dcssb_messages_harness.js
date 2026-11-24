/* THE SERVED MESSAGE MAP, for the browser-less JS harnesses.
 *
 * The console's shipped scripts no longer carry a user-visible string: they read `window.dcssbMsg`
 * from the SERVED map (`/i18n/messages.js`, `base.html` includes it before every script). A harness
 * that boots a shipped script must therefore install that map too, or the script would look its keys
 * up in an empty table. `installMessages` evaluates the REAL served script (the path the running
 * test wrote from `i18n.messages_script_for`) against the harness's `window` — no copy, no
 * re-implementation — so the harness boots the shipped script exactly as a browser does.
 *
 * Usage from a harness: const { installMessages } = require("./dcssb_messages_harness.js");
 * const window = {}; installMessages(window);
 *
 * The path comes from the environment (`DCSSB_MESSAGES_JS`); when it is absent the window is left
 * with no map, and the scripts fall back to their KEYS — which is the honest degraded state, never a
 * silently English one.
 */
"use strict";

const fs = require("fs");

function installMessages(window) {
  const path = process.env.DCSSB_MESSAGES_JS;
  if (!path) { return; }
  new Function("window", fs.readFileSync(path, "utf8"))(window);
}

module.exports = { installMessages };
