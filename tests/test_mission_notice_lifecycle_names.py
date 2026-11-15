"""The server-lifecycle and mission notices: the names in them are the PLAIN ones.

Every domain object carries its name twice: ``name`` is the plain string and ``display_name`` is that
same string escaped for Discord's markdown (``core/data/server.py`` / ``core/data/mission.py``:
``utils.escape_string``), so a server called ``[SRS-1]_Test*Server`` reads back as
``\\[SRS\\-1\\]\\_Test\\*Server``. The console renders its notice into a browser and never into Discord
(it shows the action's own message verbatim, ``templates/_notice.html``), so a sentence built from
``display_name`` shows the escapes to the person reading the banner.

The doubles here carry the REAL domain property — ``Server.display_name`` / ``Mission.display_name``
applied to the double by :meth:`LifecycleServer.__init__` — because a double whose ``display_name``
equals its ``name`` cannot fail any assertion of this file, which is how the defect survived.

Sections: 1. the process/DCS-level server sentences, 2. the mission-control sentences, 3. the mission
label, 4. the mission-file sentences that name a server, 5. the label readers, 6. the sweep (a
structural ratchet over every ``display_name`` read in the module), 7. the console notice through the
real app, 8. the Discord path, which must stay escaped.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

from core import Status
from core.data.mission import Mission
from core.data.server import Server
from core.utils.discord import escape_string

# the sibling suites' own harness, reused rather than copied
from test_mission_server_control import ctx_for  # noqa: F401  (the helper is used by name)
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    Cluster, build, clean_registry, client, config_dir, fleet, recording_bot, sign_in, token_of,
)
from test_webui_write_notice_names import notice_of  # noqa: F401  (the helper is used by name)

from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page

#: the repository root — a source pin must not depend on the shell's working directory
REPO = Path(__file__).resolve().parents[1]

ACTIONS_PY = REPO / "plugins/mission/actions.py"

#: a server name and a mission name carrying every character ``escape_string`` mangles
SERVER_NAME = "[SRS-1]_Test*Server"
MISSION_NAME = "[TAG]_Test*Mission"
MISSION_FILE = "[TAG]_Test*Mission.miz"


# ------------------------------------------------------------------------------------ test doubles

class LifecycleMission:
    """A ``Mission`` double whose ``display_name`` is the DOMAIN's own escaped value."""

    def __init__(self, name=MISSION_NAME, filename=MISSION_FILE):
        self.name = name
        self.display_name = Mission.display_name.fget(self)
        self.filename = filename
        self.map = "Caucasus"
        self.start_time = 0
        self.mission_time = 0
        self.num_slots_blue = 8
        self.num_slots_red = 6
        self.calls: list[str] = []

    async def pause(self):
        self.calls.append("pause")

    async def unpause(self):
        self.calls.append("unpause")


class LifecycleServer:
    """A ``Server`` double whose ``display_name`` is ``Server.display_name`` applied to it.

    The escaped value is produced by the domain's own property function, so the sentence under test
    reads a name an install would really carry — not the double's ``name`` twice.
    """

    def __init__(self, name=SERVER_NAME, status=Status.RUNNING, mission=None, maintenance=False,
                 missions=()):
        self.name = name
        self.display_name = Server.display_name.fget(self)
        self.status = status
        self.current_mission = mission
        self._maintenance = maintenance
        self.locals: dict = {}
        self.missions = list(missions)
        self.calls: list[tuple] = []

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, value):
        self._maintenance = bool(value)

    async def start(self, **kwargs):
        self.calls.append(("start",))
        self.status = Status.RUNNING

    async def stop(self, **kwargs):
        self.calls.append(("stop",))
        self.status = Status.STOPPED

    async def startup(self, **kwargs):
        self.calls.append(("startup", kwargs))
        self.status = Status.RUNNING

    async def shutdown(self, force=False, **kwargs):
        self.calls.append(("shutdown", force))
        self.status = Status.SHUTDOWN

    async def restart(self, **kwargs):
        self.calls.append(("restart",))
        self.status = Status.RUNNING

    async def loadNextMission(self, **kwargs):
        self.calls.append(("loadNextMission",))
        return True

    async def getMissionList(self):
        return list(self.missions)


async def run(name, server, **params):
    """Invoke one action by its function NAME, resolving *server* by name as a transport would."""
    import plugins.mission.actions as actions

    return await getattr(actions, name)(ctx_for(server), server_name=server.name, **params)


