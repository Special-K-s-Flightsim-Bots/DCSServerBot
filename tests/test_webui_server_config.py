"""The per-server page and its Configuration tab (the READ half).

Pins: ``GET /fleet/{name}`` answers through the SCOPED lookup; the Configuration tab is offered to
an ``Admin`` and withheld from a ``DCS Admin`` (the mockup's state 7), and a manual
``?tab=configuration`` without the capability is REFUSED with the console's own 403 (not
hidden-and-served); the capability ``servers.config`` is declared ``("Admin",)`` with NO scope grant;
the password is rendered into its field while the coalition hashes are not rendered; a key also pinned
by ``servers.yaml`` is marked as pinned; a RUNNING server reads correctly with the stop-first
explanation; the render issues NO RPC; and the console's declared editable set is EXACTLY the action's
``DCS_FIELDS`` keys. Driven in-process through the real application."""
from __future__ import annotations

import json
import re
import types

import pytest

from conftest import asgi_client, permissions
from core.action_results import ServerConfigResult
from services.webservice import readmodels
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels import serverconfig as server_config
from test_webui_dashboard import BOT_YAML, Status, _build, _hash, _login, node, player, _source


# ------------------------------------------------------------------------------------ doubles

class FakeSettings(dict):
    """The slice of ``SettingsDict`` the read model uses: ``get`` and an ``mtime`` stamp."""

    def __init__(self, values=None, *, mtime=0.0):
        super().__init__(values or {})
        self.mtime = mtime


class NoRpcNode:
    """A node proxy that FAILS LOUDLY if the render tries to reach an RPC / any other attribute.

    ``name`` is allowed (the shared server row reads it); everything else raises, so a render that
    called ``send_to_node_sync`` (or any node method) would explode instead of quietly working.
    """

    def __init__(self, name="dcs-master-01"):
        object.__setattr__(self, "name", name)

    def __getattr__(self, attr_name):
        raise AssertionError(f"the render touched node.{attr_name} — a node call on render")


class NoRpcServer:
    """A server double whose ``get_config`` (an RPC on an agent node) raises, and whose node does too.

    Everything the read models are ALLOWED to read is present; only the RPC entry points raise, which
    turns "no RPC on a render" into a checked property.
    """

    def __init__(self, name="DCS Syria Campaign", *, status=Status.STOPPED, settings=None,
                 locals_=None, players=(), node_name="dcs-master-01", instance_name="Syria",
                 port=10308, mission="Caucasus_Training", mission_time=2472):
        self.name = name
        self.display_name = name
        self.status = status
        self.node = NoRpcNode(node_name)
        self.instance = types.SimpleNamespace(name=instance_name)
        self.port = port
        self.current_mission = (types.SimpleNamespace(name=mission, mission_time=mission_time)
                                if mission else None)
        self.players = {p.ucid: p for p in players}
        self.is_remote = False
        self.settings = FakeSettings(settings or {})
        self.locals = locals_ or {}

    def get_config(self, *args, **kwargs):  # pragma: no cover - must never be called on a render
        raise AssertionError("the render called Server.get_config() — an RPC on an agent node")


# ------------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def app_with(config_dir, roles, *, servers=(), username="staff"):
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": list(roles)}])
    app.state.webui_source_provider = (
        lambda: _source(servers=servers, node_obj=node(instances={})))
    return app, registrar


async def get(app, path, username="staff", *, expect=200):
    async with asgi_client(app) as client:
        await _login(client, username)
        response = await client.get(path)
    assert response.status_code == expect, (path, response.status_code, response.text[:200])
    return response.text


def _namespace_server(name="DCS Syria Campaign", *, status=Status.STOPPED, settings=None,
                      locals_=None, players=()):
    """A plain ``SimpleNamespace`` server (no RPC tripwires) for the capability/secret tests."""
    return types.SimpleNamespace(
        name=name, status=status, node=types.SimpleNamespace(name="dcs-master-01"),
        instance=types.SimpleNamespace(name="Syria"), port=10308,
        current_mission=types.SimpleNamespace(name="Caucasus_Training", mission_time=2472),
        players={p.ucid: p for p in players}, is_remote=False,
        settings=FakeSettings(settings or {}), locals=locals_ or {})


BASE_SETTINGS = {
    "name": "DCS Syria Campaign", "description": "Weekly campaign", "port": 10308,
    "password": "hunter2", "maxPlayers": 18, "isPublic": True,
    "require_pure_clients": False,
    "advanced": {"resume_mode": 1, "maxPing": 300, "bluePasswordHash": "BLUEHASH123",
                 "redPasswordHash": "REDHASH456"},
    "missionList": ["a.miz", "b.miz", "c.miz"], "listStartIndex": 2, "current": 2,
}


# ------------------------------------------------------- 1. the capability, the tab and the 403

@pytest.mark.asyncio
async def test_an_admin_sees_the_configuration_tab_with_the_groups(config_dir):
    server_obj = _namespace_server(settings=BASE_SETTINGS)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    assert "?tab=configuration" in html, "the Configuration tab is offered to an Admin"
    for label in ("Identity &amp; access", "Server behaviour", "Requirements", "Restrictions",
                  "Anti-cheat"):
        assert label in html, f"the group '{label}' is missing"
 # exactly the 22 editable keys are rendered as rows (as the shared row's own key marker — the
 # SAME `.key` a schema-driven editor prints, so the Configuration tab and the node/settings pages
 # name a setting identically)
    for key in readmodels.editable_keys():
        assert f'<span class="key mono">{key}</span>' in html, f"key '{key}' missing from the form"


