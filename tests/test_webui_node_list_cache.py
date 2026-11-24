"""A node's LISTS are read once and remembered: the launch dialog opens without waiting on the node.

The launch dialog's temporary-preset fieldset is drawn on the SERVER, so every open used to cost the
node a ``clouds.lua`` read and an ``icehalo.lua`` read, and the preset pick beside it a ``list_files``
plus one ``read_file`` per preset file. On an AGENT those are drop-box round trips — seconds before the
modal appears. The two vendor files are vendor data and a node's preset files are operator config, so
both are remembered in ONE cache (``presets``), while the mission's own weather stays on demand.

One section per acceptance criterion:

* THE CENSUS — one cold open asks for the two vendor files and the preset list; a WARM open asks for
  NOTHING (``test_a_warm_dialog_open_asks_the_node_nothing``);
* THE MISSION'S WEATHER is never read on render — the fieldset carries the URL and the sentences, and
  the ONLY caller of the mission read is the read route itself;
* ONE HOME — the wizard's own steps and the plate routes read through the same cache the dialog
  warmed, and the cache's call sites are enumerated in one list;
* INVALIDATION — a changed vendor file is served from the cache inside the window and re-read after
  it, a preset save forgets the preset lists, a transport failure is never remembered, and a file that
  is not there is remembered (there is nothing to re-discover);
* NOT CACHED — the mission's weather never enters the cache.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), reusing the write suite's harness so the app under test is the running one.
"""
from __future__ import annotations

import asyncio
import re
import types
from pathlib import Path

import pytest

from conftest import host_path

from core import Status
from core.data.impl.nodeimpl import NodeImpl
from services.webservice import dcs_clouds, dcs_halo
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from services.webservice.pages import server_detail as detail_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, sign_in,
)
from test_webui_presets_clouds import fixture_text, install_clouds, install_plate
from test_webui_presets_halo import install_halo
from test_webui_presets_save import NODES_NAMING_ONE, nodes_file, post_save  # noqa: F401
#: the WIZARD suite's preset: it carries a cloud preset, so the clouds step has plates to draw
from test_webui_presets_wizard import SPRING, preset_file
from test_webui_startup_weather_picker import open_dialog
from test_webui_write_server_actions import FakeServer
# the mission-weather harness: the only surface allowed to read a mission, and its fixture mission
from test_webui_mission_weather import (  # noqa: F401 - fixtures reused
    WeatherServer, app_for, cluster_for, miz_payload, miz_service,
)

NODE = "dcs-a-01"
STARTUP = "/actions/server/startup"
REPO_ROOT = Path(__file__).resolve().parent.parent

#: a second vendor file with ONE visible cloud entry, distinct from the fixture's 34.
ONE_PLATE = """\
clouds =
{
\tpresets =
\t{
\t\tSolo =
\t\t{
\t\t\tvisibleInGUI = true,
\t\t\treadableName = _('One Plate'),
\t\t\tthumbnailName = 'Bazar/Effects/Clouds/Thumbnails/cloud_1.png',
\t\t},
\t},
}
"""


# --------------------------------------------------------------------------------- test doubles

