"""The launch dialogs' preset pick: one group per preset FILE the node carries.

The pick reads the NODE's own preset files — every file ``presets*.yaml`` or ``presets/*.yaml`` under
its config dir — through ``presets.node_preset_files``, and draws each file's names under an
``<optgroup>`` labelled with the file's ``config/…`` name. The server's own ``MizEdit.presets``
setting does NOT narrow that list: an operator must be able to pick from any preset file the node
carries, and the launch carries the preset's own document.

One section per acceptance criterion:

1. THE READ — every listed file, its ``config/…`` label, its names in file order; an ``int`` answer is
   a failure code, and an unreadable file never hides the others;
2. THE RENDER — two files draw two groups, the console's own two entries stay first and outside every
   group, and an instance naming one file still sees the other files' presets;
3. ONE FILE draws no heading, a name TWO files share is drawn once PER FILE that carries it (each
   entry standing for its own file's body), and the grouping sentence is drawn in both shapes;
4. THE CACHE — a second render re-lists but parses nothing, a file rewritten outside the console
   reaches the very next open, and a failed read is not remembered;
5. THE FALLBACK — a node that cannot be listed keeps the free-text box, and BOTH dialogs draw the same
   grouped pick.
"""
from __future__ import annotations

import html
import os
import re
import types
from pathlib import Path

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from test_webui_missions_load_form import (  # noqa: F401 - fixtures imported on purpose
    LoadNode, load_fleet, open_load_dialog,
)
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    CONFIRM, STARTUP, PresetNode, open_dialog, open_page, pick_block, pick_values,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

PRESET_FIELD = actions_page.PRESET_FIELD
CUSTOM = actions_page.PRESET_CUSTOM_VALUE
WINTER = "config/presets_winter.yaml"
FLAT = "config/presets.yaml"

#: two names, one explicitly hidden.
WINTER_YAML = """\
Winter_Clear_Morning:
  modify:
    weather:
      clouds:
        base: 1200
Winter_Storm:
  modify:
    weather:
      clouds:
        base: 400
Hidden:
  hidden: true
  modify:
    weather:
      clouds:
        base: 100
"""

#: a second file the FIRST name is repeated in, plus a combination (a LIST is a preset too).
FLAT_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
Winter_Clear_Morning:
  modify:
    weather:
      clouds:
        base: 900
Mixed:
  - Summer
  - Winter_Storm
"""


def files(config_dir, *, flat: str | None = FLAT_YAML,
          winter: str | None = WINTER_YAML) -> dict:
    """Write the node's two preset files (the flat one and one beside it); ``None`` skips one."""
    paths = {"flat": config_dir / "presets.yaml",
             "winter": config_dir / "presets_winter.yaml"}
    for key, body in (("flat", flat), ("winter", winter)):
        if body is None:
            continue
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths[key].write_text(body, encoding="utf-8")
    #: the node wire speaks posix: the pick and the double must key their paths the same way
    return {key: path.as_posix() for key, path in paths.items()}


@pytest.fixture(autouse=True)
def forget_lists():
    """The node-list cache is PROCESS state: start and end every test with nothing remembered."""
    presets_page.forget_node_lists()
    try:
        yield
    finally:
        presets_page.forget_node_lists()


class QuietNode(PresetNode):
    """A node double that RECORDS what it was asked to list and to read."""

    def __init__(self, config_dir, *, refuse=(), fail=(), **kwargs):
        super().__init__(config_dir, **kwargs)
        self.reads: list[str] = []
        self.listings: list[str] = []
        #: paths answered with an ``int`` (a failure CODE) rather than content
        self.refuse = {str(path).replace("\\", "/") for path in refuse}
        #: paths whose read RAISES
        self.fail = {str(path).replace("\\", "/") for path in fail}

    async def list_files(self, path, *, pattern="*"):
        self.listings.append(path)
        return await super().list_files(path, pattern=pattern)

    async def read_file(self, path):
        self.reads.append(path)
        if path in self.fail:
            raise PermissionError("the node said no")
        if path in self.refuse:
            return 404
        return await super().read_file(path)

    def names(self) -> list[str]:
        """The files the pick PARSED, by base name, in the order it asked for them."""
        return [path.rsplit("/", 1)[-1] for path in self.reads if path.endswith(".yaml")]


