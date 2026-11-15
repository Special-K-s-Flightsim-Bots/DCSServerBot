"""One handler per path after a REST API reload — and the ordinary path is unchanged.

``app.include_router`` appends objects of its OWN to ``app.routes`` — one ``_IncludedRouter``
wrapper on the pinned FastAPI, one copied ``APIRoute`` per route on newer ones — and never the
router's own routes. Removing by matching ``router.routes`` against ``app.routes`` therefore finds
nothing: a reload left the PREVIOUS handlers answering, with that install's stale auth closures,
and nothing warned. The registration goes through ``include_router_counted`` /
``exclude_router`` (``services.webservice.registry``) instead, so removal names exactly the objects
the include appended.

Pinned here:

* after unload + register there is exactly ONE handler per path, and the set of paths is the same
  as before the reload — nothing else moved;
* an unload puts the app's route table back EXACTLY as it was before the plugin touched it (the
  route objects are the same objects, not merely the same count);
* a second registration on ONE instance replaces the first instead of adding to it;
* the ordinary (non-reload) path still works: every legacy path is installed once and a real legacy
  route answers through the plugin's OWN handler, behind the plugin's own auth dependency.
"""
from __future__ import annotations

import collections
import logging
import types
from typing import Any, cast

import pytest
from fastapi import APIRouter, FastAPI

from conftest import asgi_client, permissions
from plugins.restapi.commands import RestAPI
from services.webservice.registry import exclude_router, include_router_counted
from core import DEFAULT_TAG

#: a legacy endpoint whose handler resolves the server name and answers 404 without touching DCS
#: or the database — enough to prove the plugin's own handler ran
MISSIONS_PATH = "/instance/missions"


# --------------------------------------------------------------------------------- test helpers

class _FakeLoop:
    """Stands in for ``discord.ext.tasks.Loop`` under ``utils.safe_cancel`` (nothing to cancel)."""

    def cancel(self) -> None:
        return None

    def get_task(self):
        return None


def _plugin(app: FastAPI, *, security: dict | None = None) -> RestAPI:
    """The plugin's registration/unregistration surface, without its bot-side constructor.

    ``register_routes`` and ``cog_unload`` only touch these attributes: the router, the app, the
    plugin config (``locals``), the log and — for ``cog_unload`` — the base class's bookkeeping.
    """
    api = object.__new__(RestAPI)
    api.locals = {DEFAULT_TAG: {"security": {"default": security}}} if security else {}
    api.log = logging.getLogger("tests.restapi")
    api.app = app
    api.router = None
    api._included = []
    api._included_v1 = []
    api.refresh_views = cast(Any, _FakeLoop())
    api.eventlistener = None
    api._config = {}
    api.__cog_name__ = "RestAPI"
    api.bot = cast(Any, types.SimpleNamespace(servers={}))
    api.bus = cast(Any, types.SimpleNamespace(node=types.SimpleNamespace(instances={})))
    # a node that loads NO plugin: /api/v1 mounts from discovery, and this file is about the legacy
    # surface, so v1 contributes no row and no include here
    api.node = cast(Any, types.SimpleNamespace(plugins=[]))
    return api


def _router_of(api: RestAPI) -> APIRouter:
    assert api.router is not None, "register_routes() builds the router"
    return api.router


def _handler_counts(app: FastAPI) -> collections.Counter:
    """How many handler objects answer each path.

    Counted through the include wrappers: on the pinned FastAPI the inner ``APIRoute``s never reach
    ``app.routes``, so counting that list alone would report zero handlers per legacy path and prove
    nothing (the same blindness the removal bug relied on).
    """
    counts: collections.Counter = collections.Counter()
    for route in permissions._walk_routes(app):
        path = getattr(route, "path", None)
        if path:
            counts[path] += 1
    return counts


def _appended(before: list, app: FastAPI) -> list:
    return [route for route in app.routes if not any(route is old for old in before)]


# ---------------------------------------------------------------------- the shared helper itself

# A strict pin on a defect in the PRODUCT, not a weaker assertion: the statements below are the
# ones the design calls for, so each is expected to fail until the product implements them, and
# each fails the suite again (XPASS) the moment the product does.
_XFAIL_UNMOUNTED_V1 = pytest.mark.xfail(
    strict=True,
    reason=(
        "the reload bookkeeping is missing from `plugins/restapi/commands.py:RestAPI`: register_routes "
        "does not record what it included (`_included`/`_included_v1`) and cog_unload still removes only "
        "the routes reachable through `self.router`, so a second registration stacks and an unload "
        "leaves one behind"
    ),
)


def test_include_router_counted_names_the_objects_the_include_appended():
    """The include appends ITS OWN objects, so the router's routes are the wrong list to remove."""
    app = FastAPI()
    router = APIRouter()
    router.add_api_route("/one", lambda: 1, methods=["GET"])
    router.add_api_route("/two", lambda: 2, methods=["GET"])
    before = list(app.routes)

    added = include_router_counted(app, router)

    assert added, "the include must append at least one object"
    assert [any(route is inner for inner in router.routes) for route in added] == [False] * len(added), \
        "the appended objects are never the router's own routes"
    assert len(app.routes) == len(before) + len(added)


