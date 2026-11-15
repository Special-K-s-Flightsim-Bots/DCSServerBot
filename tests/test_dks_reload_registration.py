"""One handler per DKS path after a plugin reload — and the ordinary path is unchanged.

``DKS.register_routes`` used to install its router with a bare ``app.include_router`` and its
``cog_unload`` removed by matching ``self.router.routes`` against ``app.routes``. The include never
appends the router's own routes — one ``_IncludedRouter`` wrapper on the pinned FastAPI, one copied
``APIRoute`` per route on other versions — so that removal matched nothing: a reload left the
PREVIOUS ``/register_dks`` and ``/install_dks_exporter`` handlers answering, with that install's own
auth decisions, and nothing warned. Registration now goes through ``include_router_counted`` /
``exclude_router`` (``services.webservice.registry``), so removal names exactly what the include
appended.

Pinned here:

* after unload + register there is exactly ONE handler per DKS path, and the set of paths is the
  same as before the reload — nothing else moved;
* an unload puts the app's route table back EXACTLY as it was before the plugin touched it (the
  route objects are the same objects, not merely the same count);
* a second registration on ONE instance replaces the first instead of adding to it;
* the ordinary path still works: a tokenless request to either DKS path is answered by the plugin's
  OWN auth dependency (403 "Invalid or expired token"), never by the framework's 404 — proof the
  DKS route is the one answering.
"""
from __future__ import annotations

import collections
import logging
import types
from typing import Any, cast

import pytest
from fastapi import FastAPI

from conftest import asgi_client, permissions
from plugins.dks.commands import DKS
from services.webservice.registry import exclude_router, include_router_counted

#: both routes DKS installs, with the empty RestAPI prefix of the test config in front of them
DKS_PATHS: tuple[str, ...] = ("/register_dks", "/install_dks_exporter")


# --------------------------------------------------------------------------------- test helpers

def _plugin(app: FastAPI) -> DKS:
    """The plugin's registration/unregistration surface, without its bot-side constructor.

    ``register_routes`` and ``cog_unload`` only touch these attributes: the router, the app, the
    bookkeeping of the included object(s), the log, the RestAPI cog it reads ``prefix`` from and —
    for ``cog_unload`` — the base class's own fields.
    """
    plugin = object.__new__(DKS)
    plugin.app = app
    plugin.router = None
    plugin._included = []
    plugin.log = logging.getLogger("tests.dks")
    plugin._config = {}
    plugin.eventlistener = None
    plugin.__cog_name__ = "DKS"
    plugin.bot = cast(Any, types.SimpleNamespace(
        cogs={"RestAPI": types.SimpleNamespace(get_config=lambda: {})}))
    return plugin


def _router_of(plugin: DKS):
    assert plugin.router is not None, "register_routes() builds the router"
    return plugin.router


def _handler_counts(app: FastAPI) -> collections.Counter:
    """How many handler objects answer each path, counted THROUGH the include wrappers.

    ``app.routes`` alone is blind to them (the inner ``APIRoute``s live inside the wrapper), so
    counting it would report zero handlers per DKS path and prove nothing.
    """
    counts: collections.Counter = collections.Counter()
    for route in permissions._walk_routes(app):
        path = getattr(route, "path", None)
        if path:
            counts[path] += 1
    return counts


def _appended(before: list, app: FastAPI) -> list:
    return [route for route in app.routes if not any(route is old for old in before)]


def _dks_counts(app: FastAPI) -> dict[str, int]:
    counts = _handler_counts(app)
    return {path: counts.get(path, 0) for path in DKS_PATHS}


# --------------------------------------------------------------------- the ordinary (non-reload) path

def test_the_ordinary_registration_installs_exactly_one_handler_per_dks_path():
    app = FastAPI()
    baseline_routes = list(app.routes)
    baseline = _handler_counts(app)
    plugin = _plugin(app)

    plugin.register_routes()

    router = _router_of(plugin)
    counts = _handler_counts(app)
    expected = dict(baseline)
    for route in router.routes:
        expected[router.prefix + getattr(route, "path", "")] = 1
    assert dict(counts) == expected, "every DKS path is installed once and nothing else appeared"
    assert _dks_counts(app) == {path: 1 for path in DKS_PATHS}
    assert len(_appended(baseline_routes, app)) == 1, \
        "the app gains ONE object per include on the pinned FastAPI (a wrapper, not the routes)"


@pytest.mark.asyncio
async def test_register_dks_answers_through_the_plugin_handler_without_a_token():
    """Its own auth dependency refuses an anonymous caller — a 404 would mean no DKS route at all."""
    app = FastAPI()
    _plugin(app).register_routes()

    async with asgi_client(app) as client:
        response = await client.post("/register_dks")

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Invalid or expired token", \
        "the DKS TokenBearer dependency answered, so the DKS route is the one answering"


@pytest.mark.asyncio
async def test_install_dks_exporter_answers_through_the_plugin_handler_without_a_token():
    app = FastAPI()
    _plugin(app).register_routes()

    async with asgi_client(app) as client:
        response = await client.post("/install_dks_exporter")

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Invalid or expired token"


# ------------------------------------------------------------------------ the reload: unload + register

@pytest.mark.asyncio
async def test_unload_then_register_leaves_exactly_one_handler_per_dks_path():
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
    assert _dks_counts(app) == {path: 1 for path in DKS_PATHS}, \
        f"a DKS path answers through more than one handler: {dict(after_reload)}"
    assert all(count == 1 for count in after_reload.values()), \
        f"a path answers through more than one handler: {dict(after_reload)}"
    assert len(_appended(baseline, app)) == 1, "one include remains, not two"


@pytest.mark.asyncio
async def test_unload_removes_the_install_entirely_by_identity():
    app = FastAPI()
    baseline = list(app.routes)
    plugin = _plugin(app)
    plugin.register_routes()

    await plugin.cog_unload()

    assert len(app.routes) == len(baseline)
    assert [route is old for route, old in zip(app.routes, baseline)] == [True] * len(baseline)
    assert plugin._included == []
    assert _dks_counts(app) == {path: 0 for path in DKS_PATHS}


@pytest.mark.asyncio
async def test_a_second_registration_on_one_instance_replaces_the_first():
    """A retried install must not leave the first set of handlers answering (own auth decisions)."""
    app = FastAPI()
    plugin = _plugin(app)
    plugin.register_routes()
    once = _handler_counts(app)

    plugin.register_routes()

    assert _handler_counts(app) == once
    assert _dks_counts(app) == {path: 1 for path in DKS_PATHS}
    assert len(plugin._included) == 1, "the second include replaced the first, it did not add to it"
