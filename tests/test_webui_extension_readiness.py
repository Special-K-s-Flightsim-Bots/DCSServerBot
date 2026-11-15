"""THE EXTENSION CARD'S THREE STATES — computed readiness, seeded defaults, the live switch.

WHAT THIS PINS

* READINESS IS COMPUTED, never "a block exists". The extension's own ``NODE_CONFIG_DICT`` /
  ``CONFIG_DICT`` entries marked ``required: True`` are the authority — exactly what the Discord
  command's modal asks for before it enables — and the schema's ``required: true`` is read only when
  the extension declares no dict for that scope. An SRS node block with no ``installation`` is
  therefore INCOMPLETE even though the schema accepts it, and a GitHub block without ``repo``/
  ``target`` is incomplete through the schema fallback (its module cannot be imported here).
* THE THREE CARD STATES: ``not configured`` (the ``+ Add`` tile), ``incomplete`` (the switch in the
  shared component's non-actionable state plus ONE warning naming the empty keys and the tab holding
  them, the cogwheel as the door), ``complete`` (a live switch).
* ON/OFF BELONGS ON THE INSTANCE TAB: the Node-wide tab's cards carry no switch at all.
* A CRAFTED enable/disable for an incomplete card is REFUSED with the file byte-identical.
* "ADD" SEEDS THE DICT'S DECLARED DEFAULTS, and an extension whose required key has NO default is
  refused here (named) instead of being refused by the schema downstream.
* THE SWITCH IS LIVE when the action is available — ``call_action`` receives the ``{"Node", "Instance"}``
  pair read from the FILE, and ``result.message`` is the notice — and falls back to the config patch
  (with the apply offer) when it is not.

The CONTROL pins are the default extensions (``LogAnalyser``/``Cloud`` run with no block), the
instance switch still being a POST, and the fallback path still writing the flag.
"""
from __future__ import annotations

import html
import types

import pytest
from ruamel.yaml import YAML

from conftest import asgi_client
from services.webservice import config_apply
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import _app, _page_url, node_data, node_dir  # noqa: F401
from test_webui_node_page_extension_switch import (_add_option_form, _card, _cards, _get,
                                                   _switch, _switch_form, _write_nodes)


def _nodes(*, srs_installation: str = "", github: bool = False,
           srs_node_block: bool = True) -> str:
    """A ``nodes.yaml`` holding the card states side by side on ONE instance tab.

    ``dcs-a-01``'s node block configures SRS — ``SRS: {}`` unless *srs_installation* names an existing
    directory, which is what makes the card complete (*srs_node_block* FALSE drops the node-level block
    entirely, so SRS becomes an ADD choice there). The Syria instance configures SRS (every required
    ``port``/``gui_server``/``autoconnect`` value filled) and Trackfile (complete). Neither
    ``LogAnalyser`` nor ``Cloud`` is configured anywhere: they are the bot's DEFAULT extensions and are
    ON regardless.

    *github* adds a GitHub block holding ONLY a key its schema defines (``filter``) and neither
    ``repo`` nor ``target`` — which the schema itself refuses, so that document is for RENDER pins
    only: a save of it would be refused by the validator, not by the readiness gate.
    """
    if not srs_node_block:
        extensions_block = ""
    elif srs_installation:
        extensions_block = f"  extensions:\n    SRS:\n      installation: {srs_installation}\n"
    else:
        extensions_block = "  extensions:\n    SRS: {}\n"
    github_block = "        GitHub:\n          filter: '*.lua'\n" if github else ""
    return (
        "dcs-a-01:\n"
        "  listen_address: 127.0.0.1\n"
        "  listen_port: 10042\n"
        f"{extensions_block}"
        "  instances:\n"
        "    Syria:\n"
        "      home: /home/a-syria\n"
        "      bot_port: 6666\n"
        "      webgui_port: 8086\n"
        "      dcs_port: 10306\n"
        "      extensions:\n"
        "        SRS:\n"
        "          port: 5002\n"
        "          gui_server: false\n"
        "          autoconnect: true\n"
        f"{github_block}"
        "        Trackfile:\n"
        "          enabled: true\n"
        "          target: /opt/track\n"
        "dcs-b-01:\n"
        "  listen_address: 127.0.0.1\n"
        "  listen_port: 10043\n"
    )


def _extension_block(path, name: str):
    """The ``extensions.<name>`` mapping of the Syria instance, read back from the file."""
    document = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    return (((document or {}).get("dcs-a-01") or {}).get("instances") or {}).get("Syria", {}) \
        .get("extensions", {}).get(name)


def _body(page: str, name: str) -> str:
    """One card's body as TEXT (a missing card is a failure, not a silent skip)."""
    card = _card(page, name)
    assert card is not None, f"no card for {name}"
    return html.unescape(card["body"])


