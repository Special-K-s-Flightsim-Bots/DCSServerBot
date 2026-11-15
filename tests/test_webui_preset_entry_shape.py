"""The launch POST's preset ENTRY keeps its SHAPE: the file label and the name are split on the RAW
value, and only the parts are cleaned afterwards.

A pick entry is ``<file label><newline><preset name>`` (:data:`actions.PRESET_PAIR_SEPARATOR`). The
shape decides what the entry MEANS, so it is read before anything is trimmed: an entry whose name part
is empty is a LABEL-ONLY entry — a file was picked and no preset — and it is refused as an empty pick,
never mistaken for a bare preset NAME. A preset name is operator data and is looked up byte for byte,
so a name that begins or ends with a space resolves exactly, and is refused only when that exact name
is absent.

1. THE REFUSAL — a label-only entry answers the empty-pick sentence, not the unknown-preset one;
2. THE READ — the entry keeps its separator, its label is cleaned, its name is kept verbatim;
3. THE RESOLUTION — a name with spaces resolves verbatim end to end, and is never trimmed into a
   different name.
"""
from __future__ import annotations

import json
import re

import pytest

from services.webservice.pages import actions as actions_page
from test_webui_preset_pick_groups_node_files import (  # noqa: F401 - fixtures imported on purpose
    FLAT, QuietNode, one_server_fleet,
)
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    STARTUP, open_dialog, pick_block,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

PRESET_FIELD = actions_page.PRESET_FIELD
FILE_FIELD = actions_page.PRESET_FILE_FIELD
MODES = actions_page.WEATHER_MODE_FIELD
SEPARATOR = actions_page.PRESET_PAIR_SEPARATOR
EXTRA = "config/presets_extra.yaml"

#: two preset files, so the pick draws GROUPS and every entry carries its file. One name of the flat
#: file carries a space at each end: a preset name is operator data.
FLAT_YAML = """\
Snow:
  modify:
    weather:
      clouds:
        base: 100
" Snow ":
  modify:
    weather:
      clouds:
        base: 777
"""
EXTRA_YAML = """\
Winter:
  modify:
    weather:
      clouds:
        base: 500
"""


class _Form:
    """The minimum of a Starlette ``FormData`` the pick's reader touches."""

    def __init__(self, values):
        self.values = values

    def get(self, name, default=None):
        value = self.values.get(name, default)
        return value if not isinstance(value, list) else value[-1]

    def getlist(self, name):
        value = self.values.get(name)
        if value is None:
            return []
        return list(value) if isinstance(value, list) else [value]


def startup_action():
    """The Startup control's own declaration — the action the pick's reader is given."""
    return next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")


def write_files(config_dir):
    """The node's two preset files: the flat one and one beside it."""
    (config_dir / "presets.yaml").write_text(FLAT_YAML, encoding="utf-8")
    (config_dir / "presets_extra.yaml").write_text(EXTRA_YAML, encoding="utf-8")


def detail(reply) -> str:
    """The refusal sentence a launch POST answers, whatever body it was rendered into."""
    try:
        return str(json.loads(reply.text)["detail"])
    except (ValueError, KeyError, TypeError):
        return reply.text


async def post_file_mode(http, dialog, *, name):
    """A mode-``file`` launch POST for ONE posted entry, with the flat file named."""
    return await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                                          MODES: actions_page.WEATHER_MODE_FILE,
                                          FILE_FIELD: FLAT, PRESET_FIELD: name})


# --------------------------------------------------------------- 1. the refusal

@pytest.mark.asyncio
async def test_a_label_only_entry_is_refused_as_an_empty_pick(config_dir, clean_registry):
    """THE ACCEPTANCE — an entry carrying a file and NO name is refused as an empty pick.

    ``config/presets.yaml`` followed by the separator is a label-only entry: a file was picked and no
    preset was ticked. The trailing newline IS that shape, so it must survive the read — dropped, the
    value reads as a bare preset NAME and the launch is refused for a preset nobody picked.
    """
    write_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await post_file_mode(http, dialog, name=f"{FLAT}{SEPARATOR}")

    assert reply.status_code == 400, reply.text
    assert detail(reply) == actions_page.REFUSAL_WEATHER_NO_PICK, detail(reply)
    assert cluster.servers[0].call_args == [], "a label-only entry launched"


# ------------------------------------------------------------------ 2. the read

def test_the_entry_is_split_on_the_raw_value_before_any_part_is_cleaned():
    """The label is cleaned; the NAME is kept verbatim, and an empty name stays empty.

    The label is the node's own ``config/…`` file name (cleaned); the name is a preset name the node
    looks up byte for byte (never cleaned).
    """
    form = _Form({PRESET_FIELD: [f"  {FLAT}  {SEPARATOR} Snow ",
                                 f"{FLAT}{SEPARATOR}",
                                 "",
                                 "   "]})
    entries = actions_page.posted_preset_entries(form, startup_action())

    assert entries == [f"{FLAT}{SEPARATOR} Snow ", f"{FLAT}{SEPARATOR}"], entries
    assert actions_page.split_preset_entry(entries[0]) == (FLAT, " Snow ")
    assert actions_page.split_preset_entry(entries[1]) == (FLAT, "")


def test_a_bare_entry_is_the_free_text_shape_and_is_cleaned_whole():
    """A BARE entry carries no label: it is the free-text box's own value, cleaned whole."""
    form = _Form({PRESET_FIELD: ["  Summer  ", "Summer"]})
    entries = actions_page.posted_preset_entries(form, startup_action())

    assert entries == ["Summer", "Summer"], entries
    assert actions_page.split_preset_entry(entries[0]) == ("", "Summer")


# ------------------------------------------------------------ 3. the resolution

@pytest.mark.asyncio
async def test_a_name_with_its_own_spaces_resolves_verbatim(config_dir, clean_registry):
    """The rendered entry posts back and resolves the `` Snow `` preset, spaces included.

    The dialog draws whatever name its file carries, so a name leading and trailing a space is
    pickable — and the body it applies is that name's own, named back as it stands.
    """
    write_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        offered = [actions_page.split_preset_entry(value)[1]
                   for value in re.findall(r'<option value="([^"]*)"', pick_block(dialog.text))]
        assert " Snow " in offered, offered
        reply = await post_file_mode(http, dialog,
                                     name=actions_page.preset_option_value(FLAT, " Snow "))

    assert reply.status_code == 303, reply.text
    call = cluster.servers[0].call_args[0]
    assert call["preset"] == {"modify": {"weather": {"clouds": {"base": 777}}}}, call
    assert call["preset"] != {"modify": {"weather": {"clouds": {"base": 100}}}}, \
        "the space-free name's body was applied for the spaced name"


@pytest.mark.asyncio
async def test_a_name_with_spaces_is_never_trimmed_into_another_name(config_dir, clean_registry):
    """`` Snow  `` and ``Snow`` are TWO presets: an entry naming the spaced one never resolves the
    other, so the answer is the unknown-preset refusal and nothing launches."""
    write_files(config_dir)
    cluster = one_server_fleet(QuietNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await post_file_mode(http, dialog,
                                     name=actions_page.preset_option_value(FLAT, " Snow  "))

    assert reply.status_code == 400, reply.text
    assert detail(reply) == actions_page.REFUSAL_WEATHER_UNKNOWN_PRESET, detail(reply)
    assert cluster.servers[0].call_args == [], \
        "a name the file does not carry was trimmed into one it does"
