"""The channels group (console half): real channel pickers and the central-admin rule.

Render + route; the action's own behaviour is in ``test_scheduler_channels.py``. The picker is
rendered from the guild's channels, grouped by category, with an explicit "not set". With a central
admin channel the per-server admin row is ABSENT (not hidden, not disabled); without one it is
offered. The channel list is read from the bot in-process. Driven through the real app; harness
reused from ``test_webui_server_config``."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, permissions, stub_identity
from core.actions import ActionContext
from services.webservice.pages import actions as actions_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels import serverconfig as server_config
from services.webservice.scope import Scope

from test_webui_server_config import (  # noqa: F401 - fixtures/harness imported on purpose
    BASE_SETTINGS, NoRpcServer, _hash, _namespace_server, _spy, app_with, config_dir, get,
)
from test_webui_dashboard import _login

DETAIL = "/fleet/DCS%20Syria%20Campaign?tab=configuration"
CHANNELS = actions_page.SERVER_CHANNELS_PATH


# ---------------------------------------------------------------------------------- the stub bot

def _channel(cid, name, *, category=None, position=0):
    return types.SimpleNamespace(id=cid, name=name, position=position,
                                 category=(types.SimpleNamespace(name=category) if category else None))


def stub_bot(*channels, central=None):
    """A bot double: one guild holding *channels*, and an optional central admin channel id."""
    guild = types.SimpleNamespace(channels=list(channels))
    locals_ = {"channels": {"admin": central}} if central else {}
    return types.SimpleNamespace(guilds=[guild], locals=locals_)


@pytest.fixture
def bot(monkeypatch):
    """Point the read model's bot resolution at a stub — the channel list's in-process source."""
    def _install(bot_obj):
        monkeypatch.setattr(server_config, "live_bot", lambda: bot_obj)
        return bot_obj
    return _install


# --------------------------------------------------------------------- 1. the pickers are rendered

@pytest.mark.asyncio
async def test_the_channel_rows_render_the_guilds_channels_grouped_by_category(config_dir, bot):
    bot(stub_bot(_channel(1001, "general"), _channel(1002, "ops", category="Ops", position=0),
                 _channel(1003, "briefing", category="Ops", position=1)))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)

 # each row is a select of the guild's channels
    for key in ("channels.status", "channels.chat", "channels.admin"):
        assert f'<select name="{key}"' in html, f"{key} must be a channel picker"
 # the options carry label + id, and the categories are the optgroups
    assert '<option value="1001">#general</option>' in html
    assert '<option value="1002">#ops</option>' in html
    assert '<optgroup label="Ops">' in html and "#briefing" in html
 # every optgroup opened is closed, and the uncategorized channel is NOT wrapped in one
    assert html.count("<optgroup") == html.count("</optgroup>") >= 1
    assert '<optgroup label="">' not in html, "an uncategorized channel is not given an empty optgroup"
 # an explicit "not set" option, whose value is the sentinel (never a blank)
    assert (f'<option value="{server_config.CHANNEL_UNSET_VALUE}"' in html
            and "— not set —" in html)
 # no display-only leftovers
    assert "<b>Display-only</b>" not in html


@pytest.mark.asyncio
async def test_a_set_and_offered_channel_is_marked_selected(config_dir, bot):
    bot(stub_bot(_channel(1002, "ops"), _channel(1003, "briefing")))
    server_obj = _namespace_server(settings=BASE_SETTINGS, locals_={"channels": {"status": 1003}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, DETAIL)
    assert '<option value="1003" selected>#briefing</option>' in html
 # the untouched form a browser sends is the current value, so the row diffs to nothing
    assert '<option value="1002">#ops</option>' in html


@pytest.mark.asyncio
async def test_a_set_channel_that_is_not_offered_is_shown_as_its_own_option(config_dir, bot):
    """A channel since deleted (or from another guild) must not read as "not set" on an untouched form."""
    bot(stub_bot(_channel(1002, "ops")))
    server_obj = _namespace_server(settings=BASE_SETTINGS, locals_={"channels": {"chat": 9999}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    html = await get(app, DETAIL)
    assert '<option value="9999" selected>' in html and "not in this guild" in html


@pytest.mark.asyncio
async def test_with_no_bot_the_rows_render_with_only_not_set(config_dir, bot):
    bot(None)
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)
    assert f'<option value="{server_config.CHANNEL_UNSET_VALUE}" selected>' in html
    assert "could not be read right now" in html


# --------------------------------------------------------- 2. the central-admin rule, both ways

@pytest.mark.asyncio
async def test_the_admin_row_is_absent_when_a_central_admin_channel_is_defined(config_dir, bot):
    """ABSENT — not hidden with CSS, not disabled: the row is not built at all."""
    bot(stub_bot(_channel(1001, "general"), central=777))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)

    assert 'name="channels.admin"' not in html, "the admin row must not be rendered"
    assert "Admin channel" not in html, "nor its label"
 # the other two rows are offered normally
    assert 'name="channels.status"' in html and 'name="channels.chat"' in html
 # and the note says why
    assert "defined in" in html and "not shown or edited here" in html


@pytest.mark.asyncio
async def test_the_admin_row_is_offered_when_no_central_admin_channel_is_defined(config_dir, bot):
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)

    assert 'name="channels.admin"' in html, "with no central channel the per-server one is offered"
    assert "Admin channel" in html


@pytest.mark.asyncio
async def test_the_render_issues_no_node_rpc(config_dir, bot):
    """A server double whose ``get_config`` and node RPCs raise still renders the whole tab."""
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[NoRpcServer(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)
    assert "Discord channels" in html and 'name="channels.status"' in html


# --------------------------------------------------------------------------------- 3. the write

async def _post_channels(app, data, username="staff", *, context=None, monkeypatch=None):
    """POST the channels form (target + CSRF from the rendered page) and return (response, landed)."""
    async with asgi_client(app) as client:
        await _login(client, username)
        page = await client.get(DETAIL)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        payload = {"server": "DCS Syria Campaign", "_csrf_token": token}
        payload.update(data)
        response = await client.post(CHANNELS, data=payload)
        landed = await client.get(DETAIL)
    return response, landed.text


def _channels_spy(monkeypatch, result_factory):
    """Replace ``call_action`` with a recorder — the route must CALL the ONE channels action."""
    calls: list[tuple[str, dict]] = []

    async def fake_call(qualname, ctx, /, *, audit_result=False, **params):
        calls.append((qualname, params))
        return result_factory()

    monkeypatch.setattr(actions_page, "call_action", fake_call)
    return calls


def _ok_result(**kw):
    from core.action_results import ServerConfigResult
    defaults = dict(success=True, server_name="DCS Syria Campaign", message="saved status")
    defaults.update(kw)
    return ServerConfigResult(**defaults)


@pytest.mark.asyncio
async def test_a_changed_row_calls_the_one_action_with_only_the_changed_row(config_dir, monkeypatch,
                                                                           bot):
    bot(stub_bot(_channel(1001, "general"), _channel(1002, "ops")))
    server_obj = _namespace_server(settings=BASE_SETTINGS, locals_={"channels": {"status": 1001}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    calls = _channels_spy(monkeypatch, lambda: _ok_result())

    response, html = await _post_channels(app, {"channels.status": "1002",
                                                "channels.chat": server_config.CHANNEL_UNSET_VALUE})
    assert response.status_code == 303
    assert len(calls) == 1, calls
    qualname, params = calls[0]
    assert qualname == "set_server_channels", "the channels write calls its OWN action"
    assert params["server_name"] == "DCS Syria Campaign"
    assert params["channels"] == {"status": 1002}, params["channels"]
    assert "saved status" in html, "the action's message must reach the one-shot notice"


@pytest.mark.asyncio
async def test_an_untouched_form_submits_no_changes(config_dir, monkeypatch, bot):
    bot(stub_bot(_channel(1001, "general"), _channel(1002, "ops")))
    server_obj = _namespace_server(settings=BASE_SETTINGS,
                                   locals_={"channels": {"status": 1001, "chat": 1002}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    calls = _channels_spy(monkeypatch, lambda: _ok_result(message="nothing changed"))

    await _post_channels(app, {"channels.status": "1001", "channels.chat": "1002",
                               "channels.admin": server_config.CHANNEL_UNSET_VALUE})
    assert calls[0][1]["channels"] == {}, calls[0][1]["channels"]


@pytest.mark.asyncio
async def test_not_set_on_a_set_row_is_an_explicit_change(config_dir, monkeypatch, bot):
    bot(stub_bot(_channel(1001, "general")))
    server_obj = _namespace_server(settings=BASE_SETTINGS, locals_={"channels": {"status": 1001}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    calls = _channels_spy(monkeypatch, lambda: _ok_result())

    await _post_channels(app, {"channels.status": server_config.CHANNEL_UNSET_VALUE})
    assert calls[0][1]["channels"] == {"status": server_config.CHANNEL_UNSET}, calls[0][1]["channels"]


@pytest.mark.asyncio
async def test_the_page_says_when_a_channel_change_applies(config_dir, bot):
    """The config tab's honesty rule: a channel change is applied immediately."""
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    html = await get(app, DETAIL)
    assert server_config.CHANNEL_APPLY_NOTE in html
    assert "applied immediately" in html
    assert "no restart" in html


