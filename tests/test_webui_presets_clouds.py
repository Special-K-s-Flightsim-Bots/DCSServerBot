"""The Presets area's node reads: the cloud-preset list and the plates.

One section per acceptance criterion of the card:

* the parser walks the OWNER'S REAL ``clouds.lua`` (``tests/fixtures/clouds.lua``): 45 entries, 34
  visible, the 11 hidden ones (``default``, ``clouds01``…``clouds10``) excluded, ``Preset1``'s short
  name and thumbnail read out, and its Lua ``readableName`` unwrapped and unescaped;
* an entry's OWN altitudes are read beside its scalars — ``presetAltMin``/``presetAltMax`` and its
  ``layers`` rows, in metres — and the ``layers`` table nested inside the entry contributes none of
  its scalars to it;
* an absent, empty or unparseable file yields NO entries and never raises;
* the shape refusals are tested over the SAME cases the existing thumbnail route refuses, with a
  POSITIVE control (a benign relative ``.png`` still resolves);
* ``GET /presets/clouds/<node>.json`` answers that node's own list, and an unreadable one answers an
  EMPTY list plus one sentence with HTTP 200;
* ``GET /presets/clouds/<node>/<name>.png`` serves the entry's own thumbnail out of the installation;
* THE PROPERTY THIS CARD EXISTS TO KEEP: the plate route never becomes a general file reader — the
  node double RECORDS every path it is asked for, and a refused name must have asked for NOTHING;
* every failure is its own plain sentence, and both routes answer 403 for a caller without
  ``presets.view``.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused so the app under test is the running one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.webservice import dcs_clouds, dcs_install
from services.webservice.pages import presets as presets_page
# the write suites' fixtures and harness, reused so this module drives a REAL page through the app
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, sign_in,
)

#: THE OWNER'S REAL FILE, attached to the card and copied here as the fixture.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "clouds.lua"

NODE = "dcs-a-01"

#: one backslash, by codepoint — a hostile name may use either separator, and a literal backslash in
#: this file would read as an escape to anyone skimming the list.
BACKSLASH = chr(92)

#: the thumbnail the fixture's ``Preset1`` names, relative to the DCS installation root.
THUMBNAIL = "Bazar/Effects/Clouds/Thumbnails/cloud_1.png"

#: the hidden entries the fixture carries — never offered.
HIDDEN = frozenset({"default"}) | frozenset(f"clouds{index:02d}" for index in range(1, 11))

#: names that must be REFUSED on their SHAPE, before anything is asked of the node. The same cases
#: the existing per-server thumbnail route refuses.
HOSTILE_NAMES = (
    "/etc/passwd.png",
    "../../../etc/passwd.png",
    "Bazar/../../../etc/passwd.png",
    "C:/Windows/Web/Wallpaper.png",
    f"{BACKSLASH}{BACKSLASH}server{BACKSLASH}share{BACKSLASH}cloud.png",
    f"{BACKSLASH}etc{BACKSLASH}passwd.png",
    "Bazar/Effects/Clouds/clouds.lua",
    "Bazar/Effects/Thumbnails/../secret.png",
    "",
)

#: the same rule as it reaches the PLATE URL: a name that is not one plain segment. A name carrying
#: ``/`` cannot travel in one path segment at all, so it is covered by the unit test above.
HOSTILE_URL_NAMES = ("..", "a..b", f"..{BACKSLASH}..", f"{BACKSLASH}etc")

#: a file whose ONE visible preset carries no picture at all.
NO_THUMBNAIL = """\
clouds = 
{
\tpresets = 
\t{
\t\tPlain =
\t\t{
\t\t\tvisibleInGUI = true,
\t\t\treadableName = _('No Plate Here'),
\t\t\treadableNameShort = _('NoPlate'),
\t\t\tthumbnailName = '',
\t\t},
\t}
}
"""

#: a file that carries no preset table this console recognizes.
UNPARSEABLE = "cloudsTechnique = 0\n"


def fixture_text() -> str:
    """The owner's file, read as text."""
    return FIXTURE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------- test doubles

class CloudsNode:
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


def clouds_path(root) -> str:
    """The vendor file's path ON a node whose installation is *root*, FORWARD-slashed.

    ``dcs_install._join`` states a node path with forward slashes on every host (it crosses JSON and
    URLs), so this expectation is not written with ``os.sep``.
    """
    return f"{Path(root).as_posix()}/{dcs_clouds.CLOUDS_LUA_RELATIVE_PATH}"


