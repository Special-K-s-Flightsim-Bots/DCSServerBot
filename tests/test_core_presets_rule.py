"""The preset rule in core: discovery, the node read, and the name -> document resolution.

These pins drive :mod:`core.utils.presets` DIRECTLY — no page module, no request, no Discord — which
is what a plugin resolving a launch pick uses. One section per rule:

1. DISCOVERY — both spellings of a preset file are found (``presets*.yaml`` and ``presets/*.yaml``),
   and a file's label is the config tree's own name for it;
2. THE READ — ``{file label: {preset name: document}}``, its order kept, an unreadable file spotted;
3. THE RULE — a name two files define resolved per label; a combination member missing from the
   picked file taken from the node's merged map; an unknown name REFUSED rather than dropped; the
   node's own config order kept whatever order the names arrived in;
4. THE PICK ENTRY — one helper turns posted values into the resolved pair the payload is built from.

The node double binds the REAL ``NodeImpl.list_files`` / ``read_file``, so the glob the rule lists
with is the transport's own.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.data.impl.nodeimpl import NodeImpl
from core.utils import presets as rule

FLAT = "config/presets.yaml"
WINTER = "config/presets_winter.yaml"
NESTED = "config/presets/extra.yaml"

#: the flat file: two own-value presets, one hidden, and a COMBINATION whose second member lives in
#: the OTHER file — so the member can only be resolved through the node's merged map.
FLAT_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 900
Storm:
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
Mixed:
  - Summer
  - Alpine
"""

#: the file beside it: the SAME name ``Storm`` with a different body, plus the combination's member.
WINTER_YAML = """\
Alpine:
  modify:
    weather:
      clouds:
        base: 3000
Storm:
  modify:
    weather:
      clouds:
        base: 1200
"""

#: the directory form of a preset file.
NESTED_YAML = """\
Extra:
  modify:
    weather:
      clouds:
        base: 500
"""


class CoreNode:
    """A node whose preset files live under *config_dir* on THIS machine.

    ``list_files`` and ``read_file`` are the REAL ``NodeImpl`` methods bound to this object, so the
    rule's discovery runs through the transport's own glob.
    """

    def __init__(self, config_dir, *, name="dcs-a-01", fail=()):
        self.name = name
        self.master = True
        self.node = self
        self.config_dir = str(config_dir)
        self.fail = {str(path).replace("\\", "/") for path in fail}

    async def list_files(self, path, *, pattern="*"):
        return await NodeImpl.list_files(self, path, pattern=pattern)  # type: ignore[arg-type]

    async def read_file(self, path):
        if path in self.fail:
            raise PermissionError("the node said no")
        return await NodeImpl.read_file(self, path)  # type: ignore[arg-type]


