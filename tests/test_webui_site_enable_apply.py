"""A switched-off site's card, and the door that switches it on WITHOUT a restart.

WHAT IS PINNED, all through the real application (the production factory, the shell, the local
backend and a stub cluster) and the page's own forms — every token read out of a rendered page:

* a site that is not enabled mounts NO ``<iframe>``: its panel states the off-state as the BODY of
  the pane (the box a frame would occupy), offers no ``Open`` door to an address that answers 404,
  and draws none of the lines that describe a frame;
* a site that IS enabled mounts exactly one frame whose ``src`` is its own ``/chrome-preview``, and
  that address answers 200 on the same application;
* ONE save flipping ``enabled`` false -> true makes the site serve — its page and its
  ``/chrome-preview`` answer 200 on the same POST, the card mounts the frame, and the route count
  grows — with the SAME application object and no restart;
* a re-read that refuses leaves the site file written, the running route table unchanged, and a
  refusal sentence on the notice that names the step that failed;
* an apply this process cannot run is REFUSED with the piece that failed named in the sentence the
  operator reads (no console application, no route table, an unservable site file), and the same
  words reach the log.

No live bot is restarted and no browser is opened: every assertion drives the ASGI app.
"""
from __future__ import annotations

import html as html_module
import logging
import types

import pytest

from services.webservice import config_apply, site_pages, sites
from services.webservice.pages import site_config as site_page
from test_webui_site_settings import (  # noqa: F401 - fixtures and helpers imported on purpose
    ADMIN, _parse, _users, cluster, client, notice_of, open_page, revision_of, save_path,
    sign_in_as, token_of,
)
from test_webui_write_pause import build  # noqa: F401 - the production factory, reused

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

SERVERS_YAML = """\
SRS-1:
  managed_by:
    - 5th Guards
"""

#: ``guards`` is the site this file switches; the root site stays enabled so the console keeps its
#: mount and the page's own addresses are the ones these pins post to.
SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Community
  guards:
    group_role: "5th Guards"
    slug: guards
    enabled: __ENABLED__
    branding:
      title: Guards
"""


def site_yaml(*, guards_enabled: bool) -> str:
    return SITE_YAML.replace("__ENABLED__", str(guards_enabled).lower())


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def make_app(config_dir):
    """An application installed from a site file where ``guards`` is on or off as the caller says."""
    def _make(*, guards_enabled: bool):
        (config_dir / "site.yaml").write_text(site_yaml(guards_enabled=guards_enabled),
                                              encoding="utf-8")
        return build(config_dir, cluster(), users=_users())
    return _make


def card_of(html: str, key: str):
    """The `<details class="sc-card">` whose header prints *key* as the site's name."""
    for node in _parse(html).walk():
        if node.tag != "details" or "sc-card" not in node.classes():
            continue
        summary = next((child for child in node.children if child.tag == "summary"), None)
        if summary is None:
            continue
        names = [n for n in summary.walk() if "mono" in n.classes()]
        if any("".join(n.text).strip() == key for n in names):
            return node
    return None


def panel_of(card):
    """The card's own preview panel (the `<aside class="sc-site">`)."""
    return next(node for node in card.walk() if "sc-site" in node.classes())


def text_of(node) -> str:
    """The visible text of *node* and every descendant, whitespace collapsed."""
    chunks = ["".join(node.text)]
    chunks.extend("".join(child.text) for child in node.walk())
    return " ".join(" ".join(chunks).split())


def console() -> config_apply.ApplyTarget:
    return config_apply.ApplyTarget(config_apply.TARGET_CONSOLE)


# =========================================================== 1. a switched-off site's card

@pytest.mark.asyncio
async def test_a_disabled_site_mounts_no_frame_and_states_the_off_state_as_the_box(make_app):
    """No `iframe`, no door, and the off-state IS the pane's body — not a caption under a 404."""
    app = make_app(guards_enabled=False)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
    card = card_of(html, "guards")
    assert card is not None, "no card header prints the switched-off site's name"
    panel = panel_of(card)
    assert not [node for node in panel.walk() if node.tag == "iframe"], (
        "a switched-off site mounts a frame at a route that is not registered")
    doors = [node for node in panel.walk() if node.tag == "a" and "st-btn" in node.classes()]
    assert doors == [], "the panel offers a door to a site that serves nothing"

    box = next(node for node in panel.walk() if "sc-pv-box" in node.classes())
    assert "sc-pv-off" in box.classes(), "the box does not carry the off-state"
    assert text_of(box) == site_page.SITE_PREVIEW_OFF, (
        f"the pane's body reads {text_of(box)!r}")
    # the lines that describe a frame are not drawn beside a pane that has none
    for cls in ("sc-pv-cap", "sc-sitewrites", "sc-sitenone"):
        assert not [node for node in panel.walk() if cls in node.classes()], (
            f"the switched-off panel still draws its {cls} line")
    # and the address it would be served at is not printed as if it answered
    assert "chrome-preview" not in text_of(panel), (
        "the switched-off panel prints the preview address")