def install_clouds(root, text: str | None = None) -> Path:
    """Write the vendor file under *root* and return its path."""
    target = Path(root) / "Config" / "Effects" / "clouds.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fixture_text() if text is None else text, encoding="utf-8")
    return target


def install_plate(root, name: str = THUMBNAIL, data: bytes = b"\x89PNG\r\n\x1a\nplate") -> Path:
    """Write the plate *name* names under *root* and return its path."""
    target = Path(root) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def app_for(config_dir, node):
    """A real application whose only node is *node*."""
    return build(config_dir, Cluster(nodes={node.name: node}, node=node))


# ------------------------------------------------------------------------------- 1. the parser

def test_the_real_vendor_file_parses_into_45_entries_and_34_visible_ones():
    """The counts the owner's own file carries — read, not assumed."""
    presets = dcs_clouds.parse_cloud_presets(fixture_text())
    assert len(presets) == 45, "the file's own entry count"
    visible = dcs_clouds.visible_presets(presets)
    assert len(visible) == 34, "the entries the picker may offer"
    assert {preset.name for preset in presets if not preset.visible} == HIDDEN
    assert not HIDDEN & {preset.name for preset in visible}


def test_preset1_carries_its_short_name_its_thumbnail_and_its_unwrapped_readable_name():
    """The card's own fixture assertions, in full.

    ``readableName`` is Lua: a quoted marker concatenated with a gettext-wrapped, escaped string. The
    readable name is the gettext text — the ``'01 ##'`` marker is NOT part of it — with ``\\n``
    resolved to a real newline.
    """
    presets = dcs_clouds.visible_presets(dcs_clouds.parse_cloud_presets(fixture_text()))
    preset = next(entry for entry in presets if entry.name == "Preset1")
    assert preset.name == "Preset1"
    assert preset.readable_name_short == "Light Scattered 1"
    assert preset.readable_name == "Few Scattered Clouds \nMETAR: FEW/SCT 7/8"
    assert "\\n" not in preset.readable_name, "the Lua escape was not resolved"
    assert not preset.readable_name.startswith("01"), "the '01 ##'.._( wrapper was not stripped"
    assert preset.thumbnail == THUMBNAIL
    assert preset.label == preset.readable_name
    assert preset.item() == {"name": "Preset1", "readable_name": preset.readable_name,
                             "thumbnail": THUMBNAIL}


def test_the_readable_name_is_never_the_url_key():
    """The KEY is the name a preset stores; the readable names are neither unique nor URL-safe.

    Three visible entries in the owner's file share the readable name "Three Layer Overcast", and the
    METAR text carries ``/`` — so a URL keyed by the readable name could name two different entries or
    none at all. The parsed entry therefore carries the key as ``name``.
    """
    presets = dcs_clouds.visible_presets(dcs_clouds.parse_cloud_presets(fixture_text()))
    labels = [preset.label for preset in presets]
    assert len(set(labels)) < len(labels), "the fixture's readable names collide"
    assert all("/" in label or " " in label for label in labels)
    assert [preset.name for preset in presets[:2]] == ["Preset1", "Preset2"]


def test_an_absent_empty_or_unparseable_file_yields_no_entries_and_never_raises():
    """A defensive read: the vendor file is UNTRUSTED, POSSIBLY ABSENT and version-dependent."""
    for text in ("", UNPARSEABLE, "clouds = {\n\tsomething = 1,\n}",
                 "clouds =\n{\n\tpresets =\n\t{\n\t}\n}"):
        assert dcs_clouds.parse_cloud_presets(text) == [], repr(text)


def test_a_table_of_hidden_entries_offers_nothing():
    """``visibleInGUI = false`` is the vendor file's own "do not offer this" flag."""
    text = ("clouds =\n{\n\tpresets =\n\t{\n\t\tdefault =\n\t\t{\n\t\t\tvisibleInGUI = false,\n"
            "\t\t\treadableName = '',\n\t\t\tthumbnailName = '',\n\t\t},\n\t}\n}\n")
    parsed = dcs_clouds.parse_cloud_presets(text)
    assert [entry.name for entry in parsed] == ["default"]
    assert dcs_clouds.visible_presets(parsed) == []


