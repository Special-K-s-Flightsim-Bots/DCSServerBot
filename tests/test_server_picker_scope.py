"""The Server picker's per-server scope: an Admin may name ANY server.

`ServerTransformer` feeds every server picker in the bot (``/dcs`` scheduler, mission, admin, ...).
Its restriction is the shared rule ``core.utils.discord.may_manage_server``:

* a non-admin-class caller is not restricted here at all (Discord's own asymmetry, preserved);
* a caller holding the bot's own Admin role is UNRESTRICTED — it may name any server and is offered
  every server, matching the console, where Admin is unscoped (``services/webservice/scope.py``);
* every other admin-class caller must hold one of the server's ``managed_by`` entries.

The Admin-role notion has ONE home — ``core.utils.discord.holds_admin_role`` — shared with the ban
rule ``member_ban_targets``; the picker restates no second spelling of it.

These tests drive the REAL ``ServerTransformer`` (``transform`` and ``autocomplete``), not a
reimplementation of its gate, through a member/client double shaped like Discord's own objects.
"""
from __future__ import annotations

import types

import pytest

from core import Status
from core.utils.discord import ServerTransformer, may_manage_server, member_ban_targets, member_scope_tokens


def _holds_admin_role():
    """The shared Admin notion at its ONE home, imported lazily: a missing name fails INSIDE the
    test that wants it, so the picker tests' behavioural RED stays an assertion, not a collection
    error."""
    from core.utils.discord import holds_admin_role
    return holds_admin_role

