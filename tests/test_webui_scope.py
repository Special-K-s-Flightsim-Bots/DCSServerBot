"""The per-server scope (``managed_by``): ONE rule, ONE per-request resolver, fail closed.

``holds_scope`` is one pure function and the ONLY implementation of the rule. It lives in
``core/utils/discord.py`` next to ``check_roles`` -- the comparison it extends -- and every caller
imports it from there (the bot's call sites and the console's ``services/webservice/scope.py``). The
address is asserted, not assumed: ``test_layering_direction.py`` walks ``core/**`` for a module-level
``services`` import and runs a subprocess proving ``import core.utils.discord`` yields the three
helpers without pulling the console in. These tests exercise the real functions the bot calls."""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from services.webservice import permissions
from services.webservice.auth import Identity, local as local_auth
from services.webservice.auth.breakglass import BreakglassAuthBackend
from services.webservice.auth.discord_oauth import DiscordOAuthBackend
from core.utils.discord import holds_scope, may_manage_server, member_scope_tokens
from services.webservice.scope import ADMIN_ROLE, Scope

# --------------------------------------------------------------------------- doubles


class _Role:
    """A Discord role as the expansion reads it: an ``id`` and a ``name``."""

    def __init__(self, role_id=None, name=None):
        self.id = role_id
        self.name = name


def _member(*specs):
    """A member double. Each spec is ``(role_id, name)``, as ``discord.Member.roles`` carries it."""
    return types.SimpleNamespace(roles=[_Role(role_id=i, name=n) for i, n in specs])


def _request(session: dict | None = None, query: dict | None = None):
    """A request double: what a resolver is allowed to read (session + query params)."""
    return types.SimpleNamespace(session=dict(session or {}), query_params=dict(query or {}),
                                 method="GET", url=types.SimpleNamespace(path="/"))


# the helper

@pytest.mark.parametrize("managed_by, tokens, expected", [
 # a server that declares no managed_by is visible to everyone who may read the console
    (None, {"Admin"}, True),
    ([], set(), True),
 # a name matches a name
    (["Host A"], {"Host A"}, True),
    (["Host A"], {"Host B"}, False),
 # an id matches an id...
    ([1234567890], {1234567890}, True),
    ([1234567890], {"1234567890"}, False),
 #...and where ids are accepted, a MIXED list matches on either part (check_roles' semantics)
    (["Host A", 1234567890], {1234567890}, True),
    (["Host A", 1234567890], {"Host A"}, True),
 # nothing held -> a restricted server is denied
    (["Host A"], set(), False),
])
def test_holds_scope_is_the_one_rule(managed_by, tokens, expected):
    assert holds_scope(managed_by, frozenset(tokens)) is expected


def test_member_scope_tokens_are_the_members_role_ids_and_names():
    """The token vocabulary is ``check_roles``' own pair, so the two sides can never disagree."""
    member = _member((1, "@everyone"), (7, "Host A"))
    assert member_scope_tokens(member) == frozenset({1, 7, "@everyone", "Host A"})


def test_member_scope_tokens_of_no_member_is_empty():
    assert member_scope_tokens(None) == frozenset()
    assert member_scope_tokens(types.SimpleNamespace(roles=None)) == frozenset()


# the server picker's rule (pinned for held / not-held)

def test_the_picker_allows_a_managed_by_role_the_caller_holds():
    assert may_manage_server(["Host A"], _member((7, "Host A")), is_admin=True) is True


def test_the_picker_refuses_a_managed_by_role_the_caller_does_not_hold():
    assert may_manage_server(["Host A"], _member((8, "Host B")), is_admin=True) is False


def test_the_picker_allows_a_server_that_declares_no_managed_by():
    assert may_manage_server(None, _member((8, "Host B")), is_admin=True) is True


def test_the_picker_leaves_a_non_admin_callers_servers_alone():
    """The restriction applies to the DCS-Admin class only (Discord skips it for non-admins)."""
    assert may_manage_server(["Host A"], _member((8, "Host B")), is_admin=False) is True


def test_the_picker_refuses_every_restricted_server_when_no_member_resolved():
    assert may_manage_server(["Host A"], None, is_admin=True) is False


# --------------------------------------------------------------------------- the Scope value

def test_an_unrestricted_scope_allows_every_server():
    assert Scope.everything().allows(["Host A"]) is True
    assert Scope.everything().allows(None) is True


