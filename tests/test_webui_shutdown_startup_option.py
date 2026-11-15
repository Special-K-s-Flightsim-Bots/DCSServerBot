"""The website's Shutdown/Startup dialogs carry the maintenance option.

A deliberate **Shutdown** sets the maintenance flag by DEFAULT, an explicit **Startup** CLEARS it by
default, and Start/Stop touch no flag -- exactly as Discord's ``/server shutdown|startup`` behave. The
ACTIONS do that in ``plugins/mission/actions.py``; this file gives the console the CONTROL. The option
is the SAME shape the node row uses (a checkbox with the hidden ``off`` companion that says "off" when
the box is cleared), because the checkbox is a form field and has nowhere else to live. Pins: the
declaration (Shutdown and Startup each carry an ON-by-default ``maintenance`` option whose field is the
action's own parameter name; Start/Stop carry none); the rendered dialogs (off-companion FIRST,
checked, the copy names the flag); the POSTs (the browser default and the cleared case); and the route
refusing an option value this console never sends. Driven through the real app against the real
mission actions."""
from __future__ import annotations

import inspect
import re
import types
from typing import Any

import pytest

from conftest import permissions  # noqa: F401 - re-exported for the harness import below
from core import Status
from core.actions import action_available
from services.webservice.pages import actions as actions_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    BOT_YAML, Cluster, build, clean_registry, client, config_dir, instance, open_page, recording_bot,
    sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

SHUTDOWN = "/actions/server/shutdown"
STARTUP = "/actions/server/startup"
START = "/actions/server/start"
STOP = "/actions/server/stop"
CONFIRM = actions_page.CONFIRM_SUFFIX
BY_KEY = {action.key: action for action in actions_page.SERVER_ACTIONS}


# ------------------------------------------------------------------------------- the fleet

def fleet(*, status=Status.RUNNING, maintenance=False, players=0):
    """ONE server, ``SRS-1`` (Host A), whose ``maintenance`` attribute the actions write directly.

    The status decides which of the pair is offered (RUNNING -> Shutdown, SHUTDOWN -> Startup), and
    ``maintenance`` is the flag's value BEFORE the write — the thing the POST's option must move.
    """
    a = FakeServer("SRS-1", status=status, managed_by=["Host A"], players=players)
    a.maintenance = maintenance
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a)])


def posted(*pairs: tuple[str, str]) -> dict[str, Any]:
    """A form body that can carry REPEATED fields, in order (the checkbox and its companion)."""
    body: dict[str, Any] = {}
    for name, value in pairs:
        if name not in body:
            body[name] = value
        elif isinstance(body[name], list):
            body[name].append(value)
        else:
            body[name] = [body[name], value]
    return body


def browser_form(name: str, csrf: str, confirm: str | None, option: str | None,
                 checked: bool = False) -> dict[str, Any]:
    """What a BROWSER sends for a server dialog's form — in the order the markup renders it."""
    pairs: list[tuple[str, str]] = [("server", name), ("_csrf_token", csrf)]
    if option is not None:
        pairs.append((option, "0"))
        if checked:
            pairs.append((option, "1"))
    if confirm is not None:
        pairs.append(("_confirm_token", confirm))
    return posted(*pairs)


def form_for(html: str, path: str) -> str:
    """The ONE ``<form>`` that posts to *path*."""
    found = re.findall(rf'<form[^>]*action="{re.escape(path)}".*?</form>', html, re.S)
    assert found, f"the page rendered no control posting to {path}"
    return found[0]


def dialog_token(html: str) -> str:
    match = re.search(r'name="_confirm_token" value="([^"]+)"', html)
    assert match, "the dialog carries no confirm token"
    return match.group(1)


async def open_dialog(http, path: str, name: str = "SRS-1", *, expect_token: bool = True):
    """Open one server control's dialog and return (response, its one-shot token)."""
    page = await open_page(http)
    reply = await http.post(path + CONFIRM, data={"server": name, "_csrf_token": token_of(page)})
    assert reply.status_code == 200, f"{path}: {reply.status_code}"
    if not expect_token:
        return reply, ""
    return reply, dialog_token(reply.text)


# ------------------------------------------------------------------- 1. the declaration

