"""A ``DCS Admin`` on the website honours ``managed_by``, exactly as it does on Discord.

Discord applies ``core.utils.discord.holds_scope`` to a ``DCS Admin``: a server naming a role the
member holds is reachable, an untagged server is reachable, and a server naming a role the member
does NOT hold is refused. The website must resolve the same scope, so these tests drive the REAL
resolvers -- the installed ``AuthManager.scope_for`` and the write seam's ``pages.actions._context``
-- and the rule under test is the production one, never a re-statement of it.

Pinned here:

* a DISCORD-shaped ``DCS Admin`` (roles ``{"DCS Admin"}``, a scope the backend built as
  ``Scope.restricted(<member tokens>)``) is resolved to ``Scope.cluster(<tokens>)``: FOUND on an
  untagged server and on one naming a held role, REFUSED on a server naming a role it does not hold;
* the SAME identity is NOT a "manager", so the Admin-only capabilities it does not carry stay out
  of reach -- the narrower view must not become a second way in;
* positive controls: an ``Admin`` is unrestricted, and a local ``DCS Admin`` (whose backend set
  ``Scope.everything()``) stays unrestricted.
"""
from __future__ import annotations

import types

import pytest

from services.webservice import permissions, readmodels
from services.webservice.auth import AuthManager, Identity, SESSION_IDENTITY_KEY
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as server_detail_page
from services.webservice.readmodels.source import LiveSource
from services.webservice.scope import ADMIN_ROLE, Scope


class _Server:
    """A ``Server`` double: a name and the ``managed_by`` list the scope compares against."""

    def __init__(self, name, managed_by=None):
        self.name = name
        self.display_name = name
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}


class _StaticBackend:
    """One identity, handed back for every request -- the shape a real backend produces.

    The Discord OAuth backend builds an identity's scope as
    ``Scope.restricted(member_scope_tokens(member))`` and leaves the cluster-role rules to
    ``AuthManager.scope_for``; this double reproduces exactly that shape.
    """

    name = "discord"
    supports_password = False

    def __init__(self, identity):
        self._identity = identity

    def authenticate(self, request):
        ref = request.session.get(SESSION_IDENTITY_KEY)
        if not ref or ref.get("backend") != self.name:
            return None
        return self._identity


def _request(app, *, method="GET", path="/"):
    return types.SimpleNamespace(
        app=app,
        session={SESSION_IDENTITY_KEY: {"backend": "discord", "subject": "424242"}},
        method=method,
        url=types.SimpleNamespace(path=path),
        query_params={})


def _app(manager, servers):
    """A request's application state: the auth manager the routes read and the source provider."""
    state = types.SimpleNamespace(webui_auth=manager,
                                  webui_source_provider=lambda: LiveSource(servers=tuple(servers)))
    return types.SimpleNamespace(state=state)


#: the member's Discord role ids and names, as ``member_scope_tokens`` walks them
TOKENS = frozenset({101, "Host A"})

FLEET = [_Server("Untagged"), _Server("Host A Server", ["Host A"]), _Server("Host B Server", ["Host B"])]


def _discord_dcs_admin_app():
    identity = Identity(subject="424242", backend="discord", roles=frozenset({"DCS Admin"}),
                        display_name="Hoster Admin", scope=Scope.restricted(TOKENS))
    return _app(AuthManager([_StaticBackend(identity)]), FLEET)


def _resolved(app, *, method="GET", path="/"):
    """The scope the REAL ``AuthManager.scope_for`` resolves for the app's identity."""
    permissions.set_scope_resolver(app.state.webui_auth.scope_for)
    try:
        return app.state.webui_auth.scope_for(_request(app, method=method, path=path))
    finally:
        permissions.set_scope_resolver(None)


# ------------------------------------------------------------------ the resolved scope

def test_a_discord_dcs_admin_is_resolved_to_the_cluster_shape():
    """The backend's restricted scope becomes the cluster shape, not ``everything``."""
    scope = _resolved(_discord_dcs_admin_app())
    assert scope == Scope.cluster(TOKENS)
    assert scope.unrestricted is False