def test_a_restricted_scope_allows_a_held_server_and_denies_another():
    scope = Scope.restricted({"Host A"})
    assert scope.allows(["Host A"]) is True
    assert scope.allows(["Host B"]) is False


def test_a_restricted_scope_denies_a_server_with_no_managed_by():
    """``managed_by`` is an ALLOW-LIST -- an untagged server is NOT a scoped viewer's server.

    (The old empty-means-no-restriction rule is correct for the bot's own Discord commands and is
    confined there; the console's manager view fails closed.)
    """
    assert Scope.restricted({"Host A"}).allows([]) is False
    assert Scope.restricted({"Host A"}).allows(None) is False


def test_an_unresolved_scope_allows_nothing_at_all():
    """THE safety property: fail closed means an empty view, not a wider one."""
    scope = Scope.unresolved()
    assert scope.allows(["Host A"]) is False
    assert scope.allows(None) is False
    assert scope.allows([]) is False


# the resolver

def test_the_scope_resolver_is_absent_by_default_and_denies():
    permissions.set_scope_resolver(None)
    assert permissions.scope_for(_request()) == Scope.unresolved()


def test_the_scope_resolver_is_consulted_for_every_request():
    """Per request, never cached: the scope is a fact about the LIVE identity, like the roles."""
    seen: list[object] = []

    def resolver(request):
        seen.append(request)
        return Scope.restricted({"Host A"})

    permissions.set_scope_resolver(resolver)
    try:
        first = permissions.scope_for(_request())
        second = permissions.scope_for(_request())
    finally:
        permissions.set_scope_resolver(None)
    assert len(seen) == 2
    assert first == second == Scope.restricted({"Host A"})


def test_a_failing_scope_resolver_denies_rather_than_crashing():
    def resolver(request):
        raise RuntimeError("the guild is unreachable")

    permissions.set_scope_resolver(resolver)
    try:
        assert permissions.scope_for(_request()) == Scope.unresolved()
    finally:
        permissions.set_scope_resolver(None)


# identity kind -> scope, through the real backends

class _StaticBackend:
    """An identity source, for the manager-level rules (Admin / unresolvable)."""

    name = "local"
    supports_password = True

    def __init__(self, identity):
        self._identity = identity

    def authenticate(self, request):
        return self._identity

    def role_names_for(self, request):
        identity = self.authenticate(request)
        return set(identity.roles) if identity is not None else set()


def test_admin_is_unscoped_by_rule():
    """Any Admin, any backend: Admin sees everything."""
    from services.webservice.auth import AuthManager, SESSION_IDENTITY_KEY
    identity = Identity(subject="specialk", backend="local", roles=frozenset({ADMIN_ROLE}),
                        scope=Scope.restricted({"Host A"}))
    manager = AuthManager([_StaticBackend(identity)])
    request = _request(session={SESSION_IDENTITY_KEY: {"backend": "local",
                                                       "subject": "specialk"}})
    assert manager.scope_for(request) == Scope.everything()


def test_a_declared_scope_overrides_the_cluster_role_bypass():
    """A DECLARED scope wins over the cluster-role bypass.

    The declaration can only ever NARROW a cluster role's view, never widen a non-cluster one. Both
    halves are asserted against the SAME cluster role, so an inversion of the two branches in
    ``scope_for`` fails here.
    """
    from services.webservice.auth import AuthManager, SESSION_IDENTITY_KEY
    request = _request(session={SESSION_IDENTITY_KEY: {"backend": "local", "subject": "specialk"}})

 # 1. a cluster role WITH a declaration -> the declaration wins (exactly its servers, not all)
    declared = Identity(subject="specialk", backend="local", roles=frozenset({ADMIN_ROLE}),
                        scope=Scope.restricted({"Host A"}, declared=True))
    scope = AuthManager([_StaticBackend(declared)]).scope_for(request)
    assert scope == Scope.restricted({"Host A"}, declared=True)
    assert scope.allows(["Host A"]) is True
    assert scope.allows(["Host B"]) is False

 # 2. the same cluster role WITHOUT a declaration -> unrestricted (the bypass still applies)
    undeclared = Identity(subject="specialk", backend="local", roles=frozenset({ADMIN_ROLE}),
                          scope=Scope.restricted({"Host A"}))
    scope = AuthManager([_StaticBackend(undeclared)]).scope_for(request)
    assert scope == Scope.everything()
    assert scope.allows(["Host A"]) is True
    assert scope.allows(["Host B"]) is True


