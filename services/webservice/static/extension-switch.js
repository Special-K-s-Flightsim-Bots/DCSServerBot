/* THE SWITCH IN FLIGHT.
 *
 * An extension's ON/OFF action runs as a BACKGROUND job on the server (switching one on can mean a pip
 * install of minutes), so the page cannot know the outcome at the moment it posted. What it can do is
 * read the job:
 *
 *   - the busy switch PULSES (its own CSS) — that is the sign of life, and it costs the foot no room.
 *     No clock is drawn beside the label: on a narrow card the time squeezed the label to "E…" and
 *     pushed the buttons out, and the journal's line names the elapsed seconds once the job is over;
 *   - it polls the switch-state endpoint named by `data-switch-state` (a plain read, nothing written);
 *     once the server no longer reports THIS key as running, the job is over and the page reloads —
 *     and that render is where the result arrives, as the console's ordinary notice.
 *
 * Nothing here is per-extension: it keys off the markup, so every card that is switching behaves the
 * same and an extension added later needs no JavaScript. */
(() => {
  const busy = Array.from(document.querySelectorAll('[data-switch-busy]'));
  if (!busy.length) return;

  const since = {};
  let stateUrl = '';
  busy.forEach((el) => {
    const key = el.dataset.switchBusy || '';
    const started = Number(el.dataset.switchSince || 0);
    if (key && started) since[key] = started;
    if (!stateUrl && el.dataset.switchState) stateUrl = el.dataset.switchState;
  });

  if (!stateUrl) return;

  // A POLL THAT NEVER ANSWERS MUST NOT HOLD THE CARD FOR EVER: a dropped request is retried, but a run
  // of failures (an expired session, a proxy eating the request) is itself an answer — reload once and
  // let the server's own render say what the switch did.
  const GIVE_UP_AFTER = 12;                 // 12 polls ≈ 36 s
  let failures = 0;

  const poll = async () => {
    let running = null;
    try {
      const response = await fetch(stateUrl, {
        headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store',
      });
      if (response.ok) {
        const data = await response.json();
        running = Object.keys((data && data.running) || {});
      }
    } catch (error) {
      running = null;
    }
    if (running === null && ++failures >= GIVE_UP_AFTER) {
      window.location.reload();
      return;
    }
    if (running !== null) {
      failures = 0;
      if (!running.some((key) => key in since)) {
        window.location.reload();
        return;
      }
    }
    window.setTimeout(poll, 3000);
  };
  window.setTimeout(poll, 2000);
})();
