"""Every write route must check the TARGET's scope, not just the capability.

Holding a capability is not the same as being allowed to act on THAT server, so this file proves, per
route, that a manager acting on a server OUTSIDE their scope is refused while the SAME manager acting
INSIDE it still works -- and that a node is never a server a manager can reach. ``EXPECTED_ROUTES`` is
the declared route table this file pins. No route is found WITHOUT a target-scope check: the seam's
``resolve_scoped_server`` / ``resolve_scoped_node`` applies the caller's ``Scope`` on EVERY write route
and is given the request's SCOPED source. The config and channels writes are Admin-only with no scope
grant, so their out-of-scope caller is refused at the capability gate (the route still resolves its
target through the same scoped seam). Driven through the real app and a stub cluster."""
from __future__ import annotations

import re
import types

import pytest

from core import Status
from services.webservice import permissions
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page

# the harness + the doubles of the three write surfaces, reused rather than copied
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_write_player_actions import (  # noqa: F401
    FakePlayer, ban_service, player_fleet,
)
from test_webui_node_controls import FakeNode, fleet as node_fleet  # noqa: F401
from test_webui_write_message import UCID_A  # noqa: F401

CONFIRM = actions_page.CONFIRM_SUFFIX
SERVERS = servers_page.SERVERS_PATH


# ───────────────────────────────────────────────────────────── the route table, written down