def test_an_unresolvable_identity_is_scoped_to_nothing():
    from services.webservice.auth import AuthManager, SESSION_IDENTITY_KEY
    manager = AuthManager([_StaticBackend(None)])
    request = _request(session={SESSION_IDENTITY_KEY: {"backend": "local", "subject": "ops"}})
    assert manager.scope_for(request) == Scope.unresolved()
 # an ANONYMOUS request (no session at all) is scoped to nothing too
    assert manager.scope_for(_request()) == Scope.unresolved()


def test_breakglass_is_unscoped():
    """Break-glass is unscoped -- it is the credential for "the operators are locked out", where a scoped
    break-glass would defeat the purpose."""
    from services.webservice.auth import AuthManager, SESSION_IDENTITY_KEY
    backend = BreakglassAuthBackend("emergency", "x")
    request = _request(session={SESSION_IDENTITY_KEY: {"backend": "breakglass",
                                                       "subject": "emergency"}})
    identity = backend.authenticate(request)
    assert identity is not None
    assert AuthManager([backend]).scope_for(request) == Scope.everything()


def test_a_discord_member_holding_a_servers_role_is_scoped_to_it():
    """A's managed_by role -> A is visible; B's is not. Same walk as the roles, no Discord call."""
    backend = DiscordOAuthBackend.__new__(DiscordOAuthBackend)
    identity = backend._identity("42", _member((1, "@everyone"), (7, "Host A")),
                                 {})
    scope = identity.scope
    assert scope.allows(["Host A"]) is True
    assert scope.allows([7]) is True
    assert scope.allows(["Host B"]) is False


def test_a_local_user_without_a_declaration_is_unrestricted(tmp_path):
    from services.webservice.auth import SESSION_IDENTITY_KEY
    user = local_auth.LocalUser(username="ops", password_hash="pbkdf2_sha256$1000$00$00",
                                roles=("DCS Admin",))
    backend = local_auth.LocalAuthBackend((user,), config_dir=tmp_path)
    request = _request(session={SESSION_IDENTITY_KEY: {"backend": "local", "subject": "ops"}})
    identity = backend.authenticate(request)
    assert identity is not None
    assert identity.scope == Scope.everything()


def test_a_local_user_with_a_declared_scope_is_scoped_to_it(tmp_path):
    user = local_auth.LocalUser(username="ops", password_hash="pbkdf2_sha256$1000$00$00",
                                roles=("DCS Admin",), scope=("Host A",))
    backend = local_auth.LocalAuthBackend((user,), config_dir=tmp_path)
    request = _request(session={"webui_identity": {"backend": "local", "subject": "ops"}})
    identity = backend.authenticate(request)
    assert identity is not None
    assert identity.scope.allows(["Host A"]) is True
    assert identity.scope.allows(["Host B"]) is False


def test_the_scope_is_never_read_from_the_session_or_a_url_parameter():
    """A URL parameter and a cookie are both client-held, so they can never widen the view.

    The identity is deliberately NON-cluster, because a cluster role's scope is the whole cluster by
    rule.
    """
    from services.webservice.auth import AuthManager, SESSION_IDENTITY_KEY
    identity = Identity(subject="ops", backend="local", roles=frozenset({"DCS"}),
                        scope=Scope.restricted({"Host A"}))
    manager = AuthManager([_StaticBackend(identity)])
    poisoned = _request(session={SESSION_IDENTITY_KEY: {"backend": "local", "subject": "ops"},
                                 "scope": ["Host B"], "webui_scope": "everything"},
                        query={"scope": "Host B", "managed_by": "Host B"})
    scope = manager.scope_for(poisoned)
    assert scope.allows(["Host A"]) is True
    assert scope.allows(["Host B"]) is False


# --------------------------------------------------------------------------- local declaration

@pytest.mark.parametrize("declared", [
    "Host A",                      # not a list
    {"value": "Host A"},           # not a list
    7,                             # not a list
    [""],                          # an empty value names no server
    ["   "],                       # whitespace is not a value either
    [1234],                        # not free text: a managed_by value is a string
    [None],
    [["Host A"]],
])
def test_an_invalid_declared_scope_is_refused_loudly(declared, tmp_path):
    with pytest.raises(ValueError) as error:
        local_auth.parse_users({"users": [{"username": "ops", "password_hash": _hash(),
                                           "roles": ["Admin"], "scope": declared}]},
                               config_dir=tmp_path)
    assert "scope" in str(error.value)
    assert "ops" in str(error.value)


