/* The site's live figures: a server's state, its pilots, its mission and its two clocks change
 * within seconds, so a page repaints those in place instead of asking a visitor to reload.
 *
 * TWO PAGES, ONE FILE, TWO MODES:
 *
 *   * the FLEET list carries `data-live-board` on its board and `data-srv` on each strip — every
 *     strip is repainted from the answer's rows;
 *   * ONE SERVER's page carries `data-live-board`, `data-live-server` and `data-live-state` on its
 *     state chip. It repaints that chip, and when the page was rendered BEFORE the cached live read
 *     (weather, extensions, schedule) had answered, it takes the page again once the answer has
 *     landed — the automatic form of the reload a visitor would otherwise do by hand. That is ONE
 *     reload per page load, and only from `pending` to a settled state.
 *
 * The figures are composed SERVER-side (`pages/site_servers.live_cells`) and the first paint is the
 * server's own rendering, so this file only writes the values the answer carries — it never formats
 * a number, translates a word or decides a fallback. Without JavaScript the page keeps the snapshot
 * the server rendered: the same figures, simply not moving.
 *
 * Any other site page carries neither attribute and returns at once. */
(function () {
  var marker = document.querySelector("[data-live-board]");
  if (!marker || !window.fetch) return;
  var url = marker.getAttribute("data-live-board");
  if (!url) return;
  var single = marker.getAttribute("data-live-server");
  var rendered = marker.getAttribute("data-live-state");
  var EVERY = 10000;
  var reloaded = false;

  function cell(root, key) {
    return root.querySelector('[data-live="' + key + '"]');
  }

  function text(root, key, value) {
    var node = cell(root, key);
    if (node && value !== undefined && node.textContent !== value) node.textContent = value;
  }

  function show(root, key, visible) {
    var node = cell(root, key);
    if (node) node.style.display = visible ? "" : "none";
  }

  function width(root, key, share) {
    var node = cell(root, key);
    if (node) node.style.width = (Number(share) || 0) + "%";
  }

  function paintRow(root, figures) {
    root.classList.toggle("is-off", !figures.online);
    var dot = cell(root, "dot");
    if (dot) dot.className = "dot " + figures.dot;
    text(root, "state", figures.state);
    show(root, "live", figures.online);
    text(root, "players", figures.players);
    text(root, "mission", figures.mission);
    var mission = cell(root, "mission");
    if (mission) mission.className = figures.in_mission ? "m" : "m quiet";
    text(root, "map", figures.map);
    text(root, "runtime", figures.runtime);
    text(root, "restart", figures.restart);
    var restart = cell(root, "restart");
    if (restart) restart.className = figures.restart_hot ? "v hot" : "v";
    show(root, "slots-coalitions", figures.slots === "coalitions");
    show(root, "slots-none", figures.slots === "none");
    show(root, "slots-sides", figures.slots === "sides");
    show(root, "slots-red", figures.slots === "sides");
    text(root, "blue-text", figures.blue_text);
    text(root, "red-text", figures.red_text);
    width(root, "blue-bar", figures.blue_share);
    width(root, "red-bar", figures.red_share);
  }

  function paintFleet(answer) {
    text(document, "up", answer.up);
    text(document, "flying", answer.flying);
    var figures = {};
    (answer.rows || []).forEach(function (entry) { figures[entry.name] = entry; });
    marker.querySelectorAll("[data-srv]").forEach(function (article) {
      var found = figures[article.getAttribute("data-srv")];
      if (found) paintRow(article, found);
    });
  }

  function paintServer(answer) {
    if (!answer || !answer.row) return;
    paintRow(marker, answer.row);
    if (reloaded || rendered !== "pending") return;
    if (!answer.live_state || answer.live_state === "pending") return;
    /* the page was drawn before the live read landed: take it once, now that it has */
    reloaded = true;
    window.location.replace(window.location.pathname + window.location.search);
  }

  function tick() {
    if (document.hidden) return;
    var target = single
      ? url + (url.indexOf("?") === -1 ? "?" : "&") + "server=" + encodeURIComponent(single)
      : url;
    fetch(target, {headers: {"Accept": "application/json"}, cache: "no-store"})
      .then(function (reply) { return reply.ok ? reply.json() : null; })
      .then(function (answer) {
        if (!answer) return;
        if (single) paintServer(answer); else paintFleet(answer);
      })
      .catch(function () { /* a failed poll leaves the rendered figures in place */ });
  }

  window.setInterval(tick, EVERY);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tick();
  });
})();
