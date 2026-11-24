"""A picked preset NAME is operator data: ``preset_name`` states it exactly as its file spells it.

The launch dialog resolves a picked entry against the preset file it was ticked in, and the name
that entry carries is looked up BYTE FOR BYTE — so a file may define both ``Snow`` and `` Snow ``,
two different presets. The document applied is the spaced one's, and the name announced beside it
(the audit line and the server page's preset name) must be that same spaced name. Trimming it
announces a preset the operator never picked while applying another one's body.

One section per acceptance criterion:

1. A SPACED NAME — a file defining ``Snow`` and `` Snow ``; a mode-``file`` pick of the spaced entry
   applies THAT entry's body and states `` Snow `` in ``preset_name``;
2. TWO SPACED NAMES — both ticked keep their own spaces and the file's own comma order;
3. NOTHING NAMED — a launch naming nothing states nothing (``None``), and the bare-string reading
   is unchanged.

``\\n`` is the page's file/name separator: the value of a grouped entry carries its own file.
"""
from __future__ import annotations

import pytest

from services.webservice.pages import actions as actions_page
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    STARTUP, PresetNode, node_fleet, open_dialog, raw_pick_values, pick_block,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

SEP = actions_page.PRESET_PAIR_SEPARATOR
MODE_FIELD = actions_page.WEATHER_MODE_FIELD
MODE_FILE = actions_page.WEATHER_MODE_FILE
FILE_FIELD = actions_page.PRESET_FILE_FIELD
PRESET_FIELD = actions_page.PRESET_FIELD
FLAT = "config/presets.yaml"
EXTRA = "config/presets/extra.yaml"

FLAT_YAML = """\
Summer:
  modify:
    weather:
      clouds:
        base: 1000
"""

#: the flat file's neighbour: BOTH the plain name and the name whose own spelling is `` Snow ``.
SPACED_YAML = """\
" Snow ":
  modify:
    weather:
      clouds:
        base: 300
Snow:
  modify:
    weather:
      clouds:
        base: 900
"""


def entry(label: str, name: str) -> str:
    """A grouped pick entry's POST value: the file, then the name."""
    return f"{label}{SEP}{name}"


def two_files(config_dir) -> None:
    """The node's two preset files, the flat one and one beside it."""
    (config_dir / "presets.yaml").write_text(FLAT_YAML, encoding="utf-8")
    (config_dir / "presets").mkdir(exist_ok=True)
    (config_dir / "presets" / "extra.yaml").write_text(SPACED_YAML, encoding="utf-8")


def mode_post(dialog, entries) -> dict:
    return {"server": "SRS-1", "_csrf_token": token_of(dialog.text),
            MODE_FIELD: MODE_FILE, FILE_FIELD: EXTRA, PRESET_FIELD: entries}


# ------------------------------------------------------------- 1. a spaced name


@pytest.mark.asyncio
async def test_a_spaced_pick_states_its_own_spelling_and_applies_its_own_body(
        config_dir, clean_registry):
    """`` Snow `` and ``Snow`` are two presets in ONE file. Ticking the spaced entry applies its
    300 body and states `` Snow `` — never the trimmed ``Snow`` the operator did not tick."""
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        block = pick_block(dialog.text)
        assert entry(EXTRA, " Snow ") in raw_pick_values(block), \
            "the spaced name was not offered as its own entry"
        reply = await http.post(STARTUP, data=mode_post(dialog, [entry(EXTRA, " Snow ")]))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 300}}}}, call
    assert call["preset_name"] == " Snow ", call


# --------------------------------------------------------- 2. two spaced names


@pytest.mark.asyncio
async def test_two_spaced_names_keep_their_spaces_and_their_comma_order(config_dir,
                                                                       clean_registry):
    """Both names ticked from the same file keep their own spelling and the FILE's order in the
    comma list — the spaced name stands first in the file, so it stands first in ``preset_name``."""
    two_files(config_dir)
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        # click order deliberately reversed: the name list follows the file's order, never clicks
        reply = await http.post(STARTUP, data=mode_post(
            dialog, [entry(EXTRA, "Snow"), entry(EXTRA, " Snow ")]))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == [{"modify": {"weather": {"clouds": {"base": 300}}}},
                              {"modify": {"weather": {"clouds": {"base": 900}}}}], call
    assert call["preset_name"] == " Snow ,Snow", call


# ------------------------------------------------------------ 3. nothing named


def test_a_launch_naming_nothing_states_nothing():
    """``None`` stays ``None`` and an empty list states nothing; a BARE string keeps today's read."""
    assert actions_page.preset_name_text(None) is None
    assert actions_page.preset_name_text([]) is None
    assert actions_page.preset_name_text([""]) is None
    # a bare string argument is a single name, not a resolved list: today's trimmed reading stands
    assert actions_page.preset_name_text(" Snow ") == "Snow"