def test_a_visible_entry_without_a_readable_name_is_offered_under_its_key():
    """H2: a visible entry is NEVER dropped for want of a label — its own KEY is its label then.

    The key is what ``clouds.preset`` stores and what the plate URL needs, so an entry the vendor ships
    with no readable name must still reach the picker. A hidden entry stays excluded, and an entry with
    no picture is listed without one.
    """
    text = ("clouds =\n{\n\tpresets =\n\t{\n"
            "\t\tUnlabeled =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n"
            "\t\t\tthumbnailName = 'Bazar/x.png',\n"
            "\t\t},\n"
            "\t\tPlain =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n"
            "\t\t\treadableName = _('No Plate'),\n"
            "\t\t\tthumbnailName = '',\n"
            "\t\t},\n"
            "\t\tHidden =\n\t\t{\n"
            "\t\t\tvisibleInGUI = false,\n"
            "\t\t\treadableName = _('Hidden'),\n"
            "\t\t},\n"
            "\t}\n}\n")
    visible = dcs_clouds.visible_presets(dcs_clouds.parse_cloud_presets(text))
    assert [entry.name for entry in visible] == ["Unlabeled", "Plain"]
    assert visible[0].label == "Unlabeled", "the key is not the label of a nameless entry"
    assert visible[0].item() == {"name": "Unlabeled", "readable_name": "Unlabeled",
                                 "thumbnail": "Bazar/x.png"}
    assert visible[1].item() == {"name": "Plain", "readable_name": "No Plate", "thumbnail": None}
    assert "Hidden" not in [entry.name for entry in visible]


def test_a_table_nested_inside_an_entry_never_lends_it_its_scalars():
    """H3: only an entry's OWN scalar fields are read — a table nested inside it contributes none.

    A ``presets`` table nested INSIDE an entry carries its own ``readableName``/``thumbnailName`` at a
    deeper indent; the walker must not fold them into the enclosing entry.
    """
    text = ("clouds =\n{\n\tpresets =\n\t{\n"
            "\t\tOuter =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n"
            "\t\t\treadableName = _('Outer Name'),\n"
            "\t\t\tpresets =\n\t\t\t{\n"
            "\t\t\t\tInner =\n\t\t\t\t{\n"
            "\t\t\t\t\treadableName = _('Inner Name'),\n"
            "\t\t\t\t\tthumbnailName = 'Bazar/inner.png',\n"
            "\t\t\t\t},\n"
            "\t\t\t},\n"
            "\t\t\tthumbnailName = 'Bazar/outer.png',\n"
            "\t\t},\n"
            "\t}\n}\n")
    outer = dcs_clouds.parse_cloud_presets(text)[0]
    assert outer.name == "Outer"
    assert outer.readable_name == "Outer Name", "the nested table's readableName leaked"
    assert outer.thumbnail == "Bazar/outer.png", "the nested table's thumbnailName leaked"


def test_a_quoted_entry_key_is_read_as_the_key():
    """H4: ``["Name"] =`` — a quoted Lua key — is an entry key, not an invisible line.

    A quoted key the walker cannot see would make the entry unservable while its fields silently fed
    the previous entry; it is read as the key instead.
    """
    text = ("clouds =\n{\n\tpresets =\n\t{\n"
            "\t\t[\"Quoted Name\"] =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n"
            "\t\t\treadableName = _('Quoted'),\n"
            "\t\t\tthumbnailName = 'Bazar/q.png',\n"
            "\t\t},\n"
            "\t}\n}\n")
    parsed = dcs_clouds.parse_cloud_presets(text)
    assert [entry.name for entry in parsed] == ["Quoted Name"]
    assert parsed[0].visible is True
    assert parsed[0].item() == {"name": "Quoted Name", "readable_name": "Quoted",
                                "thumbnail": "Bazar/q.png"}


def test_preset1_exposes_its_own_layer_altitudes_and_its_preset_altitude_figures():
    """The reader hands over a preset's OWN altitudes, in metres, as the file spells them.

    ``Preset1`` carries three layers (2520/3780, 5040/6300, 10500/12180) and
    ``presetAltMin``/``presetAltMax`` = 840/4200. The preset's default cloud base is the LOWEST layer
    altitude — 2520 m, which the Mission Editor shows as 8268 ft. The existing record is untouched:
    the altitudes are ADDED fields, so ``item()`` still carries the same three keys.
    """
    preset = next(entry for entry in dcs_clouds.parse_cloud_presets(fixture_text())
                  if entry.name == "Preset1")
    assert preset.preset_alt_min == 840.0
    assert preset.preset_alt_max == 4200.0
    assert preset.layers == ((2520.0, 3780.0), (5040.0, 6300.0), (10500.0, 12180.0))
    base = preset.base
    assert base is not None
    assert base == 2520.0
    assert round(base * 3.28084) == 8268, "the Mission Editor's own figure for this preset"
    assert set(preset.item()) == {"name", "readable_name", "thumbnail"}