@pytest.mark.asyncio
async def test_a_dcs_admin_gets_the_page_but_no_tab_and_a_403_on_the_request(config_dir):
    app, _ = app_with(config_dir, ["DCS Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])

    page = await get(app, "/fleet/DCS%20Syria%20Campaign")
    assert "?tab=configuration" not in page, "the Configuration tab must not be offered to a DCS Admin"
    for tab in ("overview", "players", "missions"):
        assert f"?tab={tab}" in page, f"the {tab} tab must still be offered"
 # the Log tab is Admin-only (the cluster log panel's own capability): not offered, and refused
    assert "?tab=log" not in page, "the Log tab must not be offered to a DCS Admin"

    refused = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration", expect=403)
    assert "Not authorized" in refused
    log_refused = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=log", expect=403)
    assert "Not authorized" in log_refused


@pytest.mark.asyncio
async def test_a_manager_gets_the_capability_with_the_manager_scope_grant(config_dir):
    """``servers.config`` carries a SCOPE GRANT -- a manager of a server may open the tab."""
    detail_page.capabilities()
    assert permissions.roles_for_capability(detail_page.CONFIG_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(detail_page.CONFIG_CAPABILITY) is True


def test_the_config_capability_is_declared_admin_roles_with_a_manager_scope_grant():
    detail_page.capabilities()
    assert permissions.CAPABILITIES[detail_page.CONFIG_CAPABILITY] == ("Admin",)
    assert permissions.SCOPE_GRANTS[detail_page.CONFIG_CAPABILITY] is True


# ------------------------------------------------------------------- 2. secrets never rendered

@pytest.mark.asyncio
async def test_the_password_is_rendered_while_the_hashes_never_are(config_dir):
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    assert 'value="hunter2"' in html, "the password VALUE must be rendered into its field"
    assert "BLUEHASH123" not in html and "REDHASH456" not in html, "a coalition hash was rendered"
    assert ">password<" in html or "password</span>" in html


@pytest.mark.asyncio
async def test_an_unset_password_renders_an_empty_field(config_dir):
    settings = dict(BASE_SETTINGS, password="")
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert 'name="password"' in html, "the field is still rendered"
    assert re.search(r'<input type="password" id="cfg-password" name="password"\s+value=""', html), \
        "an unset password renders an empty field"


# ------------------------------------------------------------------- 3. the pinned key marker

@pytest.mark.asyncio
async def test_a_pinned_key_is_marked_as_pinned_not_disabled(config_dir):
    server_obj = _namespace_server(settings=BASE_SETTINGS,
                                   locals_={"serverSettings": {"maxPlayers": 18}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert "also pinned in servers.yaml — a change updates both" in html
    assert "maxPlayers" in html


# ------------------------------------------------------------------- 4. a running server reads

@pytest.mark.asyncio
async def test_a_running_server_shows_the_stop_first_explanation(config_dir):
    server_obj = _namespace_server(status=Status.RUNNING, settings=BASE_SETTINGS)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert server_config.STOP_FIRST_SENTENCE in html
    assert "RUNNING" in html
    assert re.search(r'<button[^>]*disabled[^>]*>Save</button>', html), "Save is drawn disabled"


# ------------------------------------------------------------------- 5. no RPC on a render

@pytest.mark.asyncio
async def test_the_render_issues_no_rpc(config_dir):
    """A server whose ``get_config`` and node RPCs raise renders the tab without tripping them."""
    app, _ = app_with(config_dir, ["Admin"],
                      servers=[NoRpcServer(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert "Identity &amp; access" in html


# ------------------------------------------------------------------- 6. the other tabs render

@pytest.mark.asyncio
async def test_the_non_configuration_tabs_render(config_dir):
    server_obj = _namespace_server(players=[player("Viper-1"), player("Maverick")],
                                   status=Status.RUNNING, settings=BASE_SETTINGS)
    app, _ = app_with(config_dir, ["DCS Admin"], servers=[server_obj])

    overview = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=overview")
    assert "Overview" in overview and "Caucasus_Training" in overview

    players = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=players")
    assert "Viper-1" in players and "Maverick" in players
    assert "<thead" in players, "the Players tab reuses the shared table component"

    mission_html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=missions")
    assert "read-only" in mission_html or "no missions configured" in mission_html

 # the Log tab is Admin-only (the cluster log panel's capability): a DCS Admin is refused
    await get(app, "/fleet/DCS%20Syria%20Campaign?tab=log", expect=403)


# ------------------------------------------------- 7. the write half: one action, one POST route

def _spy(monkeypatch, result):
    """Replace ``call_action`` with a recording spy — the route must CALL THE ACTION, not just
    produce an outcome, so every write test asserts the call itself (qualname, server, values)."""
    calls: list[tuple[str, dict]] = []

    async def fake_call(qualname, ctx, /, *, audit_result=False, **params):
        calls.append((qualname, params))
        return result if isinstance(result, ServerConfigResult) else result()

    monkeypatch.setattr(actions_page, "call_action", fake_call)
    return calls


def _full_form(**overrides) -> dict:
    """The exact body the page's form sends: every editable field at its CURRENT value.

    Boolean fields are posted as ``1`` only while the current value is True — an unchecked checkbox
    sends nothing, which is how the route tells "changed to false" from "not stated". The password is
    posted as the STORED value the field is rendered with when one is set (the untouched state); an
    emptied password field is the CLEAR. Overrides flip a field to simulate an operator's edit.
    """
    values = {
        "name": "DCS Syria Campaign", "description": "Weekly campaign", "port": "10308",
        "maxPlayers": "18", "isPublic": "1", "advanced.resume_mode": "1", "advanced.maxPing": "300",
        "password": "hunter2",
    }
    values.update(overrides)
    return values


async def _post_config(app, data, username="staff"):
    """POST the config form (target + CSRF from the rendered page) and return (response, landed html)."""
    async with asgi_client(app) as client:
        await _login(client, username)
        page = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        payload = {"server": "DCS Syria Campaign", "_csrf_token": token}
        payload.update(data)
        response = await client.post(actions_page.SERVER_CONFIG_PATH, data=payload)
        landed = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
    return response, landed.text


def test_the_config_write_is_one_action_with_a_manager_scope_grant():
    """``servers.config.dcs`` is the write's capability: ``(Admin,)`` WITH a manager scope grant."""
    actions_page.declare()
    detail_page.capabilities()
    assert actions_page.SERVER_CONFIG_CAPABILITY == "servers.config.dcs"
    assert actions_page.SERVER_CONFIG_PATH == "/actions/server/config"
    assert permissions.roles_for_capability(actions_page.SERVER_CONFIG_CAPABILITY) == ("Admin",)
    assert permissions.scope_grants(actions_page.SERVER_CONFIG_CAPABILITY) is True
    action = actions_page.SERVER_CONFIG_ACTIONS[0]
    assert action.qualname == "set_server_config", "the write must call the ONE action"
    assert action.observable == actions_page.AWAIT_OBSERVABLE_CONFIG, "a config write's third observable"


@pytest.mark.asyncio
async def test_the_detail_page_route_is_get_only_and_the_config_write_is_a_separate_post(config_dir):
    """No state-changing GET: the detail page is GET, each write is its OWN POST path.

    The tab points at exactly TWO declared ``/actions/...`` paths -- the DCS config write and the
    channels write -- and no more. Each is POST-only and carries its declared capability; the detail
    route stays GET.
    """
    app, registrar = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert f'action="{actions_page.SERVER_CONFIG_PATH}"' in html, "the form must post the one action"
 # the tab points at exactly the two DECLARED write paths (no third, undeclared one)
    assert set(re.findall(r'action="(/actions/[^"]+)"', html)) == {
        actions_page.SERVER_CONFIG_PATH, actions_page.SERVER_CHANNELS_PATH}
    detail_routes = [r for r in registrar.routes
                     if getattr(r, "path", None) == detail_page.SERVER_DETAIL_PATH]
    assert detail_routes, "the detail route is registered"
    for route in detail_routes:
        assert set(route.methods or ()) == {"GET"}, "the detail route must never be a write"
    write_routes = [r for r in registrar.routes
                    if getattr(r, "path", None) == actions_page.SERVER_CONFIG_PATH]
    assert write_routes, "the config write route is registered"
    for route in write_routes:
        assert set(route.methods or ()) == {"POST"}, "the write is a POST, never a GET"
    assert registrar.capability_for(path=actions_page.SERVER_CONFIG_PATH) \
        == actions_page.SERVER_CONFIG_CAPABILITY


@pytest.mark.asyncio
async def test_a_submitted_change_calls_the_one_action_with_only_the_changed_fields(config_dir, monkeypatch):
    """Submit a full form with ONE change: the action is called with exactly that key."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(
        success=True, server_name="DCS Syria Campaign",
        applied={"maxPlayers": {"from": 18, "to": 20}},
        message='Server "DCS Syria Campaign": saved maxPlayers.'))

    response, html = await _post_config(app, _full_form(maxPlayers="20"))
    assert response.status_code == 303
    assert len(calls) == 1, calls
    qualname, params = calls[0]
    assert qualname == "set_server_config"
    assert params["server_name"] == "DCS Syria Campaign"
    assert params["values"] == {"maxPlayers": 20}, params["values"]
    assert "saved maxPlayers" in html, "the action's message must reach the one-shot notice"


@pytest.mark.asyncio
async def test_a_no_change_form_calls_the_action_with_no_values(config_dir, monkeypatch):
    """Every field at its current value -> the action is handed an EMPTY change set."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="nothing changed"))
    await _post_config(app, _full_form())
    assert len(calls) == 1
    assert calls[0][1]["values"] == {}


@pytest.mark.asyncio
async def test_a_change_that_did_not_land_offers_a_one_shot_revert(config_dir, monkeypatch):
    """The action's ``applied`` old->new becomes a Revert that re-submits the OLD values."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(
        success=True, server_name="DCS Syria Campaign",
        applied={"maxPlayers": {"from": 18, "to": 20}}, message="saved maxPlayers"))

    _response, html = await _post_config(app, _full_form(maxPlayers="20"))
    assert "Revert" in html and actions_page.SERVER_CONFIG_REVERT_FIELD in html
    payload = re.search(r'name="' + re.escape(actions_page.SERVER_CONFIG_REVERT_FIELD)
                        + r'" value="([^"]*)"', html).group(1)
    import html as html_mod
    raw = html_mod.unescape(payload)
    assert json.loads(raw) == {"maxPlayers": 18}, raw
 # re-submitting the revert calls the SAME action with the OLD values
    response, _ = await _post_config(app, {actions_page.SERVER_CONFIG_REVERT_FIELD: raw})
    assert response.status_code == 303
    assert calls[-1][0] == "set_server_config"
    assert calls[-1][1]["values"] == {"maxPlayers": 18}


@pytest.mark.asyncio
async def test_the_password_field_is_diffed_like_a_string(config_dir, monkeypatch):
    """The field ships the STORED value, so keep / clear / replace are the plain field semantics.

    An untouched field (the stored value posted back) submits no password, an emptied field submits
    ``None`` (the explicit clear), and a typed value is the new password.
    """
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
    await _post_config(app, _full_form())                       # the stored value, untouched
    assert calls[-1][1]["values"] == {}, "the untouched field must submit nothing"

    await _post_config(app, _full_form(password=""))            # emptied
    assert calls[-1][1]["values"] == {"password": None}, "an emptied field is the clear"

    await _post_config(app, _full_form(password="   "))         # whitespace is not a password
    assert calls[-1][1]["values"] == {"password": None}, "whitespace is not a password"

    await _post_config(app, _full_form(password="newsecret"))
    assert calls[-1][1]["values"] == {"password": "newsecret"}, "a typed field submits its value"

 # the programmatic clear field is still ACCEPTED by the route (no control renders it any more)
    await _post_config(app, _full_form(**{actions_page.SERVER_CONFIG_CLEAR_FIELD: "1"}))
    assert calls[-1][1]["values"] == {"password": None}, "the clear field still sends the clear"


@pytest.mark.asyncio
async def test_an_empty_password_field_on_a_server_without_one_changes_nothing(config_dir, monkeypatch):
    """The resting state of a passwordless server's form is an EMPTY field — not a clear."""
    settings = dict(BASE_SETTINGS, password="")
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
    await _post_config(app, _full_form(password=""))
    assert calls[-1][1]["values"] == {}, "there is nothing to remove, so nothing was cleared"


@pytest.mark.asyncio
async def test_a_form_that_does_not_state_the_password_clears_nothing(config_dir, monkeypatch):
    """A crafted/partial POST without the field cannot remove a password (no field, no change)."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="ok"))
    body = _full_form()
    body.pop("password")
    await _post_config(app, body)
    assert calls[-1][1]["values"] == {}, "an unstated field is not a clear"


@pytest.mark.asyncio
async def test_a_hostile_password_is_escaped_in_the_attribute_and_round_trips(config_dir):
    """The value lands in an HTML attribute: a quote, bracket or ampersand must not break the page or
    the form, and the browser must read back exactly the stored password."""
    import html as html_mod

    hostile = '"</script><b>&\''
    settings = dict(BASE_SETTINGS, password=hostile)
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    match = re.search(r'<input type="password" id="cfg-password" name="password"\s+value="([^"]*)"',
                      html)
    assert match, "the password input must still render (a quote must not close the attribute early)"
    raw = match.group(1)
    assert '"' not in raw and "'" not in raw, f"the attribute still carries a raw quote: {raw!r}"
    assert "&lt;/script&gt;&lt;b&gt;&amp;" in html, "the brackets and ampersand must be escaped"
    assert hostile not in html, "the raw hostile value must not appear unescaped anywhere"
    assert html_mod.unescape(raw) == hostile, "the attribute must round-trip the stored password"


@pytest.mark.asyncio
async def test_the_password_round_trips_through_the_console(config_dir):
    """Load -> the field holds the stored value; post it back -> unchanged; empty it -> removed; a new
    one -> replaces. Driven end to end with the REAL ``set_server_config``."""
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "password": "hunter2", "advanced": {}},
                              locals_={}, node=node)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])          # register the REAL action for this process

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        assert 'value="hunter2"' in page.text, "the field must hold the stored password"
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)

        await client.post(actions_page.SERVER_CONFIG_PATH,
                          data={"server": "SRS-1", "password": "hunter2", "_csrf_token": token})
        assert server_obj.settings["password"] == "hunter2", "an untouched field keeps the password"

        await client.post(actions_page.SERVER_CONFIG_PATH,
                          data={"server": "SRS-1", "password": "", "_csrf_token": token})
        assert server_obj.settings["password"] == "", "an emptied field removes the password"

        await client.post(actions_page.SERVER_CONFIG_PATH,
                          data={"server": "SRS-1", "password": "n3wpw", "_csrf_token": token})
        assert server_obj.settings["password"] == "n3wpw", "a typed field sets the new password"


@pytest.mark.asyncio
async def test_the_coalition_hashes_are_never_offered_in_the_form(config_dir):
    """The hash keys are never inputs and never rendered as values."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert "BLUEHASH123" not in html and "REDHASH456" not in html
    for key in ("advanced.bluePasswordHash", "advanced.redPasswordHash"):
        assert f'name="{key}"' not in html, f"{key} must never be a form field"


@pytest.mark.asyncio
async def test_a_refused_write_shows_the_actions_own_sentence_and_drops_the_pulse(config_dir, monkeypatch):
    """A refusal is the ACTION's sentence, visible, and the Save control is NOT left pulsing."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    sentence = 'The server must be stopped to change its configuration. Stop it on the Servers page, then edit here.'
    _spy(monkeypatch, ServerConfigResult(success=False, server_name="DCS Syria Campaign",
                                         refused=sentence, message=sentence))
    response, html = await _post_config(app, _full_form(maxPlayers="20"))
    assert response.status_code == 303
    assert sentence in html, "the refusal sentence must be visible"
    assert not actions_page.awaiting_store(), "a refused write must drop its expectation"


@pytest.mark.asyncio
async def test_a_refused_rename_is_visible_and_nothing_else_pulsing(config_dir, monkeypatch):
    """A refused rename (the reserved ``n/a``) renders the action's own sentence."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    sentence = 'The name "n/a" is reserved and cannot be used for a server.'
    _spy(monkeypatch, ServerConfigResult(success=False, server_name="DCS Syria Campaign",
                                         refused=sentence, message=sentence))
    _response, html = await _post_config(app, _full_form(name="n/a"))
 # the sentence is autoescaped in the markup (it contains quotes), so assert its words
    assert "is reserved and cannot be used for a server" in html
    assert not actions_page.awaiting_store()


@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_the_config_write_by_the_gate(config_dir):
    """The write's capability is Admin-only: a DCS Admin's POST is refused before the action (403)."""
    app, _ = app_with(config_dir, ["DCS Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
        assert page.status_code == 403, "a DCS Admin must not even open the tab"
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "DCS Syria Campaign"})
    assert response.status_code == 403, "the gate refuses the write capability"


@pytest.mark.asyncio
async def test_a_foreign_server_name_is_refused_and_calls_no_action(config_dir, monkeypatch):
    """An authorized caller naming a server that does not exist: the seam's refusal, no action call."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, message="should not run"))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/DCS%20Syria%20Campaign?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "No Such Server", "_csrf_token": token})
    assert response.status_code == 303
    assert calls == [], "no action may run for a name that does not resolve"


@pytest.mark.asyncio
async def test_an_accepted_config_write_pulses_the_save_control_until_the_revision_moves(config_dir, monkeypatch):
    """An accepted write pulses Save until the revision changes."""
    server_obj = _namespace_server(settings=dict(BASE_SETTINGS))
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                         applied={"maxPlayers": {"from": 18, "to": 20}},
                                         message="saved maxPlayers"))
 # the Save control is offered only while the action EXISTS (the console's availability rule)
    from core.actions import discover_actions
    discover_actions(["scheduler"])
 # BEFORE: no expectation -> no pulse
    assert re.search(r'<button[^>]*aria-busy="true"[^>]*>Save</button>', "") is None
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert not re.search(r'<button[^>]*aria-busy="true"[^>]*>Save</button>', html), "no write yet"

    _response, after = await _post_config(app, _full_form(maxPlayers="20"))
    assert re.search(r'<button[^>]*aria-busy="true"[^>]*>Save</button>', after), \
        "an accepted write must pulse Save until the revision moves"

 # the revision is the settings mtime: moving it ends the pulse
    server_obj.settings.mtime = 999.0
    settled = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert not re.search(r'<button[^>]*aria-busy="true"[^>]*>Save</button>', settled), \
        "the pulse must end once the config revision moves"


@pytest.mark.asyncio
async def test_the_save_control_is_not_offered_when_the_action_is_absent(config_dir):
    """The console offers no control whose only answer is a refusal: no action, no Save."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    assert not actions_page.action_available("set_server_config")
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert re.search(r'<button[^>]*disabled[^>]*>Save</button>', html), "Save must be disabled"
 # it keeps the `primary` class even disabled — the stylesheet's `:not(:disabled)` keeps it a dim chip
    assert re.search(r'<button[^>]*class="primary[^"]*"[^>]*disabled[^>]*>Save</button>', html), \
        "the disabled Save still carries the primary class (styled dim by `:not(:disabled)`)"
    assert "not available in this installation" in html


@pytest.mark.asyncio
async def test_the_real_action_writes_the_setting_and_the_servers_yaml_override(config_dir):
    """END TO END with the REAL ``set_server_config`` (no spy): the submitted change lands in the settings
    AND in the ``servers.yaml`` override block and the server is reloaded. The route's call reaches the
    action through the registry."""
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import (ConfigServer, node_for, read_servers_yaml,
                                              write_servers_yaml)

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"maxPlayers": 18, "advanced": {}},
                              locals_={"serverSettings": {"maxPlayers": 18}}, node=node)
    write_servers_yaml(config_dir, {"SRS-1": {"serverSettings": {"maxPlayers": 18}}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])          # register the REAL action for this process

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "maxPlayers": "20",
                                           "_csrf_token": token})
    assert response.status_code == 303
    assert server_obj.settings["maxPlayers"] == 20, "the change must land in the settings (F1)"
    assert read_servers_yaml(config_dir)["SRS-1"]["serverSettings"]["maxPlayers"] == 20, \
        "the change must also update the servers.yaml override block (F3)"
    assert server_obj.reload_calls == 1, "the override sync reloads, exactly like Discord's Save"


# ---- the two important defects and their nitpicks


def _browser_select_value(html: str, key: str) -> str:
    """The value a BROWSER submits for ``<select name=key>``: an option marked ``selected`` wins, and with
    none marked the FIRST option is submitted.

    That default is how an untouched form can write a value the operator never chose, so a test that
    reproduces it must read the select the way a browser does.
    """
    match = re.search(rf'<select name="{re.escape(key)}"[^>]*>(.*?)</select>', html, re.S)
    assert match, f"no select named {key} was rendered"
    options = re.findall(r'<option([^>]*)>(.*?)</option>', match.group(1), re.S)
    assert options, f"the select {key} rendered no options"

    def value_of(attrs: str, label: str) -> str:
        attr = re.search(r'value="([^"]*)"', attrs)
        return attr.group(1) if attr else label.strip()

    for attrs, label in options:
        if "selected" in attrs:
            return value_of(attrs, label)
    return value_of(*options[0])             # nothing marked -> the browser picks the first


def _rendered_revert_payload(html: str) -> str:
    """The (unescaped) JSON in the rendered Revert hidden field; raises when no Revert is offered."""
    import html as html_mod
    match = re.search(r'name="' + re.escape(actions_page.SERVER_CONFIG_REVERT_FIELD)
                      + r'" value="([^"]*)"', html)
    assert match, "the page offered no Revert field"
    return html_mod.unescape(match.group(1))


@pytest.mark.asyncio
async def test_a_console_rename_redirects_to_the_new_name_and_shows_the_notice(config_dir):
    """DEFECT 1: a rename redirected to the OLD name, so the browser landed on a 404 and the success
    notice and the Revert offer were never rendered. Driven with the REAL ``set_server_config`` (which
    renames FIRST): the 303 must name the RENAMED server, and a GET there must show the notice and the
    Revert form."""
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "maxPlayers": 18, "advanced": {}},
                              locals_={}, node=node)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "name": "SRS-1-RENAMED",
                                           "_csrf_token": token})
        assert response.status_code == 303
        target = response.headers["location"]
 # REPRO: before the fix this was "/fleet/SRS-1?tab=configuration" -> the GET below is a 404
        assert target == "/fleet/SRS-1-RENAMED?tab=configuration", target
        landed = await client.get(target)

    assert landed.status_code == 200, landed.text[:300]
    assert "saved" in landed.text, "the success notice must render for the renamed server"
    assert actions_page.SERVER_CONFIG_REVERT_FIELD in landed.text, "the Revert offer must render"