#: ``(path, capability, roles, scope_grants)`` for EVERY write route the console declares.
EXPECTED_ROUTES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
 # the SERVER row — ``WRITE_ROLES`` + a manager's scope grant
    ("/actions/server/startup", "servers.startup", ("Admin", "DCS Admin"), True),
    ("/actions/server/start", "servers.start", ("Admin", "DCS Admin"), True),
    ("/actions/server/pause", "missions.pause", ("Admin", "DCS Admin"), True),
    ("/actions/server/unpause", "missions.unpause", ("Admin", "DCS Admin"), True),
    ("/actions/server/restart", "servers.restart", ("Admin", "DCS Admin"), True),
    ("/actions/server/shutdown", "servers.shutdown", ("Admin", "DCS Admin"), True),
    ("/actions/server/stop", "servers.stop", ("Admin", "DCS Admin"), True),
    ("/actions/server/maintenance", "servers.maintenance", ("Admin", "DCS Admin"), True),
    ("/actions/server/end-maintenance", "servers.clear_maintenance", ("Admin", "DCS Admin"), True),
 # the SERVER MESSAGE pair — the server-row twin of the player pair: a server-wide message is a
 # server-row write, so it carries the same rule (``WRITE_ROLES`` + a manager's scope grant)
    ("/actions/server/chat", "servers.message", ("Admin", "DCS Admin"), True),
    ("/actions/server/popup", "servers.message", ("Admin", "DCS Admin"), True),
 # the PLAYER row — same rule (a manager acts on THEIR players only)
    ("/actions/player/kick", "players.kick", ("Admin", "DCS Admin"), True),
    ("/actions/player/ban", "players.ban", ("Admin", "DCS Admin"), True),
    ("/actions/player/chat", "players.message", ("Admin", "DCS Admin"), True),
    ("/actions/player/popup", "players.message", ("Admin", "DCS Admin"), True),
    ("/actions/player/mute", "players.mute", ("Admin", "DCS Admin"), True),
    ("/actions/player/unmute", "players.mute", ("Admin", "DCS Admin"), True),
    ("/actions/player/screenshot", "players.screenshot", ("Admin", "DCS Admin"), True),
 # THE BAN-ENTRY writes — the LIFT and the ADD: ``WRITE_ROLES`` WITH a manager scope grant. The
 # target is a ban ENTRY (a ucid plus the ban's own timestamp) for the lift and a bare UCID for the
 # add, and the scope is applied inside the action, so a manager adds and lifts only on THEIR OWN
 # servers. The ADD reuses ``players.ban`` — adding a ban is ONE concept.
    ("/actions/ban/lift", "players.unban", ("Admin", "DCS Admin"), True),
    ("/actions/ban/add", "players.ban", ("Admin", "DCS Admin"), True),
    ("/actions/ban/edit", "players.edit_ban", ("Admin", "DCS Admin"), True),
 # THE WATCH-ENTRY writes — the REMOVE and the EDIT: ``WRITE_ROLES`` WITH a manager scope grant.
 # The target is a watch ENTRY (its UCID) and the bound is applied inside the actions (the player
 # read's own presence rule), so a manager changes the watch entries of THEIR OWN players only. ONE
 # capability for all three: who may change what the watchlist says about a player is one rule.
    ("/actions/watch/add", "players.watch", ("Admin", "DCS Admin"), True),
    ("/actions/watch/remove", "players.watch", ("Admin", "DCS Admin"), True),
    ("/actions/watch/edit", "players.watch", ("Admin", "DCS Admin"), True),
 # THE PENALTY-POINT writes — ``WRITE_ROLES`` WITH a manager scope grant. The target is a UCID and
 # the write is FLEET-WIDE: no server is picked, and the action resolves its own reference server.
 # The grant stays because the capability is the same one the console declares for the surface.
    ("/actions/penalty/add", "players.punish", ("Admin", "DCS Admin"), True),
    ("/actions/penalty/remove", "players.punish", ("Admin", "DCS Admin"), True),
    ("/actions/penalty/forgive", "players.punish", ("Admin", "DCS Admin"), True),
 # the NODE row — Admin only, NO scope grant (a node is not a server)
    ("/actions/node/restart", "nodes.restart", ("Admin",), False),
    ("/actions/node/shutdown", "nodes.shutdown", ("Admin",), False),
    ("/actions/node/upgrade", "nodes.upgrade", ("Admin",), False),
    ("/actions/node/offline", "nodes.offline", ("Admin",), False),
    ("/actions/node/online", "nodes.online", ("Admin",), False),
 # THE DCS UPDATE — ONE control for update, roll back and switch branch. Admin only, NO scope
 # grant, for the node row's own reason: it stops every server on the machine.
    ("/actions/node/dcs_update", "nodes.dcs_update", ("Admin",), False),
 # the two faces of the configuration tab — Admin roles; the DCS write adds a manager scope grant,
 # the channels write does NOT (a manager may not write the channels at all)
    ("/actions/server/config", "servers.config.dcs", ("Admin",), True),
    ("/actions/server/channels", "servers.config.channels", ("Admin",), False),
 # the THIRD face of the configuration tab — the coalition passwords: Admin roles WITH a manager
 # scope grant (a manager reaches their OWN servers' coalition passwords, the same rule as the DCS
 # write; the action additionally refuses the REST/MCP transports)
    ("/actions/server/coalitions", "servers.config.coalitions", ("Admin",), True),
 # THE MISSION-LIST WRITES — the Missions tab's four controls: ``WRITE_ROLES`` WITH a manager
 # scope grant, so a manager adds / removes / loads / uploads missions on THEIR OWN servers.
    ("/actions/server/missions/add", "missions.add", ("Admin", "DCS Admin"), True),
    ("/actions/server/missions/delete", "missions.delete", ("Admin", "DCS Admin"), True),
    ("/actions/server/missions/load", "missions.load", ("Admin", "DCS Admin"), True),
    ("/actions/server/missions/upload", "missions.upload", ("Admin", "DCS Admin"), True),
 # THE M5 MISSION-LIST WRITES — bulk remove (re-using ``missions.delete``) and move up/down.
    ("/actions/server/missions/delete_bulk", "missions.delete", ("Admin", "DCS Admin"), True),
    ("/actions/server/missions/reorder", "missions.reorder", ("Admin", "DCS Admin"), True),
 # THE ACTIVATE WRITE (M7) — make a configured mission the START mission; ``WRITE_ROLES`` WITH a
 # manager scope grant, the same rule as the other mission writes.
    ("/actions/server/missions/activate", "missions.activate", ("Admin", "DCS Admin"), True),
 # THE RADIO WRITES (the Music tab) — ``WRITE_ROLES`` WITH a manager scope grant, the same server-write
 # rule as the mission writes: a manager drives the radios of THEIR OWN servers.
    ("/actions/server/music/control", "music.control", ("Admin", "DCS Admin"), True),
    ("/actions/server/music/playlist", "music.control", ("Admin", "DCS Admin"), True),
    ("/actions/server/music/settings", "music.control", ("Admin", "DCS Admin"), True),
 # THE SERVER CREATION PAIR — Admin only, NO scope grant (both CREATE or REMOVE an instance, which is
 # a node fact: a manager's scope is a set of server names and cannot name one that does not exist
 # yet, nor licence removing a machine's instance).
    ("/actions/server/add", "servers.add", ("Admin",), False),
    ("/actions/server/delete", "servers.delete", ("Admin",), False),
)

#: the write actions whose control posts to a DIALOG first (confirm and/or an option)
SERVER_DIALOG_ACTIONS = ("/actions/server/startup", "/actions/server/restart",
                         "/actions/server/shutdown", "/actions/server/stop",
                         "/actions/server/chat", "/actions/server/popup")
PLAYER_DIALOG_ACTIONS = ("/actions/player/kick", "/actions/player/ban",
                         "/actions/player/chat", "/actions/player/popup")
NODE_DIALOG_ACTIONS = ("/actions/node/restart", "/actions/node/shutdown", "/actions/node/upgrade",
                       "/actions/node/offline", "/actions/node/online",
                       "/actions/node/dcs_update")