def _instance_cards(page: str) -> list[dict]:
    """Every extension card of the page, minus the ``+ Add`` tile (which is a card with ``add``)."""
    return [card for card in _cards(page) if "add" not in card["classes"].split()]


def _unresolvable(*_args, **_kwargs):
    """A ``str_to_class`` that cannot resolve anything, as a machine without the extension's deps."""
    raise ImportError("this extension's module is not installed here")


# ======================================================= 1. readiness per scope, computed

@pytest.mark.asyncio
async def test_an_srs_with_an_empty_node_block_is_incomplete_and_names_installation(
        node_dir, node_data):  # noqa: F811
    """``NODE_CONFIG_DICT['installation']`` is ``required: True``, so an SRS whose node block is ``{}``
    is NOT ready even though its schema validates clean: the card is locked (no form, no POST), says
    so, names the empty key AND the Node-wide tab it is filled on, and keeps its cogwheel as the door
    it already had — the lock adds no control of its own.
    """
    _write_nodes(node_dir, _nodes())
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    card = _card(page, "SRS")
    assert card is not None, "SRS is not a card on the instance tab"
    body = _body(page, "SRS")
    assert node_settings_page.NODE_PAGE_EXTENSION_NEEDS_SETUP in body, "the lock's word is missing"
    assert "installation" in body, "the warning does not name the empty key"
    assert "Node-wide tab" in body, "the warning does not name the tab holding the value"
    assert _switch_form(page, "SRS") == {}, "an incomplete card posted a switch form"
    assert 'class="st-sw off-fixed"' in card["body"], "the locked switch is not the inert span"
    assert 'class="st-cog"' in card["body"], "the cogwheel (the door) is gone"


