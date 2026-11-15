"""The console's mount: ``/console`` while a public site owns the site root, ``/`` when none does.

Every pin here is asserted in BOTH modes, because the mount is a configuration fact and not a page
fact: the console's route table, its nav URLs and its rendered links move together, while the
installation's fixed surface — the login flow, the language switch, the served message map, the theme
switch and the asset area — keeps its addresses in both.
"""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, install_shell, permissions
from services.webservice import mount, site_pages
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.registry import RegistrationError
from test_webui_console_access import USERS, _config
from test_webui_dashboard import _hash, _login

#: a public site with a root site only: no ``group_role`` is involved, so this needs no ``managed_by``
#: token in ``servers.yaml`` and states the mount rule exactly (the site owns ``/``).
SITE_CONFIG = {"sites": {"root": {"slug": ""}}}

#: the installation's fixed surface — identical in both modes. ``/login`` and ``/logout`` are the
#: bookmarkable aliases of the two doors and keep their addresses beside the ``/auth/*`` forms.
FIXED_PATHS = ("/auth/login", "/auth/logout", "/auth/discord", "/auth/discord/callback",
               "/login", "/logout",
               "/language", "/theme", "/i18n/messages.js")

#: internal URLs a page may emit that are NOT console pages: the installation's fixed surface
NON_CONSOLE_PREFIXES = ("/static/", "/auth/", "/i18n/", "/language", "/theme")

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


SERVERS_YAML = """\
5th Guards:
  managed_by:
    - 5th Guards
"""


@pytest.fixture
def group_dir(tmp_path):
    """A config directory whose ``servers.yaml`` declares the group site's token."""
    (tmp_path / "services").mkdir()
    from test_webui_dashboard import BOT_YAML
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    from test_webui_dashboard import BOT_YAML
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def build(config_dir, *, site: bool, group_site: bool = False):
    """The real shell, from the production factory, in one of the two mount modes.

    ``group_site`` adds the group site the cross-surface pins need: a site is keyed by the
    ``managed_by`` token its servers declare, so the fixture directory must carry one (see
    :func:`group_dir`).
    """
    app = create_app()
    config = dict(_config(USERS))
    if site:
        config.update(SITE_CONFIG)
    if group_site:
        config.update({"sites": {"root": {"slug": ""},
                                 "group1": {"slug": "group1", "group_role": "5th Guards"}}})
    registrar = install_shell(app, node=types.SimpleNamespace(master=True,
                                                             config_dir=str(config_dir)),
                              config=config)
    return app, registrar


def route_paths(app) -> set[str]:
    """The application's own route table, read through the gate's walker (never the source)."""
    return {route.path for route in permissions._walk_routes(app) if getattr(route, "path", None)}


# ------------------------------------------------------------------------- the route table

@pytest.mark.parametrize("site", [False, True])
def test_the_console_is_mounted_at_the_configured_root(config_dir, site):
    app, registrar = build(config_dir, site=site)
    paths = route_paths(app)
    root = mount.CONSOLE_PREFIX if site else ""
    # the console's own root page and its whole page tree answer under the configured mount
    assert (root or "/") in paths
    assert f"{root}/fleet" in paths
    assert f"{root}/actions/server/start" in paths
    assert f"{root}/api/dashboard/stream" in paths
    # the nav follows the same root: the Dashboard entry NAMES the console's own page
    assert dashboard_page.nav_items()[0].url == "/"
    assert registrar.nav[0].url == (root or "/")
    # and nothing of the console answers at the OTHER root
    other = "" if site else mount.CONSOLE_PREFIX
    if site:
        # the site's OWN page answers "/" (services/webservice/site_pages.py); no CONSOLE page does
        site_paths = {getattr(route, "path", None)
                      for route in registrar.registrations[site_pages.OWNER].routes}
        assert "/" in site_paths, "the site's own overview answers the site root"
        assert "/" not in paths - site_paths, (
            "the site owns the site root — no console page may answer there")
        assert "/fleet" not in paths
    else:
        assert "/console" not in paths, "no site is configured — the console keeps the site root"


@pytest.mark.parametrize("site", [False, True])
def test_the_fixed_surface_keeps_its_address_in_both_modes(config_dir, site):
    app, _ = build(config_dir, site=site)
    paths = route_paths(app)
    for path in FIXED_PATHS:
        assert path in paths, f"{path} must not move with the console"
    assert "/static" in paths