def assert_plain(message: str, expected: str):
    """The sentence is exactly *expected*, and carries no backslash-escaping anywhere."""
    assert "\\" not in message, f"the notice carries an escaped name: {message!r}"
    assert message == expected, message


# ------------------------------------------- 1. the process/DCS-level server sentences

@pytest.mark.asyncio
async def test_start_and_stop_sentences_are_plain():
    """``start_server`` / ``stop_server``: the success and the state refusal."""
    stopped = LifecycleServer(status=Status.STOPPED)
    assert_plain((await run("start_server", stopped)).message, f"Server '{SERVER_NAME}' started.")

    running = LifecycleServer(status=Status.RUNNING)
    assert_plain((await run("start_server", running)).message,
                 f"Server '{SERVER_NAME}' is Running, not stopped.")
    assert_plain((await run("stop_server", running)).message, f"Server '{SERVER_NAME}' stopped.")

    still_stopped = LifecycleServer(status=Status.STOPPED)
    assert_plain((await run("stop_server", still_stopped)).message,
                 f"Server '{SERVER_NAME}' is Stopped, not running or paused.")


@pytest.mark.asyncio
async def test_restart_sentence_is_plain():
    """``restart_server``: the success (stop + start) and the state refusal."""
    server = LifecycleServer(status=Status.RUNNING)
    assert_plain((await run("restart_server", server)).message,
                 f"Server '{SERVER_NAME}' restarted.")
    assert server.calls == [("stop",), ("start",)]

    shutdown = LifecycleServer(status=Status.SHUTDOWN)
    assert_plain((await run("restart_server", shutdown)).message,
                 f"Server '{SERVER_NAME}' is Shutdown - use startup_server.")


@pytest.mark.asyncio
async def test_startup_and_shutdown_sentences_are_plain():
    """``startup_server`` / ``shutdown_server``: the successes and both state refusals."""
    down = LifecycleServer(status=Status.SHUTDOWN, maintenance=True)
    assert_plain((await run("startup_server", down)).message,
                 f"Server '{SERVER_NAME}' started up.")
    assert down.maintenance is False, "the maintenance flag is still cleared by default"

    running = LifecycleServer(status=Status.RUNNING)
    assert_plain((await run("startup_server", running)).message,
                 f"Server '{SERVER_NAME}' is Running, not shut down.")

    assert_plain((await run("shutdown_server", running)).message,
                 f"Server '{SERVER_NAME}' shut down.")

    down_again = LifecycleServer(status=Status.SHUTDOWN)
    assert_plain((await run("shutdown_server", down_again)).message,
                 f"Server '{SERVER_NAME}' is Shutdown, not running or paused.")


# ------------------------------------------------------- 2. the mission-control sentences

@pytest.mark.asyncio
async def test_pause_and_unpause_sentences_are_plain():
    """``pause_mission`` / ``unpause_mission``: success, the state refusal and the no-mission one."""
    mission = LifecycleMission()
    running = LifecycleServer(status=Status.RUNNING, mission=mission)
    assert_plain((await run("pause_mission", running)).message,
                 f"Mission on server '{SERVER_NAME}' paused.")
    assert mission.calls == ["pause"]

    paused = LifecycleServer(status=Status.PAUSED, mission=mission)
    assert_plain((await run("unpause_mission", paused)).message,
                 f"Mission on server '{SERVER_NAME}' unpaused.")
    assert mission.calls == ["pause", "unpause"]

    assert_plain((await run("pause_mission", paused)).message,
                 f"Server '{SERVER_NAME}' is Paused, not running.")
    assert_plain((await run("unpause_mission", LifecycleServer(status=Status.PAUSED))).message,
                 f"No active mission on server '{SERVER_NAME}'.")


@pytest.mark.asyncio
async def test_restart_and_rotate_mission_sentences_are_plain():
    """``restart_mission`` / ``rotate_mission``: the successes and the state refusal."""
    mission = LifecycleMission()
    server = LifecycleServer(status=Status.RUNNING, mission=mission)
    assert_plain((await run("restart_mission", server)).message,
                 f"Mission on server '{SERVER_NAME}' restarted.")
    assert_plain((await run("rotate_mission", server)).message,
                 f"Mission rotated on server '{SERVER_NAME}'.")

    shutdown = LifecycleServer(status=Status.SHUTDOWN, mission=mission)
    assert_plain((await run("restart_mission", shutdown)).message,
                 f"Server '{SERVER_NAME}' is Shutdown.")
    assert_plain((await run("rotate_mission", shutdown)).message,
                 f"Server '{SERVER_NAME}' is Shutdown.")


