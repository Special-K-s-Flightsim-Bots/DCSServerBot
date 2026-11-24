"""THE ``MizEdit.presets`` SETTING IS A FILE CHOICE, NOT A PATH THE OPERATOR HAS TO SPELL.

The setting decides which preset FILE(S) a server applies at startup
(``extensions/mizedit/schemas/mizedit_schema.yaml``, ``type: any`` / ``func: str_or_list``) and is read
at ``extensions/mizedit/extension.py`` — absent, it falls back to ``presets.yaml`` in the instance's own
config directory. It is edited on the extension page (``/nodes/<node>/extensions/<name>``), whose form is
generated from the schema, so it used to render as free text.

Pinned here, over the REAL app:

* the field offers one CHECKBOX per preset file the installation can know — the files
  ``discover_preset_files`` finds under the console's config directory plus every label another
  node/instance already names — and ONE free-text row for a path the list does not carry;
* the value keeps the shapes the schema and the extension already read: one choice is a STRING, several
  are a LIST in the order shown, none REMOVES the key (which is what turns the default on);
* a hand-written path is its own CHECKED row, so an untouched page can never drop it — in either shape;
* a typed path is stored verbatim;
* an untouched page saves the document byte-identically;
* the node-wide block AND an instance block both render the control (the node scope is the page's
  FIRST tab, then one per instance — so ``dcs-a-01``'s Syria block is ``?tab=1``);
* every OTHER field renders exactly as before (the same extension's ``timezone`` and another
  extension's boolean pair), so the change is one field's control and nothing else.
"""
from __future__ import annotations

import html
import re
import types
from pathlib import Path
from urllib.parse import urlencode

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _app
from test_webui_settings_node_scope import _csrf, _get, _revision

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: ``dcs-a-01`` (the master) configures ``MizEdit`` on the Syria INSTANCE; ``dcs-b-01`` (a remote node)
#: configures it NODE-WIDE with a two-file list centred on a path that exists only on that machine.
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        MizEdit:
          enabled: true
          presets: config/presets.yaml
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  extensions:
    MizEdit:
      enabled: true
      presets:
        - config/presets_weather.yaml
        - config/presets/remote-only.yaml
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6669
      webgui_port: 8089
      dcs_port: 10309
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""

#: the document path of the setting on the two blocks the fixture configures.
INSTANCE_PATH = "dcs-a-01.instances.Syria.extensions.MizEdit.presets"
NODE_PATH = "dcs-b-01.extensions.MizEdit.presets"


@pytest.fixture
def presets_dir(tmp_path):
    """An install whose config tree carries three preset files the console can discover."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "extra.yaml").write_text("Ramstein: {}\n", encoding="utf-8")
    (tmp_path / "presets.yaml").write_text("Default: {}\n", encoding="utf-8")
    (tmp_path / "presets_weather.yaml").write_text("Storm: {}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def presets_data(presets_dir, monkeypatch):
    """Bind the bot's validator to THIS install (the binding the sibling node-page tests use)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(presets_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


def _url(node: str, name: str = "MizEdit", tab: int | None = None) -> str:
    url = node_settings_page.node_extension_url(node, name)
    return url if tab is None else f"{url}?tab={tab}"


def _rows(page: str, path: str) -> list[tuple[str, bool]]:
    """The preset rows the field renders, in page order: ``(label, checked)``."""
    found = re.findall(
        r'name="f\.' + re.escape(path) + r'\.__presets" value="([^"]*)"( checked)?', page)
    return [(html.unescape(label), bool(checked)) for label, checked in found]


def _free_row(page: str, path: str) -> str:
    """The free-text row's ``value`` — ``""`` when the row is absent."""
    match = re.search(r'name="f\.' + re.escape(path) + r'\.__presets_free" value="([^"]*)"', page)
    return html.unescape(match.group(1)) if match else ""


def _has_bare_value_input(page: str, path: str) -> bool:
    """Whether the page still renders a whole-value text input/textarea for the setting."""
    return bool(re.search(r'<(?:input|textarea)\b[^>]*name="f\.' + re.escape(path) + r'"', page))


def _ack(page: str) -> str:
    match = re.search(r'name="f\.__remove_ack" value="([^"]*)"', page)
    return html.unescape(match.group(1)) if match else ""


def _repeated(pairs) -> dict:
    """A POST body that may carry the SAME name more than once (a checkbox per row)."""
    return {"content": urlencode(list(pairs)),
            "headers": {"content-type": "application/x-www-form-urlencoded"}}