#: The bot's own configured Admin role (``bot.roles['Admin']``): its default, and what every double
#: that stands in for a live bot declares.
ADMIN_ROLES = {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"]}


# ------------------------------------------------------------------------------- test doubles


class _Role:
    def __init__(self, name: str, role_id: int):
        self.name = name
        self.id = role_id


class _Member:
    """A caller as the picker reads it: a role list of names AND ids (``@everyone`` included)."""

    def __init__(self, *names: str):
        self.roles = [_Role("@everyone", 0)] + [_Role(name, 100 + i) for i, name in enumerate(names)]
        self.display_name = ", ".join(names) if names else "@everyone"


class _Server:
    def __init__(self, name: str, managed_by=(), status: Status = Status.RUNNING, maintenance=False):
        self.name = name
        self.display_name = name
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        self.status = status
        self.maintenance = maintenance


class _Check:
    """A command check as `ServerTransformer.is_admin` reads it: a ``role``/``roles`` attribute."""

    def __init__(self, role=None, roles=None):
        if role is not None:
            self.role = role
        if roles is not None:
            self.roles = roles


class _Command:
    def __init__(self, checks=(), can_run: bool = True):
        self.checks = list(checks)
        self._can_run = can_run

    async def _check_can_run(self, interaction):
        return self._can_run


class _Client:
    """The bot as the picker reads it: the server registry and the configured roles."""

    def __init__(self, servers: dict, roles: dict | None):
        self.servers = servers
        if roles is not None:
            self.roles = roles
        self.log = types.SimpleNamespace(exception=lambda exc: None)

    def get_server(self, interaction):
        return None


class _Interaction:
    def __init__(self, client, user, command):
        self.client = client
        self.user = user
        self.command = command


#: A command of the DCS-Admin class: its first check names ``DCS Admin``.
DCS_ADMIN_COMMAND = _Command(checks=[_Check(roles=["DCS Admin"])])
#: A non-admin-class command: its check names no privileged role.
PLAIN_COMMAND = _Command(checks=[_Check(roles=["DCS"])])


def _fleet() -> dict[str, _Server]:
    """Three servers: two tagged by different hosts, one UNTAGGED."""
    return {
        "SRS-1": _Server("SRS-1", ["Host A"]),
        "SRS-B": _Server("SRS-B", ["Host B"]),
        "SRS-U": _Server("SRS-U"),
    }


def _interaction(member, *, servers=None, roles=ADMIN_ROLES, command=DCS_ADMIN_COMMAND):
    return _Interaction(_Client(servers if servers is not None else _fleet(), roles), member, command)


async def _named(interaction, name: str) -> str:
    """The NAME the picker accepts, or ``REFUSED`` — the caller's own string, per the rule."""
    try:
        return (await ServerTransformer().transform(interaction, name)).name
    except Exception:
        return "REFUSED"


async def _offered(interaction, current: str = "") -> list[str]:
    return [choice.name for choice in await ServerTransformer().autocomplete(interaction, current)]


# ------------------------------------------------------------------- the Admin names any server


@pytest.mark.asyncio
async def test_the_picker_lets_an_admin_name_a_server_they_do_not_manage():
    """RED before the fix (the Admin's roles tag nothing here), GREEN after."""
    assert await _named(_interaction(_Member("Admin")), "SRS-B") == "SRS-B"


@pytest.mark.asyncio
async def test_the_picker_offers_an_admin_every_server():
    assert await _offered(_interaction(_Member("Admin"))) == ["SRS-1", "SRS-B", "SRS-U"]


@pytest.mark.asyncio
async def test_the_picker_lets_an_admin_name_a_tagged_server_they_do_not_manage():
    """A server managed by SOMEBODY ELSE is still the Admin's to name."""
    servers = {"SRS-1": _Server("SRS-1", ["Host A"]), "SRS-B": _Server("SRS-B", ["Host B"])}
    assert await _named(_interaction(_Member("Admin"), servers=servers), "SRS-1") == "SRS-1"


@pytest.mark.asyncio
async def test_the_admin_role_may_be_configured_as_a_role_id():
    """``bot.roles['Admin']`` may name the role by id: the member's role ids carry the match."""
    member = _Member("Admin")
    roles = {"Admin": [member.roles[1].id]}  # 100 — the id of the member's "Admin" role
    assert await _named(_interaction(member, roles=roles), "SRS-B") == "SRS-B"
    assert await _offered(_interaction(member, roles=roles)) == ["SRS-1", "SRS-B", "SRS-U"]


@pytest.mark.asyncio
async def test_a_role_named_admin_is_not_the_bots_admin_role_when_configured_otherwise():
    """The match is against the CONFIGURED role, never the literal string "Admin"."""
    roles = {"Admin": ["Floor Manager"]}
    interaction = _interaction(_Member("Admin"), roles=roles)
    assert await _named(interaction, "SRS-B") == "REFUSED"
    assert await _offered(interaction) == ["SRS-U"]


# ------------------------------------------------------------- the DCS Admin: unchanged, fail-closed


@pytest.mark.asyncio
async def test_the_picker_still_refuses_a_dcs_admin_a_foreign_server():
    assert await _named(_interaction(_Member("DCS Admin")), "SRS-B") == "REFUSED"


@pytest.mark.asyncio
async def test_the_picker_still_omits_a_foreign_server_for_a_dcs_admin():
    assert await _offered(_interaction(_Member("DCS Admin"))) == ["SRS-U"]


@pytest.mark.asyncio
async def test_the_picker_still_lets_a_dcs_admin_name_their_own_server():
    servers = {"SRS-1": _Server("SRS-1", ["Host A"]), "SRS-B": _Server("SRS-B", ["Host B"])}
    assert await _named(_interaction(_Member("DCS Admin", "Host A"), servers=servers), "SRS-1") == "SRS-1"


@pytest.mark.asyncio
async def test_a_non_admin_gains_nothing():
    """A member holding neither the Admin role nor a ``managed_by`` entry sees only the untagged."""
    interaction = _interaction(_Member("DCS Admin", "Host B"))
    assert await _named(interaction, "SRS-1") == "REFUSED"
    assert await _offered(interaction) == ["SRS-B", "SRS-U"]


# ------------------------------------------------------------ a non-admin-class caller: unchanged


@pytest.mark.asyncio
async def test_the_picker_leaves_a_non_admin_class_caller_alone():
    """The restriction applies to the DCS-Admin class only (Discord skips it for the others)."""
    interaction = _interaction(_Member("DCS"), command=PLAIN_COMMAND)
    assert await _named(interaction, "SRS-B") == "SRS-B"
    assert await _offered(interaction) == ["SRS-1", "SRS-B", "SRS-U"]


# --------------------------------------------------------------------------- the rule beneath it


def test_the_picker_allows_an_admin_whatever_the_server_declares():
    assert may_manage_server(["Host A"], _Member("Admin"), is_admin=True, admin_roles=["Admin"]) is True
    assert may_manage_server(None, _Member("Admin"), is_admin=True, admin_roles=["Admin"]) is True


def test_the_picker_still_refuses_a_non_admin_without_the_admin_role_argument():
    """Backwards-compatible: omitting *admin_roles* keeps exactly the pre-fix ``managed_by`` rule."""
    assert may_manage_server(["Host A"], _Member("Host B"), is_admin=True, admin_roles=None) is False
    assert may_manage_server(["Host A"], _Member("Host B"), is_admin=True) is False


def test_the_picker_leaves_a_non_admin_caller_alone_whatever_the_admin_roles():
    assert may_manage_server(["Host A"], _Member("Host B"), is_admin=False, admin_roles=["Admin"]) is True


def test_no_member_never_holds_the_admin_role():
    rule = _holds_admin_role()
    assert rule(None, ["Admin"]) is False
    assert rule(_Member("Admin"), None) is False
    assert rule(_Member("Admin"), []) is False


def test_holds_admin_role_matches_a_name_and_an_id():
    rule = _holds_admin_role()
    member = _Member("Admin")
    assert rule(member, ["Admin"]) is True
    assert rule(member, [member.roles[1].id]) is True
    assert rule(_Member("Host A"), ["Admin"]) is False


# --------------------------------------------------- ONE home: the picker and the ban rule share it


def test_the_picker_and_the_ban_rule_share_the_one_admin_notion(monkeypatch):
    """Both consume ``holds_admin_role``; neither re-derives the admin intersection itself.

    Spying on the shared function and seeing BOTH callers hit it is the no-drift proof: a second
    spelling anywhere in either path would leave a caller that does not appear here.
    """
    import sys

    module = sys.modules["core.utils.discord"]
    calls: list = []

    def spy(member, admin_roles=None):
        calls.append(admin_roles)
        return not member_scope_tokens(member).isdisjoint(admin_roles or ())

    monkeypatch.setattr(module, "holds_admin_role", spy)

    member_ban_targets(_Member("Admin"), _fleet(), ["Admin"])
    may_manage_server(["Host B"], _Member("Admin"), is_admin=True, admin_roles=["Admin"])

    assert calls == [["Admin"], ["Admin"]]


def test_holds_admin_role_is_the_only_spelling_of_the_intersection():
    """One home: the admin intersection appears once in the module, inside ``holds_admin_role``."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "core" / "utils" / "discord.py"
    text = source.read_text(encoding="utf-8")
    assert text.count("isdisjoint(admin_roles") == 1
    assert "def holds_admin_role(" in text