def preset_server(name, node, *, presets=None, status=Status.SHUTDOWN):
    """A server double on *node* whose MizEdit block names *presets* (``None`` = names none)."""
    server = FakeServer(name, status=status, managed_by=["Host A"])
    server.node = node
    server.instance = types.SimpleNamespace(name=f"{name}-Instance")
    if presets is not None:
        server.locals["extensions"] = {presets_page.MIZEDIT_NAME: {"presets": presets}}  # type: ignore[assignment]
    return server


def fleet(node, *servers):
    """A cluster of *servers* on ONE *node*, each mapped to its own instance."""
    instances = [instance(server.instance.name, server) for server in servers]
    node_view = types.SimpleNamespace(name=node.name, master=True,
                                      instances={entry.name: entry for entry in instances})
    return Cluster(list(servers), nodes={node.name: node_view}, node=node_view,
                   instances=instances)


def one_server_fleet(node, **kwargs):
    """ONE shutdown server on *node* — the Startup dialog's own shape."""
    return fleet(node, preset_server("SRS-1", node, **kwargs))


def presets_page_node(tmp_path, **kwargs) -> QuietNode:
    """A recording node over *tmp_path* — the config dir the preset files were written under."""
    return QuietNode(tmp_path, **kwargs)


def optgroups(block: str) -> list[tuple[str, list[str]]]:
    """Every ``<optgroup>`` of the pick as ``(label, [names])``, in the order they are drawn.

    A grouped entry's value carries its own file; the NAME is what the heading offers.
    """
    separator = actions_page.PRESET_PAIR_SEPARATOR
    groups = []
    for label, body in re.findall(r'<optgroup label="([^"]*)">(.*?)</optgroup>', block, re.S):
        names = [value.split(separator, 1)[1] if separator in value else value
                 for value in re.findall(r'<option value="([^"]*)"', body)]
        groups.append((html.unescape(label), names))
    return groups


def ungrouped(block: str) -> list[str]:
    """Every entry drawn OUTSIDE a group, in order — the console's own entries live here."""
    without = re.sub(r'<optgroup label="[^"]*">.*?</optgroup>', "", block, flags=re.S)
    return re.findall(r'<option value="([^"]*)"', without)


# --------------------------------------------------------------- 1. the read itself

@pytest.mark.asyncio
async def test_the_read_lists_every_preset_file_with_its_own_names(tmp_path):
    """Two files: two records, each labelled by the config tree and carrying its own names in order."""
    files(tmp_path)
    records, readable = await presets_page.node_preset_files(presets_page_node(tmp_path))

    assert readable is True
    assert [label for label, _path, _names in records] == [FLAT, WINTER], records
    assert [path for _label, path, _names in records] == \
        [(tmp_path / "presets.yaml").as_posix(), (tmp_path / "presets_winter.yaml").as_posix()]
    assert records[0][2] == ["Summer", "Winter_Clear_Morning", "Mixed"], records[0][2]
    assert records[1][2] == ["Winter_Clear_Morning", "Winter_Storm"], \
        "a hidden preset is not offered, and the file's own order is kept"


@pytest.mark.asyncio
async def test_a_file_answered_with_an_int_is_a_failure_code_not_content(tmp_path):
    """``read_file`` answers ``bytes | int``: an ``int`` contributes nothing and hides no other file."""
    paths = files(tmp_path)
    node = presets_page_node(tmp_path, refuse=[paths["flat"]])

    records, readable = await presets_page.node_preset_files(node)

    assert [label for label, _path, _names in records] == [WINTER], records
    assert readable is True, "one unreadable file must not turn the whole node into free text"


@pytest.mark.asyncio
async def test_one_unreadable_file_leaves_the_others_readable(tmp_path):
    """A read that RAISES is skipped; the rest of the node's preset files are still offered."""
    paths = files(tmp_path)
    node = presets_page_node(tmp_path, fail=[paths["winter"]])

    records, readable = await presets_page.node_preset_files(node)

    assert [label for label, _path, _names in records] == [FLAT], records
    assert readable is True