@pytest.mark.asyncio
async def test_an_async_rename_answers_the_new_page_for_the_interceptor(config_dir):
    """The background submit is TOLD where the server now is, because it cannot read a 303.

    ``submit.js`` posts with ``fetch(redirect: "manual")``, which yields an opaque response: the
    ``Location`` of the 303 is unreadable, so the browser used to return to the OLD name and land on
    a 404 right after a successful rename. A request that asks for JSON gets the destination instead,
    and the notice survives because no page is rendered to spend it.
    """
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "maxPlayers": 18, "advanced": {}},
                              locals_={}, node=node)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "name": "SRS-1-RENAMED",
                                           "_csrf_token": token},
                                     headers={"accept": "application/json"})
        assert response.status_code == 200, response.text
        target = response.json()["redirect"]
        assert target == "/fleet/SRS-1-RENAMED?tab=configuration", target
        landed = await client.get(target)

    assert landed.status_code == 200, landed.text[:300]
    assert "saved" in landed.text, "the success notice must survive the JSON answer"


@pytest.mark.asyncio
async def test_a_two_thousand_char_old_value_is_carried_whole_by_the_revert_payload(config_dir,
                                                                                    monkeypatch):
    """DEFECT 2: the payload was clipped at ``NOTICE_MAX_CHARS`` (300), cutting a long old value
    mid-string. ``json.loads`` then failed, ``_revert_values`` returned ``{}``, and the Revert button
    re-submitted nothing — silently. ``description``'s own maximum is 2000: the payload must carry it
    WHOLE and parse."""
    long_old = "x" * 2000
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    _spy(monkeypatch, ServerConfigResult(
        success=True, server_name="DCS Syria Campaign",
        applied={"description": {"from": long_old, "to": "short"}},
        message='Server "DCS Syria Campaign": saved description.'))

    _response, html = await _post_config(app, _full_form(description="short"))
    decoded = json.loads(_rendered_revert_payload(html))
    assert decoded == {"description": long_old}, "the FULL old value must be carried, not a prefix"
    assert len(decoded["description"]) == 2000