@pytest.mark.asyncio
async def test_the_save_channels_control_is_not_offered_when_the_action_is_absent(config_dir, bot):
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    assert not actions_page.action_available("set_server_channels")
    html = await get(app, DETAIL)
    assert re.search(r'<button[^>]*disabled[^>]*>Save channels</button>', html), \
        "Save channels must be disabled when the write is not installed"


# --------------------------------------------------------------------------- 4. the declarations

def test_the_channels_write_has_its_own_admin_only_capability():
    """The channels write has its OWN capability — Admin only, NO scope grant (a manager may not).

    The READ stays the tab's ``servers.config``; the channel rows are ALSO named in the manager
    deny-list (``core.server_config.CHANNELS_ITEM``) so the page OMITS the whole card and the action
    re-checks.
    """
    actions_page.declare()
    detail_page.capabilities()
    action = actions_page.SERVER_CHANNEL_ACTIONS[0]
    assert action.qualname == "set_server_channels"
    assert action.capability == actions_page.SERVER_CHANNELS_CAPABILITY == "servers.config.channels"
    assert action.capability != actions_page.SERVER_CONFIG_CAPABILITY, "its own, not the DCS write's"
    assert action.path == CHANNELS
    assert action.statuses == (), "a channel change is applied immediately: no state gate"
    assert permissions.roles_for_capability(action.capability) == ("Admin",)
    assert permissions.scope_grants(action.capability) is False


