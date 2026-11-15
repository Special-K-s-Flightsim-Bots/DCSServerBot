"""A public site page stays readable on an installation that also serves the legacy REST API.

THE DEFECT THIS PINS. ``plugins/restapi`` registers its legacy surface at its configured
``prefix:``, and that key is optional — an installation that does not set it serves the API at the
SITE ROOT. ``GET /leaderboard`` is one of those legacy paths, and the plugin registers it as a
NORMAL route after the shell (it waits for the bot to be ready); a site page is a LOW-PRIORITY
route, so the peer's NORMAL route wins the shared path. The request then reached the API's own auth
dependency, which answers 403 for a caller without a credential — and a signed-in browser was shown
the shell's refusal document, "Your account does not have access to this page.", on a page declared
``public``. Every other site page worked because every other site page is off the API's surface.

WHAT IS PINNED, all through the real application (no route table, no capability map):

* every served site page answers ``200`` and its own HTML to a signed-in visitor carrying no role,
  on an installation whose REST API sits at the root with its own auth verifier, the visitor
  arriving from a REMOTE address (loopback is ``local`` and needs no credential, which is why an
  in-process probe on 127.0.0.1 cannot see this defect);
* THE CENSUS: every address this build serves for the root site — the pages, one pilot profile,
  ``/me``, the search JSON and all three asset routes (the declared logo, the theme sheet and the
  uploaded mark) — answers that visitor ``200``, enumerated from the site's own declarations rather
  than a literal list here;
* the REST API keeps ``/leaderboard`` for its own callers: the same application answers the API's
  own 403 JSON there, unchanged;
* the site declares no path the legacy API claims — read from the PLUGIN's own route table, over
  every address :mod:`services.webservice.site_pages` declares, served or not: the rule that keeps
  the NEXT site page off the API's surface.
"""
from __future__ import annotations

import ast
import logging
import re
import types

import httpx
import pytest
from fastapi import APIRouter, Depends

from conftest import REPO_ROOT, create_app, install_shell
from services.webservice import api_auth, site_pages, sites
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.registry import include_router_counted
from test_webui_site_pages import (PASSWORD, SERVERS_YAML, config_dir,  # noqa: F401 - fixture
                                   door_config, site_config)

#: a public visitor's address. The REST API's policy answers ``remote`` for it, which is the network
#: a live installation's visitors arrive from; loopback is ``local`` and needs no credential.
REMOTE_VISITOR = ("203.0.113.7", 40000)

#: the plugin whose own route calls ARE the legacy REST surface's path list.
REST_API_COMMANDS = REPO_ROOT / "plugins" / "restapi" / "commands.py"

#: the asset files the census build declares, so both asset routes are inside the census.
CENSUS_LOGO = "census-logo.png"
CENSUS_SHEET = "--census: 1;"


def legacy_api_paths() -> tuple[str, ...]:
    """Every path the legacy REST surface declares, read from the plugin's own route calls.

    ``plugins/restapi/commands.py`` registers its surface through ``add_secured_api_route("<path>",
    ...)``, so an AST pass over that file IS the API's path list: an endpoint the plugin gains joins
    this census without anyone editing the test, which is the drift a literal list here would repeat.
    """
    tree = ast.parse(REST_API_COMMANDS.read_text(encoding="utf-8"))
    paths = [node.args[0].value
             for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "add_secured_api_route"
             and node.args
             and isinstance(node.args[0], ast.Constant)
             and isinstance(node.args[0].value, str)]
    return tuple(dict.fromkeys(paths))


def _rest_api_router(paths: tuple[str, ...] | None = None, prefix: str = "") -> APIRouter:
    """A REST surface shaped like ``plugins/restapi``'s legacy one: its own auth on every route."""
    verifier = api_auth.ApiVerifier(
        api_auth.ApiAuthPolicy.from_plugin_config({"auth": {"api_key": "test-key"}}),
        logging.getLogger("tests.restapi"))
    router = APIRouter(prefix=prefix)

    async def legacy_endpoint() -> dict:
        return {"who": "restapi"}

    for path in legacy_api_paths() if paths is None else paths:
        router.add_api_route(
            path, legacy_endpoint, methods=["GET"],
            dependencies=[Depends(verifier.legacy_dependency(path=path.lstrip("/")))])
    return router