class CensusNode:
    """A master-local node carrying a DCS installation and a config dir, recording every ask.

    ``read_file`` is the REAL ``NodeImpl`` method and ``list_files`` the REAL ``NodeImpl`` listing, so
    the console's own node-read path is what runs; every path is recorded so a test can assert what the
    node was asked for, not merely what a page rendered.
    """

    def __init__(self, name=NODE, *, installation, config_dir="", error=None):
        self.name = name
        self.master = True
        self.node = self
        self.config_dir = str(config_dir) if config_dir else ""
        self.locals = {"DCS": {"installation": str(installation)}} if installation else {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def read_file(self, path):
        self.calls.append(("read_file", path))
        if self.error is not None:
            raise self.error
        return await NodeImpl.read_file(self, path)  # type: ignore[arg-type]

    async def list_files(self, path, *, pattern="*"):
        self.calls.append(("list_files", path))
        if self.error is not None:
            raise self.error
        return await NodeImpl.list_files(self, path, pattern=pattern)  # type: ignore[arg-type]

    def names(self) -> list[str]:
        """The BASENAMES the node was asked to READ, in order — a path is the harness's own temp dir."""
        return [Path(path).name if path else path for kind, path in self.calls if kind == "read_file"]

    def reads(self) -> list[str]:
        return [path for kind, path in self.calls if kind == "read_file"]

    def listings(self) -> list[str]:
        """The directories the node was asked to LIST."""
        return [path for kind, path in self.calls if kind == "list_files"]


class QuietServer(FakeServer):
    """A shutdown server double whose node is the recording node — the startup dialog's own shape."""

    def __init__(self, name="SRS-1"):
        super().__init__(name, status=Status.SHUTDOWN, managed_by=["Host A"])


def fleet_with(node, *, dcs_version=""):
    """ONE shutdown server whose node is *node*, mapped into the scoped source as well."""
    server = QuietServer()
    server.node = node
    return Cluster([server], nodes={node.name: node}, node=node,
                   instances=[instance("Host-A-Instance", server)])


@pytest.fixture(autouse=True)
def forget_lists():
    """The cache is PROCESS state: start and end every test with nothing remembered."""
    presets_page.forget_node_lists()
    try:
        yield
    finally:
        presets_page.forget_node_lists()


def installation(tmp_path) -> Path:
    """A DCS installation carrying both vendor files, and a config dir carrying one preset file."""
    install_clouds(tmp_path)
    install_halo(tmp_path)
    return tmp_path


async def open_startup(http):
    """Open the server's Startup dialog and return its response (an OPTIONS form: no confirm token)."""
    return await open_dialog(http, path=STARTUP, name="SRS-1")


# ------------------------------------------------------------------------------- 1. the census

@pytest.mark.asyncio
async def test_a_warm_dialog_open_asks_the_node_nothing(config_dir, clean_registry, tmp_path):
    """THE PROPERTY THIS CARD EXISTS TO KEEP: the second open re-parses no preset file.

    The first open reads the two vendor files and lists the config dir for the node's preset files
    (one file, so one read); the second open must read NO file at all — only the listing, which is
    what keeps the file set current — and its pickers must still carry the node's own entries. A cache
    that made the controls empty would pass a "no reads" test while breaking the dialog.
    """
    installation(tmp_path)
    (config_dir / "presets.yaml").write_text(SPRING, encoding="utf-8")
    node = CensusNode(installation=tmp_path, config_dir=config_dir)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        first = await open_startup(http)
        cold = node.names()
        cold_listings = node.listings()
        node.calls.clear()
        second = await open_startup(http)

    assert sorted(cold) == sorted(["clouds.lua", "icehalo.lua", "presets.yaml"]), cold
    assert cold_listings == [str(config_dir)], \
        f"the preset pick must list the node's own config dir: {cold_listings}"
    assert node.names() == [], f"the warm open re-parsed a preset file: {node.names()}"
    assert node.listings() == [str(config_dir)], \
        f"the warm open must still list the file set, and only that: {node.calls}"
    # and the dialog is still the dialog: the same controls, filled, on both opens
    for response in (first, second):
        assert '<option value="Preset1"' in response.text, "the cloud pick lost the node's entries"
        assert '<option value="AllKinds">' in response.text, "the crystal pick lost the node's entries"
        assert "<option value=\"Spring\">" in response.text, "the preset pick lost the node's presets"


@pytest.mark.asyncio
async def test_the_first_open_reads_each_vendor_file_exactly_once(config_dir, clean_registry,
                                                                 tmp_path):
    """A COLD open reads ``clouds.lua`` once and ``icehalo.lua`` once — never twice for one render."""
    installation(tmp_path)
    node = CensusNode(installation=tmp_path)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        await open_startup(http)

    assert node.names().count("clouds.lua") == 1, node.calls
    assert node.names().count("icehalo.lua") == 1, node.calls
    # a node with no ``config_dir`` has no preset files to enumerate: the pick asks for nothing
    assert node.listings() == [], node.calls


# ---------------------------------------------------- 2. the mission's weather stays on demand

@pytest.mark.asyncio
async def test_rendering_the_dialog_never_reads_the_missions_weather(config_dir, clean_registry,
                                                                    tmp_path, miz_service):
    """The render carries the read's URL and sentences; the mission FILE is read only by the route.

    A mission is per-mission state: it is read when "Create your own weather" is selected, never while
    the modal is drawn. The pin is the node's own read log — no ``.miz`` is asked for until the route
    is called, and the read route asks for exactly one.
    """
    rotation = ["/missions/Training.miz"]
    node = WeatherServer(name="SRS-1", configured=rotation, current=rotation[0]).node
    server = WeatherServer(name="SRS-1", configured=rotation, current=rotation[0])
    server.status = Status.SHUTDOWN
    server.node = node
    node.content = miz_payload(tmp_path)
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        dialog = await open_startup(http)
        rendered = [path for path in node.reads if str(path).endswith(".miz")]

        assert rendered == [], f"the render read the mission: {rendered}"
        # the fieldset carries the URL the script calls, and NO prefilled value of its own
        assert f'data-mission-url="{detail_page.mission_weather_path("SRS-1")}"' in dialog.text
        assert 'value="15"' not in dialog.text, "a mission value was rendered into the form"
        assert "2024-06-01" not in dialog.text, "the mission's date was rendered into the form"

        response = await http.get(detail_page.mission_weather_path("SRS-1"))
        assert response.status_code == 200, response.text

    assert [path for path in node.reads if str(path).endswith(".miz")] == [host_path("/missions/Training.miz")], \
        "the read route must be the ONE caller of the mission read"


# --------------------------------------------------------------------- 3. one cache, one home

@pytest.mark.asyncio
async def test_the_wizard_steps_read_through_the_cache_the_dialog_warmed(config_dir, clean_registry,
                                                                        tmp_path):
    """The wizard's own clouds step asks the node for nothing once the dialog has read the lists.

    One cache, not a second list: the step's two reads go through the SAME seam the fieldset uses, so
    a plate grid of 34 entries costs no extra vendor read either.
    """
    installation(tmp_path)
    (config_dir / "presets.yaml").write_text(SPRING, encoding="utf-8")
    node = CensusNode(installation=tmp_path, config_dir=config_dir)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        await open_startup(http)
        node.calls.clear()
        step = await http.get(f"{presets_page.wizard_url('Spring')}?step=clouds&node={NODE}")
        assert step.status_code == 200, step.text
        clouds_json = await http.get(presets_page.clouds_list_path(NODE))
        halo_json = await http.get(presets_page.halo_list_path(NODE))

    assert presets_page.cloud_image_path(NODE, "Preset1") in step.text, \
        "the step drew no plate from the node's entries"
    assert clouds_json.status_code == 200 and clouds_json.json()["presets"], clouds_json.text
    assert halo_json.status_code == 200 and halo_json.json()["presets"], halo_json.text
    vendor = [name for name in node.names() if name in ("clouds.lua", "icehalo.lua")]
    assert vendor == [], f"the wizard re-read the vendor files the dialog had already read: {node.calls}"


@pytest.mark.asyncio
async def test_a_plate_step_reads_the_list_once_not_once_per_plate(config_dir, clean_registry,
                                                                  tmp_path):
    """Three plates off ONE list = one list read. The picture is fetched per plate; the list is not."""
    installation(tmp_path)
    (config_dir / "presets.yaml").write_text(SPRING, encoding="utf-8")
    entries = dcs_clouds.visible_presets(dcs_clouds.parse_cloud_presets(fixture_text()))[:3]
    for preset in entries:
        install_plate(tmp_path, preset.thumbnail)
    node = CensusNode(installation=tmp_path, config_dir=config_dir)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        listing = await http.get(presets_page.clouds_list_path(NODE))
        assert listing.status_code == 200, listing.text
        names = [entry["name"] for entry in listing.json()["presets"]][:3]
        assert names == [preset.name for preset in entries], names
        node.calls.clear()
        for name in names:
            plate = await http.get(presets_page.cloud_image_path(NODE, name))
            assert plate.status_code == 200, plate.text

    assert node.names().count("clouds.lua") == 0, \
        f"each plate re-read the vendor list: {node.calls}"
    assert len(node.names()) == 3, f"one picture per plate and nothing else: {node.calls}"


#: WHERE the one cache is read and written. A new entry here means a new reader of the cache: it must
#: go through the seam (``_remembered_vendor_list``) or be the preset file set, never a third spelling.
CACHE_CALL_SITES = {
    "services/webservice/pages/presets.py": (
        "def node_list_cached(", "def remember_node_list(", "def forget_node_lists(",
        "node_list_cached(*key)", "remember_node_list(*key, refusal)",
        "remember_node_list(*key, value)", "forget_node_lists(PRESET_LIST)",
        "node_list_cached(PRESET_LIST", "remember_node_list(PRESET_LIST",
    ),
}


def test_the_cache_has_one_call_site_list():
    """The cache is touched from ONE module: the seam for a node file, plus the preset file set."""
    found: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "services" / "webservice").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "__pycache__" in str(path):
            continue
        hits = [name for name in ("node_list_cached", "remember_node_list", "forget_node_lists",
                                  "_remembered_vendor_list")
                if name in text]
        if hits:
            found[path.relative_to(REPO_ROOT).as_posix()] = hits
    assert set(found) == set(CACHE_CALL_SITES), f"the cache grew a call site: {found}"
    presets_text = (REPO_ROOT / "services" / "webservice" / "pages" / "presets.py").read_text(
        encoding="utf-8")
    for marker in CACHE_CALL_SITES["services/webservice/pages/presets.py"]:
        assert marker in presets_text, f"the cache's home lost: {marker}"
    # ONE seam for both vendor files: neither reader may reach the transport without it
    assert presets_text.count("await _remembered_vendor_list(") == 2, \
        "a vendor reader bypasses the cache seam"
    # and the preset file set is read through the cache exactly ONCE, in its own kind
    assert presets_text.count("node_list_cached(PRESET_LIST") == 1, \
        "the preset file set read the cache from a second place"
    assert presets_text.count("remember_node_list(PRESET_LIST") == 1, \
        "the preset file set is remembered from a second place"


