"""The Scheduler is a reference site: a preset a rotation applies cannot be removed or renamed.

The removal/rename guard walks ``nodes.yaml`` (``MizEdit.settings``) and other presets (a combination
naming the name). The Scheduler applies presets from ITS OWN config — ``<config>/plugins/scheduler.yaml``
— where each server section states them in an action's ``settings`` (``plugins/scheduler/commands.py``
``_run_with_presets``), resolving every entry with ``utils.get_preset``. A preset only the Scheduler
names is therefore IN USE: removing it makes the next rotation log one warning and apply nothing.

* :func:`scheduler_references` is the PURE scan of that document — unit-tested without a bot;
* a name in a server section's ``settings`` REFUSES both a removal and a rename, and the refusal
  names the section;
* only the shape the runtime really resolves counts: a bare string entry, spelled exactly;
* a missing, unreadable or unparsable ``plugins/scheduler.yaml`` contributes no reference and never
  breaks the save;
* the positive control: a preset nothing names is still removable.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), reusing the write suite's harness so the app under test is the running one.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.webservice.pages import presets as presets_page
from test_webui_presets_save import (  # noqa: F401 - helpers and fixtures imported on purpose
    LONELY, NODES_OTHER, SPRING, nodes_file, page, post_save, preset_file, sha256,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a Scheduler config whose ONE instance section applies ``Spring`` from a single action.
SCHEDULER_ONE_SECTION = """\
DEFAULT:
  timezone: UTC
NODE1:
  Instance1:
    action:
      times:
        - "04:00"
      method: rotate
      settings:
        - Spring
"""

#: the same reference in a ``startup`` map, plus one action of a two-entry ``action`` seq.
SCHEDULER_STARTUP_AND_SEQ = """\
NODE1:
  Instance1:
    startup:
      settings:
        - Spring
    action:
      - times:
          - "04:00"
        method: restart
        settings:
          - Morning
      - times:
          - "12:00"
        method: rotate
        settings:
          - Spring
"""

#: ``settings`` in the shapes the runtime does NOT resolve: a comma string, a list entry, a wrong case.
SCHEDULER_UNRESOLVED_SHAPES = """\
NODE1:
  Instance1:
    action:
      method: rotate
      settings:
        - Spring, Morning
        - - Spring
        - spring
"""

#: a Scheduler config that names another preset — the positive control's environment.
SCHEDULER_OTHER = """\
NODE1:
  Instance1:
    action:
      method: rotate
      settings:
        - Morning
"""


def scheduler_file(config_dir: Path, text: str = SCHEDULER_ONE_SECTION,
                   name: str = "scheduler.yaml") -> Path:
    """Write the Scheduler plugin's config where the plugin itself keeps it."""
    plugins = config_dir / "plugins"
    plugins.mkdir(exist_ok=True)
    path = plugins / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------ 1. the pure scan of the plugin config

def test_scheduler_references_reads_a_server_section_and_names_it():
    """A section's ``settings`` is a reference, and the label is the section that states it."""
    document = {
        "DEFAULT": {"timezone": "UTC"},
        "NODE1": {"Instance1": {"action": {"method": "rotate", "settings": ["Spring"]}}},
        "NODE2": {"Instance1": {"action": {"settings": ["Morning"]}}},
    }
    assert presets_page.scheduler_references(document, "Spring") == [
        "the scheduler's section 'NODE1/Instance1'"]


def test_scheduler_references_reads_the_default_section():
    """``DEFAULT`` is the settings every server inherits, so it is a reference site of its own."""
    document = {"DEFAULT": {"startup": {"settings": ["Spring"]}}}
    assert presets_page.scheduler_references(document, "Spring") == ["the scheduler's default section"]


def test_scheduler_references_reads_startup_and_every_action_of_the_seq():
    """``startup`` is one action map and ``action`` is a map or a seq — all three are read."""
    document = {
        "NODE1": {"Instance1": {"startup": {"settings": ["Spring"]}}},
        "NODE2": {"Instance1": {"action": [{"settings": ["Morning"]}, {"settings": ["Spring"]}]}},
    }
    assert presets_page.scheduler_references(document, "Spring") == [
        "the scheduler's section 'NODE1/Instance1'",
        "the scheduler's section 'NODE2/Instance1'",
    ]


def test_scheduler_references_reads_a_bare_instance_section_and_skips_command_definitions():
    """A node-less instance section is read, and ``commands``/``chat_commands`` are not servers."""
    document = {
        "Instance1": {"action": {"settings": ["Spring"]}},
        "commands": {"Spring": {"settings": ["Spring"]}},
        "chat_commands": {"Spring": {"settings": ["Spring"]}},
    }
    assert presets_page.scheduler_references(document, "Spring") == [
        "the scheduler's section 'Instance1'"]


def test_scheduler_references_reads_no_shape_the_runtime_would_not_resolve():
    """A comma string, a list entry and a wrongly-cased name are NOT references.

    ``_run_with_presets`` hands each ``settings`` entry to ``utils.get_preset``, which resolves a name
    with ``dict.get``: it never splits a comma string, a list is not a hashable key, and the lookup is
    exact. All three miss there — the runtime only logs ``Preset "…" not found``.
    """
    document = {"NODE1": {"Instance1": {"action": {
        "method": "rotate",
        "settings": ["Spring, Morning", ["Spring"], "spring", "SPRING", ""],
    }}}}
    assert presets_page.scheduler_references(document, "Spring") == []
    # a scalar ``settings`` is not a seq, so the runtime iterates its characters: also no reference.
    assert presets_page.scheduler_references(
        {"NODE1": {"Instance1": {"action": {"settings": "Spring"}}}}, "Spring") == []


