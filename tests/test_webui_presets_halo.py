"""The Presets area's halo reads: the ice-halo preset list and the crystal plates.

One section per acceptance criterion:

* the parser walks the REAL ``icehalo.lua`` (``tests/fixtures/icehalo.lua``): 4 ``presets`` entries and
  6 ``crystalsPresets`` entries, all visible, every halo entry carrying an EMPTY ``thumbnailName`` and
  every crystal entry a real one read VERBATIM (the file's own names carry a space, and their casing
  does not always match the key), the Lua ``readableName`` unwrapped out of its ``'NN ##'.. _()`` wrapper;
* the nested ``appearance`` table of a crystal entry never lends the entry its scalars;
* an absent, empty or unparseable file yields NO entries and never raises;
* ``"auto"`` is NOT an entry of the file — it is offered by the route IN ADDITION to the parsed ones;
* ``GET /presets/halo/<node>.json`` answers both lists, and an unreadable file answers empty lists plus
  one sentence with HTTP 200;
* ``GET /presets/halo/<node>/<name>.png`` serves the entry's own thumbnail out of the installation;
* THE PROPERTY THE CLOUDS ROUTE PINS, KEPT HERE: the plate route never becomes a general file reader —
  the node double RECORDS every path it is asked for, and a refused name must have asked for NOTHING;
* every failure is its own plain sentence, and both routes answer 403 for a caller without
  ``presets.view``.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.webservice import dcs_halo, dcs_install
from services.webservice.pages import presets as presets_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, sign_in,
)

#: THE OWNER'S REAL FILE, attached to the card and copied here as the fixture.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "icehalo.lua"

NODE = "dcs-a-01"

#: one backslash, by codepoint — a hostile name may use either separator, and a literal backslash in
#: this file would read as an escape to anyone skimming the list.
BACKSLASH = chr(92)

#: the file's own entry keys, in file order.
HALO_ENTRIES = ("AtmoHighClouds", "CirrusOnly", "HighClouds", "VolumetricOnly")
CRYSTAL_ENTRIES = ("AllKinds", "BasicHaloCircle", "BasicHaloWithSundogs", "BasicSundogsTangents",
                   "SundogsArcs", "Tangents")

#: every crystal entry's ``thumbnailName``, VERBATIM — the names carry a space and their casing does
#: not always match the entry key (``BasicHaloWithSundogs`` names ``BasicHaloWithSunDogs 1.png``).
CRYSTAL_THUMBNAILS = {
    "AllKinds": "Bazar/Effects/Thumbnails/Halo/AllKinds 1.png",
    "BasicHaloCircle": "Bazar/Effects/Thumbnails/Halo/BasicHaloCircle 1.png",
    "BasicHaloWithSundogs": "Bazar/Effects/Thumbnails/Halo/BasicHaloWithSunDogs 1.png",
    "BasicSundogsTangents": "Bazar/Effects/Thumbnails/Halo/BasicSundogsTangents 1.png",
    "SundogsArcs": "Bazar/Effects/Thumbnails/Halo/SundogsArcs 1.png",
    "Tangents": "Bazar/Effects/Thumbnails/Halo/Tangents 1.png",
}

#: the entry whose stored file name differs from its key in CASE — the plate must come from the
#: stored name, never from a path rebuilt out of the key.
CASING_ENTRY = "BasicHaloWithSundogs"

#: names that must be REFUSED on their SHAPE, before anything is asked of the node. The same cases
#: the existing per-server thumbnail route refuses.
HOSTILE_NAMES = (
    "/etc/passwd.png",
    "../../../etc/passwd.png",
    "Bazar/../../../etc/passwd.png",
    "C:/Windows/Web/Wallpaper.png",
    f"{BACKSLASH}{BACKSLASH}server{BACKSLASH}share{BACKSLASH}halo.png",
    f"{BACKSLASH}etc{BACKSLASH}passwd.png",
    "Bazar/Effects/Thumbnails/Halo/icehalo.lua",
    "Bazar/Effects/Thumbnails/Halo/../secret.png",
    "",
)

#: the same rule as it reaches the PLATE URL: a name that is not one plain segment. A name carrying
#: ``/`` cannot travel in one path segment at all, so it is covered by the unit test above.
HOSTILE_URL_NAMES = ("..", "a..b", f"..{BACKSLASH}..", f"{BACKSLASH}etc")

#: a file whose ONE crystal entry carries a nested ``appearance`` table with its own scalars.
NESTED_APPEARANCE = """\
icehalo =
{
presets =
{
\tOnly =
\t{
\t\tvisibleInGUI = true,
\t\treadableName = _('Only Halo'),
\t\treadableNameShort = 'Only',
\t\tthumbnailName = '',
\t},
},
crystalsPresets =
{
\tOuter =
\t{
\t\tvisibleInGUI = true,
\t\treadableName = _('Outer Crystal'),
\t\treadableNameShort = 'Outer',
\t\tthumbnailName = 'Bazar/Effects/Thumbnails/Halo/Outer 1.png',
\t\tappearance =
\t\t{
\t\t\tsunDiameterDeg = 0.530000,
\t\t\treadableName = _('Inner Name'),
\t\t\tthumbnailName = 'Bazar/inner.png',
\t\t}
\t},
},
}
"""

#: a file that carries no halo table this console recognizes.
UNPARSEABLE = "icehaloTechnique = 0\n"


def fixture_text() -> str:
    """The owner's file, read as text."""
    return FIXTURE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------- test doubles