def test_a_valid_declared_scope_is_parsed_and_deduplicated(tmp_path):
    users = local_auth.parse_users(
        {"users": [{"username": "ops", "password_hash": _hash(), "roles": ["Admin"],
                    "scope": ["Host A", " Host B ", "Host A"]}]}, config_dir=tmp_path)
    assert users[0].scope == ("Host A", "Host B")


def test_an_absent_scope_declaration_stays_empty(tmp_path):
    users = local_auth.parse_users(
        {"users": [{"username": "ops", "password_hash": _hash(), "roles": ["Admin"]}]},
        config_dir=tmp_path)
    assert users[0].scope == ()


def _hash() -> str:
    return local_auth.hash_password("correct horse battery staple",
                                    iterations=local_auth.MIN_ITERATIONS)


# --------------------------------------------------------------------------- the two WARNINGs

def test_a_declared_scope_matching_no_server_is_warned_about_once(caplog):
    from services.webservice import scope as scope_module
    with caplog.at_level(logging.WARNING):
        first = scope_module.audit_local_scopes({"ops": ("Host Q",)}, {"Host A", "Host B"})
        second = scope_module.audit_local_scopes({"ops": ("Host Q",)}, {"Host A", "Host B"})
    assert first == [("ops", "Host Q")]
    assert second == [("ops", "Host Q")], "the audit keeps reporting at startup; the LOG does not"
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1, f"expected ONE warning for one condition, got {len(records)}"
    assert "ops" in records[0].getMessage() and "Host Q" in records[0].getMessage()


def test_a_declared_scope_matching_a_server_is_not_warned_about(caplog):
    from services.webservice import scope as scope_module
    with caplog.at_level(logging.WARNING):
        unmatched = scope_module.audit_local_scopes({"ops": ("Host A",)}, {"Host A"})
    assert unmatched == []
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_a_servers_managed_by_that_matches_no_guild_role_is_warned_about(caplog):
    """The fail-loud measure: a rename is an access change."""
    from services.webservice import scope as scope_module
    with caplog.at_level(logging.WARNING):
        unmatched = scope_module.audit_server_managed_by(
            [("Server A", ("Host A",)), ("Server B", ("Host Q",))],
            {"@everyone", "Host A"})
    assert unmatched == [("Server B", "Host Q")]
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 1
    assert "Server B" in messages[0] and "Host Q" in messages[0]


def test_scope_resolution_logs_nothing_per_request(caplog):
    """A condition, not N incidents: an unresolvable scope must not write a line per request."""
    from services.webservice.auth import AuthManager
    manager = AuthManager([_StaticBackend(None)])
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            manager.scope_for(_request())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ------------------------------------------------------ the audit waits for Discord's READY
#
# Both audits compare ``managed_by`` against the LIVE guild role names/ids, but the console is
# BUILT before Discord's READY — when ``Client.guilds`` is still empty. A comparison against an
# empty role list reports EVERY tagged server as unmatched, so the audit must skip it and run
# again once the bot is ready (``WebService._audit_scope_when_ready``).


def _audit_bot(*, ready=True, guilds=(), servers=(), locals=None):
    """A bot double for the startup audit: ``is_ready()`` plus the two inputs it reads.

    ``locals=None`` builds one with NO ``locals`` attribute at all -- a Discord install (or a strict
    double), which is deliberately NOT headless.
    """
    bot = types.SimpleNamespace(guilds=list(guilds), servers=dict(servers))
    if locals is not None:
        bot.locals = dict(locals)
    if ready is not None:                     # ``ready=None`` builds one with NO ``is_ready``
        bot.is_ready = lambda: ready
    return bot


def _guild(*roles):
    return types.SimpleNamespace(roles=list(roles))


def _server(*managed_by):
    return types.SimpleNamespace(locals={"managed_by": list(managed_by)} if managed_by else {})


@pytest.fixture
def fresh_audit_log(monkeypatch):
    """Each test gets its own ConditionLog — the audit logs once per condition, per process."""
    from services.webservice import scope as scope_module
    monkeypatch.setattr(scope_module, "_AUDIT_CONDITIONS", None)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_the_audit_skips_a_guild_cache_that_does_not_exist_yet(caplog, fresh_audit_log):
    """Pre-READY: the bot object exists from ``login()``, ``guilds`` does not yet. NO warning."""
    from services.webservice import auth as auth_module
    bot = _audit_bot(ready=False, guilds=[], servers={"SAS": _server("Server Owner")})
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({"ops": ("Host Q",)}, bot=bot)
    assert _warnings(caplog) == [], "the audit warned about a role list that is not loaded yet"


