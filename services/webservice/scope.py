"""The per-server scope (``managed_by``): ONE rule, shared by the bot and the admin web UI.

the maintainer's requirement, verbatim: *"managed_by should be like a different view for people to see only
their servers. They can operate their servers and kick players from them but they might not see
other servers or kick other players. Also, they might not do special admin tasks that might affect
other servers."*

THE RULE, taken from Discord unchanged (spec §10.1):

* a server that declares no ``managed_by`` is visible to everyone who may read the console;
* a server that declares one is visible only to a caller holding one of the listed roles;
* ``Admin`` bypasses the restriction.

The comparison is made against the caller's **Discord** role ids and names — the same pair
:func:`core.utils.discord.check_roles` builds — because ``managed_by`` entries are free text
validated only as a sequence of non-empty strings (``schemas/servers_schema.yaml``), while the
console's capability map is written in the bot's role NAMES. Discarding that difference is what
:func:`member_scope_tokens` exists for.

WHY THE RULE IS NOT DEFINED HERE. ``holds_scope`` is ONE pure function and it is implemented in
``core/utils/discord.py``, next to :func:`core.utils.discord.check_roles` — the comparison it is an
extension of. This module is an IMPLEMENTATION of the console that USES it: it imports the three
(``holds_scope``, ``member_scope_tokens``, ``may_manage_server``) and applies them to a request
(``Scope``) and to the console's own inputs (the audits). It restates no rule.

The direction matters: ``core`` is the bot and the reusable half, and the console is a deployable
beside it, so ``core`` may not import a service package. When the rule lived here, ``core`` did
exactly that — a cycle waiting to bite, since this module is on the web's import chain. One rule,
one direction: ``core`` owns it; the web consumes it.

ONE VALUE PER REQUEST. A token set alone cannot say "Admin: unscoped" or "we could not resolve this
identity": both would be an empty set, and the second must DENY while the first must ALLOW
everything. :class:`Scope` is therefore three states, and it is the single value a page, the live
stream or a read model consumes:

* :meth:`Scope.everything` — unscoped (any ``Admin``, break-glass, a local account that declares no
  scope);
* :meth:`Scope.restricted` — the resolved tokens; a server is visible iff
  :func:`holds_scope` says so;
* :meth:`Scope.unresolved` — FAIL CLOSED: nothing is visible, not everything (bot restarting, no
  guild, an empty member cache).

The resolution is per request and never cached (see ``permissions.scope_for``), exactly like the
roles: a Discord role change, a removed local user or an edited ``managed_by`` lands on the next
request.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

# The rule itself (implemented in core, next to `check_roles`). Re-exported through this module so
# the console's own call sites — `auth/discord_oauth.py` imports `member_scope_tokens` from here —
# keep one address per name without restating anything.
from core.utils.discord import holds_scope, may_manage_server, member_scope_tokens  # noqa: E402

__all__ = [
    "ADMIN_ROLE",
    "Scope",
    "holds_scope",
    "manages_any_server",
    "member_scope_tokens",
    "may_manage_server",
    "server_managed_by",
    "gather_live_scope",
    "audit_local_scopes",
    "audit_server_managed_by",
]

#: The role that bypasses the restriction, on both sides (spec §9.1's superset rule: the console's
#: one privileged role NAMES the same string, and the map module will own the constant once it
#: exists — this is the name, not a second rule).
ADMIN_ROLE = "Admin"


def manages_any_server(scope: "Scope", servers: Iterable[Any] | None = ()) -> bool:
    """Whether *scope* is what makes its identity a MANAGER of at least one server.

    THE third kind of console identity (the other two are roles): the hoster customer, who reaches
    the console because servers are declared as theirs. The dashboard is for
    ``Admin``, ``DCS Admin`` and ``'managed_by'`` roles — the general member role is not one of
    them, and this function is the ONE place that decides which identities the last clause covers.

    Two ways to hold it, and the difference matters:

    * the scope was **DECLARED** (:attr:`Scope.declared`) — a local account's
      ``auth.local.users[].scope``, the only backend that can state the ``managed_by`` values it
      manages instead of deriving them from a Discord member. A declaration IS the fact (it is
      validated for shape at startup and audited against the servers' values), so it needs no
      server to match: a typo opens the console on an empty view, loud at startup, rather than
      locking the account out with no way to tell why;
    * otherwise the scope's tokens must match a server's ``managed_by`` — the Discord case, where
      the tokens are the member's role ids AND names. A Discord member always holds roles, so
      "the scope has tokens" would make every member a manager; only a server that NAMES one of
      those roles makes them one. The match is against the LIVE role names/ids, which is why a
      ``managed_by`` rename removes access on the next request (spec §10.6: *a ``managed_by`` role
      rename is an access change*).

    What does NOT qualify, and each for its own reason:

    * an :meth:`Scope.unresolved` scope — FAIL CLOSED (the bot is restarting, the guild is
      unreachable, the member cache is empty). An unresolvable scope is not a licence, and a
      manager is refused rather than shown the cluster;
    * an :meth:`Scope.everything` scope — unscoped means "the role model already granted this"
      (``Admin``, break-glass, a local account that declares nothing). It is never a second way in:
      a roleless local account declares no scope and is exactly the account that must NOT reach
      the console;
    * a server that declares NO ``managed_by`` — visible to everyone who may read the console
      (spec §10.1), so it cannot be what makes somebody a manager, or every member would be one.
    """
    if not scope.resolved or scope.unrestricted:
        return False
    if scope.declared:
        return True
    for server in servers or ():
        managed_by = server_managed_by(server)
        if managed_by and holds_scope(managed_by, scope.tokens):
            return True
    return False


# ------------------------------------------------------------------ the scope key (the console's)

def server_managed_by(server) -> tuple:
    """A server's declared ``managed_by`` values, read tolerantly and in ONE place.

    Every reader of the scope key needs the same answer: the startup audit
    (:func:`gather_live_scope`) and the console's scoped source wrapper
    (``services.webservice.readmodels.source.ScopedSource``). The input is duck-typed — a live
    ``ServerImpl`` (whose ``locals`` is the server's config block, ``core/data/server.py:66``), a
    test double, or an object that has neither — so a missing/short-typed ``locals`` yields ``()``
    (= "no restriction") rather than raising on a page render.

    The values are returned AS DECLARED, never coerced: :func:`holds_scope` matches a ``managed_by``
    entry against a role id OR a role name, so stringifying a declared id here would break exactly
    the match :func:`core.utils.discord.check_roles` still makes. A caller that needs text (the
    startup audit compares against the guild's role names and ids) stringifies itself.
    """
    locals_ = getattr(server, "locals", None)
    if locals_ is None or not hasattr(locals_, "get"):
        return ()
    return tuple(locals_.get("managed_by") or ())


# --------------------------------------------------------------------------- the scope value

@dataclass(frozen=True, slots=True)
class Scope:
    """What servers ONE request may see and act on. Immutable, comparable, loggable."""

    unrestricted: bool
    tokens: frozenset = frozenset()
    resolved: bool = True
    #: True when the backend DECLARED the tokens (a local account's ``scope``) rather than deriving
    #: them from a member's roles. Read by :func:`manages_any_server`: a declaration is the fact
    #: itself, while a Discord member's token set is every role they hold and only becomes a licence
    #: by matching a server's ``managed_by``.
    declared: bool = False

    def allows(self, managed_by) -> bool:
        """Whether a server declaring *managed_by* is inside this scope."""
        if not self.resolved:
            return False            # fail closed: nothing is visible, not everything
        if self.unrestricted:
            return True
        return holds_scope(managed_by, self.tokens)

    @classmethod
    def everything(cls) -> "Scope":
        """Unscoped: an ``Admin``, break-glass, or a local account with no declared scope."""
        return cls(unrestricted=True)

    @classmethod
    def restricted(cls, tokens: Iterable[Any], *, declared: bool = False) -> "Scope":
        """Scoped to the given tokens (Discord role ids/names, or declared ``managed_by`` values).

        ``declared=True`` is the LOCAL backend's shape: the values are what the account says it
        manages (``auth.local.users[].scope``), not what a member's roles happen to be.
        """
        return cls(unrestricted=False, tokens=frozenset(tokens), declared=declared)

    @classmethod
    def unresolved(cls) -> "Scope":
        """The identity (or its roles) could not be resolved. DENY — never widen."""
        return cls(unrestricted=False, resolved=False)


# --------------------------------------------------------------------------- the live inputs

def gather_live_scope(bot) -> tuple[list[tuple[str, tuple[str, ...]]], set[str]]:
    """``([(server name, its managed_by values)], {guild role ids and names})``, read tolerantly.

    Both startup diagnostics need data only a RUNNING bot has: the servers' ``managed_by`` values
    (free text, per server config) and the guild's role ids/names. Read through ``getattr`` so a
    partial bot object (early start, a takeover, a headless install) yields empty inputs instead of
    raising on a startup path.
    """
    servers: list[tuple[str, tuple[str, ...]]] = []
    for name, server in (getattr(bot, "servers", None) or {}).items():
        servers.append((str(name), tuple(str(value) for value in server_managed_by(server))))

    roles: set[str] = set()
    for guild in getattr(bot, "guilds", None) or ():
        for role in getattr(guild, "roles", None) or ():
            role_id = getattr(role, "id", None)
            if role_id is not None:
                roles.add(str(role_id))
            role_name = getattr(role, "name", None)
            if isinstance(role_name, str) and role_name:
                roles.add(role_name)
    return servers, roles


# --------------------------------------------------------------------------- the two WARNINGs
#
# Both diagnostics describe a CONDITION that does not change between requests (a typo in a config
# file), so each is logged ONCE per value and counted afterwards — the discipline this console
# already applied to expected refusals (permissions.ConditionLog). They are startup diagnostics
# (install_auth) and the SAME functions are safe to call again after a server-config reload: the
# audit keeps returning what it found, the log stays quiet.

_AUDIT_CONDITIONS = None


def _audit_conditions():
    """The once-per-condition log, created lazily (``scope`` must not import ``permissions`` at
    import time: ``permissions`` is imported by every web module and stays import-cheap)."""
    global _AUDIT_CONDITIONS
    if _AUDIT_CONDITIONS is None:
        from .permissions import ConditionLog
        _AUDIT_CONDITIONS = ConditionLog(log)
    return _AUDIT_CONDITIONS


def audit_local_scopes(declared_by_user: Mapping[str, Iterable[str]],
                       server_managed_by: Iterable[str]) -> list[tuple[str, str]]:
    """WARN for every declared local scope that matches no server's ``managed_by``.

    A typo there is otherwise a SILENT loss of access: the account signs in, the gate lets it read
    the console, and every server it was meant to manage is missing from its view. Returns the
    ``(user, value)`` pairs it found unmatched, so a caller can assert on the finding and not only
    on the log.
    """
    known = {str(value) for value in (server_managed_by or ())}
    unmatched: list[tuple[str, str]] = []
    for user, declared in (declared_by_user or {}).items():
        for value in declared or ():
            text = str(value)
            if text in known:
                continue
            unmatched.append((str(user), text))
            _audit_conditions().hit(
                ("local-scope", str(user), text),
                "Admin web UI scope: auth.local.users '%s' declares scope '%s', which matches no "
                "server's managed_by in this installation - the account will only see servers that "
                "declare no managed_by at all. managed_by values are free text in the server "
                "configuration, so check the spelling.", user, text, summary=None)
    return unmatched


def audit_server_managed_by(by_server: Iterable[tuple[str, Iterable[str]]],
                            guild_roles: Iterable[str]) -> list[tuple[str, str]]:
    """WARN for every server whose ``managed_by`` value matches no role of the guild.

    The strictness is deliberate (spec §10.6): the comparison is against LIVE Discord role names and
    ids, so renaming or deleting a ``managed_by`` role removes that server from the scoped view of
    everyone holding it — which is otherwise silent. This warning is what makes **a ``managed_by``
    rename an access change** visible at the one moment somebody can still decide about it.
    """
    known = {str(role) for role in (guild_roles or ())}
    unmatched: list[tuple[str, str]] = []
    for server, values in by_server or ():
        for value in values or ():
            text = str(value)
            if text in known:
                continue
            unmatched.append((str(server), text))
            _audit_conditions().hit(
                ("server-managed-by", str(server), text),
                "Admin web UI scope: server '%s' declares managed_by '%s', which matches no role of "
                "the guild - no scoped account can see this server (renaming a managed_by role is an "
                "access change).", server, text, summary=None)
    return unmatched