class HaloNode:
    """A node double carrying a DCS installation and reading files OUT of it.

    ``read_file`` RECORDS every path it was asked for, so a test can assert not only the bytes a route
    handed back but the FILE it resolved — and that a refused name was never opened at all.
    """

    def __init__(self, name=NODE, *, installation, error=None):
        self.name = name
        self.master = True
        self.instances: dict = {}
        self.locals = {"DCS": {"installation": str(installation)}} if installation else {}
        self.error = error
        self.reads: list[str] = []

    async def read_file(self, path):
        self.reads.append(path)
        if self.error is not None:
            raise self.error
        with open(path, "rb") as handle:
            return handle.read()


def halo_path(root) -> str:
    """The vendor file's path ON a node whose installation is *root*, FORWARD-slashed.

    ``dcs_install._join`` states a node path with forward slashes on every host (it crosses JSON and
    URLs), so this expectation is not written with ``os.sep``.
    """
    return f"{Path(root).as_posix()}/{dcs_halo.HALO_LUA_RELATIVE_PATH}"


def install_halo(root, text: str | None = None) -> Path:
    """Write the vendor file under *root* and return its path."""
    target = Path(root) / "Config" / "Effects" / "icehalo.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fixture_text() if text is None else text, encoding="utf-8")
    return target


def install_plate(root, name: str, data: bytes = b"\x89PNG\r\n\x1a\nplate") -> Path:
    """Write the plate *name* names under *root* and return its path."""
    target = Path(root) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def app_for(config_dir, node):
    """A real application whose only node is *node*."""
    return build(config_dir, Cluster(nodes={node.name: node}, node=node))


# ------------------------------------------------------------------------------- 1. the parsers

def test_the_real_vendor_file_parses_into_four_halo_and_six_crystal_entries():
    """The counts the owner's own file carries — read, not assumed."""
    text = fixture_text()
    halo = dcs_halo.parse_halo_presets(text)
    crystals = dcs_halo.parse_crystal_presets(text)
    assert [entry.name for entry in halo] == list(HALO_ENTRIES)
    assert [entry.name for entry in crystals] == list(CRYSTAL_ENTRIES)
    assert all(entry.visible for entry in halo + crystals)
    assert dcs_halo.visible_presets(halo) == halo
    assert dcs_halo.visible_presets(crystals) == crystals


def test_every_halo_entry_carries_no_picture_and_its_own_readable_name():
    """The four ``presets`` entries have no thumbnail at all, and their Lua name is unwrapped.

    ``readableName`` is Lua: a gettext-wrapped string, or a quoted marker concatenated with one. The
    readable name is the gettext text — the ``'NN ##'`` marker is NOT part of it.
    """
    halo = dcs_halo.parse_halo_presets(fixture_text())
    assert all(entry.thumbnail == "" for entry in halo)
    assert all(entry.item()["thumbnail"] is None for entry in halo)
    by_name = {entry.name: entry for entry in halo}
    assert by_name["AtmoHighClouds"].readable_name == "Ice Halo on all mediums"
    assert by_name["AtmoHighClouds"].readable_name_short == "AtmoHighClouds"
    assert by_name["VolumetricOnly"].readable_name == "Ice Halo on high Volumetric Clouds"
    assert by_name["HighClouds"].readable_name == "Ice Halo on Cirrus and high Volumetric Clouds"
    assert by_name["CirrusOnly"].label == "Ice Halo on Cirrus"