def test_a_ready_bot_in_no_guild_still_audits_the_local_scopes(caplog, fresh_audit_log):
    """READY with an empty guild cache: the managed_by half has no role list and stays silent, but
    the local-scope half compares SERVER names and must still warn about a scope naming no server."""
    from services.webservice import auth as auth_module
    bot = _audit_bot(ready=True, guilds=[], servers={"SAS": _server("Server Owner")})
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({"ops": ("Host Q",)}, bot=bot)
    messages = _warnings(caplog)
    assert len(messages) == 1, f"expected exactly the local-scope warning, got {messages}"
    assert "ops" in messages[0] and "Host Q" in messages[0], \
        "the local-scope half must still warn about a scope naming no server"
    assert "SAS" not in messages[0], \
        "the managed_by half warned against an empty guild cache (the false positive)"


def test_the_audit_warns_once_the_guild_cache_exists(caplog, fresh_audit_log):
    """READY: a name that matches a role is silent, a stale one is the warning that must survive."""
    from services.webservice import auth as auth_module
    guild = _guild(_Role(1, "@everyone"), _Role(2, "Server Owner"))
    bot = _audit_bot(guilds=[guild], servers={"SAS": _server("Server Owner"),
                                              "Stale": _server("Host Z")})
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({}, bot=bot)
    messages = _warnings(caplog)
    assert len(messages) == 1, f"expected exactly the stale server, got {messages}"
    assert "Stale" in messages[0] and "Host Z" in messages[0]


def test_a_bot_without_is_ready_is_audited_as_before(caplog, fresh_audit_log):
    """A double with no ``is_ready`` (the headless DummyBot, an old stub) keeps auditing."""
    from services.webservice import auth as auth_module
    bot = _audit_bot(ready=None, guilds=[_guild(_Role(1, "Host A"))],
                     servers={"Stale": _server("Host Z")})
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({}, bot=bot)
    assert len(_warnings(caplog)) == 1


# ------------------------------------------------- a headless (``no_discord``) install
#
# A headless install runs no Discord bot, so the only "roles" the managed_by audit could compare a
# server's tag against are the CONFIGURED names -- every tagged server reads as unmatched on every
# boot, with no role to create or rename in answer. The local-scope half compares SERVER names and
# is the mechanism a Discord-less install actually uses, so it MUST keep running.


def test_a_headless_install_does_not_audit_the_managed_by_half(caplog, fresh_audit_log):
    """``no_discord``: the managed_by half stays silent; the local-scope half still warns once."""
    from services.webservice import auth as auth_module
    guild = _guild(_Role(1, "@everyone"), _Role(2, "Admin"), _Role(3, "DCS Admin"))
    bot = _audit_bot(guilds=[guild], servers={"SAS": _server("Server Owner")},
                     locals={"no_discord": True})
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({"ops": ("Host Q",)}, bot=bot)
    messages = _warnings(caplog)
    assert len(messages) == 1, f"expected only the local-scope warning, got {messages}"
    assert "ops" in messages[0] and "Host Q" in messages[0], \
        "the local-scope half must still warn on a headless install"
    assert "SAS" not in messages[0], \
        "the managed_by half ran on a headless install (the false positive)"


@pytest.mark.parametrize("locals_value", [None, {}, {"no_discord": False}, {"no_discord": 0}],
                         ids=["no locals attribute", "empty locals", "no_discord false",
                              "no_discord zero"])
def test_a_non_headless_bot_still_audits_the_managed_by_half(caplog, fresh_audit_log,
                                                             locals_value):
    """The control: without a TRUTHY ``no_discord`` the managed_by warning still fires.

    A bot that cannot supply ``locals`` (a Discord install, a strict double) is NOT headless, so the
    new gate must not leak into the normal path.
    """
    from services.webservice import auth as auth_module
    guild = _guild(_Role(1, "@everyone"), _Role(2, "Admin"), _Role(3, "DCS Admin"))
    bot = _audit_bot(guilds=[guild], servers={"SAS": _server("Server Owner")},
                     locals=locals_value)
    with caplog.at_level(logging.WARNING):
        auth_module._audit_scope_declarations({"ops": ("Host Q",)}, bot=bot)
    messages = _warnings(caplog)
    assert len(messages) == 2, f"expected the local-scope and managed_by warnings, got {messages}"
    assert any("SAS" in message and "Server Owner" in message for message in messages), \
        "the managed_by half must still warn on a non-headless bot"