@pytest.mark.asyncio
async def test_the_channels_write_route_is_post_only_with_its_own_capability(config_dir, bot):
    bot(stub_bot(_channel(1001, "general")))
    app, registrar = app_with(config_dir, ["Admin"],
                              servers=[_namespace_server(settings=BASE_SETTINGS)])
    routes = [r for r in registrar.routes if getattr(r, "path", None) == CHANNELS]
    assert routes, "the channels write route is registered"
    for route in routes:
        assert set(route.methods or ()) == {"POST"}, "the channels write is a POST, never a GET"
    assert registrar.capability_for(path=CHANNELS) == actions_page.SERVER_CHANNELS_CAPABILITY


# --------------------------------------------------------------------------------- 5. refusals

@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_the_channels_write_by_the_gate(config_dir, bot):
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["DCS Admin"],
                      servers=[_namespace_server(settings=BASE_SETTINGS)])
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.post(CHANNELS, data={"server": "DCS Syria Campaign"})
    assert response.status_code == 403, "the gate refuses the channels write capability"


@pytest.mark.asyncio
async def test_an_out_of_scope_target_is_refused_and_runs_no_action(config_dir, monkeypatch, bot):
    """No scope weakening: the route resolves through ``resolve_scoped_server`` like every write."""
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _channels_spy(monkeypatch, lambda: _ok_result())

 # a caller whose scope is restricted and whose view holds NOTHING: any name is NOT_PERMITTED
    restricted = ActionContext.from_web(
        types.SimpleNamespace(roles=set(), scope=Scope.restricted({"Host B"}),
                              shown_name="hoster", backend="local", subject="hoster"),
        None, types.SimpleNamespace(servers={}))
    monkeypatch.setattr(actions_page, "_context", lambda request, identity: restricted)

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(DETAIL)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(CHANNELS, data={"server": "DCS Syria Campaign",
                                                     "channels.status": "1001",
                                                     "_csrf_token": token})
    assert response.status_code == 403
    assert calls == [], "a scope-refused request reaches no action"


