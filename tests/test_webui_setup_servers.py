"""Step 8 of the tour — DCS servers: one screen with one row per detected Saved Games instance.

TWO LAYERS, both over the shipped code:

* the VIEW is driven through a real ASGI request against the shipped setup application, so the rows,
  the per-row fields, the derived bot port, the channel line and the rail's "DCS servers" row are
  the shipped ones and not a description of them;
* the ANSWER RESOLUTION (:func:`services.webservice.setup.servers.resolve_rows`) and the config
  shapes (:func:`instance_blocks` / :func:`servers_entries` / :func:`scheduler_entries`) are driven
  directly, so the classification of an incomplete row and the parity with the installer's own
  builders are pinned.

DETECTION IS HOST-DEPENDENT. ``findDCSInstances`` answers ``{}`` off Windows, so the empty case is
exercised FOR REAL here (asserted, not simulated) and the populated case seeds the draft's scan —
the same shape a Windows host's probe produces. The one probe itself is not exercised on this host;
see the module docstring note in the report.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import socket

from html import escape as html_escape

import pytest
from babel.messages import pofile

from conftest import asgi_client
from core.utils import setup_config
from services.webservice.setup import node as node_step
from services.webservice.setup import review as review_step
from services.webservice.setup import servers as servers_step
from services.webservice.setup import tour
from services.webservice.setup.app import SETUP_LISTEN, SETUP_PATH, create_setup_app
from services.webservice.setup.state import SetupDraft, draft_path
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH, rail_steps
from services.webservice.setup.window import SetupWindow

CODE = "ABCD2345"
TOKEN_RE = re.compile(r'name="_csrf_token" value="([^"]+)"')
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
#: the languages that ship a ``webui`` catalog. English is the source language and the msgid, so it
#: ships none: its strings are the msgids themselves.
CATALOGS = ("cn", "de", "es", "ru")

#: Two instances, one named and one in the installer's "give it a name" case.
TWO_INSTANCES = {"DCS.serverrelease": "SRS-1", "DCS.openbeta": "DCS Server"}
NODE_ANSWERED = {"listen_port": 10042, "no_dcs": False, "dcs_installation": "/opt/dcs"}


def build(config_dir, *, detection: node_step.Detection | None = None):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    app = create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN)
    if detection is not None:
        app.state.setup_node_detection = detection
    return app, window


async def enter(client, code: str = CODE):
    assert (await client.get(SETUP_PATH, params={"code": code})).status_code == 303
    return await client.get(SETUP_PATH)


def token_of(html: str) -> str:
    found = TOKEN_RE.search(html)
    assert found, "the step form carries no CSRF token"
    return found.group(1)


def rail_rows(html: str) -> list[str]:
    block = re.search(r'<details class="rail".*?</details>', html, re.S)
    scope = block.group(0) if block else ""
    return re.findall(r'<span class="lbl">([^<]*)</span>', scope)


def scanned(*, detected=TWO_INSTANCES, rows: dict | None = None) -> dict:
    """The draft's ``servers`` block as the tour writes it after a scan."""
    data: dict = {"scanned": True}
    if rows is not None:
        data["rows"] = rows
    return servers_step.non_secret_document(
        servers_step.answers_from_mapping(data, detected=detected))


def seed(config_dir, *, step: int = 7, discord: bool = True, node: dict | None = None,
         servers: dict | None = None) -> SetupDraft:
    draft = SetupDraft(step=step, discord=discord)
    draft.node = dict(NODE_ANSWERED if node is None else node)
    if servers is not None:
        draft.servers = servers
    draft.save(config_dir)
    return draft


def row_form(token: str, **fields) -> dict:
    data = {"_csrf_token": token, "n": "8"}
    data.update(fields)
    return data


def seed_roles(config_dir, *, central_admin: bool) -> None:
    """Write step 6's roles block, whose ``central_admin`` answer step 8's rows read."""
    draft = SetupDraft.load(config_dir)
    draft.roles = {"roles": {"Admin": ["Admin"], "DCS Admin": ["DCS Admin"], "DCS": ["@everyone"]},
                   "audit_channel": "", "admin_channel": "555" if central_admin else "",
                   "central_admin": central_admin}
    draft.save(config_dir)


# ------------------------------------------------------------------------------- the view