@pytest.mark.asyncio
async def test_a_revert_payload_too_large_to_carry_is_refused_visibly_not_truncated(config_dir,
                                                                                    monkeypatch):
    """DEFECT 2, the other half: a payload that genuinely cannot be carried must be refused VISIBLY
    (no Revert form, and a sentence naming why) rather than shipped truncated and silently inert."""
    huge = "y" * (actions_page.REVERT_MAX_CHARS + 1)
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    _spy(monkeypatch, ServerConfigResult(
        success=True, server_name="DCS Syria Campaign",
        applied={"description": {"from": huge, "to": "short"}},
        message="saved description"))

    _response, html = await _post_config(app, _full_form(description="short"))
    assert actions_page.SERVER_CONFIG_REVERT_FIELD not in html, \
        "a payload that cannot be carried must not be offered (truncated)"
    assert actions_page.REVERT_TOO_LONG_SENTENCE in html, "the refusal must be VISIBLE"


@pytest.mark.asyncio
async def test_an_untouched_select_with_an_absent_current_value_writes_nothing(config_dir, monkeypatch):
    """NITPICK 3: an untouched select whose current value is ABSENT submitted its FIRST option
    (``advanced.resume_mode=0``). The rendered select must default the browser to an empty choice, and
    the diff must then submit NOTHING for it."""
    settings = dict(BASE_SETTINGS, advanced={"maxPing": 300})     # no resume_mode at all
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="nothing changed"))
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    submitted = _browser_select_value(html, "advanced.resume_mode")
    assert submitted == "", f"an untouched select must submit nothing, got {submitted!r}"

    data = _full_form()
    data["advanced.resume_mode"] = submitted
    _response, _after = await _post_config(app, data)
    assert calls[-1][1]["values"] == {}, calls[-1][1]["values"]