def test_a_preset_without_a_layers_table_answers_no_altitudes():
    """A vendor entry that carries no layer answers ``None``/``()`` — never a guessed zero."""
    text = ("clouds =\n{\n\tpresets =\n\t{\n\t\tBare =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n\t\t\treadableName = _('Bare'),\n"
            "\t\t\tthumbnailName = '',\n\t\t},\n\t}\n}\n")
    preset = dcs_clouds.parse_cloud_presets(text)[0]
    assert preset.layers == ()
    assert preset.base is None
    assert preset.preset_alt_min is None
    assert preset.preset_alt_max is None


def test_only_the_layers_table_lends_its_altitudes():
    """The ``layers`` table is read by NAME: another nested table's altitudes never reach the entry."""
    text = ("clouds =\n{\n\tpresets =\n\t{\n\t\tOuter =\n\t\t{\n"
            "\t\t\tvisibleInGUI = true,\n\t\t\treadableName = _('Outer'),\n"
            "\t\t\tthumbnailName = '',\n"
            "\t\t\tother =\n\t\t\t{\n\t\t\t\taltitudeMin = 100,\n\t\t\t\taltitudeMax = 200,\n\t\t\t},\n"
            "\t\t\tlayers =\n\t\t\t{\n\t\t\t\t{\n"
            "\t\t\t\t\taltitudeMin = 300,\n\t\t\t\t\taltitudeMax = 400,\n\t\t\t\t},\n\t\t\t},\n"
            "\t\t},\n\t}\n}\n")
    preset = dcs_clouds.parse_cloud_presets(text)[0]
    assert preset.layers == ((300.0, 400.0),)
    assert preset.base == 300.0


def test_the_hidden_entries_keep_their_own_altitudes_too():
    """A hidden entry is parsed like any other: its layers are read even though it is never offered."""
    preset = next(entry for entry in dcs_clouds.parse_cloud_presets(fixture_text())
                  if entry.name == "default")
    assert preset.visible is False
    assert preset.preset_alt_min == -1.0 and preset.preset_alt_max == -1.0
    assert preset.layers == ((938.0, 2500.0), (5000.0, 5938.0), (3438.0, 4688.0))


# ------------------------------------------------------------------- 2. the ONE path rule, reused

@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_the_picture_path_rule_refuses_a_name_that_leaves_the_installation(tmp_path, hostile):
    """An absolute path, a drive, a UNC lead, a traversal and a non-``.png`` leaf are all refused."""
    node = CloudsNode(installation=tmp_path)
    assert dcs_install.preset_image_file(node, hostile) is None, hostile


def test_the_picture_path_rule_resolves_a_benign_relative_png(tmp_path):
    """POSITIVE control: the refusals above are the shape rule, not "every name fails"."""
    node = CloudsNode(installation=tmp_path)
    assert dcs_install.preset_image_file(node, THUMBNAIL) == \
        f"{Path(tmp_path).as_posix()}/{THUMBNAIL}"
    dotted = dcs_install.preset_image_file(node, "Bazar/./Effects/Clouds/Thumbnails/cloud_2.png")
    assert dotted == f"{Path(tmp_path).as_posix()}/Bazar/Effects/Clouds/Thumbnails/cloud_2.png"


def test_the_picture_path_rule_refuses_a_node_with_no_installation_root(tmp_path):
    """A node that has not reported its installation is refused rather than guessed at."""
    assert dcs_install.installation_root(CloudsNode(installation=None)) == ""
    assert dcs_install.preset_image_file(CloudsNode(installation=None), THUMBNAIL) is None


def test_the_installation_file_rule_joins_forward_slashes_and_refuses_escapes(tmp_path):
    """The clouds.lua path is built by the SAME rule, and cannot be walked out of either."""
    node = CloudsNode(installation=tmp_path)
    assert dcs_install.installation_file(node, dcs_clouds.CLOUDS_LUA_RELATIVE_PATH) == \
        clouds_path(tmp_path)
    for escape in ("/etc/passwd", "../../clouds.lua", "", f"C:{BACKSLASH}clouds.lua"):
        assert dcs_install.installation_file(node, escape) == "", escape
    assert dcs_install.installation_file(CloudsNode(installation=None),
                                         dcs_clouds.CLOUDS_LUA_RELATIVE_PATH) == ""


# ---------------------------------------------------------------------------- 3. the list route

