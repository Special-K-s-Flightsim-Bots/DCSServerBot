"""The Missions tab drops the upload control while a server starts up or shuts down.

``serverSettings.lua`` is written by DCS itself while a server loads and while it shuts down, so an
upload landing in either window races DCS's own write of that file and can mangle it. The tab
therefore OFFERS no upload control in ``LOADING`` / ``SHUTTING_DOWN`` and states the reason where the
card would have been — the shape the reorder grip and the selection bar already use — and the route
refuses a POST in those two states before anything is staged: the route's CSRF dependency has already
parsed the body by the time the gate runs, so what the gate saves is the staging and the action call,
not the transfer.

Pins: the control is absent and the sentence present in both busy states; the ordinary path (a
running server, and a shutdown one) still offers an ENABLED control — a gate that only covered the two
busy states would not prove that; a viewer without ``missions.upload`` sees neither the control nor
the busy sentence (the capability branch wins); a multipart POST in ``LOADING`` is refused with the
sentence, stages nothing and never reaches the action. Driven through the real app with the real local
login backend, the real mission actions and the stub cluster.
"""
from __future__ import annotations

import os
import re

import pytest

import plugins.mission.actions as mission_actions
from core import Status
from core.data.impl.serverimpl import MISSION_LIST_BUSY_REFUSAL as BOT_REFUSAL
from services.webservice import session as session_module
from services.webservice.pages import actions as actions_page
from test_webui_missions_tab import (  # noqa: F401 - fixtures imported on purpose
    BASE_DIR, CONFIGURED, CONFIGURED_FILES, CURRENT, SERVER_A, SERVER_B, Cluster, MissionServer,
    instance,
)
from test_webui_tab_scope import narrow
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

UPLOAD = actions_page.MISSIONS_UPLOAD_PATH
BUSY = (Status.LOADING, Status.SHUTTING_DOWN)
OPEN = (Status.RUNNING, Status.SHUTDOWN)
SENTENCE = actions_page.MISSIONS_UPLOAD_BUSY_REFUSAL
HEADING = "Upload a mission file"
#: the reason card's own heading, as the template draws it (the ADD card's help text spells the same
#: words, so the count is taken off the markup, never off the plain phrase)
HEADING_MARKUP = f"<h3>{HEADING}</h3>"