#: THE CREATION PAIR's dialogs: the ADD control IS a form (a node, an instance, a name, channels) and
#: the DELETE one asks for its target's name to be typed. Both post through `<path>/confirm`, gated by
#: the SAME capability as performing the write.
CREATION_DIALOG_ACTIONS = ("/actions/server/add", "/actions/server/delete")
#: the Mission-tab controls reached through the console's dialog (`confirm.html`) — the decisive ADD
#: flow, the destructive REMOVE, the M5 BULK remove (whose dialog is the selection), and LOAD (whose
#: dialog carries the launch form).
#: All post through `<path>/confirm`, gated by the SAME capability as performing the write.
MISSION_DIALOG_ACTIONS = ("/actions/server/missions/add", "/actions/server/missions/delete",
                          "/actions/server/missions/delete_bulk", "/actions/server/missions/load")
#: THE BAN-ENTRY writes' dialogs: the LIFT confirms through ``confirm.html`` and the ADD's dialog IS
#: its form, so both post through ``<path>/confirm``, gated by the SAME capability as the write.
BAN_DIALOG_ACTIONS = ("/actions/ban/lift", "/actions/ban/add", "/actions/ban/edit")
#: THE WATCH-ENTRY writes' dialogs: the REMOVE confirms through ``confirm.html``, the ADD's and the
#: EDIT's dialogs ARE their forms, so all three post through ``<path>/confirm``, gated by the SAME
#: capability as the write.
WATCH_DIALOG_ACTIONS = ("/actions/watch/add", "/actions/watch/remove", "/actions/watch/edit")
#: THE PENALTY-POINT writes' dialogs: every control posts through ``<path>/confirm`` (the server
#: picker and the consequence live there), gated by the SAME capability as the write.
PENALTY_DIALOG_ACTIONS = ("/actions/penalty/add", "/actions/penalty/remove", "/actions/penalty/forgive")

#: THE DECLARED PATHS THAT ARE NOT WRITES: the short-lived image URL the screenshot modal loads, and
#: the ADD ban dialog's player SEARCH (a JSON GET). Both are declared like every route (the gate is
#: deny-by-default) and carry the capability of the control they answer, but they are GETs, so they are
#: listed apart from the write table and excluded from its shape assertions.
DECLARED_READS = frozenset({"/players/screenshot/{token}.png", "/actions/ban/players",
                            # the DCS update dialog's version list (a JSON GET, the control's own
                            # capability): the door to the write it feeds, not a write itself
                            "/nodes/{node:path}/dcs/versions"})

#: EVERY path ``capabilities`` must declare: the writes plus their dialog routes
EXPECTED_DECLARED = frozenset(
    [path for path, *_ in EXPECTED_ROUTES]
    + [path + CONFIRM for path in
       SERVER_DIALOG_ACTIONS + PLAYER_DIALOG_ACTIONS + NODE_DIALOG_ACTIONS + MISSION_DIALOG_ACTIONS
       + CREATION_DIALOG_ACTIONS + BAN_DIALOG_ACTIONS + WATCH_DIALOG_ACTIONS
       + PENALTY_DIALOG_ACTIONS]
    + list(DECLARED_READS)
)

# the write surfaces under test, by their declarations — never a hand-typed path
SERVER_DIRECT = ("/actions/server/startup", "/actions/server/start", "/actions/server/pause",
                 "/actions/server/unpause", "/actions/server/maintenance",
                 "/actions/server/end-maintenance")
SERVER_CONFIRM_REQUIRED = ("/actions/server/restart", "/actions/server/shutdown",
                           "/actions/server/stop")

PLAYER_DIRECT = ("/actions/player/mute", "/actions/player/unmute", "/actions/player/screenshot")
PLAYER_CONFIRM_REQUIRED = ("/actions/player/kick", "/actions/player/ban",
                           "/actions/player/chat", "/actions/player/popup")

NODE_PATHS = ("/actions/node/restart", "/actions/node/shutdown", "/actions/node/upgrade",
              "/actions/node/offline", "/actions/node/online")


# ─────────────────────────────────────────────────────────────────────────────── test doubles

class RowMission:
    """The ``Mission`` double: pause/unpause record their call and flip the server's status."""

    def __init__(self, server=None):
        self.server = server
        self.calls: list[str] = []

    async def pause(self):
        self.calls.append("pause")
        if self.server is not None:
            self.server.status = Status.PAUSED

    async def unpause(self):
        self.calls.append("unpause")
        if self.server is not None:
            self.server.status = Status.RUNNING


class RowSettings(dict):
    """The slice of ``SettingsDict`` the config write reads: ``get`` plus an ``mtime`` stamp."""

    def __init__(self, values=None, *, mtime=0.0):
        super().__init__(values or {})
        self.mtime = mtime