def test_every_crystal_entry_carries_its_stored_picture_and_its_unwrapped_marker_name():
    """``'01 ##'.. _('All Kinds of Ice Halo')`` yields the gettext text, marker dropped."""
    crystals = dcs_halo.parse_crystal_presets(fixture_text())
    assert {entry.name: entry.thumbnail for entry in crystals} == CRYSTAL_THUMBNAILS
    by_name = {entry.name: entry for entry in crystals}
    assert by_name["AllKinds"].readable_name == "All Kinds of Ice Halo"
    assert by_name["AllKinds"].readable_name_short == "AllKinds"
    assert not by_name["AllKinds"].readable_name.startswith("01")
    assert by_name["Tangents"].readable_name == "Circumscribed Ice Halo"
    assert by_name["SundogsArcs"].readable_name == "Parhelia, arcs, subsun, subparhelia"


def test_a_crystal_picture_is_read_from_the_stored_name_never_rebuilt_from_the_key():
    """The stored name carries a SPACE and a casing that does not always match the entry key.

    ``BasicHaloWithSundogs`` names ``BasicHaloWithSunDogs 1.png`` — a capital ``D`` in the file where
    the key has a lowercase one — so a path derived from the key would miss that entry.
    """
    crystals = {entry.name: entry for entry in dcs_halo.parse_crystal_presets(fixture_text())}
    stored = crystals[CASING_ENTRY].thumbnail
    assert stored == CRYSTAL_THUMBNAILS[CASING_ENTRY]
    assert stored != f"Bazar/Effects/Thumbnails/Halo/{CASING_ENTRY} 1.png", \
        "the derived path happens to match the stored one, so this pin proves nothing"
    assert " " in stored, "the stored name's space is what the URL segment has to survive"


def test_auto_is_never_an_entry_of_the_file():
    """``"auto"`` is a value DCS decides, not a table entry: the parsers never return it."""
    text = fixture_text()
    assert "auto" not in [entry.name for entry in dcs_halo.parse_halo_presets(text)]
    assert "auto" not in [entry.name for entry in dcs_halo.parse_crystal_presets(text)]
    assert dcs_halo.auto_preset().name == dcs_halo.HALO_AUTO_PRESET == "auto"


def test_a_table_nested_inside_an_entry_never_lends_it_its_scalars():
    """Only an entry's OWN scalar fields are read — the ``appearance`` table contributes none.

    A crystal entry's ``appearance`` carries its own ``readableName``/``thumbnailName`` at a deeper
    indent; the walker must not fold them into the enclosing entry.
    """
    crystal = dcs_halo.parse_crystal_presets(NESTED_APPEARANCE)[0]
    assert crystal.name == "Outer"
    assert crystal.readable_name == "Outer Crystal", "the nested table's readableName leaked"
    assert crystal.thumbnail == "Bazar/Effects/Thumbnails/Halo/Outer 1.png", \
        "the nested table's thumbnailName leaked"
    halo = dcs_halo.parse_halo_presets(NESTED_APPEARANCE)
    assert [entry.name for entry in halo] == ["Only"]


def test_an_absent_empty_or_unparseable_file_yields_no_entries_and_never_raises():
    """A defensive read: the vendor file is UNTRUSTED, POSSIBLY ABSENT and version-dependent."""
    for text in ("", UNPARSEABLE, "icehalo = {\n\tsomething = 1,\n}",
                 "icehalo =\n{\n\tpresets =\n\t{\n\t}\n}",
                 "icehalo =\n{\n\tcrystalsPresets =\n\t{\n\t}\n}"):
        assert dcs_halo.parse_halo_presets(text) == [], repr(text)
        assert dcs_halo.parse_crystal_presets(text) == [], repr(text)


def test_a_hidden_entry_is_parsed_but_never_offered():
    """``visibleInGUI = false`` is the vendor file's own "do not offer this" flag."""
    text = ("icehalo =\n{\n\tpresets =\n\t{\n"
            "\t\tHidden =\n\t\t{\n"
            "\t\t\tvisibleInGUI = false,\n"
            "\t\t\treadableName = _('Hidden Halo'),\n"
            "\t\t\treadableNameShort = 'Hidden',\n"
            "\t\t\tthumbnailName = '',\n"
            "\t\t},\n"
            "\t\tUnlabeled =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n"
            "\t\t\tthumbnailName = '',\n"
            "\t\t},\n"
            "\t}\n}\n")
    parsed = dcs_halo.parse_halo_presets(text)
    assert [entry.name for entry in parsed] == ["Hidden", "Unlabeled"]
    visible = dcs_halo.visible_presets(parsed)
    assert [entry.name for entry in visible] == ["Unlabeled"]
    assert visible[0].label == "Unlabeled", "the key is not the label of a nameless entry"