def test_shutdown_and_startup_declare_the_maintenance_option_defaulting_on():
    """The card's contract, asserted where it lives: the option, its field and its default.

    ``field`` is BOTH the form field's name and the ACTION's parameter name — one string, so the
    route cannot post one thing and the action read another (pinned against the real signatures in
    the next test).
    """
    for key in ("shutdown", "startup"):
        action = BY_KEY[key]
        assert action.option is not None, f"{key}: the maintenance option is missing"
        assert action.option.field == "maintenance", key
        assert action.option.default is True, f"{key}: the default must be ON"
        assert action.option.on == "1" and action.option.off == "0", key
    assert BY_KEY["shutdown"].option.label == "Set maintenance so the scheduler does not restart it"
    assert BY_KEY["startup"].option.label == "End maintenance so the server is scheduled again"
    assert "Discord's /server shutdown" in BY_KEY["shutdown"].option.help
    assert "scheduled start brings the server straight back" in BY_KEY["shutdown"].option.help
 # Startup's help must not pretend the server was necessarily flagged
    assert "Discord's /server startup" in BY_KEY["startup"].option.help
    assert "An unflagged server simply stays unflagged." in BY_KEY["startup"].option.help


def test_start_and_stop_gain_no_option():
    """The PROCESS-level pair touches no flag, so it carries no option."""
    assert BY_KEY["start"].option is None
    assert BY_KEY["stop"].option is None


def test_the_option_field_is_the_actions_own_parameter_name():
    """The form field and the ACTION's parameter are ONE string, so a rename cannot drift.

    The route hands the parsed option to the action under ``action.option.field``; pinned against the
    REAL ``plugins.mission.actions`` signatures.
    """
    import sys

    import core.actions as actions_module
    from core.actions import discover_actions

    sys.modules.pop("plugins.mission.actions", None)
    saved = dict(actions_module._ACTIONS)
    actions_module._ACTIONS.clear()
    try:
        discover_actions(["mission"])
        import plugins.mission.actions as mission_actions

        shutdown = inspect.signature(mission_actions.shutdown_server).parameters
        startup = inspect.signature(mission_actions.startup_server).parameters
        assert BY_KEY["shutdown"].option.field in shutdown
        assert BY_KEY["startup"].option.field in startup
        assert "maintenance" in shutdown and "maintenance" in startup
        for qualname in ("start_server", "stop_server"):
            assert "maintenance" not in inspect.signature(
                getattr(mission_actions, qualname)).parameters, qualname
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def test_the_dialog_routes_are_registered_and_capability_declared():
    """A control that carries an option reaches its DIALOG: the dialog path exists and is gated.

    ``capabilities`` declares the dialog path for every action with a dialog; the OPTION is what puts
    Startup's dialog there — no new capability is introduced (the option rides the existing write).
    """
    caps = actions_page.capabilities()
    for key in ("shutdown", "startup"):
        action = BY_KEY[key]
        assert action.dialog is True, key
        assert action.path + CONFIRM in caps, key
        assert caps[action.path + CONFIRM] == action.capability, key
        assert caps[action.path] == action.capability, key
    assert BY_KEY["start"].dialog is False, "Start posts directly"
    assert BY_KEY["stop"].dialog is True and BY_KEY["stop"].option is None, \
        "Stop confirms (dialog) but carries no option"


# --------------------------------------------------------------------- 2. the dialogs