def _page_form(page: str, path: str, checked=(), typed: str = "") -> list[tuple[str, str]]:
    """The preset-file control as a BROWSER posts it: the marker, the free row, then each ticked file."""
    pairs = [("_csrf_token", _csrf(page)), ("_revision", _revision(page)),
             ("f.__remove_ack", _ack(page)),
             (f"f.{path}.__presets_present", "1"),
             (f"f.{path}.__presets_free", typed)]
    pairs += [(f"f.{path}.__presets", label) for label in checked]
    return pairs


@pytest.mark.asyncio
async def test_the_remote_node_says_its_files_cannot_be_listed_here(presets_dir, presets_data):  # noqa: F811
    """The console runs inside the MASTER's process: on a node that is not the master the free-text
    row is the only way to name a file that machine holds, and the field says so."""
    app = _app(presets_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        remote = await _get(client, _url("dcs-b-01"))
        master = await _get(client, _url("dcs-a-01", tab=1))
    assert "not the master" in remote, "a remote node's field does not say its files cannot be listed"
    assert "not the master" not in master, "the master's own field claims its files cannot be listed"
    for page in (remote, master):
        assert "reads presets.yaml in its own config directory" in page, \
            "the field does not state what an empty selection means"


def _mizedit_block(text: str, marker: str = "MizEdit") -> str:
    """The ``MizEdit:`` block of *text* (enough to assert on its ``presets`` value)."""
    start = text.index(marker)
    tail = text[start:]
    nxt = re.search(r"\n {0,6}\w+:\n", tail[len(marker):])
    return tail[:len(marker) + nxt.start()] if nxt else tail


# ================================================ 1. the offered rows, and the escape hatch


@pytest.mark.asyncio
async def test_the_field_offers_a_row_per_known_file_plus_a_free_text_row(presets_dir, presets_data):  # noqa: F811
    """Every file the console can KNOW is a checkbox row, ``presets.yaml`` first, and there is one
    free-text row — while the whole-value text input is gone."""
    app = _app(presets_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _url("dcs-a-01", tab=1))
    rows = _rows(page, INSTANCE_PATH)
    labels = [label for label, _checked in rows]
    assert labels[0] == "config/presets.yaml", labels
    for expected in ("config/presets/extra.yaml", "config/presets_weather.yaml"):
        assert expected in labels, (expected, labels)
    assert labels.count("config/presets.yaml") == 1, "a label is offered twice"
    assert _free_row(page, INSTANCE_PATH) == "", "the free-text row is missing"
    assert not _has_bare_value_input(page, INSTANCE_PATH), \
        "the setting still renders a whole-value text input"


@pytest.mark.asyncio
async def test_the_setting_is_offered_even_when_the_block_does_not_carry_it(presets_dir, presets_data):  # noqa: F811
    """The owner's report: with the key absent the control was not on the page at all, so there was no
    way to select a preset file — only a choice inside the ``+ Add`` picker."""
    path: Path = presets_dir / "nodes.yaml"
    path.write_text(NODES_YAML.replace("          presets: config/presets.yaml\n", ""), encoding="utf-8")
    app = _app(presets_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _url("dcs-a-01", tab=1))
    rows = _rows(page, INSTANCE_PATH)
    assert rows, "the presets control is not rendered on a block that does not set it"
    assert not any(checked for _label, checked in rows), "an unset field rendered a checked row"
    assert _free_row(page, INSTANCE_PATH) == ""


# =================================================== 2. what a save writes for each selection


@pytest.mark.asyncio
async def test_one_checked_file_saves_a_string(presets_dir, presets_data):  # noqa: F811
    path: Path = presets_dir / "nodes.yaml"
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(
            _page_form(page, INSTANCE_PATH, checked=["config/presets_weather.yaml"])))
    assert reply.status_code == 303, reply.text
    text = path.read_text(encoding="utf-8")
    assert "presets: config/presets_weather.yaml\n" in text, text


@pytest.mark.asyncio
async def test_two_checked_files_save_a_list_in_the_order_shown(presets_dir, presets_data):  # noqa: F811
    path: Path = presets_dir / "nodes.yaml"
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(_page_form(
            page, INSTANCE_PATH, checked=["config/presets_weather.yaml", "config/presets/extra.yaml"])))
    assert reply.status_code == 303, reply.text
    text = path.read_text(encoding="utf-8")
    block = _mizedit_block(text)
    assert "- config/presets_weather.yaml" in block, block
    assert block.index("- config/presets_weather.yaml") < block.index("- config/presets/extra.yaml"), block