class RowServer:
    """One server double: every power method the row strip calls, recording its calls.

    ``startup``/``start`` flip the status RUNNING and ``shutdown``/``stop`` STOPPED, so an assertion
    distinguishes "the action ran" from "the route only redirected".
    """

    def __init__(self, name, managed_by=None, *, status=Status.RUNNING, maintenance=False):
        self.name = name
        self.display_name = name
        self.status = status
        self.maintenance = maintenance
        self.node = types.SimpleNamespace(name="dcs-a-01")
        self.instance = types.SimpleNamespace(name="Host-A-Instance")
        self.port = 10309
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        self.settings = RowSettings()
        self.players = {}
        self.calls: list[str] = []
        self.current_mission = RowMission(self)

    def get_active_players(self):
        return list(self.players.values())

    async def startup(self, **kwargs):
        self.calls.append("startup")
        self.status = Status.RUNNING

    async def shutdown(self, **kwargs):
        self.calls.append("shutdown")
        self.status = Status.STOPPED

    async def start(self, **kwargs):
        self.calls.append("start")
        self.status = Status.RUNNING

    async def stop(self, **kwargs):
        self.calls.append("stop")
        self.status = Status.STOPPED


def row_fleet(*, a_status=Status.RUNNING, a_maintenance=False, untagged=False):
    """A (Host A) and B (Host B) — the scope's two sides — plus an optional UNTAGGED C.

    ``untagged`` adds a server that declares NO ``managed_by``: the allow-list makes it invisible to
    every manager (only a cluster role sees it), so a crafted POST naming it must be refused exactly
    like a name that does not exist.
    """
    a = RowServer("SRS-1", ["Host A"], status=a_status, maintenance=a_maintenance)
    b = RowServer("SRS-B", ["Host B"], status=a_status)
    servers = [a, b]
    if untagged:
        servers.append(RowServer("SRS-C", None, status=a_status))
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster(servers, nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


ALL_PLUGINS = ("mission", "scheduler", "admin")


# ─────────────────────────────────────────────── 1. the route table, asserted against the code

def test_the_route_table_is_exhaustive_and_carries_the_declared_rule():
    """THE table: every declared write path, its roles and its scope rule — from the code.

    A write route added without updating this table fails here, which is the whole point of asking
    for the table rather than assuming it.
    """
    declared = actions_page.capabilities()
    assert set(declared) == set(EXPECTED_DECLARED), (
        f"missing={sorted(set(EXPECTED_DECLARED) - set(declared))} "
        f"extra={sorted(set(declared) - set(EXPECTED_DECLARED))}")
    for path, capability, roles, scope_grants in EXPECTED_ROUTES:
        assert declared[path] == capability, path
        assert permissions.roles_for_capability(capability) == roles, path
        assert permissions.scope_grants(capability) is scope_grants, path


def test_the_scope_grant_is_exactly_the_server_player_and_dcs_config_surfaces():
    """A manager's way in (``scope_grants``) covers the server row, the player row — and the
    DCS config write. It does NOT cover the node row or the channels write.

    This is the invariant behind "a node is not a server": no node capability and no channels
    capability may ever gain a scope grant without failing here.
    """
    granted = {path for path, capability, _roles, _grant in EXPECTED_ROUTES
               if permissions.scope_grants(capability)}
    granted |= {path for path in DECLARED_READS
                if permissions.scope_grants(actions_page.capabilities()[path])}
    assert granted == set(declared_scope_granted())
    assert granted == {"/actions/server/startup", "/actions/server/start", "/actions/server/pause",
                       "/actions/server/unpause", "/actions/server/restart",
                       "/actions/server/shutdown", "/actions/server/stop",
                       "/actions/server/maintenance", "/actions/server/end-maintenance",
                       "/actions/server/chat", "/actions/server/popup",
                       "/actions/player/kick", "/actions/player/ban",
                       "/actions/player/chat", "/actions/player/popup",
                       "/actions/player/mute", "/actions/player/unmute",
                       "/actions/player/screenshot",
                       "/actions/ban/lift",
                       "/actions/ban/add",
                       "/actions/ban/edit",
                       "/actions/watch/add",
                       "/actions/watch/remove",
                       "/actions/watch/edit",
                       # the penalty-point paths are scope-granted so a managing caller reaches the
                       # surface; the write itself is fleet-wide and crosses scopes by design
                       "/actions/penalty/add", "/actions/penalty/remove",
                       "/actions/penalty/forgive",
                       "/players/screenshot/{token}.png",
                       "/actions/ban/players",
                       "/actions/server/config", "/actions/server/coalitions",
                       "/actions/server/missions/add", "/actions/server/missions/delete",
                       "/actions/server/missions/load", "/actions/server/missions/upload",
                       "/actions/server/missions/delete_bulk", "/actions/server/missions/reorder",
                       "/actions/server/missions/activate",
                       # the Music tab's radio writes: one server's radios, one server's manager
                       "/actions/server/music/control", "/actions/server/music/playlist",
                       "/actions/server/music/settings"}
    for path in NODE_PATHS + ("/actions/server/channels",):
        assert permissions.scope_grants(actions_page.capabilities()[path]) is False, path


def declared_scope_granted() -> list[str]:
    """The declared paths whose capability carries a scope grant, read from the live table."""
    caps = actions_page.capabilities()
    return sorted(path for path, cap in caps.items() if permissions.scope_grants(cap)
                  and not path.endswith(CONFIRM))


def test_every_declared_write_path_is_a_registered_post_with_its_capability(config_dir):
    """Each declared path is a POST route on the REAL app, mapped to the capability it declared."""
    app = build(config_dir, row_fleet(), plugins=ALL_PLUGINS)
    registrar = app.state.webui_registrar
    by_path: dict[str, list] = {}
    for route in registrar.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            by_path.setdefault(path, []).append(route)
    declared = actions_page.capabilities()
    for path in EXPECTED_DECLARED - DECLARED_READS:
        routes = by_path.get(path)
        assert routes, f"{path} is declared but not registered"
        assert all(set(getattr(r, "methods", ()) or ()) == {"POST"} for r in routes), path
        assert registrar.capability_for(path=path) == declared[path], path
    # the one declared READ: registered, a GET, and carrying the capability it declares
    for path in DECLARED_READS:
        routes = by_path.get(path)
        assert routes, f"{path} is declared but not registered"
        assert all(set(getattr(r, "methods", ()) or ()) == {"GET"} for r in routes), path
        assert registrar.capability_for(path=path) == declared[path], path


# ─────────────────────────────────────────── 2. the SERVER routes: out-of-scope is refused

@pytest.mark.asyncio
@pytest.mark.parametrize("path", SERVER_DIRECT)
async def test_a_manager_is_refused_a_server_write_outside_their_scope(config_dir, path,
                                                                       recording_bot):
    """A crafted POST with a valid CSRF token and the capability they hold → the seam's 403.

    The refusal is INDISTINGUISHABLE from a name that does not exist (no enumeration), nothing ran,
    and it is never a 500 and never a silent success.
    """
    cluster = row_fleet()
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster-b")                 # manages Host B only
        token = token_of(await open_page(http))
        foreign = await http.post(path, data={"server": "SRS-1", "_csrf_token": token,
                                              "maintenance": "0"})
        missing = await http.post(path, data={"server": "NOPE-9", "_csrf_token": token,
                                              "maintenance": "0"})
    assert foreign.status_code == 403, (path, foreign.status_code)
    assert missing.status_code == 403
    assert foreign.text == missing.text, path
    assert "SRS-1" not in foreign.text, "the refusal must not disclose the target"
    assert "Internal Server Error" not in foreign.text and "500" not in foreign.text
    assert a.calls == [] and a.current_mission.calls == [], "nothing may run for a refused caller"
    assert a.maintenance is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SERVER_DIALOG_ACTIONS)