def test_a_discord_dcs_admin_keeps_untagged_and_held_servers_and_refuses_foreign_ones():
    """The rule itself: Discord's ``holds_scope``, with the untagged default a cluster role keeps."""
    scope = _resolved(_discord_dcs_admin_app())
    assert scope.allows([]) is True, "an untagged server survives a cluster role"
    assert scope.allows(None) is True
    assert scope.allows(["Host A"]) is True, "a server naming a held role is inside the scope"
    assert scope.allows([101]) is True, "an id matches an id"
    assert scope.allows(["Host B"]) is False, "a role the member does not hold is refused"


def test_a_discord_dcs_admin_is_not_a_manager_so_the_admin_only_capabilities_stay_out():
    """The role, not the scope, admits a ``DCS Admin``: a narrower view is not a second way in."""
    app = _discord_dcs_admin_app()
    permissions.set_manager_resolver(app.state.webui_auth.manages_any_server)
    try:
        is_manager = permissions.manages_console(_request(app))
    finally:
        permissions.set_manager_resolver(None)
    assert is_manager is False
    # the configuration tab is Admin-only WITH a manager scope grant, so the manager flag is exactly
    # what would have handed it to a DCS Admin -- pinned against the declaration itself.
    server_detail_page.capabilities()
    assert permissions.roles_for_capability(server_detail_page.CONFIG_CAPABILITY) == (ADMIN_ROLE,)
    assert permissions.allows(server_detail_page.CONFIG_CAPABILITY, {"DCS Admin"},
                              manager=is_manager) is False


def test_the_read_path_keeps_untagged_and_held_servers_only():
    """The scoped source the pages render from filters the same three servers the same way."""
    source = LiveSource(servers=tuple(FLEET))
    scoped = readmodels.scoped_source(source, _resolved(_discord_dcs_admin_app()))
    assert scoped is not source, "a cluster scope is still a restricted scope"
    assert [server.name for server in scoped.servers] == ["Untagged", "Host A Server"]
    assert scoped.scoped_empty is False


def test_an_admin_stays_unrestricted():
    """Positive control: the owner role is untouched, tagged or not."""
    identity = Identity(subject="1", backend="discord", roles=frozenset({ADMIN_ROLE}),
                        scope=Scope.restricted(TOKENS))
    scope = _resolved(_app(AuthManager([_StaticBackend(identity)]), FLEET))
    assert scope == Scope.everything()
    assert scope.allows(["Host B"]) is True


def test_a_local_dcs_admin_without_a_declaration_stays_unrestricted():
    """Positive control: the scope the BACKEND set to unrestricted is never narrowed here."""
    identity = Identity(subject="ops", backend="discord", roles=frozenset({"DCS Admin"}),
                        scope=Scope.everything())
    scope = _resolved(_app(AuthManager([_StaticBackend(identity)]), FLEET))
    assert scope == Scope.everything()
    assert scope.allows(["Host B"]) is True


# ------------------------------------------------------------------ the write seam

def _context(app, *, path="/actions/server/startup"):
    """The seam the console's write routes build: authenticate, then ``pages.actions._context``."""
    manager = app.state.webui_auth
    permissions.set_scope_resolver(manager.scope_for)
    request = _request(app, method="POST", path=path)
    identity = manager.authenticate(request)
    assert identity is not None, "the session must resolve to an identity"
    return actions_page._context(request, identity)


@pytest.mark.asyncio
async def test_the_write_seam_finds_untagged_and_held_servers_for_a_dcs_admin():
    ctx = _context(_discord_dcs_admin_app())
    assert (await ctx.resolve_scoped_server("Untagged")).is_found is True
    assert (await ctx.resolve_scoped_server("Host A Server")).is_found is True


@pytest.mark.asyncio
async def test_the_write_seam_refuses_a_foreign_tag_and_never_an_existence_oracle():
    """A foreign tag and an unknown name get the SAME refusal, so nothing is disclosed by probing."""
    ctx = _context(_discord_dcs_admin_app())
    foreign = await ctx.resolve_scoped_server("Host B Server")
    unknown = await ctx.resolve_scoped_server("No Such Server")
    assert foreign.status == foreign.NOT_PERMITTED
    assert unknown.status == unknown.NOT_PERMITTED, \
        "a name that does not exist must not be distinguishable from one outside the scope"