@pytest.mark.asyncio
async def test_the_shutdown_dialog_draws_the_option_defaulted_on(config_dir, clean_registry):
    """The option, in the markup: the off-companion FIRST, the checkbox CHECKED, the copy honest."""
    app = build(config_dir, fleet(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, SHUTDOWN)
    text = dialog.text
    assert 'name="maintenance" value="0"' in text
    assert 'name="maintenance" value="1"' in text
    assert re.search(r'name="maintenance" value="1"[^>]*\schecked', text), \
        "the default is ON — without it a scheduled start brings the server back"
    assert text.index('name="maintenance" value="0"') < text.index('name="maintenance" value="1"'), \
        "the OFF companion must come FIRST (the last value wins)"
    assert "Set maintenance so the scheduler does not restart it" in text
    assert "a scheduled start brings the server straight back" in text
    assert "<b>Nobody is connected right now.</b>" in text, \
        "a zero count drops the player sentence instead of writing '0 players'"
    assert "players are disconnected" not in text, "the old wording is gone"
    assert "player(s) connected right now" not in text


@pytest.mark.asyncio
async def test_the_startup_dialog_draws_the_option_defaulted_on(config_dir, clean_registry):
    """Startup's dialog is the OPTIONS form (no confirmation chrome) and does not pretend a flag.

    A SHUTDOWN server offers Startup, which now posts to its dialog (the option's home). The dialog
    mints NO one-shot token: an option-only dialog has nothing for the route to refuse.
    """
    app = build(config_dir, fleet(status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
    text = dialog.text
    assert 'name="maintenance" value="0"' in text and 'name="maintenance" value="1"' in text
    assert re.search(r'name="maintenance" value="1"[^>]*\schecked', text), "the default is ON"
    assert text.index('name="maintenance" value="0"') < text.index('name="maintenance" value="1"')
    assert "End maintenance so the server is scheduled again" in text
    assert "An unflagged server simply stays unflagged." in text
    assert ">Start up server<" in text, "the button's own word (no destructive 'Shut down')"
    assert '<span class="tag">options</span>' in text, "an options form, not a confirmation"
    assert 'name="_confirm_token"' not in text, "a token nothing spends must not be minted"
    assert 'class="btn danger"' not in text, "Startup is not the destructive button"


@pytest.mark.asyncio
async def test_the_dialogs_name_the_flag_effect_in_the_maintenance_vocabulary(config_dir,
                                                                              clean_registry):
    """A person pressing Shutdown can tell from the dialog that the server also leaves service.

    Shutdown's warning names the flag; Startup's detail names the clear and states the unflagged case.
    """
    app = build(config_dir, fleet(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        shutdown, _ = await open_dialog(http, SHUTDOWN)
    assert "puts the server into maintenance" in shutdown.text
    assert "stays out of service" in shutdown.text
    assert "the scheduler does not bring it back up" in shutdown.text

    app = build(config_dir, fleet(status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "mod1")
        startup, _ = await open_dialog(http, STARTUP, expect_token=False)
    assert "any maintenance is ended" in startup.text
    assert "the scheduler may start it again" in startup.text
    assert "was not in maintenance simply stays that way" in startup.text


@pytest.mark.asyncio
async def test_stop_carries_no_option_and_no_checkbox(config_dir, clean_registry):
    """The process-level Stop confirms, but offers NO maintenance box (it touches no flag)."""
    app = build(config_dir, fleet(status=Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STOP)
    assert 'name="maintenance"' not in dialog.text
    assert 'type="checkbox"' not in dialog.text


@pytest.mark.asyncio
async def test_start_has_no_dialog_at_all(config_dir, clean_registry):
    """Start posts DIRECTLY (no option, no dialog): its row form is the action's own path."""
    app = build(config_dir, fleet(status=Status.STOPPED))
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
    assert f'action="{START}"' in page
    assert f'action="{START}{CONFIRM}"' not in page


# ----------------------------------------------------------------------- 3. the posts

@pytest.mark.asyncio
async def test_shutdown_with_the_box_ticked_sets_the_flag(config_dir, clean_registry, recording_bot):
    """The default (the box is rendered CHECKED): the server comes out of service."""
    cluster = fleet(status=Status.RUNNING, maintenance=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, confirm = await open_dialog(http, SHUTDOWN)
        reply = await http.post(SHUTDOWN, data=browser_form("SRS-1", token_of(dialog.text),
                                                            confirm, "maintenance", checked=True))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].maintenance is True
    assert cluster.servers[0].calls == ["shutdown"]


@pytest.mark.asyncio
async def test_shutdown_with_the_box_cleared_writes_no_flag(config_dir, clean_registry):
    """The cleared box posts the OFF companion: the server is shut down WITHOUT staying down."""
    cluster = fleet(status=Status.RUNNING, maintenance=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, confirm = await open_dialog(http, SHUTDOWN)
        reply = await http.post(SHUTDOWN, data=browser_form("SRS-1", token_of(dialog.text),
                                                            confirm, "maintenance", checked=False))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].maintenance is False, "a cleared box must clear the flag"
    assert cluster.servers[0].calls == ["shutdown"]


@pytest.mark.asyncio
async def test_startup_with_the_box_ticked_clears_the_flag(config_dir, clean_registry):
    """The default: an explicit Startup ends the maintenance so the scheduler starts it again."""
    cluster = fleet(status=Status.SHUTDOWN, maintenance=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
        reply = await http.post(STARTUP, data=browser_form("SRS-1", token_of(dialog.text), None,
                                                           "maintenance", checked=True))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].maintenance is False, "the flag is cleared by default"
    assert cluster.servers[0].calls == ["startup"]


@pytest.mark.asyncio
async def test_startup_with_the_box_cleared_leaves_the_flag(config_dir, clean_registry):
    """The cleared box: the flag is left exactly as it was — here, still set."""
    cluster = fleet(status=Status.SHUTDOWN, maintenance=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
        reply = await http.post(STARTUP, data=browser_form("SRS-1", token_of(dialog.text), None,
                                                           "maintenance", checked=False))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].maintenance is True, "the parameter says leave it — and it stays"


@pytest.mark.asyncio
async def test_the_off_option_is_never_read_as_on(config_dir, clean_registry):
    """``bool('0')`` is True: the companion ALONE must mean OFF (the parse, not a truthiness test).

    The body states the box ticked then the companion — the LAST value wins, which is the OFF the
    browser sent for a box the person cleared after rendering.
    """
    cluster = fleet(status=Status.RUNNING, maintenance=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, confirm = await open_dialog(http, SHUTDOWN)
        reply = await http.post(SHUTDOWN, data=posted(
            ("server", "SRS-1"), ("_csrf_token", token_of(dialog.text)),
            ("maintenance", "1"), ("maintenance", "0"), ("_confirm_token", confirm)))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].maintenance is False, "the LAST value wins, and it is the companion's 0"


@pytest.mark.asyncio
async def test_start_and_stop_post_no_option_and_leave_the_flag_alone(config_dir, clean_registry):
    """The process-level pair: nothing to post, and nothing writes the flag in either direction."""
    from test_webui_write_server_actions import fleet as running_fleet  # RUNNING env for Stop

    cluster = running_fleet(Status.RUNNING)
    for server in cluster.servers:
        server.maintenance = True
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
 # Stop confirms; its dialog has no option, so the action route takes the body as-is
        dialog, confirm = await open_dialog(http, STOP)
        assert 'name="maintenance"' not in dialog.text
        stopped = await http.post(STOP, data={"server": "SRS-1", "_csrf_token": token_of(dialog.text),
                                              "_confirm_token": confirm})
        assert stopped.status_code == 303
    assert cluster.servers[0].maintenance is True, "Stop never touches the flag"

 # Start posts directly (no dialog) and touches no flag either
    stopped_cluster = fleet(status=Status.STOPPED, maintenance=True)
    app = build(config_dir, stopped_cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)
        started = await http.post(START, data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert started.status_code == 303
    assert stopped_cluster.servers[0].maintenance is True, "Start never touches the flag"


# --------------------------------------------------------------- 4. a bad option is refused

@pytest.mark.asyncio
async def test_an_option_this_console_never_sends_is_refused(config_dir, clean_registry,
                                                             recording_bot):
    """A body stating an option outside the known spellings is a 400 — never a guessed boolean."""
    cluster = fleet(status=Status.RUNNING)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, confirm = await open_dialog(http, SHUTDOWN)
        reply = await http.post(SHUTDOWN, data=posted(
            ("server", "SRS-1"), ("_csrf_token", token_of(dialog.text)),
            ("maintenance", "maybe"), ("_confirm_token", confirm)))
    assert reply.status_code == 400
    assert cluster.servers[0].calls == [], "nothing ran"
    assert cluster.servers[0].maintenance is False


# --------------------------------------- 5. Startup's OTHER two options: the extension run

def test_startup_declares_the_extension_pair_and_its_siblings_do_not():
    """Startup carries /server startup's own choices beside the flag: run the extensions, and
    whether they are applied to the ORIGINAL file.

    The declaration is asserted where it lives — the fields' names are the ACTION's parameter names
    (``modify_mission``/``use_orig``), so the route cannot post one thing and the action read another.
    """
    startup = BY_KEY["startup"]
    fields = [option.field for option in actions_page.dialog_options(startup)]
    assert fields == ["maintenance", "modify_mission", "use_orig"], fields
    for option in actions_page.dialog_options(startup):
        if option.field == "announce":
            # the per-launch weather announcement: OFF unless the operator asks for it
            assert option.default is False, "the announce box defaults OFF"
        else:
            assert option.default is True, f"{option.field}: /server startup defaults both ON"
        assert option.on == "1" and option.off == "0", option.field
    # The flag pair's siblings stay single-option: nothing else grew a second box.
    for key in ("shutdown", "stop", "start", "restart", "pause", "unpause"):
        assert len(actions_page.dialog_options(BY_KEY[key])) <= 1, key


def test_the_extension_fields_are_the_actions_own_parameter_names():
    """``modify_mission``/``use_orig`` reach ``startup_server`` under their own names, and the action
    hands them to ``Server.startup`` unchanged — pinned against the REAL signatures."""
    import sys

    import core.actions as actions_module
    from core.actions import discover_actions

    sys.modules.pop("plugins.mission.actions", None)
    saved = dict(actions_module._ACTIONS)
    actions_module._ACTIONS.clear()
    try:
        discover_actions(["mission"])
        import plugins.mission.actions as mission_actions

        params = inspect.signature(mission_actions.startup_server).parameters
        for field in ("maintenance", "modify_mission", "use_orig"):
            assert field in params, field
        source = inspect.getsource(mission_actions.startup_server)
        assert "modify_mission=modify_mission" in source and "use_orig=use_orig" in source, \
            "the action must pass both choices through, not accept and drop them"
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


@pytest.mark.asyncio
async def test_the_startup_dialog_draws_the_extension_boxes_defaulted_on(config_dir, clean_registry):
    """The three boxes, in the declaration's order, each with its OFF companion ahead of it."""
    app = build(config_dir, fleet(status=Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
    text = dialog.text
    for field in ("maintenance", "modify_mission", "use_orig"):
        assert f'name="{field}" value="0"' in text, f"{field}: the OFF companion is missing"
        assert f'name="{field}" value="1"' in text, f"{field}: the checkbox is missing"
        assert re.search(rf'name="{field}" value="1"[^>]*\schecked', text), \
            f"{field}: the default must render CHECKED"
        assert text.index(f'name="{field}" value="0"') < text.index(f'name="{field}" value="1"'), \
            f"{field}: the OFF companion must come FIRST"
    assert "Run extensions on the mission" in text
    assert "Apply them to the original mission file" in text
    assert ">Start up server<" in text


@pytest.mark.asyncio
async def test_startup_posts_both_extension_choices_to_the_action(config_dir, clean_registry):
    """The two boxes reach ``Server.startup`` — the default and the flipped case, both asserted."""
    cluster = fleet(status=Status.SHUTDOWN, maintenance=True)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
        reply = await http.post(STARTUP, data=browser_form(
            "SRS-1", token_of(dialog.text), None, "maintenance", checked=True))
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}], cluster.servers[0].call_args


@pytest.mark.asyncio
async def test_startup_can_skip_the_extensions_entirely(config_dir, clean_registry):
    """Unticking the extension box posts ``modify_mission`` OFF, and the action forwards it."""
    cluster = fleet(status=Status.SHUTDOWN, maintenance=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
        body = browser_form("SRS-1", token_of(dialog.text), None, "maintenance", checked=True)
        body["modify_mission"] = "0"
        reply = await http.post(STARTUP, data=body)
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": False, "use_orig": True, "preset": None}], cluster.servers[0].call_args


@pytest.mark.asyncio
async def test_startup_can_apply_the_extensions_to_the_current_file(config_dir, clean_registry):
    """``use_orig`` OFF means "apply them to the file the server is on now", and it travels too."""
    cluster = fleet(status=Status.SHUTDOWN, maintenance=False)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog, _ = await open_dialog(http, STARTUP, expect_token=False)
        body = browser_form("SRS-1", token_of(dialog.text), None, "maintenance", checked=True)
        body["use_orig"] = "0"
        reply = await http.post(STARTUP, data=body)
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": False, "preset": None}], cluster.servers[0].call_args