async def test_a_manager_is_refused_a_server_dialog_outside_their_scope(config_dir, path,
                                                                        recording_bot):
    """The DIALOG route carries the SAME target-scope check as its action — resolved first.

    The dialog is where the scope is decided for a confirm-required control: a POST that skips it is
    refused on the token, so this is the route a manager actually meets when they aim at a foreign
    server.
    """
    cluster = row_fleet()
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        token = token_of(await open_page(http))
        foreign = await http.post(path + CONFIRM, data={"server": "SRS-1", "_csrf_token": token})
        missing = await http.post(path + CONFIRM, data={"server": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403, (path, foreign.status_code)
    assert foreign.text == missing.text
    assert "SRS-1" not in foreign.text
    assert a.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SERVER_CONFIRM_REQUIRED)
async def test_a_crafted_post_to_a_confirm_route_without_its_dialog_is_refused(config_dir, path,
                                                                              recording_bot):
    """A confirmation curl can bypass is decoration: the action's own route refuses everything else."""
    cluster = row_fleet()
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": token})
    assert reply.status_code == 403, path
    assert "confirmed through its dialog" in reply.json()["detail"], path
    assert a.calls == []


# ─────────────────────────────────────────── 3. the SERVER routes: in-scope still works

#: ``key`` -> the state/flag the route is submitted from, whether it confirms, and the observable
#: effect the action must produce. Every route of the row strip, one entry each.
SERVER_CASES = (
    ("startup", Status.SHUTDOWN, False, False),
    ("start", Status.STOPPED, False, False),
    ("pause", Status.RUNNING, False, False),
    ("unpause", Status.PAUSED, False, False),
    ("restart", Status.RUNNING, False, True),
    ("shutdown", Status.RUNNING, False, True),
    ("stop", Status.RUNNING, False, True),
    ("maintenance", Status.RUNNING, False, False),
    ("end-maintenance", Status.RUNNING, True, False),
)

#: what each server action must have DONE to its own server when the in-scope manager pressed it
SERVER_EFFECT = {
    "startup": {"calls": ("startup",), "mission": (), "status": Status.RUNNING},
    "start": {"calls": ("start",), "mission": (), "status": Status.RUNNING},
    "pause": {"calls": (), "mission": ("pause",), "status": Status.PAUSED},
    "unpause": {"calls": (), "mission": ("unpause",), "status": Status.RUNNING},
    "restart": {"calls": ("stop", "start"), "mission": (), "status": Status.RUNNING},
    "shutdown": {"calls": ("shutdown",), "mission": (), "status": Status.STOPPED},
    "stop": {"calls": ("stop",), "mission": (), "status": Status.STOPPED},
    "maintenance": {"calls": (), "mission": (), "flag": True},
    "end-maintenance": {"calls": (), "mission": (), "flag": False},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("route_key, status, maintenance, confirm",
                         SERVER_CASES, ids=[case[0] for case in SERVER_CASES])
async def test_a_manager_acts_on_their_own_server_through_every_write_route(
        config_dir, route_key, status, maintenance, confirm, recording_bot):
    """The other half of the pair: the SAME manager on a server INSIDE their scope still works.

    ``hoster`` holds NO console role at all (``DCS`` only) and reaches every server write through the
    SCOPE alone, so a route that refuses everything would fail here.
    """
    path = f"/actions/server/{route_key}"
    cluster = row_fleet(a_status=status, a_maintenance=maintenance)
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")                   # manages Host A
        token = token_of(await open_page(http))
        if confirm:
            dialog = await http.post(path + CONFIRM, data={"server": "SRS-1",
                                                           "_csrf_token": token})
            assert dialog.status_code == 200, (path, dialog.status_code)
            match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
            assert match, f"{path}: the dialog carries no confirm token"
            reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": token,
                                                "_confirm_token": match.group(1)})
        else:
            reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": token})
        assert reply.status_code == 303, (path, reply.status_code, reply.text[:200])

    effect = {"calls": tuple(a.calls), "mission": tuple(a.current_mission.calls),
              "status": a.status, "flag": a.maintenance}
    expected = SERVER_EFFECT[route_key]
    for field, value in expected.items():
        assert effect[field] == value, (route_key, field, effect[field])
 #...and the write left ONE audit entry naming the WEB actor
    assert len(recording_bot.calls) == 1, (route_key, recording_bot.calls)
    assert recording_bot.calls[0][1].get("actor") == "[web:local/hoster]", route_key