# ------------------------------------------------------------------------------ 4. invalidation

@pytest.mark.asyncio
async def test_a_changed_vendor_file_is_read_again_once_the_window_passes(tmp_path, monkeypatch):
    """A cache that never expires would be a finding, not a fix: the window is honoured, then re-read.

    Inside the window the remembered answer is served (that is the whole point) and the node is not
    asked; past it the node is asked again and the NEW content is what comes back.
    """
    monkeypatch.setattr(presets_page, "NODE_LIST_TTL_SECONDS", 0.05)
    installation(tmp_path)
    node = CensusNode(installation=tmp_path)
    first, _path = await presets_page._read_cloud_presets(node, NODE)
    assert "Preset1" in [preset.name for preset in first]

    install_clouds(tmp_path, ONE_PLATE)
    remembered, _path = await presets_page._read_cloud_presets(node, NODE)
    assert [preset.name for preset in remembered] == [preset.name for preset in first], \
        "inside the window the remembered answer is what is served"
    assert len(node.reads()) == 1, "the second read inside the window still went to the node"

    await asyncio.sleep(0.1)
    expired, _path = await presets_page._read_cloud_presets(node, NODE)
    assert [preset.name for preset in expired] == ["Solo"], \
        "past the window the file is read again and its NEW content is served"
    assert len(node.reads()) == 2, "the expired entry was not re-read"