def write_files(config_dir: Path) -> CoreNode:
    """Write all three preset files (flat, beside it, in ``presets/``) and return their node."""
    (config_dir / "presets.yaml").write_text(FLAT_YAML, encoding="utf-8")
    (config_dir / "presets_winter.yaml").write_text(WINTER_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir(parents=True, exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(NESTED_YAML, encoding="utf-8")
    return CoreNode(config_dir)


@pytest.fixture()
def node(tmp_path) -> CoreNode:
    """The node carrying the three preset files, under its own config dir."""
    return write_files(tmp_path)


async def documents_by_label(node: CoreNode) -> dict[str, dict]:
    """The read of the node's files as the rule returns them — the input of the resolutions."""
    read = await rule.read_node_presets(node)
    assert read.readable is True, read
    return read.files


# ------------------------------------------------------------------ 1. discovery: BOTH spellings

@pytest.mark.asyncio
async def test_both_preset_file_spellings_are_discovered(tmp_path):
    """``presets*.yaml`` AND ``presets/*.yaml`` — a file in the directory is a preset file too."""
    node = write_files(tmp_path)

    listed, ok = await rule.list_preset_files(node)

    assert ok is True
    labels = [rule.preset_file_label(str(tmp_path), path) for path, _version in listed]
    assert labels == sorted(labels), f"the listing is ordered by config label: {labels}"
    assert labels == [FLAT, NESTED, WINTER], labels
    assert rule.PRESET_FILE_PATTERNS == ("presets*.yaml", "presets/*.yaml")


# ------------------------------------------------------------------------- 2. the node read itself

@pytest.mark.asyncio
async def test_the_read_returns_a_document_map_per_file_label(tmp_path):
    """The read is ``{file label: {preset name: document}}``, each file's own order kept."""
    node = write_files(tmp_path)

    read = await rule.read_node_presets(node)

    assert list(read.files) == [FLAT, NESTED, WINTER], list(read.files)
    assert list(read.files[FLAT]) == ["Summer", "Storm", "Hidden", "Mixed"], list(read.files[FLAT])
    assert read.files[WINTER]["Storm"]["modify"]["weather"]["clouds"]["base"] == 1200, \
        "a name two files define must keep ITS OWN file's body"
    assert read.paths[WINTER] == (tmp_path / "presets_winter.yaml").as_posix()
    assert read.readable is True and read.complete is True


@pytest.mark.asyncio
async def test_one_unreadable_file_leaves_the_read_incomplete_but_readable(tmp_path):
    """A file the node refuses is skipped and marks the answer incomplete; the others still read."""
    node = write_files(tmp_path)
    node.fail.add((tmp_path / "presets_winter.yaml").as_posix())

    read = await rule.read_node_presets(node)

    assert list(read.files) == [FLAT, NESTED], list(read.files)
    assert read.readable is True, "one unreadable file must not turn the whole node into no answer"
    assert read.complete is False, "an incomplete answer must not be remembered"


# --------------------------------------------------------------- 3. the name -> document rule

@pytest.mark.asyncio
async def test_a_name_two_files_define_resolves_per_label(node):
    """The entry's FILE decides the body: ``Storm`` out of each file is that file's ``Storm``."""
    read_files = await documents_by_label(node)
    flat = rule.resolve_presets([(FLAT, "Storm")], read_files)
    winter = rule.resolve_presets([(WINTER, "Storm")], read_files)

    assert flat.reason is None and winter.reason is None
    assert flat.documents[0]["modify"]["weather"]["clouds"]["base"] == 400
    assert winter.documents[0]["modify"]["weather"]["clouds"]["base"] == 1200
    assert flat.names == ["Storm"] and winter.names == ["Storm"]


@pytest.mark.asyncio
async def test_a_combination_member_absent_from_its_file_comes_from_the_merged_map(node):
    """``Mixed`` lives in the flat file; its member ``Alpine`` only lives in the other one."""
    read_files = await documents_by_label(node)
    answer = rule.resolve_presets([(FLAT, "Mixed")], read_files)

    assert answer.reason is None, answer
    assert answer.names == ["Mixed"]
    assert [document["modify"]["weather"]["clouds"]["base"] for document in answer.documents] \
        == [900, 3000], answer.documents


@pytest.mark.asyncio
async def test_an_unknown_name_is_refused_not_dropped(node):
    """A name nothing carries refuses the WHOLE pick — no partial answer is ever returned."""
    read_files = await documents_by_label(node)
    answer = rule.resolve_presets([(FLAT, "Summer"), (FLAT, "Nowhere")], read_files)

    assert answer.refused is True
    assert answer.reason == rule.REFUSAL_UNKNOWN_PRESET
    assert answer.documents == [] and answer.names == [], \
        "a refusal must not carry the entries it did resolve"


@pytest.mark.asyncio
async def test_an_unknown_or_path_shaped_file_label_is_refused(node):
    """A label the read does not list, and a value shaped like a path, are both UNKNOWN_FILE."""
    read_files = await documents_by_label(node)
    unknown = rule.resolve_presets([("config/presets_gone.yaml", "Summer")], read_files)
    shaped = rule.resolve_presets([("/etc/presets.yaml", "Summer")], read_files)

    assert unknown.reason == rule.REFUSAL_UNKNOWN_FILE
    assert shaped.reason == rule.REFUSAL_UNKNOWN_FILE


@pytest.mark.asyncio
async def test_the_documents_follow_the_nodes_own_order_not_the_posted_one(node):
    """Posted winter-first: the flat file's presets still come first, as the node's merge orders them."""
    read_files = await documents_by_label(node)
    answer = rule.resolve_presets(
        [(WINTER, "Alpine"), (FLAT, "Summer"), (FLAT, "Storm")], read_files)

    assert answer.reason is None, answer
    assert answer.names == ["Summer", "Storm", "Alpine"], answer.names
    assert [document["modify"]["weather"]["clouds"]["base"] for document in answer.documents] \
        == [900, 400, 3000], answer.documents


@pytest.mark.asyncio
async def test_a_bare_name_resolves_in_the_named_file_else_in_the_merged_map(node):
    """A caller naming no file resolves against the merged map; naming one resolves in that file."""
    read_files = await documents_by_label(node)
    merged_map = rule.resolve_presets([("", "Alpine")], read_files)
    per_file = rule.resolve_presets([("", "Alpine")], read_files, named_file=WINTER)
    absent = rule.resolve_presets([("", "Alpine")], read_files, named_file=NESTED)

    assert merged_map.documents[0]["modify"]["weather"]["clouds"]["base"] == 3000
    assert per_file.documents[0]["modify"]["weather"]["clouds"]["base"] == 3000
    assert absent.reason == rule.REFUSAL_UNKNOWN_PRESET, \
        "a bare name is not resolved in a file the caller did not name"


@pytest.mark.asyncio
async def test_a_pick_that_names_no_preset_is_refused(node):
    """An empty pick, and a pick naming a file and no preset, both refuse as NO_PICK."""
    read_files = await documents_by_label(node)
    assert rule.resolve_presets([], read_files).reason == rule.REFUSAL_NO_PICK
    assert rule.resolve_presets([(FLAT, "")], read_files).reason == rule.REFUSAL_NO_PICK


# ------------------------------------------------------------------------ 4. the pick entry shape

@pytest.mark.asyncio
async def test_picked_values_build_the_same_pair_as_the_pairs_themselves(node):
    """One helper turns the POSTED values into (documents, names) — the website's and the bot's one."""
    read_files = await documents_by_label(node)
    values = [rule.preset_option_value(WINTER, "Storm"), rule.preset_option_value(FLAT, "Summer")]

    answer = rule.resolve_picked_values(values, read_files)

    assert answer.reason is None, answer
    assert answer.names == ["Summer", "Storm"], answer.names
    assert answer.documents == rule.resolve_presets(
        [(FLAT, "Summer"), (WINTER, "Storm")], read_files).documents