# ─────────────────────────────────────────── 4. the PLAYER routes: out-of-scope is refused

@pytest.mark.asyncio
@pytest.mark.parametrize("path", PLAYER_DIRECT)
async def test_a_manager_is_refused_a_player_write_outside_their_scope(config_dir, path,
                                                                       recording_bot):
    """A crafted POST naming another hoster's server (and player) is refused, nothing runs."""
    cluster = player_fleet()
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    data = {"server": "SRS-1", "ucid": UCID_A}
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        token = token_of(await open_page(http))
        foreign = await http.post(path, data=dict(data, _csrf_token=token))
        missing = await http.post(path, data=dict(data, server="NOPE-9", _csrf_token=token))
    assert foreign.status_code == 403, (path, foreign.status_code)
    assert foreign.text == missing.text
    assert "SRS-1" not in foreign.text
    player = next(iter(a.players.values()))
    assert player.calls == [] and player.messages == [], "nothing may run for a refused caller"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PLAYER_CONFIRM_REQUIRED)
async def test_a_manager_is_refused_a_player_dialog_outside_their_scope(config_dir, path,
                                                                       recording_bot):
    """The player DIALOG carries the same target-scope check, resolved before the token is minted."""
    cluster = player_fleet()
    a = cluster.servers[0]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    data = {"server": "SRS-1", "ucid": UCID_A, "reason": "teamkill"}
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        token = token_of(await open_page(http))
        foreign = await http.post(path + CONFIRM, data=dict(data, _csrf_token=token))
        missing = await http.post(path + CONFIRM, data=dict(data, server="NOPE-9",
                                                            _csrf_token=token))
    assert foreign.status_code == 403, (path, foreign.status_code)
    assert foreign.text == missing.text
    assert "SRS-1" not in foreign.text
    assert next(iter(a.players.values())).calls == []


# ─────────────────────────────────────────── 5. the PLAYER routes: in-scope still works

@pytest.mark.asyncio
async def test_a_manager_mutes_and_unmutes_a_player_on_their_own_server(config_dir, recording_bot):
    """mute / unmute — the two DIRECT player writes, on a server in the scope."""
    cluster = player_fleet(a_muted=False)
    player = next(iter(cluster.servers[0].players.values()))
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")
        token = token_of(await open_page(http))
        muted = await http.post("/actions/player/mute",
                                data={"server": "SRS-1", "ucid": UCID_A, "_csrf_token": token})
        unmuted = await http.post("/actions/player/unmute",
                                  data={"server": "SRS-1", "ucid": UCID_A, "_csrf_token": token})
    assert [muted.status_code, unmuted.status_code] == [303, 303]
    assert player.calls == ["mute", "unmute"], player.calls
    assert player.muted is False


