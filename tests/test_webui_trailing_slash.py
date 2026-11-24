"""A trailing slash on a moved surface: ``<path>/`` answers ``308`` to ``<path>``.

Starlette's own ``redirect_slashes`` never fires here — every page is declared without the slash, so
``/rankings/`` matched nothing and answered ``404`` on a surface whose unslashed address answers
``200``. The redirect is a middleware (``services.webservice.redirects``), which runs before routing
and therefore cannot shadow a page.

WHAT IS PINNED
--------------
* the site's pages, a dynamic site page and the console's mount root all redirect, in BOTH mount
  modes, and the query string survives;
* a path that is NOT registered still answers ``404`` — a redirect must never invent an address;
* a route that IS registered at a slashed path is served, never redirected away from;
* the unslashed addresses keep answering as they did;
* a write is never redirected.
"""
from __future__ import annotations

import types

import pytest

from conftest import asgi_client, create_app, install_shell, permissions, simple_router
from services.webservice import mount

SERVERS_YAML = """\
Root-1:
  port: 10308
Group-1:
  managed_by:
    - 5th Guards
"""


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def restore_console_mount():
    saved = mount._PREFIX
    try:
        yield
    finally:
        mount._PREFIX = saved


def build(config_dir, *, site: bool):
    """The real shell in one of the two mount modes (``site`` owns ``/`` and moves the console)."""
    app = create_app()
    config: dict = {"session": {"secret": "s" * 40}}
    if site:
        config["sites"] = {"root": {"slug": ""},
                           "group1": {"slug": "group1", "group_role": "5th Guards"}}
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                  config=config)
    return app


async def get(app, path, *, method: str = "GET", follow_redirects: bool = False):
    async with asgi_client(app) as client:
        return await client.request(method, path, follow_redirects=follow_redirects)


@pytest.mark.asyncio
async def test_a_site_page_with_a_slash_redirects_to_the_page(config_dir):
    app = build(config_dir, site=True)
    for path in ("/rankings/", "/players/"):
        response = await get(app, path)
        assert response.status_code == 308, path
        assert response.headers["location"] == path[:-1]


@pytest.mark.asyncio
async def test_a_dynamic_site_page_with_a_slash_redirects_to_the_page(config_dir):
    """The target is matched by the route's own pattern, so a parameterised page is covered too."""
    app = build(config_dir, site=True)
    response = await get(app, "/player/u-somebody/")
    assert response.status_code == 308
    assert response.headers["location"] == "/player/u-somebody"


@pytest.mark.asyncio
async def test_a_group_sites_page_with_a_slash_redirects_inside_its_own_prefix(config_dir):
    app = build(config_dir, site=True)
    response = await get(app, "/group1/rankings/")
    assert response.status_code == 308
    assert response.headers["location"] == "/group1/rankings"


@pytest.mark.asyncio
async def test_the_console_mount_root_with_a_slash_redirects_to_the_mount(config_dir):
    app = build(config_dir, site=True)
    response = await get(app, "/console/")
    assert response.status_code == 308
    assert response.headers["location"] == "/console"


@pytest.mark.asyncio
async def test_a_console_page_with_a_slash_redirects_at_the_root_mount_too(config_dir):
    """With no site the console keeps ``/``. ``/fleet`` also serves ``/fleet/{name}`` sub-paths, so
    this pins that a parameterised sibling does not claim the slashed form of the page itself."""
    app = build(config_dir, site=False)
    for path in ("/fleet/", "/players/", "/logs/"):
        response = await get(app, path)
        assert response.status_code == 308, path
        assert response.headers["location"] == path[:-1]


@pytest.mark.asyncio
async def test_a_registered_path_with_a_slash_answers_as_before(config_dir):
    """The unslashed addresses are the control: nothing about them changed."""
    app = build(config_dir, site=True)
    assert (await get(app, "/rankings")).status_code == 200
    assert (await get(app, "/players")).status_code == 200
    fleet = await get(build(config_dir, site=False), "/fleet")
    assert fleet.status_code == 403, "the console page is gated, exactly as before"


@pytest.mark.asyncio
async def test_an_unregistered_path_with_a_slash_still_answers_404(config_dir):
    """A redirect must not invent an address: ``/nonsense/`` stays as dead as ``/nonsense``."""
    app = build(config_dir, site=True)
    assert (await get(app, "/nonsense/")).status_code == 404
    assert (await get(app, "/nonsense")).status_code == 404


@pytest.mark.asyncio
async def test_a_route_registered_at_a_slashed_path_is_never_redirected_away(config_dir):
    """A path that is itself registered keeps its own answer — the slashed form is a real address."""
    app = build(config_dir, site=True)
    registrar = app.state.webui_registrar
    registrar.register_pages("slash-probe", simple_router("/slash-probe/one/"),
                             capabilities={"/slash-probe/one/": permissions.PUBLIC}, prefix="")
    registrar.register_pages("plain-probe", simple_router("/slash-probe/one"),
                             capabilities={"/slash-probe/one": permissions.PUBLIC}, prefix="")
    slashed = await get(app, "/slash-probe/one/")
    assert slashed.status_code == 200, "a registered slashed path was redirected away"
    assert slashed.json()["path"] == "/slash-probe/one/"
    assert (await get(app, "/slash-probe/one")).status_code == 200


@pytest.mark.asyncio
async def test_the_query_string_survives_the_redirect(config_dir):
    app = build(config_dir, site=True)
    response = await get(app, "/rankings/?metric=air_targets")
    assert response.status_code == 308
    assert response.headers["location"] == "/rankings?metric=air_targets"


@pytest.mark.asyncio
async def test_a_write_is_never_redirected(config_dir):
    """The browser would re-send the body to another address; a page's form action is where it goes."""
    app = build(config_dir, site=True)
    response = await get(app, "/rankings/", method="POST")
    assert response.status_code == 404, "a POST must reach routing, never the slash redirect"