def test_both_node_reads_are_declared_under_the_pages_own_capability():
    """A route with no declared capability would be refused by the deny-by-default gate."""
    declared = presets_page.capabilities()
    assert declared[presets_page.PRESETS_CLOUDS_LIST_PATH] == presets_page.PRESETS_CAPABILITY
    assert declared[presets_page.PRESETS_CLOUD_IMAGE_PATH] == presets_page.PRESETS_CAPABILITY


@pytest.mark.asyncio
async def test_the_list_route_answers_the_nodes_own_cloud_presets(config_dir, clean_registry,
                                                                 tmp_path):
    """One record per VISIBLE entry, in file order; the thumbnail is passed through unchanged."""
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path(NODE))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["presets"]) == 34
    assert "notice" not in body, "a readable list carries no notice"
    assert body["presets"][0] == {"name": "Preset1",
                                  "readable_name": "Few Scattered Clouds \nMETAR: FEW/SCT 7/8",
                                  "thumbnail": THUMBNAIL}
    assert not HIDDEN & {record["name"] for record in body["presets"]}
    assert node.reads == [clouds_path(tmp_path)], "the list is a read of the vendor file only"


@pytest.mark.asyncio
async def test_an_absent_list_answers_an_empty_list_and_one_sentence(config_dir, clean_registry,
                                                                    tmp_path):
    """A node with no readable vendor file is a NORMAL state, not an error to render."""
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path(NODE))

    assert response.status_code == 200, response.text
    assert response.json() == {"presets": [],
                               "notice": presets_page.PRESETS_NO_CLOUD_LIST.format(
                                   node=NODE, path=clouds_path(tmp_path))}


@pytest.mark.asyncio
async def test_an_unparseable_list_answers_the_same_empty_list_and_sentence(config_dir,
                                                                           clean_registry, tmp_path):
    """A DCS version that moved a key degrades the PICTURES, never the wizard."""
    install_clouds(tmp_path, UNPARSEABLE)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path(NODE))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["presets"] == []
    assert body["notice"] == presets_page.PRESETS_NO_CLOUD_LIST.format(
        node=NODE, path=clouds_path(tmp_path))
    assert node.reads == [clouds_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_list_route_refuses_a_node_it_cannot_reach(config_dir, clean_registry, tmp_path):
    """An unknown node answers its own sentence, and nothing is read."""
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path("nope"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_NODE_OFFLINE.format(node="nope")
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_list_route_refuses_one_above_its_cap(config_dir, clean_registry, tmp_path,
                                                        monkeypatch):
    """The cap is real, not decorative: above it the route refuses rather than parse a file."""
    monkeypatch.setattr(dcs_clouds, "CLOUDS_LUA_MAX_BYTES", 4)
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path(NODE))

    assert response.status_code == 413, response.text
    assert "above the 4-byte limit" in response.text
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_both_node_reads_refuse_a_caller_without_the_capability(config_dir, clean_registry,
                                                                      tmp_path):
    """``presets.view`` is Admin work: a plain member reaches neither read."""
    install_clouds(tmp_path)
    install_plate(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "plain")
        listing = await http.get(presets_page.clouds_list_path(NODE))
        plate = await http.get(presets_page.cloud_image_path(NODE, "Preset1"))

    assert listing.status_code == 403
    assert plate.status_code == 403
    assert node.reads == []


# --------------------------------------------------------------------------- 4. the plate route

@pytest.mark.asyncio
async def test_the_plate_is_served_out_of_the_nodes_installation(config_dir, clean_registry,
                                                                 tmp_path):
    """The bytes come from the node's DCS installation, resolved from the entry's own thumbnail."""
    install_clouds(tmp_path)
    data = b"\x89PNG\r\n\x1a\npreset-plate"
    install_plate(tmp_path, data=data)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "Preset1"))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == dcs_install.PRESET_IMAGE_MEDIA_TYPE
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.content == data, "the bytes served are not the file's own"
    assert node.reads == [clouds_path(tmp_path), f"{Path(tmp_path).as_posix()}/{THUMBNAIL}"], \
        "the plate was not resolved inside the install (or a second path was read)"