@pytest.mark.asyncio
async def test_the_card_shows_one_row_per_detected_instance_with_its_fields(tmp_path):
    seed(tmp_path, servers=scanned())
    # the per-server admin channel only exists on the per-server scope (central is the default)
    seed_roles(tmp_path, central_admin=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert "Searching for existing DCS server configurations" in html
    assert "2 found." in html
    assert "DCS.serverrelease" in html and "DCS.openbeta" in html
    for marker in ('name="row_manage_0"', 'name="row_name_0"', 'name="row_autostart_0"',
                   'name="row_status_0"', 'name="row_chat_0"', 'name="row_admin_0"',
                   'name="row_manage_1"'):
        assert marker in html, marker
    # the derived bot ports, in managed order
    assert "6666" in html and "6667" in html
    # the installer's channel guide, once (its own text is escaped where it is rendered)
    assert html_escape(tour.T_SRV_CHANNELS_INTRO) in html
    assert html.count("Manage this server through DCSServerBot") == 2


@pytest.mark.asyncio
async def test_the_row_of_an_unnamed_instance_starts_with_an_empty_name(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert 'name="row_name_0" value="SRS-1"' in html
    assert 'name="row_name_1" value=""' in html


@pytest.mark.asyncio
async def test_the_no_discord_path_has_no_channel_line(tmp_path):
    seed(tmp_path, discord=False, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert 'name="row_manage_0"' in html
    assert 'name="row_status_0"' not in html and 'name="row_chat_0"' not in html


@pytest.mark.asyncio
async def test_the_nothing_detected_case_keeps_the_row_on_the_rail(tmp_path, monkeypatch):
    """Nothing detected: the step keeps its place on the rail.

    The scan is STUBBED empty so the case is identical on a bare host and on one that carries real
    installs; the probe itself is pinned separately, by its shape rather than by one machine's
    contents. The operator's only other way to add a server is the command line, so the row that
    would have had nothing to ask stays — and the screen of the next test is the one that asks.
    """
    monkeypatch.setattr(servers_step, "detect_instances", lambda: {})
    real = tmp_path / "DCS World"
    real.mkdir()
    seed(tmp_path, step=3, node={})
    app, _ = build(tmp_path, detection=node_step.Detection())
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "4"})).text
        assert "DCS servers" in rail_rows(page)
        response = await client.post(STEP_PATH, data={
            "_csrf_token": token_of(page), "n": "4", "dcs_choice": "other",
            "dcs_path": str(real), "listen_port": "10042", "dgsa_dcs": "on",
            "dgsa_discord": "on"})
        assert response.status_code == 303
        after = (await client.get(STEP_PATH, params={"n": "4"})).text
    draft = SetupDraft.load(tmp_path)
    assert draft.servers["scanned"] is True
    assert draft.servers["detected"] == {}
    assert draft.step == 4
    # row 5 is no longer dropped from the rail, and every other row is where it was
    assert "DCS servers" in rail_rows(after)
    assert rail_rows(after) == ["Language", "General", "Database", "This node",
                                "Discord — the bot", "Discord — roles and channels",
                                "Web console", "DCS servers", "What do you run?",
                                "Review and write"]


@pytest.mark.asyncio
async def test_the_empty_scan_asks_with_one_blank_row(tmp_path):
    """The screen of a scan that found nothing: the note, then ONE blank row that carries the fields
    a detected row asks for — with the Saved Games / instance name as its one name field."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    # the per-server admin channel is only offered on the per-server scope (see _servers_discord)
    seed_roles(tmp_path, central_admin=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        step = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert html_escape(tour.T_SRV_NONE) in step
    for marker in ('name="row_manage_0"', 'name="row_name_0"', 'name="row_autostart_0"',
                   'name="row_status_0"', 'name="row_chat_0"', 'name="row_admin_0"'):
        assert marker in step, marker
    assert html_escape(tour.T_SRV_ADD) in step
    assert html_escape(tour.T_SRV_ADD_INSTANCE) in step
    assert html_escape(tour.T_SRV_ADD_INSTANCE_HELP) in step
    # the channel guide the detected rows get is offered here too, with the port the row would take
    assert html_escape(tour.T_SRV_CHANNELS_INTRO) in step
    assert "6666" in step
    assert step.count('<details class="st-block" open>') == 1     # the one blank row, and it is open
    assert "Searching for existing DCS server configurations" not in step


# ---------------------------------------------------------- the placeholder on the add row's name

def test_the_field_helper_renders_a_placeholder_only_when_it_is_given():
    """The parameter is ADDITIVE: every existing call shape keeps its exact markup."""
    assert tour._field("Port", "listen_port", 10042) == (
        '<label class="field"><span class="lbl">Port</span>'
        '<input type="text" name="listen_port" value="10042"></label>')
    assert tour._field("Host", "host", "localhost", "The DNS name or IP") == (
        '<label class="field"><span class="lbl">Host</span>'
        '<input type="text" name="host" value="localhost">'
        '<span class="help">The DNS name or IP</span></label>')
    assert tour._field("Secret", "password", "", type_="password", required=True) == (
        '<label class="field"><span class="lbl">Secret</span>'
        '<input type="password" name="password" value="" required></label>')
    # and with it, the hint is escaped hint text — never part of the value
    assert tour._field("Instance", "row_name_0", "", "Pick a name",
                       placeholder='a"b&c') == (
        '<label class="field"><span class="lbl">Instance</span>'
        '<input type="text" name="row_name_0" value="" placeholder="a&quot;b&amp;c">'
        '<span class="help">Pick a name</span></label>')


@pytest.mark.asyncio
async def test_the_blank_add_row_shows_the_default_instance_name_as_a_placeholder(tmp_path):
    """The empty row's one name field carries the customary folder name as a HINT, value still empty."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        step = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert ('<input type="text" name="row_name_0" value="" '
            f'placeholder="{tour.DEFAULT_INSTANCE_NAME}">') in step
    assert tour.DEFAULT_INSTANCE_NAME == "DCS.dcs_serverrelease"
    # the hint is drawn ONCE: no other field on the screen took a placeholder
    assert step.count("placeholder=") == 1