@pytest.mark.asyncio
async def test_every_file_failing_is_the_free_text_answer(tmp_path):
    """No name could be read at all: the caller is told, so it draws its typed box."""
    paths = files(tmp_path)
    node = presets_page_node(tmp_path, fail=[paths["flat"], paths["winter"]])

    records, readable = await presets_page.node_preset_files(node)

    assert records == [] and readable is False


# ------------------------------------------------------------------- 2. the rendered pick

@pytest.mark.asyncio
async def test_two_preset_files_render_two_groups(config_dir, clean_registry):
    """(a) Two files on one node draw two ``<optgroup>``s, each with that file's names in order."""
    files(config_dir)
    app = build(config_dir, one_server_fleet(QuietNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert optgroups(block) == [(FLAT, ["Summer", "Winter_Clear_Morning", "Mixed"]),
                                (WINTER, ["Winter_Clear_Morning", "Winter_Storm"])], \
        "the pick did not draw one group per preset file, each with its own names"
    # the console's own choices are NOT entries any more: they are the mode radios above the pick
    assert ungrouped(block) == [], ungrouped(block)
    assert block.count(f'name="{PRESET_FIELD}"') == 1, "the pick must carry the field ONE time"


@pytest.mark.asyncio
async def test_an_instance_naming_one_file_still_shows_the_other_files_presets(config_dir,
                                                                              clean_registry):
    """(b) The server's ``presets`` setting does NOT narrow the pick: the NODE's files are offered."""
    paths = files(config_dir)
    node = QuietNode(config_dir)
    app = build(config_dir, one_server_fleet(node, presets=paths["winter"]))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    labels = [label for label, _values in optgroups(pick_block(dialog.text))]
    assert labels == [FLAT, WINTER], \
        "a server naming one file must still be offered the node's other preset files"


ALPINE_YAML = """\
Alpine_Dawn:
  modify:
    weather:
      clouds:
        base: 3000
"""


@pytest.mark.asyncio
async def test_an_unreadable_file_leaves_the_other_groups_intact(config_dir, clean_registry):
    """(c) One file refuses to load: its group is absent, the others are drawn with their names."""
    files(config_dir)
    (config_dir / "presets_alpine.yaml").write_text(ALPINE_YAML, encoding="utf-8")
    node = QuietNode(config_dir, fail=[str(config_dir / "presets_alpine.yaml")])
    app = build(config_dir, one_server_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert optgroups(block) == [(FLAT, ["Summer", "Winter_Clear_Morning", "Mixed"]),
                                (WINTER, ["Winter_Clear_Morning", "Winter_Storm"])], \
        "one unreadable preset file hid the other files' groups"
    assert "Alpine_Dawn" not in block
    assert ungrouped(block) == []


# --------------------------------------------------- 3. one file, a shared name, the copy

@pytest.mark.asyncio
async def test_one_preset_file_draws_no_group_heading(config_dir, clean_registry):
    """A node carrying ONE preset file draws its names flat — a heading would name nothing."""
    files(config_dir, winter=None)
    app = build(config_dir, one_server_fleet(QuietNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert "<optgroup" not in block, "a single preset file must not draw a group heading"
    assert pick_values(block) == ["Summer", "Winter_Clear_Morning", "Mixed"], pick_values(block)


@pytest.mark.asyncio
async def test_a_name_two_files_share_is_drawn_once_per_file_that_carries_it(config_dir,
                                                                             clean_registry):
    """A shared name is offered by EVERY file that carries it, not only the first.

    The option's value stays the bare name, so two groups legitimately carry the same value: the FILE
    pick is what tells them apart, and it is what the POST resolves the name against — deduplicating
    across groups would make the name unpickable from the file the operator named.
    """
    files(config_dir)
    app = build(config_dir, one_server_fleet(QuietNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    groups = optgroups(block)
    assert block.count(">Winter_Clear_Morning</option>") == 2, \
        "a name two files define was not offered by both files that carry it"
    assert groups == [(FLAT, ["Summer", "Winter_Clear_Morning", "Mixed"]),
                      (WINTER, ["Winter_Clear_Morning", "Winter_Storm"])], groups


@pytest.mark.asyncio
async def test_the_grouping_sentence_is_drawn_in_both_shapes(config_dir, clean_registry):
    """The help states the grouping AND the single-file case, whichever the node answers."""
    paths = files(config_dir)
    two = build(config_dir, one_server_fleet(QuietNode(config_dir)))
    async with client(two) as http:
        await sign_in(http, "mod1")
        grouped = await open_dialog(http)

    Path(paths["winter"]).unlink()
    presets_page.forget_node_lists()
    one = build(config_dir, one_server_fleet(QuietNode(config_dir)))
    async with client(one) as http:
        await sign_in(http, "mod1")
        single = await open_dialog(http)

    for dialog in (grouped, single):
        assert html.escape(actions_page.PRESET_GROUP_HELP, quote=False) in dialog.text, \
            "the pick does not say what its groups are"


@pytest.mark.asyncio
async def test_the_grouping_sentence_renders_in_german(config_dir, clean_registry):
    """The sentence is wrapped and translated, while a FILE label stays operator data."""
    from services.webservice import i18n

    files(config_dir)
    app = build(config_dir, one_server_fleet(QuietNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        dialog = await http.post(STARTUP + CONFIRM,
                                 data={"server": "SRS-1", "_csrf_token": token_of(page)})

    block = pick_block(dialog.text)
    assert "Eine Gruppe je Preset-Datei auf diesem Node" in dialog.text, \
        "the grouping sentence is not German"
    assert f'<optgroup label="{FLAT}">' in block, "a file label is operator data"


# ------------------------------------------------------------------ 4. the cache

@pytest.mark.asyncio
async def test_a_second_render_re_lists_but_parses_nothing(config_dir, clean_registry):
    """The file set must be current on every open; the PARSE of it is what the cache keeps."""
    files(config_dir)
    node = QuietNode(config_dir)
    app = build(config_dir, one_server_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        await open_dialog(http)
        cold = node.names()
        node.reads.clear()
        node.listings.clear()
        await open_dialog(http)

    assert sorted(cold) == ["presets.yaml", "presets_winter.yaml"], cold
    assert node.reads == [], f"the warm open re-parsed {node.reads}"
    assert len(node.listings) == 1, "the warm open must still LIST the node's files"


@pytest.mark.asyncio
async def test_a_file_rewritten_outside_the_console_reaches_the_next_open(config_dir,
                                                                        clean_registry):
    """A preset file edited on the node by hand is offered on the very next open, not after the TTL."""
    paths = files(config_dir)
    node = QuietNode(config_dir)
    app = build(config_dir, one_server_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        before = await open_dialog(http)
        assert ">Autumn_Morning</option>" not in before.text
        Path(paths["flat"]).write_text(
            FLAT_YAML + "Autumn_Morning:\n  modify:\n    weather: {}\n", encoding="utf-8")
        after = await open_dialog(http)

    assert ">Autumn_Morning</option>" in after.text, \
        "a preset added to the file outside the console is missing from the next open"


@pytest.mark.asyncio
async def test_a_failed_read_is_not_remembered(tmp_path):
    """A node that did not answer is asked again, never pinned for the window."""
    paths = files(tmp_path)
    node = presets_page_node(tmp_path, fail=[paths["flat"]])

    first, first_readable = await presets_page.node_preset_files(node)
    node.fail.clear()
    second, second_readable = await presets_page.node_preset_files(node)

    assert [label for label, _p, _n in first] == [WINTER]
    assert first_readable is True and second_readable is True
    assert [label for label, _p, _n in second] == [FLAT, WINTER], \
        "an answer that lost a file to a failed read was remembered"


# ------------------------------------------------------------------ 5. the fallback, both dialogs

@pytest.mark.asyncio
async def test_a_node_that_cannot_be_listed_says_why_the_file_mode_is_missing(config_dir,
                                                                             clean_registry):
    """No listing, no names: the pick is empty, the FILE mode is withheld, and the copy says why."""
    files(config_dir)
    node = QuietNode(config_dir, list_error=PermissionError("the node said no"))
    app = build(config_dir, one_server_fleet(node))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert pick_values(block) == [], pick_values(block)
    assert "could not be read" in dialog.text
    assert 'value="file"' not in dialog.text.split('class="dlg-field dlg-radio"')[1][:2000]


class TwoFileLoadNode(LoadNode):
    """The Load dialog's node double, carrying TWO preset files beside its mission fixture."""

    async def _list_preset_files(self, path, *, pattern="*"):  # type: ignore[override]
        if not self.presets_yaml:
            return []
        return [(os.path.join(self.config_dir, "presets.yaml"), len(FLAT_YAML), 10.0),
                (os.path.join(self.config_dir, "presets_winter.yaml"), len(WINTER_YAML), 20.0)]

    async def read_file(self, path):
        if path.endswith("presets_winter.yaml"):
            return WINTER_YAML.encode("utf-8")
        return await super().read_file(path)


@pytest.mark.asyncio
async def test_the_load_dialog_draws_the_same_groups(config_dir, clean_registry):
    """The Missions tab's Load dialog reads the SAME pick, so it draws the same two groups."""
    cluster = load_fleet()
    for server in cluster.servers:
        if server.node.name == "dcs-a-01":
            server.node = TwoFileLoadNode(server.node.name, content=server.node.content,
                                          config_dir=server.node.config_dir,
                                          presets_yaml=FLAT_YAML)
    app = build(config_dir, cluster, plugins=("mission",))

    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)

    block = pick_block(dialog.text)
    assert [label for label, _values in optgroups(block)] == [FLAT, WINTER], \
        "the Load dialog did not draw one group per preset file"
    assert ungrouped(block) == [], "the console's own choices are the mode radios above the pick"


# ------------------------------- 6. a name TWO files carry: the file pick decides the body

#: the SAME name in both files with DIFFERENT bodies, so the applied document names the file it came
#: from. ``config/presets.yaml`` sorts first, so it is the file the pick's heading offers and the file
#: the no-JS form posts by default.
DUPLICATE_FLAT = """\
Storm:
  modify:
    weather:
      clouds:
        base: 900
Flat_Only:
  modify:
    weather:
      clouds:
        base: 1200
"""

DUPLICATE_WINTER = """\
Storm:
  modify:
    weather:
      clouds:
        base: 100
Winter_Only:
  modify:
    weather:
      clouds:
        base: 200
"""

MODES = actions_page.WEATHER_MODE_FIELD
FILE_FIELD = actions_page.PRESET_FILE_FIELD


def duplicate_files(config_dir) -> None:
    """The node's two preset files, both defining ``Storm`` — with different bodies."""
    (config_dir / "presets.yaml").write_text(DUPLICATE_FLAT, encoding="utf-8")
    (config_dir / "presets_winter.yaml").write_text(DUPLICATE_WINTER, encoding="utf-8")


def file_options(html: str) -> list[str]:
    """Every ``<option>`` value of the preset-FILE select, in drawn order — ``[]`` when undrawn."""
    match = re.search(r'<select[^>]*name="preset_file"[^>]*>(.*?)</select>', html, re.S)
    return re.findall(r'<option value="([^"]*)"', match.group(1)) if match else []


async def post_file_mode(http, dialog, *, file_value: str, name: str):
    """A mode-``file`` launch POST for ONE name, with the file value the form carries."""
    return await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                                          MODES: actions_page.WEATHER_MODE_FILE,
                                          FILE_FIELD: file_value, PRESET_FIELD: name})


@pytest.mark.asyncio
async def test_a_name_two_files_define_applies_the_picked_files_body(config_dir, clean_registry):
    """The file the operator PICKED decides the body, not the node's merge order.

    ``Storm`` is in both files (900 in the flat one, 100 in ``presets_winter.yaml``). Picking
    ``config/presets.yaml`` must apply ITS body — the merge's last-file-wins answer would apply the
    other file's.
    """
    duplicate_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await post_file_mode(http, dialog, file_value=FLAT, name="Storm")

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 900}}}}, \
        "the launch applied the other file's body for the name the picked file carries"


@pytest.mark.asyncio
async def test_the_file_mode_with_javascript_off_posts_a_file_bearing_entry(
        config_dir, clean_registry):
    """No JavaScript: every group stays visible, the filter does nothing and keeps its "All files"
    default, and each ticked ENTRY still posts its own file — so the launch applies the body of the
    group it was ticked in."""
    duplicate_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        options = file_options(dialog.text)
        entry = actions_page.preset_option_value(FLAT, "Storm")
        assert options and options[0] == actions_page.PRESET_FILE_ALL, options
        assert f'value="{entry}"' in pick_block(dialog.text), \
            "the no-JS form offers no entry carrying the file it was ticked under"
        reply = await post_file_mode(http, dialog, file_value=options[0], name=entry)

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 900}}}}, \
        "the no-JS form applied a body other than the file carried by the entry"