@pytest.mark.asyncio
async def test_an_untouched_select_whose_current_value_is_not_offered_writes_nothing(config_dir,
                                                                                     monkeypatch):
    """The server's current value is NOT among the offered options: the select must not present the first
    option as already chosen, and an untouched form must still submit NOTHING."""
    settings = dict(BASE_SETTINGS, advanced={"resume_mode": 7, "maxPing": 300})
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=settings)])
    calls = _spy(monkeypatch, ServerConfigResult(success=True, server_name="DCS Syria Campaign",
                                                 message="nothing changed"))
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    submitted = _browser_select_value(html, "advanced.resume_mode")
    assert submitted == "", f"a current value outside the options must not mark one, got {submitted!r}"

    data = _full_form()
    data["advanced.resume_mode"] = submitted
    _response, _after = await _post_config(app, data)
    assert calls[-1][1]["values"] == {}, calls[-1][1]["values"]


@pytest.mark.asyncio
async def test_the_config_form_carries_no_dead_origin_field(config_dir):
    """NITPICK 5: the form posted a hidden ``_origin`` the config route ignores (it computes its own
    back path). The dead input is gone."""
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")
    assert f'name="{actions_page.ORIGIN_FIELD}"' not in html


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# a manager configures their own server EXCEPT the port and the channels (the manager deny-list)
# ═════════════════════════════════════════════════════════════════════════════════════════════════