@pytest.mark.asyncio
async def test_a_detected_rows_name_field_carries_no_placeholder(tmp_path):
    """The hint belongs to the HAND-ADDED row only: a detected row's name stays a plain input."""
    seed(tmp_path, servers={"scanned": True,
                            "detected": {"DCS.serverrelease": "SRS-1"},
                            "added": ["DCS.openbeta"],
                            "rows": {"DCS.openbeta": {"manage": True, "name": "DCS.openbeta"}}})
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    # row 0 is the DETECTED instance, row 1 the hand-added one: only the second is a hint field
    assert '<input type="text" name="row_name_0" value="SRS-1">' in html
    assert ('<input type="text" name="row_name_1" value="DCS.openbeta" '
            f'placeholder="{tour.DEFAULT_INSTANCE_NAME}">') in html
    assert html.count("placeholder=") == 1


@pytest.mark.asyncio
async def test_an_untouched_blank_row_submits_no_name_and_adds_nothing(tmp_path):
    """The placeholder is a hint, so the empty field a browser submits is empty — nothing lands."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        assert (f'placeholder="{tour.DEFAULT_INSTANCE_NAME}"' in page)     # the hint IS drawn
        token = token_of(page)
        # what a browser sends for an untouched field: the placeholder is NOT the value
        response = await client.post(STEP_PATH, data=row_form(
            token, row_manage_0="on", row_name_0=""))
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.servers["added"] == [] and draft.servers["rows"] == {}
    assert tour.DEFAULT_INSTANCE_NAME not in str(draft.servers)


@pytest.mark.asyncio
async def test_the_blank_add_row_posts_and_the_instance_lands_in_the_draft(tmp_path):
    """The blank row's name IS the instance: posting it makes an ADDED instance out of it."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    seed_roles(tmp_path, central_admin=False)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        token = token_of(page)
        assert 'name="row_name_0" value=""' in page and 'name="row_manage_0" checked' in page
        # the progressive re-render: the row it just took, and a NEW blank row after it
        stayed = await client.post(STEP_PATH, data=row_form(
            token, stay="1", row_manage_0="on", row_name_0="DCS.openbeta",
            row_status_0="1234567890", row_chat_0="-1", row_admin_0="-1", row_autostart_0="on"))
        assert stayed.status_code == 200
        assert 'name="row_name_0" value="DCS.openbeta"' in stayed.text
        assert 'name="row_name_1" value=""' in stayed.text
        # and the same row posted for real advances the step
        advanced = await client.post(STEP_PATH, data=row_form(
            token, row_manage_0="on", row_name_0="DCS.openbeta", row_status_0="1234567890",
            row_chat_0="-1", row_admin_0="-1", row_autostart_0="on"))
    assert advanced.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 8
    assert draft.servers["added"] == ["DCS.openbeta"]
    assert draft.servers["detected"] == {}
    assert draft.servers["rows"]["DCS.openbeta"] == {
        "manage": True, "name": "DCS.openbeta", "status": "1234567890", "chat": "-1", "admin": "-1",
        "autostart": True}


@pytest.mark.asyncio
async def test_a_blank_add_row_with_no_name_adds_nothing_and_continue_advances(tmp_path):
    """The blank row is always on the screen, so a tick alone must not hold the step open."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(token_of(page), row_manage_0="on"))
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 8
    assert draft.servers["added"] == [] and draft.servers["rows"] == {}


@pytest.mark.asyncio
async def test_an_added_instance_takes_the_same_refusals_as_a_detected_row(tmp_path):
    """Every rule a detected row obeys applies to the blank row: channels and the unique name."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        token = token_of(page)
        # an impossible status id: the classified refusal, and the typed value comes back
        bad_channel = await client.post(STEP_PATH, data=row_form(
            token, row_manage_0="on", row_name_0="OB-1", row_status_0="-5"))
        # no status channel at all: the required one
        missing = await client.post(STEP_PATH, data=row_form(
            token, row_manage_0="on", row_name_0="OB-1", row_status_0=""))
    assert bad_channel.status_code == 200
    assert "The status channel id for OB-1 must be a number, or -1 for none." in bad_channel.text
    assert 'value="-5"' in bad_channel.text
    assert missing.status_code == 200
    assert "Enter the status channel id for OB-1." in missing.text
    assert SetupDraft.load(tmp_path).step == 7


def test_a_hand_added_row_named_like_a_detected_server_is_refused():
    """The unique-name rule spans BOTH kinds of row: servers.yaml is keyed by server name."""
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "detected": {"DCS.serverrelease": "SRS-1"}, "added": ["DCS.openbeta"],
         "rows": {"DCS.openbeta": {"manage": True, "name": "DCS.openbeta"}}})
    assert [row.instance for row in answers.rows] == ["DCS.serverrelease", "DCS.openbeta"]
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": "1",
         "row_manage_1": "on", "row_name_1": "SRS-1", "row_status_1": "2"},
        answers, validate=True, channels=True)
    assert resolved is None
    assert failure == ("server_name_duplicate", {"name": "SRS-1"})
    # the positive control: a name of its own passes — and an added row's name IS its instance, so
    # the row the operator renamed leaves under the name they typed
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1", "row_status_0": "1",
         "row_manage_1": "on", "row_name_1": "OB-1", "row_status_1": "2"},
        answers, validate=True, channels=True)
    assert failure is None and resolved is not None
    assert [row.instance for row in resolved.rows] == ["DCS.serverrelease", "OB-1"]
    assert [row.name for row in resolved.rows] == ["SRS-1", "OB-1"]
    assert resolved.added == ["OB-1"]