@pytest.mark.asyncio
async def test_a_manager_sends_both_message_modes_to_a_player_on_their_own_server(
        config_dir, recording_bot):
    """chat / popup — the two message controls, each through its own dialog, on a server in scope."""
    cluster = player_fleet(a_muted=False)
    player = next(iter(cluster.servers[0].players.values()))
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")
        token = token_of(await open_page(http))
        sent = []
        for path, message in (("/actions/player/popup", "hello"),
                              ("/actions/player/chat", "hi")):
            dialog = await http.post(path + CONFIRM,
                                     data={"server": "SRS-1", "ucid": UCID_A,
                                           "_csrf_token": token})
            assert dialog.status_code == 200, (path, dialog.text)
            token_match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
            assert token_match, (path, "no confirm token")
            sent.append(await http.post(path, data={"server": "SRS-1", "ucid": UCID_A,
                                                    "message": message, "_csrf_token": token,
                                                    "_confirm_token": token_match.group(1)}))
    assert [reply.status_code for reply in sent] == [303, 303]
    assert player.messages[0][0] == "popup" and player.messages[0][1] == "hello"
    assert player.messages[1][0] == "chat" and player.messages[1][1] == "hi"


@pytest.mark.asyncio
async def test_a_manager_kicks_and_bans_a_player_on_their_own_server(config_dir, ban_service,
                                                                    recording_bot):
    """kick / ban — the two confirm-required player writes, through their dialogs."""
    cluster = player_fleet()
    server = cluster.servers[0]
    player = next(iter(server.players.values()))
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")
        token = token_of(await open_page(http))
        for path, extra in (("/actions/player/kick", {}),
                            ("/actions/player/ban", {"days": ""})):
            dialog = await http.post(path + CONFIRM,
                                     data={"server": "SRS-1", "ucid": UCID_A,
                                           "_csrf_token": token})
            assert dialog.status_code == 200, (path, dialog.status_code)
            match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
            assert match, path
            reply = await http.post(path, data={"server": "SRS-1", "ucid": UCID_A,
                                                "reason": "teamkill", "_csrf_token": token,
                                                "_confirm_token": match.group(1), **extra})
            assert reply.status_code == 303, (path, reply.status_code, reply.text[:200])
    assert server.kicks == [(UCID_A, "teamkill")], server.kicks
    assert ban_service.calls == [(UCID_A, "hoster", "teamkill", None, "SRS-1")], ban_service.calls
    assert player.calls == []


# ───────────────────────────────── 6. NODE routes stay Admin-only (a node is not a server)

@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["mod1", "hoster", "hoster-b", "plain"])
@pytest.mark.parametrize("path", NODE_PATHS + tuple(p + CONFIRM for p in NODE_PATHS))
async def test_a_node_write_is_refused_for_every_non_admin(config_dir, who, path, recording_bot):
    """A hoster — and even a DCS Admin — never reaches a node route: the gate refuses first.

    No node capability carries a scope grant, so "one of mine runs there" never becomes "let me stop
    that machine", and neither the node's action nor its dialog may run.
    """
    cluster = node_fleet()
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, who)
        reply = await http.post(path, data={"node": "dcs-a-01", "_csrf_token": "t" * 43})
    assert reply.status_code == 403, (who, path, reply.status_code)
    assert "Not authorized" in reply.json()["detail"], (who, path)
    assert cluster.nodes["dcs-a-01"].calls == [], (who, path)
    assert recording_bot.calls == [], (who, path)


@pytest.mark.asyncio
async def test_an_admin_reaches_a_node_route_through_its_dialog(config_dir, recording_bot):
    """The in-scope half for a node: an Admin IS offered the control and the dialog renders."""
    cluster = node_fleet()
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http))
        dialog = await http.post("/actions/node/restart" + CONFIRM,
                                 data={"node": "dcs-a-01", "_csrf_token": token})
        direct = await http.post("/actions/node/restart",
                                 data={"node": "dcs-a-01", "_csrf_token": token})
    assert dialog.status_code == 200, dialog.status_code
    assert "Restart node" in dialog.text
 # a POST that skipped the dialog is refused by the CONFIRM step (past the gate)
    assert direct.status_code == 403
    assert "confirmed through its dialog" in direct.json()["detail"]
    assert cluster.nodes["dcs-a-01"].calls == []


# ─────────── 7. the config + channels writes: Admin, and (config only) a manager's scope grant

@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/actions/server/config", "/actions/server/channels"])
async def test_the_config_writes_are_refused_for_a_plain_caller(config_dir, path):
    """A caller with no role and no scope (neither Admin nor manager) is refused at the gate on BOTH.

    ``plain`` holds no cluster role and declares no scope, so ``allows`` is False for the config write's
    scope grant and the channels write's Admin-only capability alike.
    """
    app = build(config_dir, row_fleet(), plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "plain")
        reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": "t" * 43})
    assert reply.status_code == 403, (path, reply.status_code)
    assert "Not authorized" in reply.json()["detail"], path


