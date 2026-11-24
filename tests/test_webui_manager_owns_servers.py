"""A manager sees and acts on THEIR servers only (``managed_by`` as an allow-list, fail-closed).

Two fail-open defaults are closed, both exercised against a cluster with one tagged and one untagged
server: ``Scope.allows`` used to treat a server declaring NO ``managed_by`` as "no restriction" (now
an allow-list -- such a server is invisible and not actionable), and ``LocalUser.scope`` used to be
unrestricted whatever roles it declared (now unrestricted ONLY with a cluster role, ``CLUSTER_ROLES``;
otherwise scoped to nothing, named by a startup WARNING). The rule is asserted on the read surfaces
(the write routes are covered separately) plus the two pure primitives it must not disturb
(``holds_scope`` / ``may_manage_server``, whose empty-``managed_by`` semantics the bot's Discord
commands still depend on)."""
from __future__ import annotations

import logging
import types

import pytest

from conftest import asgi_client, permissions
from services.webservice.auth import local as local_auth
from services.webservice.scope import CLUSTER_ROLES, Scope
from core.utils.discord import holds_scope, may_manage_server
from test_webui_dashboard import _hash, _login
from test_webui_scope_view import (Cluster, build as _build, instance, node, player, server,
                                   assert_no_out_of_scope)

#: the untagged server's name and every name that belongs to it (scanned for on every surface).
UNTAGGED = "Untagged Server"
UNTAGGED_NODE = "dcs-a-01"          # SHARED with the tagged server, so the node IS the manager's
UNTAGGED_INSTANCE = "Untagged-Instance"
UNTAGGED_PLAYER = "Rogue-1"

TAGGED = "Host A Server"


def fleet():
    """ONE node carrying TWO servers: the manager's (``managed_by: [Host A]``) and an UNTAGGED one.

    Both sit on the same node so the manager's node row survives (the node carries their server);
    the untagged server's own row, instance and player must NOT, and the untagged server must not be
    visible or actionable by any means.
    """
    a = server(TAGGED, ["Host A"], node_name=UNTAGGED_NODE, instance="Host-A-Instance",
               status="Running", players=[player("Viper-1")])
    u = server(UNTAGGED, None, node_name=UNTAGGED_NODE, instance=UNTAGGED_INSTANCE,
               status="Running", players=[player(UNTAGGED_PLAYER)])
    node_a = node(UNTAGGED_NODE, master=True,
                  instances={"Host-A-Instance": instance("Host-A-Instance", a),
                             UNTAGGED_INSTANCE: instance(UNTAGGED_INSTANCE, u)})
    return Cluster([a, u], nodes={UNTAGGED_NODE: node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance(UNTAGGED_INSTANCE, u)])


#: every persona: a MANAGER (a declared scope), and the two CLUSTER roles (unrestricted).
USERS = [
    {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]},
    {"username": "dcs_admin", "password_hash": _hash(), "roles": ["DCS Admin"]},
    {"username": "manager", "password_hash": _hash(), "roles": [], "scope": ["Host A"]},
]

assert set(CLUSTER_ROLES) == {"Admin", "DCS Admin"}, CLUSTER_ROLES


@pytest.fixture
def config_dir(tmp_path):
    from test_webui_dashboard import BOT_YAML
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def app_for(config_dir, cluster, *, users=None):
    return _build(config_dir, cluster, users=users if users is not None else USERS)


async def get(app, path, username="manager"):
    async with asgi_client(app) as client:
        assert (await _login(client, username)).status_code == 303, username
        return await client.get(path)


def assert_untagged_invisible(text: str, where: str = ""):
    for name in (UNTAGGED, UNTAGGED_INSTANCE, UNTAGGED_PLAYER):
        assert name not in text, f"{where} leaked {name!r}"


# ============================================================ the primitive: fail-closed allow-list

def test_a_restricted_scope_denies_a_server_that_declares_no_managed_by():
    """THE inversion: for a non-cluster viewer, no ``managed_by`` means NOT theirs."""
    scope = Scope.restricted({"Host A"})
    assert scope.allows(["Host A"]) is True
    assert scope.allows(["Host B"]) is False
    assert scope.allows([]) is False, "an untagged server is not a manager's server"
    assert scope.allows(None) is False


def test_an_unrestricted_scope_still_allows_an_untagged_server():
    """Positive control: the cluster roles are unchanged — everything is theirs."""
    assert Scope.everything().allows(None) is True
    assert Scope.everything().allows([]) is True
    assert Scope.everything().allows(["Host A"]) is True


def test_the_local_account_scoped_to_nothing_allows_nothing():
    """The fail-closed local scope: ``restricted(())`` matches nothing, tagged or not."""
    scope = Scope.restricted(())
    assert scope.allows(["Host A"]) is False
    assert scope.allows(None) is False