@pytest.mark.asyncio
async def test_unchecking_every_file_removes_the_key(presets_dir, presets_data):  # noqa: F811
    """No choice at all is the DEFAULT — the key is gone, which is what makes the extension read
    ``presets.yaml`` in the instance's own config directory."""
    path: Path = presets_dir / "nodes.yaml"
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(_page_form(page, INSTANCE_PATH)))
    assert reply.status_code == 303, reply.text
    text = path.read_text(encoding="utf-8")
    assert "presets:" not in _mizedit_block(text), text


# ==================================== 3. a hand-written path survives an untouched save


@pytest.mark.asyncio
async def test_a_hand_written_string_is_its_own_checked_row_and_survives(presets_dir, presets_data):  # noqa: F811
    path: Path = presets_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(
            _page_form(page, INSTANCE_PATH, checked=["config/presets.yaml"])))
    rows = dict(_rows(page, INSTANCE_PATH))
    assert rows.get("config/presets.yaml") is True, "the document's own path is not checked"
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an untouched string value was rewritten"


@pytest.mark.asyncio
async def test_a_hand_written_list_survives_an_untouched_save(presets_dir, presets_data):  # noqa: F811
    """Both rows the remote node names are on the page, both checked, and a save that touches nothing
    leaves the LIST shape exactly as it was."""
    path: Path = presets_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(presets_dir)
    url = _url("dcs-b-01")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(_page_form(
            page, NODE_PATH,
            checked=["config/presets_weather.yaml", "config/presets/remote-only.yaml"])))
    rows = dict(_rows(page, NODE_PATH))
    assert rows.get("config/presets/remote-only.yaml") is True, \
        "a path the console cannot discover lost its row"
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an untouched list value was rewritten"


@pytest.mark.asyncio
async def test_a_path_typed_into_the_free_row_is_saved_verbatim(presets_dir, presets_data):  # noqa: F811
    path: Path = presets_dir / "nodes.yaml"
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    typed = "MyPresets/odd name.yaml"
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(
            _page_form(page, INSTANCE_PATH, typed=typed)))
    assert reply.status_code == 303, reply.text
    text = path.read_text(encoding="utf-8")
    assert f"presets: {typed}\n" in text, text


@pytest.mark.asyncio
async def test_an_untouched_page_saves_the_document_byte_identically(presets_dir, presets_data):  # noqa: F811
    path: Path = presets_dir / "nodes.yaml"
    before = path.read_bytes()
    app = _app(presets_dir)
    url = _url("dcs-a-01", tab=1)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, url)
        reply = await client.post(url, **_repeated(
            _page_form(page, INSTANCE_PATH, checked=["config/presets.yaml"])))
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "an untouched page rewrote nodes.yaml"


# ============================== 4. both scopes render it; no other field's control changed


@pytest.mark.asyncio
async def test_the_node_wide_block_renders_the_same_control(presets_dir, presets_data):  # noqa: F811
    app = _app(presets_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _url("dcs-b-01"))
    rows = _rows(page, NODE_PATH)
    assert [label for label, _checked in rows][0] == "config/presets.yaml", rows
    assert _free_row(page, NODE_PATH) == ""
    assert not _has_bare_value_input(page, NODE_PATH)


@pytest.mark.asyncio
async def test_no_other_field_renders_differently(presets_dir, presets_data):  # noqa: F811
    """The same extension's ``timezone`` is still a plain text box and its ``enabled`` is still the
    hidden-``0``/checkbox-``1`` pair — the change is one field's control."""
    app = _app(presets_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _url("dcs-a-01", tab=1))
    timezone = re.search(
        r'<input type="text" name="f\.dcs-a-01\.instances\.Syria\.extensions\.MizEdit\.timezone"', page)
    assert timezone, "another MizEdit field lost its ordinary control"
    enabled = re.search(
        r'<input type="hidden" name="f\.dcs-a-01\.instances\.Syria\.extensions\.MizEdit\.enabled" '
        r'value="0">\s*<input type="checkbox" '
        r'name="f\.dcs-a-01\.instances\.Syria\.extensions\.MizEdit\.enabled" value="1" checked>', page)
    assert enabled, "another MizEdit field's boolean pair changed"