@pytest.mark.asyncio
async def test_a_preset_save_forgets_the_preset_list_so_the_new_name_is_offered(config_dir,
                                                                               clean_registry,
                                                                               tmp_path):
    """A preset created in the wizard is in the dialog's pick on the next open, not after the window."""
    installation(tmp_path)
    (config_dir / "presets.yaml").write_text(SPRING, encoding="utf-8")
    nodes_file(config_dir, NODES_NAMING_ONE)
    node = CensusNode(installation=tmp_path, config_dir=config_dir)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        before = await open_startup(http)
        assert '<option value="Spring">' in before.text
        assert '<option value="Fresh">' not in before.text
        saved = await post_save(http, "Fresh", new=True, file="config/presets.yaml")
        assert saved.status_code in (200, 303), saved.text
        after = await open_startup(http)

    assert '<option value="Fresh">' in after.text, \
        "a preset saved through the console is missing from the launch dialog's pick"


def test_forgetting_the_preset_lists_leaves_the_vendor_lists():
    """The two kinds are forgotten independently: a save must not throw away the vendor reads."""
    presets_page.remember_node_list(presets_page.VENDOR_LIST, NODE, "/dcs/clouds.lua", ["Preset1"])
    presets_page.remember_node_list(presets_page.PRESET_LIST, NODE, "/cfg", (("Spring",), True))
    presets_page.forget_node_lists(presets_page.PRESET_LIST)
    assert presets_page.node_list_cached(presets_page.VENDOR_LIST, NODE, "/dcs/clouds.lua") == \
        (True, ["Preset1"])
    assert presets_page.node_list_cached(presets_page.PRESET_LIST, NODE, "/cfg") == (False, None)
    presets_page.forget_node_lists()
    assert presets_page.node_list_cached(presets_page.VENDOR_LIST, NODE, "/dcs/clouds.lua") == \
        (False, None)


