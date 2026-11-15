"""The Configuration tab renders through the SAME editor shell as the schema-driven config views.

THE ARCHITECTURAL RULE THIS PINS: config files of different FORMATS (yaml, lua) may need
different PARSERS, but they must not grow different EDITORS. ``serverSettings.lua`` has no schema, so
the Configuration tab is DECLARATION-driven (``readmodels.serverconfig``) where a plugin/service file
is SCHEMA-driven (``configgen``) — the parser differs. The editor does not: the tab now draws the
SAME ``st-head`` / ``st-bar`` / ``st-layout`` shell and the SAME collapsible ``<details
class="st-block">`` blocks the scoped cogwheel view draws, from the ONE shared macro
(``templates/_editor.html``, imported by BOTH ``_scope_fields.html`` and ``server_detail.html``).

WHAT THIS FILE ASSERTS, and what it deliberately does NOT:

* the structural markers the yaml editor's own suite asserts (``class="st-layout"``, ``<details
  class="st-block"``, ``class="st-yaml"``);
* that the byte-honest write path is UNCHANGED (the DCS face still posts the one action; a no-op form
  still submits NO change);
* that the gate and the 403 message are UNCHANGED;
* and, plainly, that the Expert Mode TOGGLE is NOT rendered here — because the console never opens or
  writes this lua file (the bot's own action does), so a text editor over it would be a SECOND write
  path, which the console forbids. This is the format-specific part that stayed format-specific, and
  it is stated on the page, not faked.
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice import readmodels
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from test_webui_dashboard import _login
from test_webui_server_config import (  # noqa: F401 - fixtures imported on purpose
    BASE_SETTINGS, _full_form, _namespace_server, app_with, config_dir, get,
)


def _config_html(config_dir, *, settings=None, roles=("Admin",)):
    server_obj = _namespace_server(settings=settings if settings is not None else BASE_SETTINGS)
    app, _ = app_with(config_dir, list(roles), servers=[server_obj])
    return app


#: the shared row's OPEN tag, `class` aside: `<div class="st-row …" data-cfg-field …>`. The SAME
#: marker the scoped editor's own suite looks for (``test_webui_node_extension_tiles``).
ROW_OPEN = re.compile(r'<div class="st-row(?P<cls>[^"]*)"(?P<attrs>[^>]*)>')


def _config_rows(page: str) -> list[tuple[str, str]]:
    """Every shared row of the Configuration tab, as ``(attrs, row_html)`` — one per field.

    A row runs from its own open tag to the next, so each block is the whole ``st-lab``/``st-w``
    pair. Splitting this way (rather than on ``st-lab``) keeps the row's ``data-cfg-*`` attributes
    with the row they belong to.
    """
    marks = list(ROW_OPEN.finditer(page))
    rows: list[tuple[str, str]] = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(page)
        rows.append((mark.group("attrs"), page[mark.start():end]))
    return rows


# ------------------------------------------------------------------ 1. the shared editor markers

@pytest.mark.asyncio
async def test_the_configuration_tab_draws_the_same_shell_as_the_yaml_editor(config_dir):
    """The SAME layout / collapsible-block / file-panel markers the scoped view's suite asserts."""
    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    # the layout shell: head, bar and the two-column layout with the file's aside
    assert 'class="st-head"' in page, "the shared editor head is missing"
    assert 'class="st-bar"' in page, "the shared editor bar is missing"
    assert 'class="st-layout"' in page, "the shared two-column layout is missing"
    assert 'class="st-aside"' in page, "the file's aside is missing"
    # the collapsible blocks — a native <details>, so the collapse works with JavaScript OFF
    assert '<details class="st-block"' in page, "the collapsible block marker is missing"
    # the read-only file panel the yaml editor also ships (its no-JS state)
    assert 'class="st-yaml"' in page, "the read-only file panel is missing"
    # a group heading lives in a block's summary, and its fields are still rendered (a collapse
    # cannot drop a value, because the value is server-rendered inside the <details>)
    assert "Identity &amp; access" in page
    assert 'name="maxPlayers"' in page


@pytest.mark.asyncio
async def test_every_config_field_is_the_shared_editor_row(config_dir):
    """Every declared field is ONE ``st-row`` — the SAME row the schema editor draws.

    editors'. The row is now the SHARED ``.st-row`` (``_editor.field_row``), carrying the SAME
    ``data-cfg-field`` / ``data-cfg-kind`` the scoped editor's rows carry, so the two surfaces cannot
    drift apart. The fact sits in ``.st-lab`` and the control in ``.st-w``, on EVERY row.
    """
    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    rows = _config_rows(page)
    # one row per declared editable field, plus the channels and coalition rows of this tab
    assert len(rows) >= len(readmodels.editable_keys()), \
        "a declared field did not render as its own row"
    # every row is the shared row: the SAME data hook the schema editor's rows assert
    assert all("data-cfg-field" in attrs for attrs, _row in rows), \
        "a config row is missing the shared `data-cfg-field` hook"
    assert all("data-cfg-kind=" in attrs for attrs, _row in rows), \
        "a config row is missing the shared `data-cfg-kind` the schema editor's rows carry"
    # ... and EVERY row puts its fact in `.st-lab` and its control in `.st-w`, one pair per row
    assert page.count('<div class="st-lab">') == len(rows) == page.count('<div class="st-w">'), \
        "a row does not use the shared label/control arrangement"