@pytest.mark.asyncio
async def test_a_completed_step_eight_keeps_the_row_when_an_instance_was_found(tmp_path):
    seed(tmp_path, step=7, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "4"})).text
    assert "DCS servers" in rail_rows(html)
    assert rail_steps(True, True, True) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert rail_steps(True, True, False) == [1, 2, 3, 4, 5, 6, 7, 9, 10]


# ------------------------------------------------------------------------------- the answers

@pytest.mark.asyncio
async def test_a_complete_post_writes_the_rows_and_advances(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1234567890",
            row_chat_0="-1", row_admin_0="-1", row_autostart_0="on",
            row_manage_1="on", row_name_1="Server 2", row_status_1="42"))
    assert response.status_code == 303
    draft = SetupDraft.load(tmp_path)
    assert draft.step == 8
    assert draft.servers["rows"]["DCS.serverrelease"] == {
        "manage": True, "name": "SRS-1", "status": "1234567890", "chat": "-1", "admin": "-1",
        "autostart": True}
    assert draft.servers["rows"]["DCS.openbeta"]["autostart"] is False


@pytest.mark.asyncio
async def test_an_unmanaged_row_is_not_written_but_its_answers_are_kept(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1234567890",
            row_name_1="Kept anyway", row_status_1="999"))
    assert response.status_code == 303
    answers = servers_step.answers_from_mapping(SetupDraft.load(tmp_path).servers)
    assert [row.instance for row in servers_step.managed_rows(answers)] == ["DCS.serverrelease"]
    stored = SetupDraft.load(tmp_path).servers["rows"]["DCS.openbeta"]
    assert stored["name"] == "Kept anyway" and stored["manage"] is False


@pytest.mark.asyncio
async def test_a_row_without_a_name_is_reported_on_the_step(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="", row_status_0="1234567890",
            row_name_1="Server 2", row_status_1="42"))
    assert response.status_code == 200
    assert "Give every managed server a name." in response.text
    assert SetupDraft.load(tmp_path).step == 7


@pytest.mark.asyncio
async def test_a_row_without_a_status_channel_is_reported_on_the_step(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="",
            row_name_1="Server 2", row_status_1="42"))
    assert response.status_code == 200
    assert "Enter the status channel id for SRS-1." in response.text
    assert SetupDraft.load(tmp_path).step == 7


@pytest.mark.asyncio
async def test_a_non_numeric_channel_id_is_reported_and_the_value_is_kept(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1234567890",
            row_chat_0="not-a-number", row_name_1="Server 2", row_status_1="42"))
    assert response.status_code == 200
    assert "The chat channel id for SRS-1 must be a number, or -1 for none." in response.text
    assert "not-a-number" in response.text            # the typed value comes back
    assert SetupDraft.load(tmp_path).step == 7


@pytest.mark.asyncio
async def test_two_rows_with_the_same_server_name_are_reported(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1",
            row_manage_1="on", row_name_1="SRS-1", row_status_1="2"))
    assert response.status_code == 200
    assert "Two servers are named SRS-1. Names must be unique." in response.text
    assert SetupDraft.load(tmp_path).step == 7


@pytest.mark.asyncio
async def test_the_answers_land_in_the_draft_and_never_in_main_yaml(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1234567890",
            row_manage_1="on", row_name_1="Server 2", row_status_1="42"))
    body = draft_path(tmp_path).read_text(encoding="utf-8")
    assert "servers:" in body and "SRS-1" in body
    assert not (tmp_path / "main.yaml").exists()


def test_the_rows_survive_a_rescan_and_a_disappeared_instance_loses_its_row(tmp_path, monkeypatch):
    from services.webservice.setup.tour import _rescan_servers

    seed(tmp_path, servers=scanned(rows={"DCS.serverrelease": {
        "manage": True, "name": "Named", "status": "5", "chat": "", "admin": "",
        "autostart": True}}))
    monkeypatch.setattr(servers_step, "detect_instances",
                        lambda: {"DCS.openbeta": "DCS Server"})
    draft = SetupDraft.load(tmp_path)
    _rescan_servers(draft, tmp_path, force=True)
    rescanned = SetupDraft.load(tmp_path)
    assert set(rescanned.servers["detected"]) == {"DCS.openbeta"}
    assert "DCS.serverrelease" not in rescanned.servers["rows"]


# ------------------------------------------------------------------------------- the resolution

def test_resolve_rows_reports_the_first_incomplete_managed_row():
    answers = servers_step.answers_from_mapping({"scanned": True}, detected=TWO_INSTANCES)
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "SRS-1"}, answers, validate=True)
    assert resolved is None and failure == ("status_required", {"name": "SRS-1"})


def test_resolve_rows_accepts_an_empty_chat_and_admin_as_none():
    answers = servers_step.answers_from_mapping({"scanned": True}, detected=TWO_INSTANCES)
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "A", "row_status_0": "1"}, answers, validate=True)
    assert failure is None and resolved is not None
    assert resolved.rows[0].chat == "" and resolved.rows[0].admin == ""