@pytest.mark.asyncio
async def test_load_mission_refusal_sentence_is_plain():
    """``load_mission``: the state refusal that names the server."""
    shutdown = LifecycleServer(status=Status.SHUTDOWN)
    assert_plain((await run("load_mission", shutdown, mission=1)).message,
                 f"Server '{SERVER_NAME}' is Shutdown.")


# ------------------------------------------------------------------- 3. the mission label

@pytest.mark.asyncio
async def test_the_active_mission_is_named_plainly():
    """``get_mission_info``: both the sentence and the machine-readable ``name`` carry the plain name."""
    mission = LifecycleMission()
    server = LifecycleServer(status=Status.RUNNING, mission=mission)
    result = await run("get_mission_info", server)

    assert_plain(result.message, f"Active mission: {MISSION_NAME}")
    assert result.data["name"] == MISSION_NAME, "the read model carries the escaped name"
    assert result.data["filename"] == MISSION_FILE


@pytest.mark.asyncio
async def test_the_no_mission_refusal_is_plain():
    """The other exit of the same action names the server."""
    server = LifecycleServer(status=Status.RUNNING)
    assert_plain((await run("get_mission_info", server)).message,
                 f"No active mission on server '{SERVER_NAME}'.")


# ------------------------- 4. the mission-file sentences that name a server

@pytest.mark.asyncio
async def test_the_mission_file_sentences_name_the_server_plainly():
    """The ``display`` local of the mission-file actions is the same label — two of them pinned."""
    server = LifecycleServer(status=Status.RUNNING, missions=["A.miz"])
    assert_plain((await run("set_active_mission", server, mission="NOPE")).message,
                 f"Mission 'NOPE' not found on server '{SERVER_NAME}'.")
    assert_plain((await run("delete_mission", server, mission="NOPE")).message,
                 f"Mission 'NOPE' not found on server '{SERVER_NAME}'.")


# ------------------------------------------------------------------- 5. the label readers

def test_the_server_label_prefers_the_plain_name():
    """``_server_label``: ``name`` first, a display name only for a double that has nothing else."""
    from plugins.mission.actions import _server_label

    both = types.SimpleNamespace(name=SERVER_NAME, display_name=escape_string(SERVER_NAME))
    assert _server_label(both, "fallback") == SERVER_NAME
    only_display = types.SimpleNamespace(display_name=escape_string(SERVER_NAME))
    assert _server_label(only_display, "fallback") == escape_string(SERVER_NAME)
    assert _server_label(types.SimpleNamespace(name=""), "fallback") == "fallback"


def test_the_mission_label_prefers_the_plain_name():
    """``_mission_label``: the twin of ``_server_label``, with the filename as the last resort."""
    from plugins.mission.actions import _mission_label

    both = types.SimpleNamespace(name=MISSION_NAME, display_name=escape_string(MISSION_NAME))
    assert _mission_label(both, MISSION_FILE) == MISSION_NAME
    only_display = types.SimpleNamespace(display_name=escape_string(MISSION_NAME))
    assert _mission_label(only_display, MISSION_FILE) == escape_string(MISSION_NAME)
    assert _mission_label(types.SimpleNamespace(name=""), MISSION_FILE) == MISSION_FILE


# --------------------------------------------------------------------------- 6. the sweep

def escaped_name_reads(source: str) -> set[str]:
    """The names of the functions in *source* that read an escaped name.

    Catches BOTH spellings ``escape_string``'s consumers use: an attribute read
    (``server.display_name``) and the tolerant ``getattr(obj, "display_name", ...)``, whose second
    argument is a string constant. A module-level read reports as ``"<module>"``.
    """
    tree = ast.parse(source)
    found: set[str] = set()

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        elif isinstance(node, ast.Attribute) and node.attr == "display_name":
            found.add(owner)
        elif isinstance(node, ast.Constant) and node.value == "display_name":
            found.add(owner)
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "<module>")
    return found


