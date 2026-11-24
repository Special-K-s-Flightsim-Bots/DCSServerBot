"""The web write seam resolves the caller's per-server scope through the ONE scope resolver.

A page and a write must decide "which servers may this caller act on" with the SAME rule. Every page
and read model applies ``permissions.scope_for(request)`` (the cluster-role rule's single home,
``AuthManager.scope_for``); the write seam is built by ``pages.actions._context`` and hands the
identity to ``ActionContext.from_web``. A backend that builds a RESTRICTED identity for a caller who
holds a cluster role -- the Discord OAuth backend always does -- reaches the seam restricted, and the
route refuses with ``out_of_scope_refusal()`` unless the seam resolves the scope through the same
resolver the read path uses. These tests drive ``_context`` exactly as the console's write routes do:
``identity = app.state.webui_auth.authenticate(request)`` then ``_context(request, identity)``.

Two callers are pinned, so the fix cannot widen a manager's view while it fixes the cluster role:

* a DISCORD CLUSTER-ROLE member -- roles ``{"Admin", "DCS Admin"}`` and a scope the backend built as
  ``Scope.restricted(<member tokens>)`` -- is FOUND on a server that declares no ``managed_by``;
* a NON-cluster member whose tokens match a server's ``managed_by`` stays FOUND on that server and
  NOT_PERMITTED on a foreign server and on an untagged one.
"""
from __future__ import annotations

import types

import pytest

from services.webservice import permissions
from services.webservice.auth import AuthManager, Identity, SESSION_IDENTITY_KEY
from services.webservice.pages import actions as actions_page
from services.webservice.readmodels.source import LiveSource
from services.webservice.scope import Scope


class _Server:
    """A ``Server`` double: a name and the ``managed_by`` list the scope compares against."""

    def __init__(self, name, managed_by=None):
        self.name = name
        self.display_name = name
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}


class _StaticBackend:
    """One identity, handed back for every request -- the shape a real backend produces.

    ``AuthManager`` resolves roles and scope per request from the SAME backend, so a double that
    returns a fixed identity is what the manager-level rules are exercised against. The Discord
    OAuth backend builds its identity scope as ``Scope.restricted(member_scope_tokens(member))`` and
    does NOT apply the cluster-role rule (that lives in ``AuthManager.scope_for``) -- the identity
    this double returns reproduces exactly that shape.
    """

    supports_password = False

    def __init__(self, name, identity):
        self.name = name
        self._identity = identity

    def authenticate(self, request):
        return self._identity


def _app(manager, servers):
    """A request's application state: the auth manager the routes read and the source provider."""
    state = types.SimpleNamespace(webui_auth=manager,
                                  webui_source_provider=lambda: LiveSource(servers=tuple(servers)))
    return types.SimpleNamespace(state=state)


def _request(app, backend_name, subject):
    """A request double: the signed-in session, an app whose state carries the manager and source."""
    return types.SimpleNamespace(
        app=app,
        session={SESSION_IDENTITY_KEY: {"backend": backend_name, "subject": subject}},
        method="POST",
        url=types.SimpleNamespace(path="/actions/server/start"),
        query_params={})


def _context(app, backend_name, subject):
    """The seam the console's write routes build: authenticate the request, then ``_context``.

    ``permissions.scope_for`` is wired to the SAME manager the app carries -- the state
    ``install_auth`` leaves behind -- so ``_context``'s resolved scope is the production rule and
    never a test's re-statement of it.
    """
    manager = app.state.webui_auth
    permissions.set_scope_resolver(manager.scope_for)
    request = _request(app, backend_name, subject)
    identity = manager.authenticate(request)
    assert identity is not None, "the session must resolve to an identity"
    return actions_page._context(request, identity)


def _discord_cluster_role_app():
    """A Discord member holding the cluster roles, with a backend-built restricted scope."""
    identity = Identity(subject="424242", backend="discord",
                        roles=frozenset({"Admin", "DCS Admin"}), display_name="Cluster Admin",
                        scope=Scope.restricted(frozenset({101, "Host A"})))
    manager = AuthManager([_StaticBackend("discord", identity)])
    return _app(manager, [_Server("Untagged"), _Server("Host A Server", ["Host A"]),
                          _Server("Host B Server", ["Host B"])])


def _discord_manager_app():
    """A Discord member holding NO cluster role, whose tokens match one server's ``managed_by``."""
    identity = Identity(subject="777", backend="discord", roles=frozenset({"DCS"}),
                        display_name="Hoster", scope=Scope.restricted(frozenset({202, "Host A"})))
    manager = AuthManager([_StaticBackend("discord", identity)])
    return _app(manager, [_Server("Untagged"), _Server("Host A Server", ["Host A"]),
                          _Server("Host B Server", ["Host B"])])


@pytest.mark.asyncio
async def test_a_discord_cluster_role_acts_on_a_server_with_no_managed_by():
    """A Discord ``Admin`` / ``DCS Admin`` is FOUND on an untagged server through the write seam."""
    app = _discord_cluster_role_app()
    ctx = _context(app, "discord", "424242")
    resolution = await ctx.resolve_scoped_server("Untagged")
    assert resolution.is_found is True, "the cluster role is unscoped on the write path too"


@pytest.mark.asyncio
async def test_a_discord_cluster_role_acts_on_a_tagged_server_it_does_not_match():
    """The cluster role is unscoped, not merely "scoped to what it holds": a foreign tag is FOUND."""
    app = _discord_cluster_role_app()
    ctx = _context(app, "discord", "424242")
    resolution = await ctx.resolve_scoped_server("Host B Server")
    assert resolution.is_found is True, "a cluster role reaches every server, tagged or not"


@pytest.mark.asyncio
async def test_a_manager_stays_found_on_their_own_server():
    """A non-cluster member whose tokens match a server's ``managed_by`` stays FOUND there."""
    app = _discord_manager_app()
    ctx = _context(app, "discord", "777")
    resolution = await ctx.resolve_scoped_server("Host A Server")
    assert resolution.is_found is True


@pytest.mark.asyncio
async def test_a_manager_is_refused_a_foreign_server():
    """The fix must not widen a manager's view: a foreign tag is NOT_PERMITTED, not FOUND."""
    app = _discord_manager_app()
    ctx = _context(app, "discord", "777")
    resolution = await ctx.resolve_scoped_server("Host B Server")
    assert resolution.status == resolution.NOT_PERMITTED


@pytest.mark.asyncio
async def test_a_manager_is_refused_an_undeclared_server():
    """``managed_by`` is an allow-list: a server declaring none is NOT_PERMITTED for a manager."""
    app = _discord_manager_app()
    ctx = _context(app, "discord", "777")
    resolution = await ctx.resolve_scoped_server("Untagged")
    assert resolution.status == resolution.NOT_PERMITTED
