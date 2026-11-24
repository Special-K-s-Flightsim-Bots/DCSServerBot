"""The launch dialog's mission-weather READ route: ``GET /fleet/<name>/weather/mission.json``.

One section per acceptance criterion:

* the route answers JSON — the mission's weather as the wizard's own DOTTED-KEY values (read through
  ``presets.preset_values``, never a second reader) plus the mission's FILE NAME for the dialog's line;
* the mission is resolved by the ACTION against the server's own list, so a request that names a PATH
  is refused and the node is never asked for it;
* every failure — an unreachable node, a missing file, permission denied, a timeout, a raised node, an
  unparseable ``.miz`` — answers a FAILURE with the console's own sentence, never a document of zeros;
* it is a READ: no CSRF, no audit entry, nothing written;
* it is gated by the server page's own ``servers.view``, like every other node-read route.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

import os

import types
from pathlib import Path

import pytest


from core import Status
from core.services.registry import ServiceRegistry
from services.servicebus import ServiceBus
from services.webservice.pages import presets as presets_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import servers as servers_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, recording_bot, sign_in,
)
# the fixture mission, its document and its archive builder, so the route reads the SAME mission the
# action test does
from test_mission_weather_read import EXPECTED_DOCUMENT, build_miz

NODE = "dcs-a-01"
INSTANCE = "Host-A-Instance"


# --------------------------------------------------------------------------------- test doubles

#: ONE host spelling of the fake mission tree. The action hands the CONFIGURED value through
#: untouched, so both the configured list and the expectations must be spelled the same way the bot
#: spells a path — ``"/missions"`` alone is not an absolute path on Windows.
MISSIONS = os.path.abspath("/missions")


def m(*parts: str) -> str:
    """*parts* joined below :data:`MISSIONS`, normalised the way the bot normalises a path."""
    return os.path.normpath(os.path.join(MISSIONS, *parts))


class WeatherNode:
    """A server's node: it hands back the bytes a read is asked for, or raises the installed error."""

    def __init__(self, name=NODE, *, content=b"", error=None, dcs_version="2.9.14"):
        self.name = name
        self.master = True
        self.dcs_version = dcs_version
        self.locals: dict = {}
        self.content = content
        self.error = error
        self.reads: list[str] = []

    async def read_file(self, path):
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        return self.content


class WeatherServer:
    """A ``Server`` double: the list, the missions dir and the current mission the action reads."""

    def __init__(self, name="SRS-1", *, node=None, configured=(), current=None):
        self.name = name
        self.display_name = name
        self.status = Status.RUNNING
        self.node = node if node is not None else WeatherNode()
        self.configured = list(configured)
        self.current = current
        self.locals: dict = {}
        self.players: dict = {}

    async def getMissionList(self):
        return list(self.configured)

    async def get_missions_dir(self):
        return MISSIONS

    async def get_current_mission_file(self):
        return self.current

    #: the two reads the mission LIST needs, so a dialog opened on this double renders the mission
    #: pick and the weather it qualifies (the resolver names each configured entry).
    async def getAllMissionFiles(self):
        return [(str(path).replace("\\", "/").rsplit("/", 1)[-1], str(path))
                for path in self.configured]

    async def getStartIndex(self):
        return 1


def instance_of(name, server_obj):
    return types.SimpleNamespace(name=name, dcs_port=10309, webgui_port=8088, server=server_obj)


def cluster_for(server, *, nodes=None):
    """A cluster whose only server is *server*, with *nodes* as the source's node mapping."""
    node_obj = types.SimpleNamespace(name=server.node.name, master=True,
                                     instances={INSTANCE: instance_of(INSTANCE, server)})
    mapping = {server.node.name: node_obj} if nodes is None else nodes
    return Cluster([server], nodes=mapping, node=node_obj,
                   instances=[instance_of(INSTANCE, server)])


def app_for(config_dir, server, *, nodes=None):
    return build(config_dir, cluster_for(server, nodes=nodes))


def miz_payload(tmp_path: Path, name: str = "Training.miz") -> bytes:
    return Path(build_miz(tmp_path / name)).read_bytes()


@pytest.fixture(autouse=True)
def miz_service(monkeypatch):
    """A ``ServiceBus`` whose ``node`` is a node double, so ``MizFile`` can be built in-process.

    ``MizFile.__init__`` reads the process's own node (``ServiceRegistry.get(ServiceBus).node``); the
    action then points the parsed file at the SERVER's node for the DCS-version-dependent fog keys.
    """
    node = types.SimpleNamespace(name="bus", master=True, dcs_version="2.9.14",
                                 locals={"DCS": {"installation": ""}})

    def fake_get(cls, t):
        return types.SimpleNamespace(node=node) if t is ServiceBus else None

    monkeypatch.setattr(ServiceRegistry, "get", classmethod(fake_get))
    return node


# ------------------------------------------------------------------ 1. the read, and its name