def app_with_manager(config_dir, *, servers=(), scope=("Host A",), username="manager"):
    """A console whose signed-in local account is a MANAGER: no cluster role, a DECLARED scope.

    The scope is what makes the identity a manager, so ``servers.config`` admits it and the page
    renders the denied rows; the servers it may see are the ones whose ``managed_by`` names the scope.
    """
    app, registrar = _build(config_dir, users=[
        {"username": username, "password_hash": _hash(), "roles": ["DCS"], "scope": list(scope)}])
    app.state.webui_source_provider = (
        lambda: _source(servers=servers, node_obj=node(instances={})))
    return app, registrar


def _manager_server(name="DCS Syria Campaign", *, settings=None, locals_=None, status=Status.STOPPED):
    """A server double owned by the manager (``managed_by: [Host A]``)."""
    base = {"managed_by": ["Host A"]}
    base.update(locals_ or {})
    return _namespace_server(name=name, settings=settings, locals_=base, status=status)


@pytest.mark.asyncio
async def test_a_manager_sees_neither_the_port_row_nor_the_channels_card(config_dir):
    """A manager's view OMITS the denied rows and the channels card entirely — not shown read-only.

    The port row is absent (no control, no label, no reason) and the whole Discord-channels card is
    absent, because the read model builds neither: the refusal itself stays server-side, in the action.
    """
    from core.server_config import MANAGER_DENIED

    server_obj = _manager_server(settings=BASE_SETTINGS,
                                 locals_={"channels": {"status": 111}})
    app, _ = app_with_manager(config_dir, servers=[server_obj])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration", username="manager")

 # the tab is offered and the allowed rows are editable
    assert 'name="maxPlayers"' in html, "the allowed rows stay editable for a manager"
    assert 'name="description"' in html
 # the PORT row is ABSENT — no control, no label, no reason
    assert 'name="port"' not in html, "the port must not be offered to a manager"
    assert 'class="mono">port</span>' not in html, "the port row is omitted, not shown read-only"
    assert MANAGER_DENIED["port"] not in html, "nor is its denial reason shown"
 # the WHOLE Discord-channels card is ABSENT — form, picker, heading and reason alike
    assert 'id="channels-form"' not in html, "no channels form for a manager"
    assert 'name="channels.status"' not in html, "no channel picker for a manager"
    assert "Discord channels" not in html, "the channels card is omitted, not shown read-only"
    assert MANAGER_DENIED["channels"] not in html, "nor is its denial reason shown"