def test_parse_channel_reads_ids_and_rejects_text():
    assert servers_step.parse_channel("123") == 123
    assert servers_step.parse_channel("-1") == -1
    assert servers_step.parse_channel("") is None
    assert servers_step.parse_channel("", none=-1) == -1
    assert servers_step.parse_channel("abc") is None


# ------------------------------------------------------------------------------- the module

def test_the_config_shapes_match_the_installers_own_builders():
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "rows": {"DCS.serverrelease": {
            "manage": True, "name": "SRS-1", "status": "123", "chat": "-1", "admin": "9",
            "autostart": True}}}, detected={"DCS.serverrelease": "SRS-1"})
    instance = servers_step.instance_blocks(answers)["DCS.serverrelease"]
    assert instance == setup_config.build_instance(
        home=os.path.join(servers_step.SAVED_GAMES, "DCS.serverrelease"), bot_port=6666)
    assert servers_step.servers_entries(answers, discord=True) == {
        "SRS-1": {"channels": setup_config.build_server_channels(status=123, chat=-1, admin=9)}}
    assert servers_step.scheduler_entries(answers) == {
        "DCS.serverrelease": setup_config.build_scheduler(True)}


def test_the_no_discord_shape_carries_no_channels():
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "rows": {"DCS.serverrelease": {
            "manage": True, "name": "SRS-1", "status": "123", "autostart": False}}},
        detected={"DCS.serverrelease": "SRS-1"})
    assert servers_step.servers_entries(answers, discord=False) == {"SRS-1": {}}


def test_a_central_admin_channel_removes_the_per_server_one():
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "rows": {"DCS.serverrelease": {
            "manage": True, "name": "SRS-1", "status": "123", "chat": "-1", "admin": "9",
            "autostart": True}}}, detected={"DCS.serverrelease": "SRS-1"})
    assert servers_step.servers_entries(answers, discord=True, central_admin=True) == {
        "SRS-1": {"channels": setup_config.build_server_channels(status=123, chat=-1)}}


def test_the_bot_ports_are_the_installers_formula_over_managed_rows():
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "rows": {
            "a": {"manage": True, "name": "A", "status": "1"},
            "b": {"manage": False, "name": "B", "status": "2"},
            "c": {"manage": True, "name": "C", "status": "3"}}},
        detected={"a": "A", "b": "DCS Server", "c": "C"})
    assert servers_step.bot_ports(answers) == {"a": 6666, "c": 6667}


def test_the_bot_ports_start_above_every_port_nodes_yaml_already_assigns():
    """The installer's formula counts from 6666 and ignores the file, so a joining install collides.

    The base is read off the ``nodes.yaml`` on disk: every ``bot_port`` it names, over every node,
    pushes the first assigned port past it — and a document that names none leaves the installer's own
    first port, which is what a first run gets.
    """
    nodes = {"NodeOne": {"instances": {"DCS.serverrelease": {"bot_port": 6666},
                                       "DCS.openbeta": {"bot_port": 6669}}},
             "NodeTwo": {"listen_port": 10043}}
    assert servers_step.existing_bot_ports(nodes) == [6666, 6669]
    assert servers_step.bot_port_base(nodes) == 6670
    assert servers_step.bot_port_base({}) == servers_step.DEFAULT_BOT_PORT
    assert servers_step.bot_port_base(None) == servers_step.DEFAULT_BOT_PORT
    # a document that is not a pool of node blocks, or whose ports are not numbers, adds nothing
    assert servers_step.existing_bot_ports({"NodeOne": "not a block"}) == []
    assert servers_step.existing_bot_ports({"N": {"instances": {"i": {"bot_port": "later"}}}}) == []
    assert servers_step.existing_bot_ports({"N": {"instances": "not a mapping"}}) == []

    answers = servers_step.answers_from_mapping(
        {"scanned": True}, detected={"a": "A", "b": "DCS Server"})
    assert servers_step.bot_ports(answers) == {"a": 6666, "b": 6667}          # a first run, unchanged
    assert servers_step.bot_ports(answers, base=6670) == {"a": 6670, "b": 6671}
    assert servers_step.instance_blocks(answers, base=6670)["b"]["bot_port"] == 6671


def test_an_added_row_leaves_through_the_installers_own_builders():
    """No parallel shape: a hand-added instance is the SAME row a detected one is.

    Its one field is the instance AND the server name, so the three documents are keyed exactly as a
    detected instance's are — only the values' origin differs.
    """
    answers = servers_step.answers_from_mapping(
        {"scanned": True, "detected": {}, "added": ["DCS.openbeta"],
         "rows": {"DCS.openbeta": {"manage": True, "name": "DCS.openbeta", "status": "123",
                                   "chat": "-1", "admin": "9", "autostart": True}}})
    assert [row.instance for row in answers.rows] == ["DCS.openbeta"]
    assert servers_step.instance_blocks(answers) == {"DCS.openbeta": setup_config.build_instance(
        home=os.path.join(servers_step.SAVED_GAMES, "DCS.openbeta"), bot_port=6666)}
    assert servers_step.servers_entries(answers, discord=True) == {
        "DCS.openbeta": {"channels": setup_config.build_server_channels(status=123, chat=-1, admin=9)}}
    assert servers_step.scheduler_entries(answers) == {
        "DCS.openbeta": setup_config.build_scheduler(True)}