def test_a_headless_install_logs_its_own_debug_line(caplog, fresh_audit_log):
    """Headless is its own fact, not the 'ready but in no guild' case.

    The two are different states -- one is permanent by configuration, the other resolves at the
    next boot -- so a headless bot with an EMPTY guild cache must read as headless, never as 'ready
    but in no guild'.
    """
    from services.webservice import auth as auth_module
    bot = _audit_bot(guilds=[], servers={"SAS": _server("Server Owner")},
                     locals={"no_discord": True})
    with caplog.at_level(logging.DEBUG):
        auth_module._audit_scope_declarations({}, bot=bot)
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("headless" in message for message in messages), \
        f"no headless debug line: {messages}"
    assert not any("ready but in no guild" in message for message in messages), \
        "a headless install was reported as the 'ready but in no guild' case"


def test_install_auth_records_the_declared_scopes_for_the_ready_time_run(tmp_path):
    """The real install path: what the deferred audit reads must be recorded while it is known."""
    from test_webui_dashboard import _build, _hash
    app, _registrar = _build(tmp_path, users=[{"username": "ops", "password_hash": _hash(),
                                               "roles": ["Admin"], "scope": ["Host A"]}])
    assert app.state.webui_scope_declarations == {"ops": ("Host A",)}


# ------------------------------------------------- the ready-time dispatch (service.py)


def _audit_service():
    return types.SimpleNamespace(log=logging.getLogger("scope-ready-test"),
                                 app=types.SimpleNamespace(state=object()))


def test_the_ready_audit_waits_for_the_bot_then_runs_against_the_app(monkeypatch, fresh_audit_log):
    from services.webservice import auth as auth_module
    from services.webservice.auth import discord_oauth as oauth_module
    from services.webservice.service import WebService

    waited = []

    async def wait_until_ready():
        waited.append(True)

    monkeypatch.setattr(oauth_module, "current_bot",
                        lambda: types.SimpleNamespace(wait_until_ready=wait_until_ready))
    audits = []
    monkeypatch.setattr(auth_module, "audit_scope_declarations", lambda app: audits.append(app))

    service = _audit_service()
    asyncio.run(WebService._audit_scope_when_ready(service))

    assert waited == [True], "the audit did not wait for the bot to be ready"
    assert audits == [service.app], "the audit did not run against this app"


@pytest.mark.parametrize("bot", [None, types.SimpleNamespace()],
                         ids=["no bot at all", "a bot with no way to wait"])
def test_the_ready_audit_survives_a_bot_it_cannot_wait_on(monkeypatch, fresh_audit_log, bot):
    """Early start, takeover or a stub: the dispatch must never raise, and the audit still runs."""
    from services.webservice import auth as auth_module
    from services.webservice.auth import discord_oauth as oauth_module
    from services.webservice.service import WebService

    monkeypatch.setattr(oauth_module, "current_bot", lambda: bot)
    audits = []
    monkeypatch.setattr(auth_module, "audit_scope_declarations", lambda app: audits.append(app))

    service = _audit_service()
    asyncio.run(WebService._audit_scope_when_ready(service))   # must not raise
    assert audits == [service.app]


def test_a_cancelled_wait_until_ready_propagates(monkeypatch, fresh_audit_log):
    """A CancelledError from ``wait_until_ready`` propagates out of ``_audit_scope_when_ready``.

    The service cancels this task on stop while it waits for READY; a cancellation absorbed here
    would leave the task reported as cleanly finished and hide a wait that never completed.
    Cancellation propagates on its own — ``CancelledError`` is a ``BaseException``, so the method's
    ``except Exception`` cannot catch it — and the audit must not run once the wait is cancelled.
    """
    from services.webservice import auth as auth_module
    from services.webservice.auth import discord_oauth as oauth_module
    from services.webservice.service import WebService

    async def wait_until_ready():
        raise asyncio.CancelledError()

    monkeypatch.setattr(oauth_module, "current_bot",
                        lambda: types.SimpleNamespace(wait_until_ready=wait_until_ready))
    audits = []
    monkeypatch.setattr(auth_module, "audit_scope_declarations", lambda app: audits.append(app))

    service = _audit_service()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(WebService._audit_scope_when_ready(service))
    assert audits == [], "a cancelled wait must not reach the audit"