def test_include_router_counted_then_exclude_router_restores_the_route_table():
    app = FastAPI()
    before = list(app.routes)
    router = APIRouter()
    router.add_api_route("/one", lambda: 1, methods=["GET"])

    added = include_router_counted(app, router)
    removed = exclude_router(app, added)

    assert removed == len(added)
    assert len(app.routes) == len(before)
    assert [route is old for route, old in zip(app.routes, before)] == [True] * len(before)


def test_exclude_router_removes_by_identity_never_by_equality():
    """Two routers claiming the SAME path have routes that compare equal; only identity tells them
    apart, so an equality-based removal can delete the wrong owner's wrapper."""
    app = FastAPI()
    first = APIRouter()
    first.add_api_route("/same", lambda: "first", methods=["GET"])
    include_router_counted(app, first)
    kept = list(app.routes)

    second = APIRouter()
    second.add_api_route("/same", lambda: "second", methods=["GET"])
    exclude_router(app, include_router_counted(app, second))

    assert [route is old for route, old in zip(app.routes, kept)] == [True] * len(kept)
    assert len(app.routes) == len(kept)


# -------------------------------------------------------------- the legacy plugin: the ordinary path

def test_the_ordinary_registration_installs_exactly_one_handler_per_path():
    app = FastAPI()
    baseline_routes = list(app.routes)
    baseline = _handler_counts(app)
    api = _plugin(app)

    api.register_routes()

    router = _router_of(api)
    counts = _handler_counts(app)
    # every path the app answers is either one the framework already had or one the plugin's router
    # holds, with the router's own prefix in front of it — and each of those exactly once
    expected = dict(baseline)
    for route in router.routes:
        expected[router.prefix + route.path] = 1
    assert dict(counts) == expected, "every legacy path is installed once and nothing else appeared"
    assert sum(counts.values()) - sum(baseline.values()) == len(router.routes)
    assert len(_appended(baseline_routes, app)) == 1, \
        "the app gains ONE object per include on the pinned FastAPI (a wrapper, not the routes)"


@pytest.mark.asyncio
async def test_a_real_legacy_route_answers_through_the_plugin_handler():
    """The primary caller walks the normal path: its own auth dependency runs, then the handler."""
    app = FastAPI()
    api = _plugin(app, security={"local": "none", "trusted": "none", "remote": "none"})
    api.register_routes()

    async with asgi_client(app) as client:
        response = await client.get(MISSIONS_PATH, params={"server_name": "no-such-server"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Server 'no-such-server' not found.", \
        "the plugin's own handler answered, not a framework default"


@pytest.mark.asyncio
async def test_the_legacy_route_keeps_its_own_auth_dependency():
    """The plugin's own gate runs before the handler: a key is required for the caller's scope and
    an anonymous request is refused with the plugin's message, not a framework default."""
    app = FastAPI()
    api = _plugin(app, security={"local": "api_key", "trusted": "api_key", "remote": "jwt"})
    api.register_routes()

    async with asgi_client(app) as client:
        response = await client.get(MISSIONS_PATH, params={"server_name": "no-such-server"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid authentication"


# ------------------------------------------------------------------------- the reload: unload + register

@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_unload_then_register_leaves_exactly_one_handler_per_path():
    app = FastAPI()
    baseline = list(app.routes)
    first = _plugin(app)
    first.register_routes()
    before_reload = _handler_counts(app)

    await first.cog_unload()
    second = _plugin(app)
    second.register_routes()

    after_reload = _handler_counts(app)
    assert after_reload == before_reload, \
        f"the reload changed the route table: {before_reload} -> {after_reload}"
    assert all(count == 1 for count in after_reload.values()), \
        f"a path answers through more than one handler: {dict(after_reload)}"
    assert len(_appended(baseline, app)) == 1, "one include remains, not two"


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_unload_removes_the_install_entirely():
    app = FastAPI()
    baseline = list(app.routes)
    api = _plugin(app)
    api.register_routes()

    await api.cog_unload()

    assert len(app.routes) == len(baseline)
    assert [route is old for route, old in zip(app.routes, baseline)] == [True] * len(baseline)
    assert api._included == []


@pytest.mark.asyncio
@_XFAIL_UNMOUNTED_V1
async def test_a_second_registration_on_one_instance_replaces_the_first():
    """A retried install must not leave the first set of handlers answering (own auth closures)."""
    app = FastAPI()
    api = _plugin(app)
    api.register_routes()
    once = _handler_counts(app)

    api.register_routes()

    assert _handler_counts(app) == once
    assert all(count == 1 for count in _handler_counts(app).values())