def test_a_row_added_completely_by_hand_leaves_the_builders_as_well():
    """The blank row as the OPERATOR posts it: a name, its channels and autostart — nothing else."""
    answers = servers_step.answers_from_mapping({"scanned": True, "detected": {}})
    assert answers.rows == [] and answers.added == []
    resolved, failure = servers_step.resolve_rows(
        {"row_manage_0": "on", "row_name_0": "DCS.openbeta", "row_status_0": "123",
         "row_chat_0": "-1", "row_admin_0": "-1", "row_autostart_0": "on"},
        answers, validate=True, channels=True)
    assert failure is None and resolved is not None
    assert resolved.added == ["DCS.openbeta"] and resolved.detected == {}
    assert servers_step.instance_blocks(resolved) == {"DCS.openbeta": setup_config.build_instance(
        home=os.path.join(servers_step.SAVED_GAMES, "DCS.openbeta"), bot_port=6666)}
    assert servers_step.servers_entries(resolved, discord=True) == {
        "DCS.openbeta": {"channels": setup_config.build_server_channels(status=123, chat=-1,
                                                                       admin=-1)}}
    assert servers_step.scheduler_entries(resolved) == {
        "DCS.openbeta": setup_config.build_scheduler(True)}


def test_the_real_dcs_instance_detection_runs_and_reports_this_host():
    """The installer's own instance scan runs here: ``instance -> name``, never an exception.

    A host carrying installs answers them (a Windows box); a bare one answers ``{}``. Both are a
    working probe, so this pins the CALL and its shape rather than one machine's contents.
    """
    found = servers_step.detect_instances()
    assert isinstance(found, dict)
    assert all(isinstance(instance, str) and isinstance(name, str)
               for instance, name in found.items())


# ------------------------------------------------------------------- the collapsible row

def _rows_of(html: str) -> list[str]:
    """Every tour server row in *html*, in document order."""
    return re.findall(r'<details class="st-block"[^>]*>.*?</details>', html, re.S)