def _build(config_dir, *, users=None, assets: bool = False):
    """The application a real installation builds: a sign-in door, a ``sites:`` block, the console
    at ``/console`` and — registered AFTER the shell, the way a plugin does it — the REST API.

    ``assets`` additionally declares a logo and a theme sheet for the root site and publishes an
    uploaded mark, so all three asset routes answer and the census covers them.
    """
    config = door_config(users=users)
    if assets:
        config["sites"]["root"]["branding"]["logo"] = CENSUS_LOGO
        config["sites"]["root"]["theme"] = {"expert": CENSUS_SHEET}
        config["sites"]["root"]["avatar"] = True
        (config_dir / CENSUS_LOGO).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        (config_dir / sites.ASSET_DIR).mkdir(parents=True, exist_ok=True)
        for size in sites.AVATAR_SIZES:
            (config_dir / sites.ASSET_DIR / f"avatar-{size}.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    include_router_counted(app, _rest_api_router())
    return app


def _visitor(roles):
    """One local account with exactly *roles* — a visitor signed in to the site and nothing more."""
    return [{"username": "visitor", "roles": list(roles),
             "password_hash": local_auth.hash_password(PASSWORD,
                                                       iterations=local_auth.MIN_ITERATIONS)}]


def _client(app, *, accept: str = "text/html"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE_VISITOR),
                             base_url="https://public.example", headers={"Accept": accept})


async def _sign_in(client) -> None:
    """Sign in through the real form: mint the token, post it, keep the session cookie."""
    page = await client.get(auth_routes.LOGIN_PATH)
    field = re.search(r'name="_csrf_token" value="([^"]*)"', page.text)
    assert field is not None, "the login page carries no CSRF field"
    response = await client.post(auth_routes.LOGIN_PATH,
                                 data={"username": "visitor", "password": PASSWORD,
                                       "_csrf_token": field.group(1)})
    assert response.status_code == 303, "the visitor could not sign in"


def _declared_paths() -> list[str]:
    """Every address this build's site DECLARES, from the module's own tables — served or not.

    ``NAV_PATHS`` is read WHOLE, not narrowed to the served ids: an id whose page is not built yet
    still declares the address that page will take, so a declaration left on the API's surface is a
    page that would be shadowed the day it lands. Narrowing this census to the served ids would
    leave exactly those declarations unexamined.
    """
    pages = [path or "/" for path in site_pages.NAV_PATHS.values()]
    pages += [site_pages.PLAYER_PATH + "/u-somebody", site_pages.ME_PATH,
              site_pages.SEARCH_JSON_PATH, site_pages.LOGO_PATH, site_pages.THEME_SHEET_PATH,
              site_pages.AVATAR_PATH, site_pages.CHROME_PREVIEW_PATH]
    return pages


def _served_paths(app) -> list[str]:
    """The declared addresses THIS build registers a route for, in declaration order."""
    registrar = app.state.webui_registrar
    return [path for path in _declared_paths() if registrar.serves(path)]


def _registered_root_paths(app) -> set[str]:
    """Every GET address the site registers for the ROOT site, from the build's own route table.

    The census's completeness test: a route registered at an address ``site_pages`` declares nowhere
    is a page the declared-address enumeration cannot see, so it must not quietly exist.
    """
    registration = app.state.webui_registrar.registrations[site_pages.OWNER]
    groups = [str(site.prefix) for site in sites.sites_for(app).sites
              if getattr(site, "prefix", "")]
    registered = set()
    for route in registration.routes:
        path = getattr(route, "path", "") or ""
        if "GET" not in (getattr(route, "methods", None) or ()):
            continue
        if any(path == group or path.startswith(group + "/") for group in groups):
            continue
        registered.add(re.sub(r"\{[^}]+\}", "u-somebody", path))
    return registered