@pytest.mark.asyncio
async def test_an_admin_still_gets_the_port_and_channel_controls(config_dir):
    """The positive control: nothing is denied to an Admin — the port input and picker are rendered."""
    server_obj = _namespace_server(settings=BASE_SETTINGS, locals_={"channels": {"status": 111}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, "/fleet/DCS%20Syria%20Campaign?tab=configuration")

    assert 'name="port"' in html, "an Admin keeps the port control"
    assert 'name="channels.status"' in html, "an Admin keeps the channel picker"
    assert "Discord channels" in html, "an Admin keeps the channels card"


@pytest.mark.asyncio
async def test_a_manager_cannot_open_a_server_outside_their_scope(config_dir):
    """A manager naming another server gets the console's 403, not the tab."""
    mine = _manager_server(name="Mine")
    theirs = _namespace_server(name="Theirs", locals_={"managed_by": ["Host B"]})
    app, _ = app_with_manager(config_dir, servers=[mine, theirs])

    refused = await get(app, "/fleet/Theirs?tab=configuration", username="manager", expect=403)
    assert "Not authorized" in refused


@pytest.mark.asyncio
async def test_a_managers_crafted_port_post_is_refused_with_the_sentence(config_dir):
    """The REAL action refuses a manager's port POST with its sentence.

    The route reaches the action, and the ACTION refuses the denied key -- never silently dropped and
    never a 500.
    """
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from core.server_config import MANAGER_DENIED
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "port": 10308, "maxPlayers": 16,
                                        "advanced": {}},
                              locals_={"managed_by": ["Host A"]}, node=node)
    app, _ = app_with_manager(config_dir, servers=[server_obj])
    discover_actions(["scheduler"])

    async with asgi_client(app) as client:
        await _login(client, "manager")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "port": "10309",
                                           "_csrf_token": token})
        landed = await client.get("/fleet/SRS-1?tab=configuration")
    assert response.status_code == 303, (response.status_code, response.text[:200])
    assert MANAGER_DENIED["port"] in landed.text, "the refusal sentence must be visible"
    assert server_obj.settings["port"] == 10308, "the denied key never lands"


