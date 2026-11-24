"""The preset files ONE server's MizEdit READS: the one home of that answer, and its agreement.

``presets.mizedit_read_paths`` answers which preset files a server's ``MizEdit`` extension opens — a
path, a list of paths, or the extension's own default beside the node's config dir — and
``presets.mizedit_config`` is the ``MizEdit`` block it is read from, resolved from the same three
snapshots the runtime's ``load_extension`` merges.

The launch dialog's preset pick is NOT this answer: it lists every preset file the node carries (see
``test_webui_preset_pick_groups_node_files.py``). What is pinned here is the rule itself, against the
REAL extension's read, because the apply path needs to know which files a server's launch reads.

One section per criterion: the setting's four shapes; the default file named in exactly one place; the
rule agreeing with the extension's own ``_init_presets``; and the block being the runtime's merge.
"""
from __future__ import annotations

import logging
import types
from pathlib import Path

from core.utils.yaml_helper import new_yaml
from services.webservice.pages import presets as presets_page

#: the default file's names: one offered preset and one explicitly hidden.
DEFAULT_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
Hidden:
  hidden: true
  modify:
    weather:
      clouds:
        base: 100
"""

#: a file only a server that NAMES it reads.
TOURNAMENT_YAML = """\
Tournament:
  modify:
    weather:
      clouds:
        base: 500
"""

#: a file under ``presets/`` — a name no default-configured server reads.
EXTRA_YAML = """\
Winter:
  modify:
    weather:
      clouds:
        base: 250
"""


def test_the_rule_reads_the_files_the_setting_names_and_the_extension_default(tmp_path):
    """A single path, a list of paths, an explicitly empty list and an absent setting."""
    config = str(tmp_path)
    default = str(tmp_path / presets_page.MIZEDIT_DEFAULT_FILE)
    assert presets_page.mizedit_read_paths({}, config) == [default]
    assert presets_page.mizedit_read_paths({"presets": None}, config) == [default]
    assert presets_page.mizedit_read_paths({"presets": "config/a.yaml"}, config) == \
        ["config/a.yaml"]
    assert presets_page.mizedit_read_paths({"presets": ["config/a.yaml", "config/b.yaml"]},
                                           config) == ["config/a.yaml", "config/b.yaml"]
    assert presets_page.mizedit_read_paths({"presets": []}, config) == [], \
        "an explicitly empty list reads nothing, exactly as the extension's own loop does"
    assert presets_page.mizedit_read_paths(None, config) == [default]
    assert presets_page.mizedit_read_paths({}, "") == [], "no config dir, no default to name"


def test_the_rule_has_one_home(tmp_path):
    """The default file is NAMED once and never spelled again: the module reads the constant only.

    Five reads: the export, the constant, its docstring, ``mizedit_read_paths``'s default and the
    wizard's default-file sentence (a preset file is read by a node that lists none, so that file may
    not be called unread). A sixth read is a second spelling of the rule, not a refactor.
    """
    repo = Path(__file__).resolve().parent.parent
    actions_text = (repo / "services" / "webservice" / "pages" / "actions.py").read_text(
        encoding="utf-8")
    presets_text = (repo / "services" / "webservice" / "pages" / "presets.py").read_text(
        encoding="utf-8")
    assert '"presets.yaml"' not in actions_text and "'presets.yaml'" not in actions_text, \
        "the default preset file is spelled outside its one home"
    assert presets_text.count("MIZEDIT_DEFAULT_FILE") == 5, \
        ("the default preset file is named a number of times nobody recorded: the export, the "
         "constant, its docstring, the ONE read in mizedit_read_paths and the wizard's sentence")


def test_the_rule_is_the_extensions_own_read(tmp_path):
    """Driven through the REAL extension: its ``_init_presets`` reads exactly the resolved files."""
    from extensions.mizedit.extension import MizEdit

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "presets.yaml").write_text(DEFAULT_YAML, encoding="utf-8")
    (config_dir / "presets_tournament.yaml").write_text(TOURNAMENT_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir()
    (config_dir / "presets" / "extra.yaml").write_text(EXTRA_YAML, encoding="utf-8")

    extension = MizEdit.__new__(MizEdit)
    extension.log = logging.getLogger("tests.mizedit.files")
    extension.node = types.SimpleNamespace(config_dir=str(config_dir), config={"validation": "none"})
    for config in ({},
                   {"presets": str(config_dir / "presets_tournament.yaml")},
                   {"presets": [str(config_dir / "presets.yaml"),
                                str(config_dir / "presets_tournament.yaml")]},
                   {"presets": []}):
        extension.config = config
        expected = set(extension._init_presets())
        offered = set()
        for path in presets_page.mizedit_read_paths(config, str(config_dir)):
            offered |= set(new_yaml().load(Path(path).read_text(encoding="utf-8")) or {})
        assert offered == expected, f"the rule and the extension disagree on {config}"


def test_the_config_is_the_runtime_merge_of_node_instance_and_server(tmp_path):
    """The block is read from the same three snapshots the runtime's ``load_extension`` merges."""
    node = types.SimpleNamespace(locals={"extensions": {"MizEdit": {"presets": "config/node.yaml"}}})
    server = types.SimpleNamespace(
        node=node,
        instance=types.SimpleNamespace(locals={"extensions": {"MizEdit": {"presets": "config/i.yaml"}}}),
        locals={})
    assert presets_page.mizedit_config(server)["presets"] == "config/i.yaml", \
        "the instance block overrides the node-wide one"
    server.locals = {"extensions": {"MizEdit": {"presets": "config/server.yaml"}}}
    assert presets_page.mizedit_config(server)["presets"] == "config/server.yaml", \
        "the server's own block overrides the instance's"
    assert presets_page.mizedit_config(types.SimpleNamespace(node=None, instance=None,
                                                             locals={})) == {}, \
        "a server that declares no block answers an empty mapping, never None"