def test_scheduler_references_answers_nothing_for_a_document_that_is_not_a_map():
    """A missing or unusable document contributes no reference and raises nothing."""
    for document in (None, "", [], 3, {}):
        assert presets_page.scheduler_references(document, "Spring") == []


def test_preset_references_includes_the_scheduler_sections():
    """One home for the walk: node references, combinations and the Scheduler together."""
    nodes = {"NODE1": {"instances": {"Instance1": {"extensions": {"MizEdit": {"settings": "Spring"}}}}}}
    who = presets_page.preset_references(nodes, [], "Spring",
                                         {"NODE2": {"Instance1": {"action": {"settings": ["Spring"]}}}})
    assert who == ["node 'NODE1', instance 'Instance1'", "the scheduler's section 'NODE2/Instance1'"]
    assert presets_page.preset_references(nodes, [], "Spring", None) == [
        "node 'NODE1', instance 'Instance1'"]


# ----------------------------------------------- 2. what the plugin's own lookup really does

def test_the_plugins_own_lookup_resolves_an_exact_bare_name_only(tmp_path):
    """``utils.get_preset`` is a plain ``dict.get``: exact key, no comma split, no list key.

    This is the reading :func:`scheduler_references` mirrors, so the guard and the rotation cannot
    disagree about what a ``settings`` entry means.
    """
    from core.utils.helper import get_preset

    (tmp_path / "presets.yaml").write_text(SPRING + "Morning:\n  temperature: 12\n", encoding="utf-8")
    node = SimpleNamespace(config_dir=str(tmp_path))  # type: ignore[arg-type] - a deliberately minimal Node
    preset_file_arg = str(tmp_path / "presets.yaml")
    assert get_preset(node, "Spring", preset_file_arg) == {
        "date": "2016-04-21", "temperature": 8, "qnh": 760}
    assert get_preset(node, "spring", preset_file_arg) is None
    assert get_preset(node, "Spring, Morning", preset_file_arg) is None
    assert get_preset(node, ["Spring"], preset_file_arg) is None  # type: ignore[arg-type] - the shape under test
    assert get_preset(node, "Nope", preset_file_arg) is None


# ----------------------------------------------------- 3. the guard on the ONE write route

@pytest.mark.asyncio
async def test_removing_a_scheduler_referenced_preset_is_refused(config_dir, clean_registry):
    """A preset a rotation applies cannot be removed — nothing is written, the section is named."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, presets_page.PRESETS_PATH)
    assert "still named by" in html
    assert "NODE1/Instance1" in html


@pytest.mark.asyncio
async def test_renaming_a_scheduler_referenced_preset_is_refused(config_dir, clean_registry):
    """A rename of a preset a rotation applies is refused and the file is byte-identical."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"name": "Renamed"})
        assert reply.status_code == 303
        assert sha256(path) == before
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "still named by" in html
    assert "NODE1/Instance1" in html


@pytest.mark.asyncio
async def test_a_scheduler_reference_in_a_startup_map_is_refused(config_dir, clean_registry):
    """The other action site (``startup``) refuses on the same rule."""
    path = preset_file(config_dir)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir, SCHEDULER_STARTUP_AND_SEQ)
    before = sha256(path)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert sha256(path) == before


@pytest.mark.asyncio
async def test_a_case_differing_scheduler_name_is_not_a_reference(config_dir, clean_registry):
    """The plugin resolves exactly, so a wrongly-cased entry is no reference — the removal lands."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir, SCHEDULER_UNRESOLVED_SHAPES)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", action="remove")
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH
    document = configwrite.load_config_file(path)
    assert isinstance(document, dict)
    assert "Spring" not in document
    assert "Lonely" in document


@pytest.mark.asyncio
async def test_a_missing_scheduler_config_contributes_no_reference(config_dir, clean_registry):
    """An install without the plugin has no ``plugins/scheduler.yaml``: the guard still works."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    assert not (config_dir / "plugins" / "scheduler.yaml").exists()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Lonely", action="remove")
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH
    document = configwrite.load_config_file(path)
    assert isinstance(document, dict)
    assert "Lonely" not in document


@pytest.mark.asyncio
async def test_an_unreadable_scheduler_config_contributes_no_reference(config_dir, clean_registry):
    """A ``scheduler.yaml`` that cannot be read contributes nothing and never stops the save."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "scheduler.yaml").mkdir()      # a directory where a file belongs
    assert presets_page._scheduler_document(config_dir) is None
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Lonely", action="remove")
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH
    document = configwrite.load_config_file(path)
    assert isinstance(document, dict)
    assert "Lonely" not in document


@pytest.mark.asyncio
async def test_an_unparsable_scheduler_config_contributes_no_reference(config_dir, clean_registry):
    """A ``scheduler.yaml`` that does not parse contributes nothing and never stops the save."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir, "NODE1: [unclosed\n")
    assert presets_page._scheduler_document(config_dir) is None
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Lonely", action="remove")
        assert reply.status_code == 303
    document = configwrite.load_config_file(path)
    assert isinstance(document, dict)
    assert "Lonely" not in document


@pytest.mark.asyncio
async def test_a_preset_named_nowhere_is_still_removable(config_dir, clean_registry):
    """The positive control: a Scheduler config that names ANOTHER preset does not wall the guard."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING + LONELY)
    nodes_file(config_dir, NODES_OTHER)
    scheduler_file(config_dir, SCHEDULER_OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Lonely", action="remove")
        assert reply.status_code == 303
        assert reply.headers["location"] == presets_page.PRESETS_PATH
    document = configwrite.load_config_file(path)
    assert isinstance(document, dict)
    assert "Lonely" not in document and "Spring" in document