@pytest.mark.asyncio
async def test_an_enabled_site_mounts_one_frame_and_that_address_answers_200(make_app):
    """The other configuration is untouched: one frame, its own address, and it answers 200."""
    app = make_app(guards_enabled=True)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        html = await open_page(http)
        card = card_of(html, "guards")
        panel = panel_of(card)
        frames = [node for node in panel.walk() if node.tag == "iframe"]
        assert len(frames) == 1, f"the enabled card mounts {len(frames)} frame(s)"
        source = frames[0].attrs.get("src") or ""
        assert source.startswith(f"/guards{site_pages.CHROME_PREVIEW_PATH}"), source
        served = await http.get(source)
    assert served.status_code == 200, f"{source} answered {served.status_code}"
    assert not [node for node in panel.walk() if "sc-pv-off" in node.classes()], (
        "an enabled site's pane states the off-state")
    assert [node for node in panel.walk() if node.tag == "a" and "st-btn" in node.classes()], (
        "the enabled card's panel offers no door")
    assert [node for node in panel.walk() if "sc-pv-cap" in node.classes()], (
        "the enabled card's panel draws no caption")


# ==================================================== 2. the save applies it, no restart

@pytest.mark.asyncio
async def test_enabling_a_site_through_ONE_save_serves_it_without_a_restart(make_app, caplog):
    """Save -> the site answers, on the SAME application object. One POST, no second press.

    The operator's own path: sign in, post the card once with ``enabled`` flippped false -> true, and
    read the answer. The re-read runs BEFORE the response, so no waiting is needed — and the route
    count is the rebuild's own, quoted before and after.
    """
    app = make_app(guards_enabled=False)
    registrar = app.state.webui_registrar
    caplog.set_level(logging.INFO, logger="services.webservice.config_apply")
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        page = await open_page(http)
        assert (await http.get("/guards")).status_code == 404, "the site served before it was on"
        routes_before = registrar.route_count

        saved = await http.post(save_path(),
                                data={"_csrf_token": token_of(page), "_revision": revision_of(page),
                                      "_site": "guards", "f.sites.guards.enabled": "1"})
        assert saved.status_code == 303, saved.text[:400]

        # NO FURTHER POST: the save itself applied the file
        assert sites.load_site_block(app.state.webui_config_dir)["guards"]["enabled"] is True
        assert (await http.get("/guards")).status_code == 200, (
            "the save did not apply: the site's page does not answer")
        assert (await http.get("/guards/chrome-preview")).status_code == 200, (
            "the save did not apply: the preview address does not answer")
        routes_after = registrar.route_count
        assert routes_after > routes_before, (routes_before, routes_after)

        landed = await open_page(http)
        told = html_module.unescape(notice_of(landed))
        assert "re-read the site settings file" in told, told
        assert "live now" in told, told
        assert "notice-apply" not in landed, "the save still offered a second press"
        # the card now mounts the frame it refused to mount while the site was off
        panel = panel_of(card_of(landed, "guards"))
        frames = [node for node in panel.walk() if node.tag == "iframe"]
        assert len(frames) == 1, "the card still draws no frame for a site that now serves"
        assert (await http.get(frames[0].attrs["src"])).status_code == 200
    # nothing was rebuilt or re-installed: the same registrar, on the same application
    assert app.state.webui_registrar is registrar
    # the operator's report was "no Apply: line appears": the save now says what it did
    assert "Apply: the public site's route table was rebuilt from its settings file." in caplog.text
    print(f"routes before={routes_before} after={routes_after}")


# ============================================ 3. an apply this process cannot run is REFUSED

def _request(app=None):
    return types.SimpleNamespace(app=app)