@pytest.mark.asyncio
async def test_a_transport_failure_is_never_remembered(tmp_path):
    """The node did not answer: the next render must ask again, not serve a failure for five minutes."""
    installation(tmp_path)
    node = CensusNode(installation=tmp_path, error=TimeoutError())
    for _ in range(2):
        with pytest.raises(presets_page._NodeFileUnreadable) as failure:
            await presets_page._read_cloud_presets(node, NODE)
        assert failure.value.status_code == 504
    assert len(node.reads()) == 2, "a transport failure was remembered"


@pytest.mark.asyncio
async def test_a_missing_vendor_file_is_remembered_and_names_the_path(tmp_path):
    """There is nothing to re-discover: an absent file is remembered, and the sentence still names it."""
    node = CensusNode(installation=tmp_path)
    for _ in range(2):
        with pytest.raises(presets_page._NodeFileUnreadable) as failure:
            await presets_page._read_cloud_presets(node, NODE)
        assert failure.value.status_code == 404
        assert "clouds.lua" in failure.value.sentence
    assert len(node.reads()) == 1, "an absent vendor file was read twice"


@pytest.mark.asyncio
async def test_a_node_pointed_at_another_installation_is_read_afresh(config_dir, clean_registry,
                                                                    tmp_path):
    """The key carries the RESOLVED path, so re-pointing a node invalidates by construction."""
    one = tmp_path / "one"
    two = tmp_path / "two"
    install_clouds(one)
    install_clouds(two, ONE_PLATE)
    node = CensusNode(installation=one)
    first, _path = await presets_page._read_cloud_presets(node, NODE)
    assert "Preset1" in [preset.name for preset in first]
    node.locals = {"DCS": {"installation": str(two)}}
    second, _path = await presets_page._read_cloud_presets(node, NODE)
    assert [preset.name for preset in second] == ["Solo"], \
        "a node re-pointed at another installation served the old installation's list"


# --------------------------------------------------------- 5. the mission's weather is NOT cached

@pytest.mark.asyncio
async def test_the_missions_weather_never_enters_the_node_list_cache(config_dir, clean_registry,
                                                                    tmp_path, miz_service):
    """A cached mission would fill a form with another mission's sky: the read leaves the cache alone."""
    rotation = ["/missions/Training.miz"]
    node = WeatherServer(name="SRS-1", configured=rotation, current=rotation[0]).node
    server = WeatherServer(name="SRS-1", configured=rotation, current=rotation[0])
    server.status = Status.SHUTDOWN
    server.node = node
    node.content = miz_payload(tmp_path)
    async with client(app_for(config_dir, server)) as http:
        await sign_in(http, "specialk")
        response = await http.get(detail_page.mission_weather_path("SRS-1"))
        assert response.status_code == 200, response.text
        assert presets_page._NODE_LISTS == {}, \
            f"the mission read remembered something: {presets_page._NODE_LISTS}"
        # ... and a second read asks the node AGAIN — the mission is per-mission state
        second = await http.get(detail_page.mission_weather_path("SRS-1"))
        assert second.status_code == 200, second.text

    missions = [path for path in node.reads if str(path).endswith(".miz")]
    assert missions == [host_path("/missions/Training.miz"), host_path("/missions/Training.miz")], missions


# ------------------------------------------------------------- 6. the dialog's own promise kept

@pytest.mark.asyncio
async def test_the_warmed_dialog_still_carries_the_read_url_and_the_three_sentences(config_dir,
                                                                                   clean_registry,
                                                                                   tmp_path):
    """Caching the lists must not cost the fieldset its on-demand mission read: the URL still rides."""
    installation(tmp_path)
    node = CensusNode(installation=tmp_path)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        await open_startup(http)
        warm = await open_startup(http)

    block = re.search(r'<div class="dlg-temp">.*?<p class="dlg-temp-read"[^>]*>', warm.text, re.S)
    assert block, "the warm dialog lost the temporary fieldset"
    line = block.group(0)
    assert f'data-mission-url="{detail_page.mission_weather_path("SRS-1")}"' in line
    # STARTUP NAMES the mission its pick shows — the rotation's start mission — so the line states
    # which file the weather comes from before anything is fetched.
    assert 'data-mission-name="Training.miz"' in warm.text
    for attribute in ("data-mission-loading", "data-mission-read", "data-mission-error"):
        assert attribute in line, attribute