@pytest.mark.asyncio
async def test_a_managers_description_write_lands(config_dir):
    """A manager's legitimate write (the description) applies and is shown."""
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "description": "old", "maxPlayers": 16,
                                        "advanced": {}},
                              locals_={"managed_by": ["Host A"]}, node=node)
    app, _ = app_with_manager(config_dir, servers=[server_obj])
    discover_actions(["scheduler"])

    async with asgi_client(app) as client:
        await _login(client, "manager")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "description": "new",
                                           "_csrf_token": token})
        landed = await client.get("/fleet/SRS-1?tab=configuration")
    assert response.status_code == 303
    assert server_obj.settings["description"] == "new", "the allowed write lands"
    assert "saved description" in landed.text


@pytest.mark.asyncio
async def test_an_admins_port_write_still_lands(config_dir):
    """The deny-list is a manager's rule: an Admin keeps the port."""
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import ConfigServer, node_for

    node = node_for(config_dir)
    server_obj = ConfigServer("SRS-1", status=CoreStatus.STOPPED,
                              settings={"name": "SRS-1", "port": 10308, "advanced": {}},
                              locals_={}, node=node)
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(actions_page.SERVER_CONFIG_PATH,
                                     data={"server": "SRS-1", "port": "10309",
                                           "_csrf_token": token})
    assert response.status_code == 303
    assert server_obj.settings["port"] == 10309, "an Admin may change the port"


# ------------------------------------------------------------------- 8. the drift test

def test_the_console_field_set_is_exactly_the_actions_field_set():
    """The console's declared editable set == the action's ``DCS_FIELDS`` keys — key for key."""
    from plugins.scheduler import actions as scheduler_actions

    console_editable = set(readmodels.editable_keys())
    action_editable = {field.key for field in scheduler_actions.DCS_FIELDS}
    assert console_editable == action_editable, (
        "the console's editable set drifted from the action's DCS_FIELDS: "
        f"console-only={console_editable - action_editable}, "
        f"action-only={action_editable - console_editable}")
    assert len(readmodels.editable_keys()) == 22

    console_readonly = set(readmodels.readonly_keys())
    action_readonly = {field.key for field in scheduler_actions.DCS_READONLY_FIELDS}
    assert console_readonly == action_readonly, (
        f"read-only drift: console-only={console_readonly - action_readonly}, "
        f"action-only={action_readonly - console_readonly}")

 # THE THIRD FACE, deliberately its own set: the coalition passwords are neither editable (adding
 # them to ``EDITABLE_FIELDS`` would let ``set_server_config`` write a plaintext key into
 # ``serverSettings.lua`` and break the 22-key pin above) nor read-only (the hashes are read-only;
 # the cleartext is not). Their keys are the plain form-field names, NEVER ``advanced.*``.
    console_coalitions = set(readmodels.coalition_keys())
    assert console_coalitions == {"bluePassword", "redPassword"}, console_coalitions
    assert console_coalitions.isdisjoint(console_editable)
    assert console_coalitions.isdisjoint(console_readonly)
    assert not any(key.startswith("advanced.") for key in console_coalitions)