@pytest.mark.asyncio
async def test_both_groups_offer_a_shared_name_with_each_files_own_body(config_dir, clean_registry):
    """The two entries stand for their OWN file's body: each group lists the name it defines, and the
    file that carries a name only it has stays in its own group."""
    duplicate_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    groups = optgroups(pick_block(dialog.text))
    assert groups == [(FLAT, ["Storm", "Flat_Only"]), (WINTER, ["Storm", "Winter_Only"])], \
        "the shared name is not offered under BOTH files that define it"
    assert file_options(dialog.text) == [actions_page.PRESET_FILE_ALL, FLAT, WINTER], \
        file_options(dialog.text)


@pytest.mark.asyncio
async def test_a_shared_name_picked_under_the_second_file_applies_that_files_body(config_dir,
                                                                                 clean_registry):
    """Picking the name from the SECOND file applies 100, not the 900 the first file defines.

    Both groups carry the name, so the entry drawn under ``presets_winter.yaml`` must be the one the
    dialog offers for that file — and the FILE field alone tells the two entries apart.
    """
    duplicate_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await post_file_mode(http, dialog, file_value=WINTER, name="Storm")

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 100}}}}, \
        "the entry drawn under the second file applied another file's body"
    assert call["preset_name"] == "Storm", call


@pytest.mark.asyncio
async def test_a_name_the_picked_file_does_not_carry_is_refused(config_dir, clean_registry):
    """A name another file carries is NOT resolved from that other file: the launch is refused, so a
    visible file choice never silently applies a body it did not offer."""
    files(config_dir)          # flat: Summer, Winter_Clear_Morning, Mixed; winter: Winter_Clear_Morning, Winter_Storm
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await post_file_mode(http, dialog, file_value=FLAT, name="Winter_Storm")

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == [], "a name the picked file does not carry launched"
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_PRESET in reply.text


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_preset_map_is_neither_offered_nor_accepted(config_dir,
                                                                              clean_registry):
    """The files the dialog draws and the files the POST accepts are ONE set.

    ``presets_notes.yaml`` parses to a list, which carries no preset: it must not be drawn as a file
    choice, and posting it must be refused — the two answers cannot disagree.
    """
    files(config_dir)
    (config_dir / "presets_notes.yaml").write_text("- a list is not a preset map\n", encoding="utf-8")
    notes = "config/presets_notes.yaml"
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        assert notes not in dialog.text, "a file carrying no preset was drawn as a file choice"
        assert file_options(dialog.text) == [actions_page.PRESET_FILE_ALL, FLAT, WINTER], \
            file_options(dialog.text)
        reply = await post_file_mode(http, dialog, file_value=notes, name="Summer")

    assert reply.status_code == 400, reply.text
    assert cluster.servers[0].call_args == []
    assert actions_page.REFUSAL_WEATHER_UNKNOWN_FILE in reply.text


@pytest.mark.asyncio
async def test_the_post_lists_the_node_s_preset_files_once(config_dir, clean_registry):
    """ONE read per request: the POST re-reads the node's file set once — never a second listing
    behind the dialog's back — and parses nothing when the render already parsed it."""
    files(config_dir)
    node = QuietNode(config_dir)
    cluster = one_server_fleet(node)
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        node.listings.clear()
        node.reads.clear()
        reply = await post_file_mode(http, dialog, file_value=FLAT, name="Summer")

    assert reply.status_code == 303, reply.text
    assert len(node.listings) == 1, f"the POST listed the node {len(node.listings)} times"
    assert node.reads == [], f"the POST re-parsed {node.reads}"