def test_the_route_is_declared_under_the_server_pages_own_capability():
    """A route with no declared capability would be refused by the deny-by-default gate."""
    assert detail_page.capabilities()[detail_page.MISSION_WEATHER_PATH] == \
        servers_page.SERVERS_CAPABILITY


@pytest.mark.asyncio
async def test_the_route_answers_the_missions_weather_and_its_name(config_dir, clean_registry,
                                                                  tmp_path):
    """The dotted-key values come from the wizard's OWN reader, and the mission's file name rides."""
    node = WeatherNode(content=miz_payload(tmp_path))
    server = WeatherServer(node=node, current=m("Training.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["name"] == "Training.miz"
    assert body["values"] == presets_page.display_values(
        presets_page.preset_values("", EXPECTED_DOCUMENT, {}), feet=True)
    # a few of the wizard's own dotted keys, spelled as the wizard draws them
    assert body["values"]["date"] == "2024-06-01"
    assert body["values"]["clouds"] == "preset"
    assert body["values"]["clouds.preset"] == "Preset1"
    assert body["values"]["temperature"] == "15"
    assert body["values"]["wind.atGround.speed"] == "3"
    # ONE either-or value: this mission carries NEITHER dust nor fog, so the choice is ``off`` — the
    # fabricated empty fog map the document used to carry read as an enabled fog.
    assert body["values"]["enable_dust"] == "off", "the either-or choice is the wizard's own reading"
    assert body["values"]["fog.mode"] == "manual"
    assert node.reads == [m("Training.miz")], "the current mission's file is what was read"


#: THE FIVE HAZE STATES a mission file can carry, and the prefill each must produce: the either-or
#: word, the fog mode and the fog keyframes. The rows are the value's own spelling
#: (``time:thickness:visibility``) stated in the DIALOG's unit — feet for an English render. The mode
#: is only READ while fog is the choice (the control is gated out otherwise), so a dust/no-haze
#: mission states the mode's own default.
HAZE_STATES = (
    ("dust", {"enable_dust": True, "dust_density": 3048}, "dust", "manual", ""),
    ("fog2 auto", {"fog2": {"mode": 2}}, "fog", "auto", ""),
    ("fog2 manual", {"fog2": {"mode": 4, "manual": [
        {"time": 0, "thickness": 200, "visibility": 1500}]}}, "fog", "manual", "0:656:4921"),
    ("old fog", {"enable_fog": True, "fog": {"thickness": 300, "visibility": 2000}},
     "fog", "manual", "0:984:6562"),
    ("neither", {}, "off", "manual", ""),
)


@pytest.mark.parametrize("label,weather,choice,mode,rows", HAZE_STATES)
@pytest.mark.asyncio
async def test_the_prefill_states_the_missions_haze_state(config_dir, clean_registry, tmp_path,
                                                          label, weather, choice, mode, rows):
    """The dialog's prefill states what the mission REALLY has — dust, fog or neither.

    ``enable_dust`` is the either-or word, so a dust mission reads ``dust`` and a mission with no fog
    reads ``off`` (a fabricated empty fog map used to make every mission read ``fog``); a fog mission
    states its mode, and the manual keyframes ride ``fog.rows`` so the dialog's own table can show
    them.
    """
    from test_mission_weather_read import MISSION

    mission = dict(MISSION, weather=dict(MISSION["weather"], **weather))
    payload = Path(build_miz(tmp_path / "State.miz", mission)).read_bytes()
    node = WeatherNode(content=payload)
    server = WeatherServer(node=node, current=m("State.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 200, response.text
    values = response.json()["values"]
    assert values["enable_dust"] == choice, label
    assert values["fog.mode"] == mode, label
    assert values["fog.rows"] == rows, f"{label}: the keyframes must reach the dialog's table"


@pytest.mark.asyncio
async def test_the_read_follows_the_apply_to_the_original_mission_box(config_dir, clean_registry,
                                                                     tmp_path):
    """``original`` picks the FILE the launch rewrites: the ``.orig`` twin, or the file it is on.

    A launch with "apply them to the original mission" ticked (``use_orig``, on by default) rewrites
    the original and loads its own copy, so the prefill must state the ORIGINAL's weather; unticked,
    the base is the file the server is on. The two missions here carry DIFFERENT weather, so the
    answer's own values say which file was read.
    """
    from test_mission_weather_read import MISSION

    missions = tmp_path / "missions" / ".dcssb"
    missions.mkdir(parents=True)
    plain = tmp_path / "missions" / "Mission.miz"
    original = missions / "Mission.miz.orig"
    dust = dict(MISSION, weather=dict(MISSION["weather"], enable_dust=True, dust_density=3048))
    fog = dict(MISSION, weather=dict(MISSION["weather"], fog2={"mode": 4, "manual": [
        {"time": 0, "thickness": 200, "visibility": 1500}]}))
    for path, mission in ((plain, dust), (original, fog)):
        build_miz(path, mission)

    class FileNode(WeatherNode):
        async def read_file(self, path):
            self.reads.append(path)
            return Path(path).read_bytes()

    node = FileNode()
    server = WeatherServer(node=node, current=str(plain))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        with_orig = await http.get(detail_page.mission_weather_path("SRS-1"))
        without = await http.get(detail_page.mission_weather_path("SRS-1") + "?original=0")

    assert with_orig.status_code == 200 and without.status_code == 200
    assert with_orig.json()["values"]["enable_dust"] == "fog", "the ORIGINAL's weather is what is read"
    assert with_orig.json()["values"]["fog.rows"], "the original's keyframes are stated"
    assert without.json()["values"]["enable_dust"] == "dust", "the file the server is on is what is read"
    assert node.reads == [str(original), str(plain)], node.reads


@pytest.mark.asyncio
async def test_the_route_reads_a_one_based_numeric_index(config_dir, clean_registry, tmp_path):
    """``?mission=2`` NAMES the second configured mission — a numeric string is the 1-based index.

    A query parameter is always a string; before the index convention was unified this fell down the
    NAME path and 404'd as ``Mission '2' not found``. The read names the SECOND entry, and the node is
    asked for exactly that file.
    """
    node = WeatherNode(content=miz_payload(tmp_path))
    server = WeatherServer(node=node, configured=[m("Other.miz"), m("Training.miz")])
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"), params={"mission": "2"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Training.miz"
    assert node.reads == [m("Training.miz")]


@pytest.mark.asyncio
async def test_the_route_refuses_index_zero_with_the_one_based_sentence(config_dir, clean_registry,
                                                                       tmp_path):
    """``?mission=0`` is out of range under 1-based indexing — refused, and the node is never asked."""
    node = WeatherNode(content=miz_payload(tmp_path))
    server = WeatherServer(node=node, configured=[m("Training.miz")])
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"), params={"mission": "0"})

    assert response.status_code == 404, response.text
    assert "out of range (1-" in response.text, response.text
    assert node.reads == []


# ---------------------------------------------------- 2. the traversal refusal (the risk to close)

@pytest.mark.parametrize("bad", [
    "../evil.miz", "/etc/passwd", "sub/evil.miz", "sub\\evil.miz", "..",
])
@pytest.mark.asyncio
async def test_the_route_refuses_a_path_without_asking_the_node(config_dir, clean_registry,
                                                                tmp_path, bad):
    """THE PROPERTY THIS CARD EXISTS TO KEEP: a path never reaches ``read_file``."""
    node = WeatherNode(content=b"MISSION")
    server = WeatherServer(node=node, configured=[m("A.miz")])
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"), params={"mission": bad})

    assert response.status_code == 400, response.text
    assert "never by a path" in response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert node.reads == [], f"a refused path was still asked of the node: {node.reads}"


# ------------------------------------------------------------- 3. every failure is its own answer

@pytest.mark.parametrize("error,status", [
    (FileNotFoundError(), 404),
    (PermissionError(), 403),
    (TimeoutError(), 504),
    (RuntimeError("the transport exploded"), 502),
])
@pytest.mark.asyncio
async def test_every_node_read_failure_answers_its_own_sentence(config_dir, clean_registry,
                                                                error, status):
    """The action's own sentence is rendered with its own status — never a document of zeros."""
    node = WeatherNode(error=error)
    server = WeatherServer(node=node, current=m("Training.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.strip(), "a failure carries its own sentence"
    assert "values" not in response.text


@pytest.mark.asyncio
async def test_an_unparseable_mission_answers_a_failure(config_dir, clean_registry):
    """A ``.miz`` that cannot be parsed is refused — never a document of zeros."""
    node = WeatherNode(content=b"this is not a zip archive")
    server = WeatherServer(node=node, current=m("Broken.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_the_route_refuses_a_node_it_cannot_reach(config_dir, clean_registry):
    """An offline node answers its own sentence, and nothing is read."""
    node = WeatherNode(content=b"MISSION")
    server = WeatherServer(node=node, current=m("Training.miz"))
    async with client(app_for(config_dir, server, nodes={NODE: None})) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == detail_page.MISSION_WEATHER_NODE_OFFLINE.format(node=NODE)
    assert node.reads == []


# ----------------------------------------------------------- 4. a read: no audit, no capability

@pytest.mark.asyncio
async def test_the_route_writes_no_audit_entry(config_dir, clean_registry, recording_bot, tmp_path):
    """A READ: the route answers, and NOTHING is audited."""
    node = WeatherNode(content=miz_payload(tmp_path))
    server = WeatherServer(node=node, current=m("Training.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 200, response.text
    assert recording_bot.calls == [], "a read must write no trail"


@pytest.mark.asyncio
async def test_a_caller_without_the_capability_is_refused(config_dir, clean_registry, tmp_path):
    """``servers.view`` gates it: a plain member reaches no server read."""
    node = WeatherNode(content=miz_payload(tmp_path))
    server = WeatherServer(node=node, current=m("Training.miz"))
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "plain")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))

    assert response.status_code == 403
    assert node.reads == []