def test_a_mount_is_not_a_page_change(config_dir):
    off_app, off = build(config_dir, site=False)
    on_app, on = build(config_dir, site=True)
    # the public site's page tree exists ONLY in the site-on mode (it IS the site), so the console's
    # own registration is what must be identical — compared with that owner excluded
    owner = site_pages.OWNER
    assert [name for name in off.owners if name != owner] == \
           [name for name in on.owners if name != owner]
    assert len(off.nav) == len(on.nav)
    assert len(off.page_paths) == len(on.page_paths)
    site_registration = on.registrations[owner]
    assert off.capability_count == on.capability_count - len(site_registration.capabilities)
    assert off.route_count == on.route_count - len(site_registration.routes)
    # the same routes, moved as a set: stripping the mount from the site-on table gives the site-off
    # table exactly (the fixed surface is in both unchanged)
    off_paths, on_paths = route_paths(off_app), route_paths(on_app)
    moved = {path[len(mount.CONSOLE_PREFIX):] or "/"
             for path in on_paths if mount.is_console_path(path)}
    unmoved = {path for path in off_paths if path not in FIXED_PATHS and not path.startswith("/static")}
    assert moved == unmoved


# ------------------------------------------------------------------- the root is not claimable

@pytest.mark.parametrize("site", [False, True])
def test_no_page_can_claim_the_site_root(config_dir, site):
    app, registrar = build(config_dir, site=site)
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/", response_class=None, name="probe-root")
    async def _root():
        return None

    with pytest.raises(RegistrationError):
        registrar.register_pages("probe", router, capabilities={"/": permissions.PUBLIC},
                                 prefix="")


@pytest.mark.parametrize("site", [False, True])
def test_no_owner_can_claim_the_console_mount(config_dir, site):
    app, registrar = build(config_dir, site=site)
    from fastapi import APIRouter

    router = APIRouter()

    @router.get(mount.CONSOLE_PREFIX, name="probe-console")
    async def _console_root():
        return None

    with pytest.raises(RegistrationError):
        registrar.register_pages("probe", router,
                                 capabilities={mount.CONSOLE_PREFIX: permissions.PUBLIC},
                                 prefix="")


# ------------------------------------------------------------------------------- the door

@pytest.mark.parametrize("site", [False, True])
@pytest.mark.asyncio
async def test_the_login_page_keeps_its_address_in_both_modes(config_dir, site):
    app, _ = build(config_dir, site=site)
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGIN_PATH, headers=HTML)
        assert response.status_code == 200, response.text
        assert f'action="{auth_routes.LOGIN_PATH}"' in response.text


@pytest.mark.parametrize("site,root", [(False, "/"), (True, mount.CONSOLE_PREFIX)])
@pytest.mark.asyncio
async def test_an_anonymous_console_request_bounces_into_its_own_surface(config_dir, site, root):
    app, _ = build(config_dir, site=site)
    async with asgi_client(app) as client:
        response = await client.get(root, headers=HTML)
    assert response.status_code == 303, response.text
    # the door carries the target AND the surface the visitor was refused on, so the sign-in
    # returns into the surface that started it
    assert response.headers["location"] == (
        f"{auth_routes.LOGIN_PATH}?next={root}&surface={root}")


def test_next_never_crosses_surfaces(group_dir):
    app, _ = build(group_dir, site=True, group_site=True)
    safe = auth_routes._safe_next
    # started from the console: the console's own pages are accepted, a site path is not
    assert safe("/console/fleet", surface="/console", app=app) == "/console/fleet"
    assert safe("/group1/overview", surface="/console", app=app) == "/console"
    assert safe("/", surface="/console", app=app) == "/console"
    # started from a group site: that site's pages are accepted, the console is not
    assert safe("/group1/players", surface="/group1", app=app) == "/group1/players"
    assert safe("/console/fleet", surface="/group1", app=app) == "/group1"
    # off-site and protocol-relative targets stay refused, in every surface
    for bad in ("https://evil.example/x", "//evil.example", "\\evil", ""):
        assert safe(bad, surface="/console", app=app) == "/console"
    # a surface this installation does not serve is ignored: the target is confined to its OWN
    # served mount, so the parameter can neither widen the accepted set nor invent a mount
    assert safe("/console/fleet", surface="/elsewhere", app=app) == "/console/fleet"
    assert safe("/group1/players", surface="/elsewhere", app=app) == "/group1/players"


def test_off_site_root_is_refused_when_a_site_owns_it(group_dir):
    app, _ = build(group_dir, site=True, group_site=True)
    # the site owns ``/``: a console login never lands there, and the site's own login never lands
    # in the console
    assert auth_routes._safe_next("/", surface="/group1", app=app) == "/group1"
    assert auth_routes._safe_next("/overview", surface="/group1", app=app) == "/group1"
    # and the site's own pages do return into the site
    assert auth_routes._safe_next("/group1/overview", surface="/group1", app=app) == "/group1/overview"


# ------------------------------------------------------------------- the rendered page's links

_LINK = re.compile(r'(?:href|action|src)\s*=\s*"([^"]*)"')


def _internal_targets(html: str) -> set[str]:
    """Every internal URL a document emits — attributes only, never a template's own text."""
    out: set[str] = set()
    for value in _LINK.findall(html):
        if not value.startswith("/") or value.startswith("//"):
            continue
        out.add(value.split("?", 1)[0])
    return out


@pytest.mark.parametrize("page", ["/console", "/console/fleet", "/console/nodes", "/console/players",
                                  "/console/instances", "/console/logs", "/console/statistics",
                                  "/console/music", "/console/bans", "/console/presets",
                                  "/console/audit", "/console/settings", "/console/access"])