@pytest.mark.asyncio
async def test_every_served_site_address_answers_a_visitor_while_the_rest_api_serves_the_root(
        config_dir):
    """THE CENSUS: every address this build serves, on a root-prefix REST API, remote signed-in.

    Enumerated from ``site_pages``' own declarations narrowed by what the build actually registers,
    with a logo and a theme sheet declared so both asset routes are inside it. Every address is
    checked on its own, so a page nobody thought about is covered because its address is declared —
    and the last assertion refuses to pass while a registered route has no declaration to enumerate.
    """
    app = _build(config_dir, users=_visitor(roles=()), assets=True)
    addresses = _served_paths(app)
    expected = {site_pages.NAV_PATHS[page] or "/" for page in site_pages.SERVED_NAV_IDS}
    expected |= {site_pages.ME_PATH, site_pages.PLAYER_PATH + "/u-somebody",
                 site_pages.SEARCH_JSON_PATH, site_pages.LOGO_PATH, site_pages.THEME_SHEET_PATH,
                 site_pages.AVATAR_PATH}
    assert expected <= set(addresses), (
        f"the census lost {sorted(expected - set(addresses))}: it would then pass while that "
        f"address is served, or while the route behind it is gone")
    registered = _registered_root_paths(app)
    assert registered == set(addresses), (
        f"the site registers {sorted(registered - set(addresses))} that site_pages declares "
        f"nowhere; give the page an address in the module's table so the census covers it")
    async with _client(app) as client:
        await _sign_in(client)
        for path in addresses:
            response = await client.get(path)
            assert response.status_code == 200, f"{path} answered {response.status_code}"
            assert "does not have access" not in response.text, \
                f"{path} answered the shell's refusal document"


@pytest.mark.asyncio
async def test_the_leaderboard_page_is_reachable_signed_out_too(config_dir):
    """The page is public: no session, no redirect, no refusal — the API's route must not take it."""
    app = _build(config_dir)
    async with _client(app) as client:
        response = await client.get(site_pages.NAV_PATHS["leaderboard"] or "/")
    assert response.status_code == 200
    assert "does not have access" not in response.text


@pytest.mark.asyncio
async def test_the_rest_api_keeps_the_leaderboard_path_for_its_own_callers(config_dir):
    """The API's wire contract is untouched: its caller still reaches it, with its own refusal."""
    app = _build(config_dir)
    async with _client(app, accept="application/json") as client:
        response = await client.get("/leaderboard")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid authentication", \
        "the site page answered a REST API caller's path"


def test_the_site_declares_no_path_the_legacy_rest_api_claims():
    """The rule that keeps the NEXT site page off the API's surface.

    Both halves are censuses rather than lists: the API's paths are read from
    ``plugins/restapi/commands.py``'s own route calls (so a new endpoint is compared automatically)
    and the site's from every address ``site_pages`` declares, served or not.
    """
    claimed = set(legacy_api_paths())
    assert len(claimed) > 20, (
        f"the plugin's route table could not be read: {len(claimed)} path(s) parsed")
    assert {"/leaderboard", "/squadrons"} <= claimed, \
        "the census did not read the plugin's surface, so it proves nothing"
    clashes = [path for path in _declared_paths() if path in claimed]
    assert clashes == [], (
        f"site address(es) {clashes} share a path with the legacy REST API; a site page is a "
        f"low-priority route, so the API's normal route wins it and the page answers the API's "
        f"refusal (see site_pages.NAV_PATHS)")


def test_the_group_sites_pages_are_registered_under_their_own_prefix(config_dir):
    """A group site is unaffected either way — pinned so the root site's rule is not generalised."""
    app = _build(config_dir)
    registrar = app.state.webui_registrar
    paths = {getattr(route, "path", None) for route in registrar.routes}
    assert "/group1/rankings" in paths
    assert "/rankings" in paths