@pytest.mark.asyncio
async def test_a_dcs_admin_with_a_declared_scope_is_refused_the_config_by_the_action(config_dir):
    """``mod1`` holds ``DCS Admin`` AND declares a scope: the allow-list makes it a manager, so the
    GATE admits it.

    The ACTION then refuses it (a DCS Admin is not a "manager ONLY", so the deny-list does not apply and
    the config write's Admin requirement does) — the route answers 303 with the refusal notice and
    nothing is written. The channels write carries NO scope grant, so it is refused at the gate (403).
    """
    app = build(config_dir, row_fleet(), plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        config = await http.post("/actions/server/config",
                                 data={"server": "SRS-1", "_csrf_token": token})
        landed = await http.get("/fleet/SRS-1?tab=configuration")
        channels = await http.post("/actions/server/channels",
                                   data={"server": "SRS-1", "_csrf_token": token})
    assert config.status_code == 303, (config.status_code, config.text[:200])
    assert "Admin" in landed.text, "the action's Admin-only refusal must be visible"
    assert channels.status_code == 403, channels.status_code


@pytest.mark.asyncio
async def test_the_channels_write_is_refused_for_a_manager(config_dir):
    """The channels write has NO scope grant — a manager's POST is refused at the gate (403)."""
    app = build(config_dir, row_fleet(), plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")                 # manages Host A / SRS-1
        token = token_of(await open_page(http))
        reply = await http.post("/actions/server/channels",
                                data={"server": "SRS-1", "_csrf_token": token})
    assert reply.status_code == 403, reply.status_code
    assert "Not authorized" in reply.json()["detail"]


@pytest.mark.asyncio
async def test_the_config_write_is_reachable_for_a_manager(config_dir):
    """The DCS config write DOES carry the scope grant — a manager's POST is not a 403 at the gate.

    The route is reached (the action then refuses any denied key with its own typed sentence — that is
    proved in ``test_scheduler_server_config`` and ``test_webui_server_config``); here the claim is only
    that a manager is not turned away by the gate on the config write.
    """
    app = build(config_dir, row_fleet(), plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")                 # manages Host A / SRS-1
        token = token_of(await open_page(http))
        reply = await http.post("/actions/server/config",
                                data={"server": "SRS-1", "_csrf_token": token})
    assert reply.status_code == 303, (reply.status_code, reply.text[:200])


@pytest.mark.asyncio
async def test_an_admin_reaches_the_config_and_channels_writes(config_dir, recording_bot):
    """The in-scope half: an Admin's POST passes the gate and the route resolves its target.

    The write itself (values landing in ``serverSettings.lua`` / ``servers.yaml``) is pinned end to
    end by ``tests/test_webui_server_config.py``; here the claim is only that the route is REACHABLE
    for the one role it declares and never becomes a 403 or a 500 for an authorized Admin.
    """
    cluster = row_fleet(a_status=Status.STOPPED)
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await open_page(http))
        for path in ("/actions/server/config", "/actions/server/channels"):
            reply = await http.post(path, data={"server": "SRS-1", "_csrf_token": token})
            assert reply.status_code == 303, (path, reply.status_code)
            assert reply.status_code not in (403, 500), path


# ─────────────────── 8. no route becomes a way around the allow-list (untagged = refused)

@pytest.mark.asyncio
async def test_a_server_that_declares_no_managed_by_is_refused_like_a_nonexistent_one(config_dir,
                                                                                     recording_bot):
    """An untagged server is invisible to a manager — and a crafted POST must be REFUSED too.

    The route answers the SAME 403 for the untagged server (a real name, invisible to this viewer) as
    for a name nothing answers to.
    """
    cluster = row_fleet(untagged=True)
    c = cluster.servers[2]
    assert c.name == "SRS-C" and not c.locals.get("managed_by")
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "hoster")                    # manages Host A; SRS-C is nobody's
        page = await open_page(http)
        assert "SRS-C" not in page, "an untagged server must not be in a manager's view"
        token = token_of(page)
        untagged = await http.post("/actions/server/pause",
                                   data={"server": "SRS-C", "_csrf_token": token})
        missing = await http.post("/actions/server/pause",
                                  data={"server": "NOPE-9", "_csrf_token": token})
    assert untagged.status_code == 403 and missing.status_code == 403
    assert untagged.text == missing.text
    assert "SRS-C" not in untagged.text
    assert c.calls == [] and c.current_mission.calls == []


@pytest.mark.asyncio
async def test_the_unscoped_admin_may_act_on_the_untagged_server(config_dir, recording_bot):
    """The other half: an ``Admin`` (unscoped) still sees and acts on the untagged server.

    A route that refused the untagged target for EVERY caller would be a fix that broke the cluster
    role's own view.
    """
    cluster = row_fleet(untagged=True)
    c = cluster.servers[2]
    app = build(config_dir, cluster, plugins=ALL_PLUGINS)
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http)
        assert "SRS-C" in page, "an Admin sees every server, tagged or not"
        reply = await http.post("/actions/server/pause",
                                data={"server": "SRS-C", "_csrf_token": token_of(page)})
    assert reply.status_code == 303
    assert c.current_mission.calls == ["pause"]