@pytest.mark.asyncio
async def test_a_managed_row_is_open_and_an_unticked_row_is_collapsed_to_its_header(tmp_path):
    """Unticking a row collapses it to name + manage switch + the one-line handover sentence.

    The collapse is the SHARED editor block — a native ``<details class="st-block">`` — so the header
    is a ``<summary>`` and the edit block is ``.bd``. It is server-rendered: a managed row carries
    ``open``, an unticked row does not, and NO script decides it (so a JS-off browser gets the same).
    """
    seed(tmp_path, servers=scanned(rows={
        "DCS.serverrelease": {"manage": True, "name": "SRS-1", "status": "1", "chat": "",
                              "admin": "", "autostart": True},
        "DCS.openbeta": {"manage": False, "name": "OB-1", "status": "2", "chat": "",
                         "admin": "", "autostart": True}}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text

    assert html.count('<details class="st-block" open>') == 1        # the managed row
    assert html.count('<details class="st-block">') == 1             # the unticked row
    rows = _rows_of(html)
    assert len(rows) == 2

    managed, collapsed = rows
    assert re.match(r'<details class="st-block" open>', managed)     # row 0 is the open one
    msummary = re.search(r"<summary>(.*?)</summary>", managed, re.S)
    assert msummary is not None
    msummary = msummary.group(1)
    assert 'class="chev"' in msummary and "SRS-1" in msummary
    assert 'name="row_manage_0"' in msummary
    assert tour.T_SRV_HANDOVER not in managed                       # no handover while it is managed

    csummary = re.search(r"<summary>(.*?)</summary>", collapsed, re.S)
    assert csummary is not None
    csummary = csummary.group(1)
    assert 'class="chev"' in csummary and "OB-1" in csummary        # the server name
    assert 'name="row_manage_1"' in csummary                        # the manage switch
    assert html_escape(tour.T_SRV_HANDOVER) in csummary             # the handover sentence
    # the collapse is server-rendered: the state is the `open` attribute in the served HTML, and no
    # script decides it — a browser with JavaScript off renders exactly this
    assert re.search(r'<details class="st-block">\s*<summary>.', html)


@pytest.mark.asyncio
async def test_unticking_a_row_collapses_it_on_the_stay_re_render(tmp_path):
    """The untick is re-rendered by the row's own stay re-post — and the row comes back collapsed."""
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        assert page.count('<details class="st-block" open>') == 2  # both managed to start with
        response = await client.post(STEP_PATH, data=row_form(
            token_of(page), stay="1", row_name_0="SRS-1",
            row_manage_1="on", row_name_1="Server 2", row_status_1="2"))
    assert response.status_code == 200
    assert response.text.count('<details class="st-block" open>') == 1   # only the managed row
    assert response.text.count('<details class="st-block">') == 1        # the unticked, collapsed row
    stored = SetupDraft.load(tmp_path).servers["rows"]["DCS.serverrelease"]
    assert stored["manage"] is False


# --------------------------------------------------------- the central admin channel rule

@pytest.mark.asyncio
async def test_a_central_admin_channel_removes_the_per_server_admin_field_from_the_document(tmp_path):
    """With ``admin_scope = central`` the per-server admin field is NOT in the document at all."""
    seed(tmp_path, servers=scanned())
    seed_roles(tmp_path, central_admin=True)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        central = (await client.get(STEP_PATH, params={"n": "8"})).text
        # the operator flips the scope to per-server: the field is in the document again
        seed_roles(tmp_path, central_admin=False)
        per_server = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert 'name="row_admin_0"' not in central and 'name="row_admin_1"' not in central
    assert "row_admin" not in central                               # not disabled-but-present
    assert 'name="row_status_0"' in central and 'name="row_chat_0"' in central
    assert 'name="row_admin_0"' in per_server and 'name="row_admin_1"' in per_server


@pytest.mark.asyncio
async def test_an_admin_typed_before_switching_to_central_never_reaches_the_write(tmp_path):
    """A per-server admin typed on a per-server pass cannot survive a switch to central.

    The stored row still CARRIES the typed id (the draft keeps what was answered), but the scope makes
    the field vanish from the document AND the write never reads the row's admin: nothing that is
    written depends on it.
    """
    seed(tmp_path, servers=scanned(rows={"DCS.serverrelease": {
        "manage": True, "name": "SRS-1", "status": "1", "chat": "-1", "admin": "999",
        "autostart": True}}))
    seed_roles(tmp_path, central_admin=True)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        page = (await client.get(STEP_PATH, params={"n": "8"})).text
        assert 'name="row_admin_0"' not in page                     # the field is gone
        # and the submit that follows cannot put it back: the field is not in the form
        await client.post(STEP_PATH, data=row_form(
            token_of(page), row_manage_0="on", row_name_0="SRS-1", row_status_0="1"))

    draft = SetupDraft.load(tmp_path)
    answers = servers_step.answers_from_mapping(draft.servers)
    assert answers.rows[0].admin in ("", "999")                     # the field is not in the form
    document = review_step._servers_document(draft)
    assert "admin" not in document["SRS-1"]["channels"]            # never written
    assert document["SRS-1"]["channels"] == {"status": 1, "chat": -1}


def test_a_central_admin_scope_writes_no_per_server_admin_under_any_row():
    """Every managed row drops its admin channel while the scope is central."""
    draft = SetupDraft()
    draft.discord = True
    draft.servers = servers_step.non_secret_document(servers_step.answers_from_mapping(
        {"scanned": True, "rows": {"a": {"manage": True, "name": "A", "status": "1", "admin": "7"},
                                   "b": {"manage": True, "name": "B", "status": "2", "admin": "8"}}},
        detected={"a": "A", "b": "B"}))
    draft.roles = {"central_admin": True}
    document = review_step._servers_document(draft)
    assert document == {"A": {"channels": {"status": 1, "chat": -1}},
                        "B": {"channels": {"status": 2, "chat": -1}}}
    # the per-server scope still writes each row's own admin channel
    draft.roles = {"central_admin": False}
    assert review_step._servers_document(draft)["A"]["channels"]["admin"] == 7


def plant_guild(app, config_dir, *, bot_is_admin, token: str = "guild-token",
                guild_id: int = 111111111111111111) -> None:
    """Plant a guild read under the draft's own token+guild key, with the bot's admin state.

    The step derives the key from the held token and the draft's guild id, so the draft's
    ``discord_bot`` block is written first and the cache entry keyed to match it.
    """
    from services.webservice.setup import discord as discord_step

    draft = SetupDraft.load(config_dir)
    draft.discord_bot = {"guild_id": guild_id, "owner": 222222222222222222,
                         "privileged_intents": True}
    draft.save(config_dir)
    discord_step.token_store(app)[discord_step.TOKEN_KEY] = token
    data = discord_step.GuildData(guild_id=guild_id, guild_name="Test Guild",
                                  channels=(), roles=(), guilds=((guild_id, "Test Guild"),),
                                  bot_is_admin=bot_is_admin)
    discord_step.guild_cache(app)[(token, guild_id)] = data
    discord_step.guild_status(app).update({"key": (token, guild_id), "code": ""})


def guide_items(html: str) -> list[str]:
    """The channel guide's list entries, in order (the ``<div class="note guide">`` block)."""
    block = re.search(r'<div class="note guide">.*?</div>', html, re.S)
    scope = block.group(0) if block else ""
    return re.findall(r"<li>([^<]*)</li>", scope)


@pytest.mark.asyncio
async def test_the_permissions_are_a_real_list_when_the_admin_state_is_unknown(tmp_path):
    """With no guild read the bot's admin state is UNKNOWN, so the list is printed — one <li> each."""
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    items = guide_items(html)
    # three channel roles, then the eight permissions — each its own <li>, never a run-on
    assert items[3:11] == ["View Channel", "Send Messages", "Read Messages",
                           "Read Message History", "Add Reactions", "Attach Files",
                           "Embed Links", "Manage Messages"]
    assert "DCSServerBot needs these permissions on the channels:" in html
    assert "The bot is an administrator on your server" not in html
    # the copy no longer tells the operator to find channel ids
    assert "ask for the IDs" not in html


@pytest.mark.asyncio
async def test_the_permission_list_is_dropped_when_the_bot_is_an_administrator(tmp_path):
    """The bot already holds Administrator, so the list is not printed — one sentence says so."""
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    plant_guild(app, tmp_path, bot_is_admin=True)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert "View Channel" not in html
    assert "Manage Messages" not in html
    assert "DCSServerBot needs these permissions on the channels:" not in html
    assert "The bot is an administrator on your server" in html
    # the channel roles are still explained
    assert guide_items(html)[:3] == [
        "Status Channel — readable by everyone, writable only by the bot.",
        "Chat Channel — readable and writable by everyone.",
        "Admin channel, central or per server — readable and writable only by Admin and DCS Admin "
        "users."]


@pytest.mark.asyncio
async def test_the_permission_list_is_printed_when_the_bot_is_not_an_administrator(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    plant_guild(app, tmp_path, bot_is_admin=False)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert "View Channel" in html and "Manage Messages" in html
    assert "The bot is an administrator on your server" not in html


# ------------------------------------------------------------------------------- i18n coverage

def test_every_step_eight_string_is_shipped_in_every_catalog():
    source = (REPO_ROOT / "services/webservice/setup/tour.py").read_text(encoding="utf-8")
    marked = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "i18n" and node.args
                and isinstance(node.args[0], ast.Constant)):
            marked.add(str(node.args[0].value))
    for msgid in ("Searching for existing DCS server configurations ...", "{count} found.",
                  "No configured DCS servers found. Add one below, or continue and add your servers "
                  "later in the admin console.",
                  "Add a server", "instance — the Saved Games folder name",
                  "The name of this instance's folder under Saved Games, for example DCS.openbeta. "
                  "DCSServerBot starts DCS from there.",
                  "Manage this server through DCSServerBot",
                  "server name", "bot port", "autostart", "status", "chat", "admin",
                  "Give every managed server a name.", "Enter the status channel id for {name}.",
                  "The {field} channel id for {name} must be a number, or -1 for none.",
                  "Two servers are named {name}. Names must be unique.",
                  tour.T_SRV_CHANNELS_INTRO, tour.T_SRV_CH_STATUS, tour.T_SRV_CH_CHAT,
                  tour.T_SRV_CH_ADMIN, tour.T_SRV_PERMS_LEAD, tour.T_SRV_PERMS_ADMIN,
                  *tour.T_SRV_PERMS, tour.T_SRV_HANDOVER):
        assert msgid in marked, msgid
    for language in CATALOGS:
        catalog = pofile.read_po(open(
            REPO_ROOT / f"locale/{language}/LC_MESSAGES/webui.po", encoding="utf-8"))
        have = {message.id for message in catalog if message.id}
        for msgid in ("Manage this server through DCSServerBot", "{count} found.",
                      "Searching for existing DCS server configurations ...",
                      "Add a server", "instance — the Saved Games folder name",
                      "The name of this instance's folder under Saved Games, for example "
                      "DCS.openbeta. DCSServerBot starts DCS from there.",
                      tour.T_SRV_NONE, tour.T_SRV_CHANNELS_INTRO, tour.T_SRV_PERMS_LEAD,
                      *tour.T_SRV_PERMS, tour.T_SRV_PERMS_ADMIN, tour.T_SRV_HANDOVER):
            assert msgid in have, f"'{msgid}' is missing from the {language} catalog"


@pytest.mark.asyncio
async def test_the_card_is_translated_when_the_tour_language_is_german(tmp_path):
    seed(tmp_path, servers=scanned())
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=5"})
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert "DCS-Server" in rail_rows(html)
    assert "Diesen Server über DCSServerBot verwalten" in html
    assert "6666" in html


@pytest.mark.asyncio
async def test_the_blank_add_row_renders_in_german(tmp_path):
    """The blank row's own strings reach the page in the reader's language, through the COMPILED
    catalog: the note, the row's header and its one field's label and help line."""
    seed(tmp_path, step=7, servers=scanned(detected={}))
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=8"})
        html = (await client.get(STEP_PATH, params={"n": "8"})).text
    assert "Keine konfigurierten DCS-Server gefunden" in html
    assert "Server hinzufügen" in html
    assert "Instanz — der Name des Saved-Games-Ordners" in html
    assert "Der Name des Ordners dieser Instanz unter Saved Games" in html
    assert "No configured DCS servers found" not in html


# ------------------------------------------------------------------------------- the entry point

@pytest.mark.asyncio
async def test_the_real_entry_point_serves_step_eight_over_a_socket(tmp_path):
    """``start_setup_console`` (the ``run.py`` boot path) up to a real Step 8 over a real socket."""
    import httpx

    from services.webservice.setup.boot import should_run_setup, start_setup_console

    seed(tmp_path, servers=scanned())
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    service = await start_setup_console(config_dir=tmp_path, open_browser=False, announce=False,
                                        port=port)
    try:
        assert service.bound, "the setup console never bound its port"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                     follow_redirects=True, timeout=5.0) as client:
            entered = await client.get("/setup", params={"code": service.window.display_code})
            assert entered.status_code == 200
            page = await client.get(STEP_PATH, params={"n": "8"})
            assert page.status_code == 200
            assert "DCS.serverrelease" in page.text
            assert 'name="row_manage_0"' in page.text
    finally:
        await service.stop()

    assert should_run_setup(tmp_path)