@pytest.mark.asyncio
async def test_a_checkbox_row_and_a_text_row_share_one_control_column(config_dir):
    """Inside ONE block, a bool (a checkbox) and a number (a text box) share the arrangement.

    The bug the operator saw was exactly this: a checkbox row and an input row in the same block did not
    line up. Both are now the shared row, whose control column (``.st-w``) is what lines them up.
    """
    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    pairs = _config_rows(page)

    def row_for(key: str) -> tuple[str, str]:
        found = [(attrs, html) for attrs, html in pairs if f'name="{key}"' in html]
        assert len(found) == 1, f"expected exactly one row for {key!r}, found {len(found)}"
        return found[0]

    number_attrs, number_row = row_for("maxPlayers")
    check_attrs, checkbox_row = row_for("isPublic")
    # the two live in the SAME block (no block closes between them) — so their alignment is a fact
    span = page[page.index('name="maxPlayers"'):page.index('name="isPublic"')]
    assert "</details>" not in span, "the two rows must sit in one block"
    assert 'data-cfg-kind="int"' in number_attrs
    assert 'data-cfg-kind="bool"' in check_attrs
    # ... and they carry the SAME label-then-control arrangement
    for html in (number_row, checkbox_row):
        assert html.index('<div class="st-lab">') < html.index('<div class="st-w">'), \
            "the control column must follow the fact column on every row"
    assert '<input type="checkbox"' in checkbox_row, "the bool row is a checkbox"
    assert 'type="number"' in number_row, "the int row is a number box"


@pytest.mark.asyncio
async def test_every_config_group_is_one_collapsible_block(config_dir):
    """One ``st-block`` per declared group — the SAME control a schema ``map`` renders."""
    from services.webservice.readmodels import serverconfig as server_config

    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    # the DCS groups (identity/behaviour/requirements/restrictions/anticheat) are all blocks
    assert page.count('<details class="st-block"') >= len(server_config.GROUP_ORDER)
    for group in server_config.GROUP_ORDER:
        label = server_config.GROUP_LABELS[group]
        assert label in page or label.replace("&", "&amp;") in page, label


# --------------------------------------------------- 2. the Expert Mode toggle is honestly ABSENT

@pytest.mark.asyncio
async def test_the_config_tab_renders_no_expert_toggle_and_says_why(config_dir):
    """The lua file is written by the bot's action, not by the console — so there is no text editor.

    A toggle here would be a SECOND write path (the console forbids it). The page states the reason
    in the aside; this pins that it is stated and that no toggle is drawn (a toggle that could not
    save would lie).
    """
    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert "data-cfg-expert-toggle" not in page, \
        "an Expert Mode toggle was drawn for a file the console never writes — it could only lie"
    assert "data-cfg-expert" not in page, "the expert carrier must not ship on this page"
    # the honest reason IS on the page (a translated sentence, rendered through the request's _)
    assert "no Expert Mode text editor here" in page, "the reason for the absence is not stated"
    # the panel is the read-only one the yaml editor shows with JavaScript off
    assert 'class="st-yaml"' in page and "read-only" in page


# --------------------------------------------------------------- 3. the write path is unchanged

@pytest.mark.asyncio
async def test_the_tab_still_posts_the_one_declared_action(config_dir):
    app = _config_html(config_dir)
    page = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert f'action="{actions_page.SERVER_CONFIG_PATH}"' in page, "the DCS form must post the one action"
    assert 'id="cfg-form"' in page, "the DCS form keeps its id"
    # the Save references that form by id (the scoped editor's own technique), and is the primary chip
    assert re.search(r'<button[^>]*form="cfg-form"[^>]*class="primary[^"]*"[^>]*>Save</button>', page), \
        "the tab's Save must reference the form by id and stay the primary control"


@pytest.mark.asyncio
async def test_a_no_op_config_save_submits_no_change(config_dir, monkeypatch):
    """Byte-honesty: a form posted back at the current values hands the action an EMPTY change set."""
    from core.action_results import ServerConfigResult

    app = _config_html(config_dir)
    calls: list[tuple[str, dict]] = []

    async def fake_call(qualname, ctx, /, *, audit_result=False, **params):
        calls.append((qualname, params))
        return ServerConfigResult(success=True, server_name="DCS Syria Campaign", message="nothing changed")

    monkeypatch.setattr(actions_page, "call_action", fake_call)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        payload = {"server": "DCS Syria Campaign", "_csrf_token": token}
        payload.update(_full_form())
        reply = await client.post(actions_page.SERVER_CONFIG_PATH, data=payload)
    assert reply.status_code == 303
    assert calls and calls[0][0] == "set_server_config"
    assert calls[0][1]["values"] == {}, "a no-op form must submit no change"


# --------------------------------------------------------------- 4. the gate and the 403 unchanged

@pytest.mark.asyncio
async def test_a_dcs_admin_is_still_refused_the_tab_with_the_console_403(config_dir):
    app = _config_html(config_dir, roles=("DCS Admin",))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        refused = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert refused.status_code == 403, refused.status_code
    assert "Not authorized" in refused.text
    # the gate itself is untouched
    detail_page.capabilities()
    from services.webservice import permissions
    assert permissions.roles_for_capability(detail_page.CONFIG_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(detail_page.CONFIG_CAPABILITY) is True


# --------------------------------------------------------------- 5. the new chrome renders in German

@pytest.mark.asyncio
async def test_the_config_editor_chrome_renders_in_german(config_dir):
    from services.webservice import i18n

    app = _config_html(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = (await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")).text
    # the shared shell's chrome and the new aside sentence are German
    assert "Konfiguration — die DCS-Seite" in page
    assert "Identität &amp; Zugriff" in page
    assert "keinen Expert-Mode-Texteditor" in page
    assert "no Expert Mode text editor here" not in page