def test_the_discord_restriction_rule_is_unchanged():
    """The bot's own Discord commands keep the empty-``managed_by`` rule — it is NOT the console's.

    This is the property that must not move: ``holds_scope`` / ``may_manage_server`` are consumed by
    the Server picker (``core/utils/discord.py``) and the bot's ``get_servers``.
    """
    assert holds_scope(None, {"Host A"}) is True
    assert holds_scope([], set()) is True
    assert holds_scope(["Host A"], {"Host A"}) is True
    assert holds_scope(["Host A"], {"Host B"}) is False
    member = types.SimpleNamespace(roles=[types.SimpleNamespace(id=8, name="Host B")])
    assert may_manage_server(None, member, is_admin=True) is True


# ============================================================ the manager's reads: only their rows

@pytest.mark.asyncio
async def test_a_manager_sees_only_their_tagged_server(config_dir):
    app = app_for(config_dir, fleet())
    html = (await get(app, "/fleet")).text
    assert TAGGED in html
    assert_untagged_invisible(html, "the manager's /fleet page")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/fleet", "/players", "/instances"])
async def test_the_untagged_server_is_withheld_on_every_read_page(config_dir, path):
    app = app_for(config_dir, fleet())
    response = await get(app, path)
    assert response.status_code == 200, path
    assert_untagged_invisible(response.text, f"the manager's {path}")


@pytest.mark.asyncio
async def test_the_dashboard_counts_follow_the_managers_scope(config_dir):
    """The tiles are THEIR figures: one server up, not two — the cluster's counts never leak."""
    app = app_for(config_dir, fleet())
    html = (await get(app, "/")).text
    assert "1/1 servers up" in html
    assert "2/2 servers up" not in html
    assert f"1 players online" in html
    assert_untagged_invisible(html, "the manager's dashboard")


@pytest.mark.asyncio
async def test_a_crafted_request_for_the_untagged_server_is_refused(config_dir):
    """Naming it directly is the console's 403 — and it discloses nothing (same for a name that
    does not exist)."""
    app = app_for(config_dir, fleet())
    refused = await get(app, f"/fleet/{UNTAGGED}")
    assert refused.status_code == 403, refused.status_code
    assert UNTAGGED not in refused.text
 # the name that DOES not exist answers the same refusal shape
    absent = await get(app, "/fleet/No Such Server")
    assert absent.status_code == 403


@pytest.mark.asyncio
async def test_the_manager_opens_their_own_servers_page(config_dir):
    """Positive control: the tagged server's page resolves for the manager."""
    app = app_for(config_dir, fleet())
    response = await get(app, f"/fleet/{TAGGED}")
    assert response.status_code == 200
    assert TAGGED in response.text


# ============================================================ the cluster roles are unchanged

@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["specialk", "dcs_admin"])
async def test_the_cluster_roles_reach_every_server(config_dir, username):
    app = app_for(config_dir, fleet())
    html = (await get(app, "/fleet", username)).text
    assert TAGGED in html and UNTAGGED in html, f"{username} lost the untagged server"
    dashboard = (await get(app, "/", username)).text
    assert "2/2 servers up" in dashboard


@pytest.mark.asyncio
async def test_a_cluster_role_opens_the_untagged_servers_page(config_dir):
    app = app_for(config_dir, fleet())
    response = await get(app, f"/fleet/{UNTAGGED}", "specialk")
    assert response.status_code == 200


# ============================================================ the fail-closed local account

def test_a_non_cluster_local_account_without_a_scope_is_scoped_to_nothing(caplog, tmp_path):
    """No ``scope:`` and no cluster role -> scoped to NOTHING, and a WARNING names it (quoted)."""
    from services.webservice.auth import SESSION_IDENTITY_KEY
    (tmp_path / "services").mkdir()
    user = local_auth.LocalUser(username="ghost", password_hash=_hash(), roles=("DCS",))
    backend = local_auth.LocalAuthBackend((user,), config_dir=tmp_path)
    request = types.SimpleNamespace(
        session={SESSION_IDENTITY_KEY: {"backend": "local", "subject": "ghost"}},
        method="GET", url=types.SimpleNamespace(path="/"))
    identity = backend.authenticate(request)
    assert identity is not None
    assert identity.scope.allows(["Host A"]) is False
    assert identity.scope.allows(None) is False
    assert identity.scope.unrestricted is False

    with caplog.at_level(logging.WARNING):
        local_auth.parse_users(
            {"users": [{"username": "ghost", "password_hash": _hash(), "roles": ["DCS"]}]},
            config_dir=tmp_path)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ghost" in message and "scope" in message for message in warnings), warnings


def test_a_local_account_with_a_cluster_role_and_no_scope_is_unrestricted(tmp_path):
    """The OTHER half: a cluster role with no declaration keeps the whole cluster — unchanged."""
    for role in CLUSTER_ROLES:
        user = local_auth.LocalUser(username="ops", password_hash=_hash(), roles=(role,))
        backend = local_auth.LocalAuthBackend((user,), config_dir=tmp_path)
        identity = backend.authenticate(types.SimpleNamespace(
            session={"webui_identity": {"backend": "local", "subject": "ops"}},
            method="GET", url=types.SimpleNamespace(path="/")))
        assert identity.scope == Scope.everything(), role