@pytest.mark.parametrize("name", HOSTILE_URL_NAMES)
@pytest.mark.asyncio
async def test_the_plate_refuses_a_name_that_is_not_a_name_without_asking_the_node(
        config_dir, clean_registry, tmp_path, name):
    """THE PROPERTY THIS CARD EXISTS TO KEEP.

    A refused name must not have reached the node at all: the assertion on ``reads`` is the point —
    a 400 that had already opened a path would look identical in the response. ``clouds.lua`` is not
    read either: the name is refused before anything is resolved.
    """
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, name))

    assert response.status_code == 400, response.text
    assert response.text.strip() == presets_page.PRESETS_CLOUD_NAME_UNUSABLE.format(name=name)
    assert node.reads == [], f"a refused name was still asked of the node: {node.reads}"


@pytest.mark.asyncio
async def test_the_plate_refuses_a_preset_the_node_does_not_carry(config_dir, clean_registry,
                                                                 tmp_path):
    """A name the node's own list does not carry is refused — and only the list was read."""
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "NoSuchPreset"))

    assert response.status_code == 400, response.text
    assert response.text.strip() == presets_page.PRESETS_CLOUD_UNKNOWN.format(node=NODE,
                                                                             name="NoSuchPreset")
    assert node.reads == [clouds_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_plate_refuses_a_hidden_preset_the_picker_never_offered(config_dir,
                                                                         clean_registry, tmp_path):
    """``clouds01`` is in the vendor file but hidden: it is not a preset this console offers."""
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "clouds01"))

    assert response.status_code == 400, response.text
    assert node.reads == [clouds_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_plate_says_so_when_the_picture_is_not_in_the_installation(config_dir,
                                                                             clean_registry,
                                                                             tmp_path):
    """A missing file answers its own sentence, never a zero-byte 200 that renders as a frame."""
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "Preset1"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_PICTURE_MISSING.format(
        thumbnail=THUMBNAIL, path=f"{Path(tmp_path).as_posix()}/{THUMBNAIL}")
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_the_plate_says_so_when_there_is_no_readable_list(config_dir, clean_registry,
                                                               tmp_path):
    """The card's NEW sentence: a vendor path that cannot be resolved refuses plainly."""
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "Preset1"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_NO_CLOUD_LIST.format(
        node=NODE, path=clouds_path(tmp_path))
    assert node.reads == [clouds_path(tmp_path)]


@pytest.mark.asyncio
async def test_a_preset_without_a_picture_is_listed_but_has_no_plate(config_dir, clean_registry,
                                                                     tmp_path):
    """Listed without a picture (the mockup's "no picture" band); asking for the plate says so."""
    install_clouds(tmp_path, NO_THUMBNAIL)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        listing = await http.get(presets_page.clouds_list_path(NODE))
        plate = await http.get(presets_page.cloud_image_path(NODE, "Plain"))

    assert listing.json() == {"presets": [{"name": "Plain", "readable_name": "No Plate Here",
                                           "thumbnail": None}]}
    assert plate.status_code == 404, plate.text
    assert plate.text.strip() == presets_page.PRESETS_CLOUD_NO_PICTURE.format(node=NODE, name="Plain")
    # NO picture path was ever resolved (the entry carries no thumbnail), and the vendor file was read
    # ONCE: the plate route reuses the list the list route has already read (``presets``' node-list
    # cache), so a step of N plates does not read the list N times.
    assert node.reads == [clouds_path(tmp_path)]


@pytest.mark.asyncio
async def test_the_plate_refuses_one_above_its_cap(config_dir, clean_registry, tmp_path,
                                                   monkeypatch):
    """The picture cap is the SAME constant the server page's thumbnail route reads."""
    monkeypatch.setattr(dcs_install, "PRESET_IMAGE_MAX_BYTES", 4)
    install_clouds(tmp_path)
    install_plate(tmp_path, data=b"12345")
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path(NODE, "Preset1"))

    assert response.status_code == 413, response.text
    assert "above the 4-byte limit" in response.text
    assert Path(tmp_path).as_posix() in response.text, response.text


@pytest.mark.asyncio
async def test_the_plate_refuses_a_node_it_cannot_reach(config_dir, clean_registry, tmp_path):
    """An unknown node answers its own sentence, and nothing is read."""
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.cloud_image_path("nope", "Preset1"))

    assert response.status_code == 404, response.text
    assert response.text.strip() == presets_page.PRESETS_NODE_OFFLINE.format(node="nope")
    assert node.reads == []


@pytest.mark.asyncio
async def test_the_node_name_in_the_url_is_matched_case_insensitively(config_dir, clean_registry,
                                                                      tmp_path):
    """The SAME fallback the per-server log routes use."""
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    async with client(app_for(config_dir, node)) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.clouds_list_path(NODE.upper()))

    assert response.status_code == 200, response.text
    assert len(response.json()["presets"]) == 34