@pytest.mark.asyncio
async def test_a_console_page_links_only_inside_the_mount(config_dir, page):
    app, _ = build(config_dir, site=True)
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        response = await client.get(page, headers=HTML)
    assert response.status_code == 200, response.text
    # THE PUBLIC SITE'S OWN DOOR is the one deliberate link out of the mount: it opens the installed
    # public site's root in a NEW TAB. It is the site's address, not a console page, so it is exempt
    # exactly as the installation's fixed surface is.
    public_site = site_pages.public_site_href(app)
    outside = {target for target in _internal_targets(response.text)
               if not target.startswith(mount.CONSOLE_PREFIX)
               and not target.startswith(NON_CONSOLE_PREFIXES)
               and target != public_site}
    assert not outside, f"{page} emits console URLs outside the mount: {sorted(outside)}"


@pytest.mark.asyncio
async def test_with_no_site_the_console_owns_the_root_and_nothing_else_answers(config_dir):
    app, _ = build(config_dir, site=False)
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        dashboard = await client.get("/", headers=HTML)
        assert dashboard.status_code == 200, dashboard.text
        # a site address answers nothing at all: no site is configured
        assert (await client.get("/group1", headers=HTML)).status_code == 404
    assert mount.console_prefix(app) == ""


@pytest.mark.parametrize("page", ["/", "/fleet", "/nodes", "/players", "/settings", "/presets",
                                  "/bans", "/audit", "/access", "/statistics", "/music"])
@pytest.mark.asyncio
async def test_at_the_site_root_no_page_emits_a_prefixed_url(config_dir, page):
    app, _ = build(config_dir, site=False)
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        response = await client.get(page, headers=HTML)
    assert response.status_code == 200, response.text
    assert f'href="{mount.CONSOLE_PREFIX}' not in response.text
    assert not any(target.startswith(mount.CONSOLE_PREFIX)
                   for target in _internal_targets(response.text))


def _door_app(config_dir, sites_config):
    """An app built from a site declaration, for the door's own contract."""
    app = create_app()
    config = dict(_config(USERS))
    config.update(sites_config)
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    return app


def test_the_public_site_door_answers_the_root_site_first(group_dir):
    """A root site owns the site root, so it is the door — even beside a group site."""
    app = _door_app(group_dir, {"sites": {"root": {"slug": ""},
                                           "group1": {"slug": "group1",
                                                      "group_role": "5th Guards"}}})
    assert site_pages.public_site_href(app) == "/"


def test_the_public_site_door_answers_the_one_group_site(group_dir):
    """With no root site, exactly ONE enabled group site answers at its own prefix."""
    app = _door_app(group_dir, {"sites": {"group1": {"slug": "group1",
                                                     "group_role": "5th Guards"}}})
    assert site_pages.public_site_href(app) == "/group1"


def test_the_public_site_door_cannot_guess_between_group_sites(group_dir):
    """Several group sites and no root site: a door that picks one would lie about the rest."""
    (group_dir / "servers.yaml").write_text(
        "A:\n  managed_by:\n    - A\nB:\n  managed_by:\n    - B\n", encoding="utf-8")
    app = _door_app(group_dir, {"sites": {
        "a": {"slug": "a", "group_role": "A"},
        "b": {"slug": "b", "group_role": "B"}}})
    assert site_pages.public_site_href(app) is None


def _new_tab_targets(html: str) -> set[str]:
    """Every href a page opens in a NEW TAB (``target="_blank"``)."""
    return set(re.findall(r'<a[^>]*href="([^"]*)"[^>]*target="_blank"', html))


@pytest.mark.asyncio
async def test_the_public_site_door_opens_the_enabled_site_in_a_new_tab(config_dir):
    """The console's one link out: the enabled site's root, in a new tab."""
    app, _ = build(config_dir, site=True)
    expected = site_pages.public_site_href(app)
    assert expected == "/", "the enabled root site owns the site root"
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        response = await client.get(mount.CONSOLE_PREFIX, headers=HTML)
    assert response.status_code == 200, response.text
    assert expected in _new_tab_targets(response.text), (
        f"the console chrome carries no new-tab link to {expected}: {_new_tab_targets(response.text)}")


@pytest.mark.asyncio
async def test_the_public_site_door_is_hidden_when_no_site_is_enabled(config_dir):
    """With no site enabled the console serves ``/`` itself, so there is nowhere to send the operator."""
    app, _ = build(config_dir, site=False)
    assert site_pages.public_site_href(app) is None
    async with asgi_client(app) as client:
        assert (await _login(client, "admin")).status_code == 303
        response = await client.get("/", headers=HTML)
    assert response.status_code == 200, response.text
    assert "/" not in _new_tab_targets(response.text), (
        f"a console page opens ``/`` in a new tab with no site enabled: {_new_tab_targets(response.text)}")