@pytest.mark.asyncio
async def test_a_foreign_server_name_is_refused_and_calls_no_action(config_dir, monkeypatch, bot):
    bot(stub_bot(_channel(1001, "general")))
    app, _ = app_with(config_dir, ["Admin"], servers=[_namespace_server(settings=BASE_SETTINGS)])
    calls = _channels_spy(monkeypatch, lambda: _ok_result())

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get(DETAIL)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(CHANNELS, data={"server": "No Such Server",
                                                     "_csrf_token": token})
    assert response.status_code == 303
    assert calls == [], "no action may run for a name that does not resolve"


# ------------------------------------------------------- 6. end to end through the REAL action

class _YamlChannelServer:
    """A server double that writes a real ``servers.yaml`` through the bot's own ``update_channels``.

    Mirrors ``core/data/impl/serverimpl.py`` (rewrite the entry's ``channels`` map, update ``locals``)
    so the end-to-end test proves the write LANDED in the file it targets.
    """

    def __init__(self, name, *, status, locals_, node):
        self.name = name
        self.display_name = name
        self.status = status
        self.locals = locals_
        self.node = node
        self.settings = types.SimpleNamespace(mtime=0.0)

    async def update_channels(self, channels: dict) -> None:
        import os
        from pathlib import Path
        from ruamel.yaml import YAML
        config_file = os.path.join(self.node.config_dir, "servers.yaml")
        data = YAML().load(Path(config_file).read_text(encoding="utf-8"))
 # Mirrors the REAL helper: merge onto the FILE's current map, so a key the write does not
 # name is preserved rather than replaced.
        entry = data[self.name]
        current = entry.get("channels")
        current = dict(current) if isinstance(current, dict) else {}
        current.update(channels)
        entry["channels"] = current
        with open(config_file, mode="w", encoding="utf-8") as outfile:
            YAML().dump(data, outfile)
        self.locals.setdefault("channels", {}).update(channels)


@pytest.mark.asyncio
async def test_the_real_action_writes_the_channel_into_servers_yaml(config_dir, monkeypatch, bot):
    """END TO END with the REAL ``set_server_channels`` (no spy): a changed row POSTed through the
    route lands in ``servers.yaml`` through the bot's own ``update_channels``. The route's call
    reaches the action through the registry."""
    import plugins.scheduler.actions as sa_module
    from core import Status as CoreStatus
    from core.actions import discover_actions
    from test_scheduler_server_config import node_for, read_servers_yaml, write_servers_yaml

    bot(stub_bot(_channel(1001, "general"), _channel(1002, "ops")))
    node = node_for(config_dir)
    server_obj = _YamlChannelServer("SRS-1", status=CoreStatus.STOPPED,
                                    locals_={"channels": {"status": 1001}}, node=node)
    write_servers_yaml(config_dir, {"SRS-1": {"channels": {"status": 1001}}})
    app, _ = app_with(config_dir, ["Admin"], servers=[server_obj])
    discover_actions(["scheduler"])
 # the ACTION's own bot resolution (the guild's channel list) needs the stub too
    monkeypatch.setattr(sa_module, "_live_bot", lambda: stub_bot(_channel(1001, "general"),
                                                                 _channel(1002, "ops")))

    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await client.get("/fleet/SRS-1?tab=configuration")
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        response = await client.post(CHANNELS, data={"server": "SRS-1", "channels.status": "1002",
                                                     "_csrf_token": token})
    assert response.status_code == 303
    assert read_servers_yaml(config_dir)["SRS-1"]["channels"]["status"] == 1002, \
        "the change must land in servers.yaml through the bot's own update_channels"