def test_every_escaped_name_read_lives_in_a_label_reader():
    """RATCHET — the module reads an escaped name in the label helpers and nowhere else.

    Every sentence the console renders is built from :func:`_player_label` / :func:`_server_label` /
    :func:`_mission_label`, so a new ``.display_name`` in a sentence fails here instead of reaching
    the browser. The Discord commands keep their own escaped wording (section 8).
    """
    reads = escaped_name_reads(ACTIONS_PY.read_text(encoding="utf-8"))
    # ``_member_names`` reads a DISCORD member's shown name, not a domain object's ESCAPED name:
    # the two are different values that happen to share the attribute name.
    assert reads <= {"_player_label", "_server_label", "_mission_label", "_member_names"}, \
        f"an escaped name is read outside the label helpers: {sorted(reads)}"


def test_the_ratchet_detects_both_spellings():
    """Positive control — the detector reports the attribute and the ``getattr`` spelling."""
    attribute = 'async def f(server, server_name):\n    return f"{server.display_name}"\n'
    assert escaped_name_reads(attribute) == {"f"}

    tolerant = 'def g(server, fallback):\n    return getattr(server, "display_name", None) or fallback\n'
    assert escaped_name_reads(tolerant) == {"g"}

    clean = 'def h(server, fallback):\n    return getattr(server, "name", "") or fallback\n'
    assert escaped_name_reads(clean) == set()


# --------------------------------------------- 7. the console notice, through the real app

def faithful_fleet():
    """The pause suite's fleet with its first server carrying a markdown-heavy name.

    The NAME is changed on the double the source, the scope and the action all read (``fleet()``
    builds the server and its instance around the same object), and ``display_name`` is then the
    DOMAIN's own escaped value for that name — so the console sees one server under the plain name,
    exactly as an install named like this would.
    """
    cluster = fleet()
    server = cluster.servers[0]
    server.name = SERVER_NAME
    server.display_name = Server.display_name.fget(server)
    return cluster


@pytest.mark.asyncio
async def test_the_console_notice_of_a_lifecycle_write_is_plain(config_dir, clean_registry,
                                                               recording_bot):
    """ACCEPTANCE — the banner a lifecycle write leaves behind carries no escape.

    Driven through the real app with a faithful double, and the AUDIT line is asserted beside it:
    the route writes that entry from the action's own message (``core/actions.py``), so it inherits
    the plain name.
    """
    cluster = faithful_fleet()
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = (await http.get(servers_page.SERVERS_PATH)).text
        reply = await http.post(actions_page.PAUSE_PATH,
                                data={"server": SERVER_NAME, "_csrf_token": token_of(page)})
        assert reply.status_code == 303, reply.text
        after = (await http.get(servers_page.SERVERS_PATH)).text

    sentence = notice_of(after)
    assert_plain(sentence, f"Mission on server '{SERVER_NAME}' paused.")
    assert recording_bot.calls[0][0] == f"{sentence}", \
        "the audit line does not carry the same plain name"


# ------------------------------------- 8. the Discord path stays escaped (unchanged)

def test_the_domain_still_escapes_the_display_name_for_discord():
    """The escaping exists for Discord's markdown and is untouched: it is what the commands send."""
    assert Server.display_name.fget(types.SimpleNamespace(name=SERVER_NAME)) == \
        escape_string(SERVER_NAME)
    assert Mission.display_name.fget(types.SimpleNamespace(name=MISSION_NAME)) == \
        escape_string(MISSION_NAME)
    assert "\\" in escape_string(SERVER_NAME) and "\\" in escape_string(MISSION_NAME)


def test_the_discord_commands_are_unchanged_and_do_not_reach_the_action_layer():
    """The Discord commands compose their own wording with ``display_name`` and never call an action.

    A source pin over both untouched modules: their sentences must keep the escaped name, and neither
    may start consuming ``plugins/mission/actions.py`` (which would make the console's label the
    Discord one).
    """
    for path in ("plugins/mission/commands.py", "plugins/mission/listener.py"):
        source = (REPO / path).read_text(encoding="utf-8")
        assert ".display_name" in source, f"{path} no longer names anything with display_name"
        assert "from core.actions import" not in source and "call_action" not in source, \
            f"{path} started consuming the action layer"

    commands = (REPO / "plugins/mission/commands.py").read_text(encoding="utf-8")
    for qualname in ("start_server", "rotate_mission", "pause_mission", "_server_label",
                     "_mission_label"):
        assert qualname not in commands, f"a Discord command reads the console's {qualname}"