# ------------------------------------------------------------------- 2. the ONE path rule, reused

@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_the_picture_path_rule_refuses_a_name_that_leaves_the_installation(tmp_path, hostile):
    """An absolute path, a drive, a UNC lead, a traversal and a non-``.png`` leaf are all refused."""
    node = HaloNode(installation=tmp_path)
    assert dcs_install.preset_image_file(node, hostile) is None, hostile


def test_the_picture_path_rule_resolves_a_stored_name_carrying_a_space(tmp_path):
    """POSITIVE control: a stored name's space is a filename character, not a refusal."""
    node = HaloNode(installation=tmp_path)
    stored = CRYSTAL_THUMBNAILS[CASING_ENTRY]
    assert dcs_install.preset_image_file(node, stored) == f"{Path(tmp_path).as_posix()}/{stored}"


# ---------------------------------------------------------------------------- 3. the list route

def test_both_halo_node_reads_are_declared_under_the_pages_own_capability():
    """A route with no declared capability would be refused by the deny-by-default gate."""
    declared = presets_page.capabilities()
    assert declared[presets_page.PRESETS_HALO_LIST_PATH] == presets_page.PRESETS_CAPABILITY
    assert declared[presets_page.PRESETS_HALO_IMAGE_PATH] == presets_page.PRESETS_CAPABILITY


@pytest.mark.asyncio
async def test_the_list_route_answers_both_lists_and_offers_auto(config_dir, clean_registry,
                                                                 tmp_path):
    """The halo presets (with ``"auto"`` prepended) and the crystal presets, each as its own list."""
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path(NODE))

    assert response.status_code == 200, response.text
    body = response.json()
    assert "notice" not in body, "a readable list carries no notice"
    assert [record["name"] for record in body["presets"]] == ["auto", *HALO_ENTRIES]
    assert body["presets"][0] == {"name": "auto", "readable_name": "auto", "thumbnail": None}
    assert [record["name"] for record in body["crystalsPresets"]] == list(CRYSTAL_ENTRIES)
    assert body["crystalsPresets"][0] == {
        "name": "AllKinds", "readable_name": "All Kinds of Ice Halo",
        "thumbnail": CRYSTAL_THUMBNAILS["AllKinds"]}
    assert all(record["thumbnail"] is None for record in body["presets"][1:])
    assert node.reads == [halo_path(tmp_path)], "the list is a read of the vendor file only"


@pytest.mark.asyncio
async def test_an_absent_list_answers_empty_lists_and_one_sentence(config_dir, clean_registry,
                                                                   tmp_path):
    """A node with no readable vendor file is a NORMAL state, not an error to render."""
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path(NODE))

    assert response.status_code == 200, response.text
    assert response.json() == {"presets": [], "crystalsPresets": [],
                               "notice": presets_page.PRESETS_NO_HALO_LIST.format(
                                   node=NODE, path=halo_path(tmp_path))}


@pytest.mark.asyncio
async def test_an_unparseable_list_answers_the_same_empty_lists_and_sentence(config_dir,
                                                                             clean_registry,
                                                                             tmp_path):
    """A DCS version that moved a key degrades the PICTURES, never the wizard."""
    install_halo(tmp_path, UNPARSEABLE)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path(NODE))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["presets"] == [] and body["crystalsPresets"] == []
    assert body["notice"] == presets_page.PRESETS_NO_HALO_LIST.format(
        node=NODE, path=halo_path(tmp_path))
    assert node.reads == [halo_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_list_route_refuses_a_node_it_cannot_reach(config_dir, clean_registry, tmp_path):
    """An unknown node answers its own sentence, and nothing is read."""
    node = HaloNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path("nope"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_NODE_OFFLINE.format(node="nope")
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_list_route_refuses_one_above_its_cap(config_dir, clean_registry, tmp_path,
                                                        monkeypatch):
    """The cap is real, not decorative: above it the route refuses rather than parse a file."""
    monkeypatch.setattr(dcs_halo, "HALO_LUA_MAX_BYTES", 4)
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path(NODE))

    assert response.status_code == 413, response.text
    assert "above the 4-byte limit" in response.text
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_both_halo_node_reads_refuse_a_caller_without_the_capability(config_dir,
                                                                           clean_registry, tmp_path):
    """``presets.view`` is Admin work: a plain member reaches neither read."""
    install_halo(tmp_path)
    install_plate(tmp_path, CRYSTAL_THUMBNAILS["AllKinds"])
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "plain")
        listing = await http.get(presets_page.halo_list_path(NODE))
        plate = await http.get(presets_page.halo_image_path(NODE, "AllKinds"))

    assert listing.status_code == 403
    assert plate.status_code == 403
    assert node.reads == []