def test_the_door_refuses_when_this_process_holds_no_console_app(monkeypatch, caplog):
    """No console application to re-read on: the operator reads that, the log says it too."""
    monkeypatch.setattr(config_apply, "_console_app", lambda: None)
    with caplog.at_level(logging.WARNING, logger="services.webservice.config_apply"):
        refusal = config_apply.preflight(_request(None), console())
    assert config_apply.WHY_NO_CONSOLE_APP in refusal, refusal
    assert "could not re-read it" in refusal
    assert "cannot re-read the site settings" in caplog.text
    assert config_apply.WHY_NO_CONSOLE_APP in caplog.text


def test_the_door_refuses_when_the_console_carries_no_route_table(caplog):
    """An application with no ``webui_registrar``: nothing to swap the site's routes on."""
    bare = types.SimpleNamespace(state=types.SimpleNamespace())
    with caplog.at_level(logging.WARNING, logger="services.webservice.config_apply"):
        refusal = config_apply.preflight(_request(bare), console())
    assert config_apply.WHY_NO_CONSOLE_REGISTRAR in refusal, refusal
    assert config_apply.WHY_NO_CONSOLE_REGISTRAR in caplog.text


def test_the_door_refuses_a_site_file_the_console_cannot_serve(make_app, monkeypatch, caplog):
    """A file the loader refuses is named with the parser's own sentence, before anything runs."""
    app = make_app(guards_enabled=False)
    site_file = app.state.webui_config_dir + "/site.yaml"
    with open(site_file, "w", encoding="utf-8") as handle:
        handle.write("sites:\n  guards:\n    slug: guards\n    group_role: \"No Such Role\"\n")
    with caplog.at_level(logging.WARNING, logger="services.webservice.config_apply"):
        refusal = config_apply.preflight(_request(app), console())
    assert "cannot be served" in refusal, refusal
    assert "matches no managed_by value" in refusal, "the parser's sentence is not carried through"
    assert "cannot be served" in caplog.text


@pytest.mark.asyncio
async def test_the_operator_is_told_in_the_notice_when_the_reread_refuses(make_app, monkeypatch):
    """The file stays written, the running table does not move, and the notice names the step."""
    app = make_app(guards_enabled=False)
    registrar = app.state.webui_registrar
    routes_before = registrar.route_count

    async def refusing_reread(_app=None):
        raise RuntimeError("the route table refused the swap")

    monkeypatch.setattr(config_apply, "_reread_sites", refusing_reread)
    async with client(app) as http:
        await sign_in_as(http, ADMIN)
        page = await open_page(http)
        saved = await http.post(save_path(),
                                data={"_csrf_token": token_of(page), "_revision": revision_of(page),
                                      "_site": "guards", "f.sites.guards.enabled": "1"})
        assert saved.status_code == 303, saved.text[:400]
        # THE FILE STAYS WRITTEN — the save is never rolled back over a failed re-read
        assert sites.load_site_block(app.state.webui_config_dir)["guards"]["enabled"] is True
        # THE RUNNING TABLE DID NOT MOVE
        assert registrar.route_count == routes_before
        assert (await http.get("/guards")).status_code == 404
        told = html_module.unescape(notice_of(await open_page(http)))
    assert "The file holds the change, but the console could not re-read it" in told, told
    assert config_apply.WHY_REBUILD_FAILED in told, told
    assert "could not rebuild the site's pages from the file" in told, told


# ================================= 4. a second submission cannot re-read at the same time

@pytest.mark.asyncio
async def test_a_save_while_a_reread_is_in_flight_applies_nothing_and_says_so(make_app):
    """The save claims the SAME in-flight key the door's console apply does."""
    app = make_app(guards_enabled=False)
    registrar = app.state.webui_registrar
    routes_before = registrar.route_count
    config_apply.mark_in_flight(config_apply.CONSOLE_CLAIM)
    try:
        async with client(app) as http:
            await sign_in_as(http, ADMIN)
            page = await open_page(http)
            saved = await http.post(save_path(),
                                    data={"_csrf_token": token_of(page),
                                          "_revision": revision_of(page),
                                          "_site": "guards", "f.sites.guards.enabled": "1"})
            assert saved.status_code == 303, saved.text[:400]
            assert sites.load_site_block(app.state.webui_config_dir)["guards"]["enabled"] is True
            assert registrar.route_count == routes_before, "a second re-read ran while one was in flight"
            told = html_module.unescape(notice_of(await open_page(http)))
    finally:
        config_apply.reset_in_flight()
    assert "already being re-read" in told, told