def fleet(status=Status.RUNNING):
    """Server A (Host A) with the configured mission fixture in *status*, and server B."""
    a = MissionServer(SERVER_A, ["Host A"], files=CONFIGURED_FILES, current=CURRENT, start=1,
                      configured=CONFIGURED, status=status)
    b = MissionServer(SERVER_B, ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    return Cluster([a, b], nodes={}, instances=[instance("Host-A-Instance", a),
                                               instance("Host-B-Instance", b)])


async def missions_page(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=missions")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


def upload_form(html: str) -> str:
    """The ONE ``<form>`` that posts an upload, or ``""`` when the tab offers none."""
    found = re.findall(r'<form[^>]*action="[^"]*'
                       + re.escape(actions_page.MISSIONS_UPLOAD_PATH) + r'[^"]*"[^>]*>', html)
    return found[0] if found else ""


# ------------------------------------------------------------- 1. the busy states: omitted, reasoned

@pytest.mark.asyncio
@pytest.mark.parametrize("status", BUSY, ids=lambda s: s.name)
async def test_the_upload_control_is_absent_while_the_server_is_busy(config_dir, clean_registry,
                                                                     status):
    """``LOADING`` / ``SHUTTING_DOWN``: no upload control at all, and the refusal in its place."""
    cluster = fleet(status)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    assert upload_form(html) == "", "the tab still offers the upload form in a busy state"
    assert 'type="file"' not in html, "a file input survived the gate"
    assert SENTENCE in html, "the omission is stated where the control would have been"
    # OMITTED, never drawn disabled: the sentence lives in the card's own body, and the card carries
    # the heading it always carried — reached through the reason branch, not a dead control.
    assert f'<div class="bd"><div class="sub">{SENTENCE}</div></div>' in html
    assert html.count(HEADING_MARKUP) == 1, "the busy card must appear once, not once per branch"


@pytest.mark.asyncio
async def test_the_busy_card_is_the_uploads_own_reason(config_dir, clean_registry):
    """The sentence is the shared mission-write refusal, and it is the UPLOAD's card that states it."""
    cluster = fleet(Status.LOADING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    card = html[html.index(HEADING_MARKUP):]
    assert SENTENCE in card[:card.index("</div></div>") + len("</div></div>")], card[:400]


# ------------------------------------------------- 2. CONTROL: the ordinary path is untouched

@pytest.mark.asyncio
@pytest.mark.parametrize("status", OPEN, ids=lambda s: s.name)
async def test_the_upload_control_is_offered_and_enabled_on_the_ordinary_path(config_dir,
                                                                            clean_registry,
                                                                            status):
    """CONTROL: ``RUNNING`` and ``SHUTDOWN`` offer the whole card, with nothing disabled."""
    cluster = fleet(status)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    form = upload_form(html)
    assert form, f"the upload control is gone in {status.name}"
    assert 'enctype="multipart/form-data"' in form
    assert 'type="file"' in html and 'name="file"' in html
    assert "disabled" not in form, "the upload control was drawn disabled"
    assert SENTENCE not in html, "a busy sentence is shown in a state that accepts an upload"


@pytest.mark.asyncio
async def test_the_upload_form_states_the_target_in_the_query_for_the_early_gate(config_dir,
                                                                               clean_registry):
    """The form carries the server in the query BESIDE the body, which is what lets the route refuse a
    busy server from the query alone, without consulting the body."""
    cluster = fleet(Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)
    form = upload_form(html)

    assert form, "the upload control is gone on the ordinary path"
    assert f"?{actions_page.SERVER_CONFIG_FIELD}={SERVER_A}" in form, form
    assert f'name="{actions_page.SERVER_CONFIG_FIELD}" value="{SERVER_A}"' in html, \
        "the body's own target must stay as it was"


# ---------------------------------------------------------- 3. the capability branch wins

@pytest.mark.asyncio
async def test_a_viewer_without_the_upload_capability_sees_no_control_and_no_busy_sentence(
        config_dir, clean_registry):
    """A ``DCS Admin`` narrowed out of ``missions.upload`` sees neither the card nor its sentence."""
    cluster = fleet(Status.LOADING)
    app = build(config_dir, cluster, plugins=("mission",))
    narrow(actions_page.MISSIONS_UPLOAD_CAPABILITY)  # after the install: it re-declares every rule
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await missions_page(http)

    assert upload_form(html) == "", "a viewer without the capability was offered the upload"
    assert SENTENCE not in html, "the busy sentence leaked to a viewer who may not upload anyway"


# ------------------------------------------- 4. the route refuses a busy POST, nothing staged

@pytest.mark.asyncio
async def test_a_busy_upload_post_is_refused_and_never_reaches_the_action(config_dir,
                                                                         clean_registry,
                                                                         monkeypatch):
    """A real multipart POST in ``LOADING``: the sentence, nothing staged, the action never called."""
    import plugins.mission.actions as mission_actions  # the module the registry will run

    staged: list[tuple] = []

    async def record_stage(node, name, data):
        staged.append((name, data))
        return 1

    calls: list[str] = []
    real_call = actions_page.call_action

    async def record_call(qualname, *args, **kwargs):
        calls.append(qualname)
        return await real_call(qualname, *args, **kwargs)

    monkeypatch.setattr(mission_actions, "_stage_mission_bytes", record_stage)
    monkeypatch.setattr(actions_page, "call_action", record_call)

    cluster = fleet(Status.LOADING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await missions_page(http))
        reply = await http.post(
            f"{UPLOAD}?{actions_page.SERVER_CONFIG_FIELD}={SERVER_A}",
            data={"server": SERVER_A, session_module.CSRF_FIELD: token},
            files={"file": ("Inbound.miz", b"MIZ-BYTES", "application/octet-stream")})
        assert reply.status_code == 303, (reply.status_code, reply.text[:300])
        assert f"/fleet/{SERVER_A}" in reply.headers["location"], reply.headers
        after = await missions_page(http)

    assert calls == [], f"the action was reached on a refused upload: {calls}"
    assert staged == [], "bytes were staged for a refused upload"
    assert cluster.servers[0].uploads == [], "the ONE writer was reached"
    assert SENTENCE in after, "the refusal must be readable on the tab it redirects to"


@pytest.mark.asyncio
async def test_a_busy_upload_post_without_the_query_is_refused_by_the_action(config_dir,
                                                                            clean_registry):
    """The second line of defence: a hand-made POST with no query still refuses, staging nothing."""
    cluster = fleet(Status.SHUTTING_DOWN)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await missions_page(http))
        reply = await http.post(
            UPLOAD,
            data={"server": SERVER_A, session_module.CSRF_FIELD: token},
            files={"file": ("Inbound.miz", b"MIZ-BYTES", "application/octet-stream")})
        assert reply.status_code == 303, (reply.status_code, reply.text[:300])
        after = await missions_page(http)

    assert SENTENCE in after
    assert cluster.servers[0].uploads == []


@pytest.mark.asyncio
async def test_the_same_post_uploads_on_the_ordinary_path(config_dir, clean_registry,
                                                          monkeypatch):
    """CONTROL: the identical multipart POST against a ``RUNNING`` server writes through as always."""
    import plugins.mission.actions as mission_actions  # the module the registry will run

    async def fake_stage(node, name, data):
        assert data == b"MIZ-BYTES"
        return 1

    async def fake_discard(node, row_id):
        return None

    monkeypatch.setattr(mission_actions, "_stage_mission_bytes", fake_stage)
    monkeypatch.setattr(mission_actions, "_discard_staged_mission", fake_discard)

    cluster = fleet(Status.RUNNING)
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        token = token_of(await missions_page(http))
        reply = await http.post(
            f"{UPLOAD}?{actions_page.SERVER_CONFIG_FIELD}={SERVER_A}",
            data={"server": SERVER_A, session_module.CSRF_FIELD: token, "autostart": "1"},
            files={"file": ("Inbound.miz", b"MIZ-BYTES", "application/octet-stream")})
        after = await missions_page(http)
        assert reply.status_code == 303, (reply.status_code, reply.text[:300])
    server = cluster.servers[0]
    assert server.uploads and server.uploads[0][0] == "Inbound.miz", (server.uploads, after[-3000:])
    assert os.path.normpath(server.configured[-1]).endswith("Inbound.miz")


# ------------------------------------------------------------ 5. one sentence, one object

def test_the_upload_refusal_is_the_sentence_every_surface_uses():
    """Console, plugin and core name ONE string, and the console's upload copy is that same object."""
    assert SENTENCE == BOT_REFUSAL
    assert SENTENCE is mission_actions.MISSION_LIST_BUSY_REFUSAL
    assert SENTENCE is actions_page.MISSION_LIST_BUSY_REFUSAL
    assert actions_page.MISSIONS_UPLOAD_BUSY_REFUSAL is SENTENCE, "the upload alias is the same object"
    assert "MISSIONS_UPLOAD_BUSY_REFUSAL" in actions_page.__all__
    assert "MISSIONS_UPLOAD_STATES" in actions_page.__all__
    for busy in (Status.LOADING.name, Status.SHUTTING_DOWN.name):
        assert busy not in actions_page.MISSIONS_UPLOAD_STATES, busy