# --------------------------------------------------------------------------- 4. the plate route

@pytest.mark.asyncio
async def test_a_crystal_plate_is_served_from_the_stored_name(config_dir, clean_registry, tmp_path):
    """The bytes come from the node's DCS installation, resolved from the entry's OWN thumbnailName.

    The entry's stored name differs from its key in case, so this also proves the path is not derived
    from the key.
    """
    install_halo(tmp_path)
    data = b"\x89PNG\r\n\x1a\nhalo-plate"
    stored = CRYSTAL_THUMBNAILS[CASING_ENTRY]
    install_plate(tmp_path, stored, data=data)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, CASING_ENTRY))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == dcs_install.PRESET_IMAGE_MEDIA_TYPE
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.content == data, "the bytes served are not the file's own"
    assert node.reads == [halo_path(tmp_path), f"{Path(tmp_path).as_posix()}/{stored}"], \
        "the plate was not resolved inside the install (or a second path was read)"


@pytest.mark.parametrize("name", HOSTILE_URL_NAMES)
@pytest.mark.asyncio
async def test_the_plate_refuses_a_name_that_is_not_a_name_without_asking_the_node(
        config_dir, clean_registry, tmp_path, name):
    """THE PROPERTY THE CLOUDS ROUTE PINS, KEPT HERE.

    A refused name must not have reached the node at all: the assertion on ``reads`` is the point — a
    400 that had already opened a path would look identical in the response. ``icehalo.lua`` is not
    read either: the name is refused before anything is resolved.
    """
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, name))

    assert response.status_code == 400, response.text
    assert response.text.strip() == presets_page.PRESETS_HALO_NAME_UNUSABLE.format(name=name)
    assert node.reads == [], f"a refused name was still asked of the node: {node.reads}"


@pytest.mark.asyncio
async def test_the_plate_refuses_a_preset_the_node_does_not_carry(config_dir, clean_registry,
                                                                  tmp_path):
    """A name neither table carries is refused — and only the list was read."""
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, "NoSuchPreset"))

    assert response.status_code == 400, response.text
    assert response.text.strip() == presets_page.PRESETS_HALO_UNKNOWN.format(node=NODE,
                                                                             name="NoSuchPreset")
    assert node.reads == [halo_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_plate_says_so_when_a_halo_preset_has_no_picture(config_dir, clean_registry,
                                                                   tmp_path):
    """The four halo presets carry no thumbnail at all: asking for their plate says so."""
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, "VolumetricOnly"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_HALO_NO_PICTURE.format(
        node=NODE, name="VolumetricOnly")
    # the list read the vendor file, and NO picture path was ever resolved.
    assert node.reads == [halo_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_plate_says_so_when_the_picture_is_not_in_the_installation(config_dir,
                                                                             clean_registry,
                                                                             tmp_path):
    """A missing file answers its own sentence, never a zero-byte 200 that renders as a frame."""
    install_halo(tmp_path)
    stored = CRYSTAL_THUMBNAILS["AllKinds"]
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, "AllKinds"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_PICTURE_MISSING.format(
        thumbnail=stored, path=f"{Path(tmp_path).as_posix()}/{stored}")
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_the_plate_refuses_one_above_its_cap(config_dir, clean_registry, tmp_path,
                                                   monkeypatch):
    """The picture cap is the SAME constant the cloud plate route reads."""
    monkeypatch.setattr(dcs_install, "PRESET_IMAGE_MAX_BYTES", 4)
    install_halo(tmp_path)
    install_plate(tmp_path, CRYSTAL_THUMBNAILS["AllKinds"], data=b"12345")
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path(NODE, "AllKinds"))

    assert response.status_code == 413, response.text
    assert "above the 4-byte limit" in response.text
    assert Path(tmp_path).as_posix() in response.text, response.text


@pytest.mark.asyncio
async def test_the_plate_refuses_a_node_it_cannot_reach(config_dir, clean_registry, tmp_path):
    """An unknown node answers its own sentence, and nothing is read."""
    node = HaloNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_image_path("nope", "AllKinds"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_NODE_OFFLINE.format(node="nope")
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_node_name_in_the_url_is_matched_case_insensitively(config_dir, clean_registry,
                                                                      tmp_path):
    """The SAME fallback the per-server log routes use."""
    install_halo(tmp_path)
    node = HaloNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.halo_list_path(NODE.upper()))

    assert response.status_code == 200, response.text
    assert len(response.json()["crystalsPresets"]) == 6