@pytest.mark.asyncio
async def test_an_srs_with_installation_set_is_complete_and_switchable(node_dir, node_data):  # noqa: F811
    """The same card with the required key FILLED is complete: no lock, no warning, and the switch is
    the plain turn-OFF form (CONTROL — readiness must not lock a configured extension)."""
    _write_nodes(node_dir, _nodes(srs_installation=node_dir.as_posix()))
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    body = _body(page, "SRS")
    assert node_settings_page.NODE_PAGE_EXTENSION_NEEDS_SETUP not in body, "a ready card is locked"
    assert "st-warn" not in _card(page, "SRS")["body"], "a ready card carries a warning line"
    assert _switch_form(page, "SRS")["fields"][
        node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
        == node_settings_page.NODE_PAGE_EXTENSION_DISABLE


@pytest.mark.asyncio
async def test_a_github_block_without_repo_and_target_is_incomplete_from_the_schema(
        node_dir, node_data, monkeypatch):  # noqa: F811
    """When the extension's class cannot be resolved the SCHEMA is the fallback: ``github_schema``
    marks ``repo`` and ``target`` ``required: true`` and the block sets neither, so both are named and
    both are attributed to the extension's own tab (GitHub is instance-scoped — the node schema has no
    controls for it). The block also proves the lock follows the REQUIRED keys, not block presence.
    """
    _write_nodes(node_dir, _nodes(github=True))
    node_settings_page._EXTENSION_DICTS.clear()
    monkeypatch.setattr("core.utils.str_to_class", _unresolvable)
    try:
        app = _app(node_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _get(client, _page_url("dcs-a-01", tab=1))
    finally:
        node_settings_page._EXTENSION_DICTS.clear()
    body = _body(page, "GitHub")
    assert node_settings_page.NODE_PAGE_EXTENSION_NEEDS_SETUP in body
    assert "repo" in body and "target" in body, "the warning does not name both empty keys"
    assert "branch" not in body, "a key the block FILLED was reported as empty"
    assert "own tab" in body, "the warning does not name the tab holding the values"
    assert _switch_form(page, "GitHub") == {}, "an incomplete card posted a switch form"


@pytest.mark.asyncio
async def test_a_default_extension_with_no_block_is_on_and_switchable(node_dir, node_data):  # noqa: F811
    """CONTROL. ``LogAnalyser`` and ``Cloud`` are DEFAULT extensions: the runtime loads them with no
    block, neither declares a required key, so they are ON, unlocked, and their switch is a POST."""
    _write_nodes(node_dir, _nodes())
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    for name in ("LogAnalyser", "Cloud"):
        card = _card(page, name)
        assert card is not None, f"{name} is not a card"
        assert "off" not in card["classes"].split(), f"{name} rendered OFF while it runs"
        assert node_settings_page.NODE_PAGE_EXTENSION_NEEDS_SETUP not in _body(page, name)
        assert _switch_form(page, name)["fields"][
            node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
            == node_settings_page.NODE_PAGE_EXTENSION_DISABLE, f"{name}'s switch is not a POST"


# ============================================================ 2. ON/OFF is instance-tab only

@pytest.mark.asyncio
async def test_the_node_wide_tab_carries_no_switch_and_the_instance_tab_does(node_dir, node_data):  # noqa: F811
    """The Node-wide tab's cards show the shared values only — NO switch, no form, no POST — because
    ``enable()`` runs on a Server, ``init_extensions()`` needs an instance block to trigger a load, and
    the action writes the Instance scope. CONTROL: the same page's instance tab still posts one.
    """
    _write_nodes(node_dir, _nodes(srs_installation=node_dir.as_posix()))
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        node_wide = await _get(client, _page_url("dcs-a-01"))
        instance = await _get(client, _page_url("dcs-a-01", tab=1))
    cards = _instance_cards(node_wide)
    assert cards, "the Node-wide tab rendered no extension cards at all"
    for card in cards:
        assert "st-sw" not in card["body"], "a Node-wide card grew a switch back"
        assert "<form" not in card["body"], "a Node-wide card grew a form back"
    # CONTROL: the instance tab's configured extension is still a POST
    assert _switch_form(instance, "Trackfile")


# ============================================== 3. a crafted switch on an incomplete card is refused

@pytest.mark.asyncio
async def test_a_crafted_enable_or_disable_on_an_incomplete_card_is_refused(node_dir, node_data):  # noqa: F811
    """The lock is a SERVER rule, not a screen: a POST that skips the card (valid CSRF, valid revision,
    the right scope) for an extension whose required value is empty is REFUSED with the file
    byte-identical — both directions, the same discipline the scope guard already applies.
    """
    _write_nodes(node_dir, _nodes())
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        enable = await _switch(client, "dcs-a-01", 1, "SRS",
                               node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                               node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
        disable = await _switch(client, "dcs-a-01", 1, "SRS",
                                node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                                node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
        shown = await _get(client, _page_url("dcs-a-01", tab=1))
    assert enable.status_code == disable.status_code == 303
    assert path.read_text(encoding="utf-8") == before, "a refused switch wrote to the file"
    body = html.unescape(shown)
    assert "is not ready to switch on" in body, "the refusal is not stated"
    assert "installation" in body, "the refusal does not name the empty key"


# ================================================================= 4. Add seeds the declared defaults

@pytest.mark.asyncio
async def test_add_seeds_the_dicts_declared_defaults(node_dir, node_data):  # noqa: F811
    """The ``+ Add`` control writes the block seeded from the extension's OWN dict ``default`` values
    (never an invented value, never a schema ``example``), so one click yields a card whose values are
    editable in place: SRS's node dict declares ``installation``, ``beta`` and ``autoupdate``.
    """
    from core.utils import str_to_class

    declared = str_to_class("extensions.srs.extension.SRS").NODE_CONFIG_DICT
    expected = {key: spec["default"] for key, spec in declared.items() if "default" in spec}
    assert expected, "SRS declares no defaults to seed"

    _write_nodes(node_dir, _nodes(srs_node_block=False))
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "SRS",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert reply.status_code == 303, reply.text
    document = YAML(typ="safe").load((node_dir / "nodes.yaml").read_text(encoding="utf-8"))
    written = document["dcs-a-01"]["extensions"]["SRS"]
    assert written == expected, f"the block was not seeded from the dict's defaults: {written}"


@pytest.mark.asyncio
async def test_an_add_whose_required_key_has_no_default_is_refused_and_names_it(
        node_dir, node_data):  # noqa: F811
    """GitHub's ``repo`` declares no default, so a block created from the card could never be valid:
    the ADD is REFUSED before the patch, the file stays byte-identical, and the sentence names the key
    and the way out (Expert Mode) instead of letting the schema refusal speak in pykwalify's words.
    """
    _write_nodes(node_dir, _nodes())
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await _switch(client, "dcs-a-01", 1, "DKS",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
        shown = await _get(client, _page_url("dcs-a-01", tab=1))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a refused add wrote to the file"
    body = html.unescape(shown)
    assert "no default value is declared for key" in body, "the refusal is not stated"
    assert "Expert Mode" in body, "the refusal does not name the way out"


# ============================================================ 5. the live switch and its fallback

@pytest.mark.asyncio
async def test_the_live_enable_calls_the_action_with_the_pair_read_from_the_file(
        node_dir, node_data, monkeypatch):  # noqa: F811
    """When ``enable_extension`` is available the switch does NOT patch the file: it calls the action
    with the instance's SERVER NAME and the ``{"Node", "Instance"}`` pair read from ``nodes.yaml`` (the
    process's ``locals`` are stale until its node restarts, which is what that parameter exists for),
    and renders ``result.message`` in the console's one-shot notice — success and refusal alike.
    """
    _write_nodes(node_dir, _nodes(srs_installation=node_dir.as_posix()))
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    calls: list[dict] = []

    async def fake_call(qualname, ctx, **params):
        calls.append({"qualname": qualname, "params": params})
        return types.SimpleNamespace(success=True, message="live-action-message")

    monkeypatch.setattr(node_settings_page, "action_available", lambda _q: True)
    monkeypatch.setattr(node_settings_page, "call_action", fake_call)
    monkeypatch.setattr(node_settings_page, "_instance_server_name",
                        lambda *_a, **_k: "SRS-1")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await _switch(client, "dcs-a-01", 1, "SRS",
                              node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
        shown = await _get(client, _page_url("dcs-a-01", tab=1))
    assert reply.status_code == 303, reply.text
    assert len(calls) == 1, calls
    assert calls[0]["qualname"] == node_settings_page.NODE_PAGE_EXTENSION_ENABLE_ACTION
    params = calls[0]["params"]
    assert params["server_name"] == "SRS-1"
    assert params["extension"] == "SRS"
    # the pair is the FILE's, node block under the instance block
    assert params["config"]["Node"]["installation"] == node_dir.as_posix()
    assert params["config"]["Instance"]["port"] == 5002
    # the action writes the flag, so the console patches nothing
    assert path.read_text(encoding="utf-8") == before, "the live branch patched the file"
    assert "live-action-message" in html.unescape(shown), "result.message is not the notice"


@pytest.mark.asyncio
async def test_the_disabled_action_falls_back_to_the_config_patch_and_offers_the_apply(
        node_dir, node_data, monkeypatch):  # noqa: F811
    """CONTROL. With no action available (the bot is down, a headless run) the switch keeps today's
    path: ``enabled: false`` is written into that instance's block through the one document patch, and
    the notice offers the apply control — the file is a boot file, so the operator is told it takes
    effect on the next start.
    """
    _write_nodes(node_dir, _nodes(srs_installation=node_dir.as_posix()))
    path = node_dir / "nodes.yaml"
    monkeypatch.setattr(node_settings_page, "action_available", lambda _q: False)
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await _switch(client, "dcs-a-01", 1, "SRS",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
        off_page = await _get(client, _page_url("dcs-a-01", tab=1))
    assert reply.status_code == 303, reply.text
    assert _extension_block(path, "SRS")["enabled"] is False, "the flag did not land in the file"
    assert config_apply.APPLY_LABEL in html.unescape(off_page), "the apply offer is missing"
    # CONTROL: the re-read shows the card OFF, and the switch still posts the opposite action
    card = _card(off_page, "SRS")
    assert card is not None and "off" in card["classes"].split(), "the card is not shown OFF"
    assert _switch_form(off_page, "SRS")["fields"][
        node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
        == node_settings_page.NODE_PAGE_EXTENSION_ENABLE


# ================================================================ 6. the readiness helper itself

def test_the_readiness_helper_reads_the_effective_value_of_both_blocks():
    """``_extension_missing_keys`` merges the instance block OVER the node block (the order the runtime
    resolves an extension's config in) and attributes each empty key to the tab that holds it."""
    missing = node_settings_page._extension_missing_keys(
        "SRS", (), node_block={"installation": ""},
        instance_block={"port": 5002, "gui_server": False, "autoconnect": True}, node_scope=True)
    assert missing == [("installation", node_settings_page.NODE_PAGE_DICT_SCOPE_NODE)]
    # the node-level value SATISFIES the key: the merge is over, not "the instance block only"
    assert node_settings_page._extension_missing_keys(
        "SRS", (), node_block={"installation": "/opt/srs"},
        instance_block={"port": 5002, "gui_server": False, "autoconnect": True}) == []
    # ``False`` is a VALUE, not a hole — a required bool default is a decision
    assert node_settings_page._is_empty(False) is False
    assert node_settings_page._is_empty("   ") is True


def test_the_instance_server_name_is_not_guessed_from_the_instance_name(monkeypatch):
    """The live switch needs the INSTANCE's server name; it comes from the console's own scoped view,
    and a name it does not carry answers ``""`` (the fallback's condition, never a guess)."""
    from test_webui_dashboard import _source, node, server

    nodes = {"dcs-a-01": node("dcs-a-01", master=True), "dcs-b-01": node("dcs-b-01", master=False)}
    source = _source(nodes=nodes, node_obj=nodes["dcs-a-01"],
                     servers=(server("SRS-1", node="dcs-a-01", instance="Syria"),))
    monkeypatch.setattr(node_settings_page.dashboard_page, "request_source",
                        lambda _request: source)
    assert node_settings_page._instance_server_name(object(), "dcs-a-01", "Syria") == "SRS-1"
    assert node_settings_page._instance_server_name(object(), "dcs-a-01", "Caucasus") == ""
    assert node_settings_page._instance_server_name(object(), "dcs-b-01", "Syria") == ""
