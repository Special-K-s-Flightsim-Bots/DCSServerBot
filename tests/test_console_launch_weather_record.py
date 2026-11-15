"""A console weather pick is recorded only when the launch that received it applies it.

The console's two launch actions (``startup_server`` and ``load_mission``) carry the dialog's weather
pick to the node's own launch call. The node records what a ``modify_mission=False`` launch states,
because the SCHEDULER's launch states there the weather it has ALREADY applied to the mission file
(it calls ``server.modifyMission`` first). A console pick is the opposite statement: "apply this".
Sending that pick into a launch whose extensions do not run records a preset nothing applied, and
the server page then claims weather no launch produced.

Contract pinned here, one section per acceptance item:

* a console launch with the extensions UNTICKED and a preset picked does not run at all — it is
  REFUSED with one sentence, records nothing, and therefore cannot claim weather;
* the same refusal covers the temporary preset DOCUMENT shape, not only a preset name;
* a console pick WITH the extensions ticked records exactly as before (name and document);
* a console launch with no pick still runs and clears (the pre-existing ``modify_mission=False``
  behaviour);
* the NODE-side meaning of ``preset`` on a ``modify_mission=False`` launch is UNCHANGED, so the
  scheduler's own load still records what it applied.

Both hops are real: the console action is the plugin's own ``@action`` function, and the server it
calls is the REAL ``ServerImpl`` wired with only the attributes the launch path touches
(``build_server`` from ``test_mission_load_clears_stale_preset``). The row the recording pool keeps is
fed to the page's OWN read model (``launched_preset_view``), so the assertion is on the label.
"""
from __future__ import annotations

import inspect
import types

import pytest

from core import Status
from core.actions import ActionContext
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ServerImpl
from plugins.mission import actions as mission_actions
from services.webservice import readmodels

from test_mission_load_clears_stale_preset import (  # noqa: F401 - fixtures imported on purpose
    SERVER, RecordingPool, RowState, build_mizedit, build_server, label_of, mission, no_write,
    record_writes,
)


# --------------------------------------------------------------------------------- the console double

def console_target(srv: ServerImpl, *, status: Status) -> ServerImpl:
    """The object a console action talks to: ``srv`` itself, whose launch methods run the REAL node code.

    The action only needs ``name`` / ``status`` / ``maintenance`` and a launch coroutine; binding the
    real ``ServerImpl`` methods onto the instance keeps the node's own record decision in the test
    instead of a stand-in that could agree with the console by construction.
    """
    srv._status = status
    srv._maintenance = False

    async def startup(**kwargs):
        return await ServerImpl.startup(srv, **kwargs)

    async def loadMission(mission_file, **kwargs):
        return await ServerImpl.loadMission(srv, mission_file, **kwargs)

    async def restart(**kwargs):
        return await ServerImpl.restart(srv, **kwargs)

    srv.startup = startup
    srv.loadMission = loadMission
    srv.restart = restart
    return srv


def stub_startup_path(srv: ServerImpl) -> ServerImpl:
    """Everything ``ServerImpl.startup`` reaches around the record — none of it is what this file pins."""
    async def noop(*args, **kwargs):
        return None

    srv._assert_resource_available = noop
    srv._assert_ports_free = noop
    srv.init_extensions = noop
    srv.prepare_extensions = noop
    srv._cleanup_sqlite = lambda: None
    srv.do_startup = lambda: None
    srv._minimize = lambda: None      # Win32: walks the console window of a real process
    srv.is_running = noop
    return srv


@pytest.fixture(autouse=True)
def desanitized(monkeypatch):
    """The node's DCS install check is not what this file pins."""
    monkeypatch.setattr(serverimpl.utils, "is_desanitized", lambda node: True)


def ctx_for(server) -> ActionContext:
    """The seam's context over a one-server stub cluster — the shape the console builds."""
    return ActionContext(node=None, bus=types.SimpleNamespace(servers={server.name: server}))


PRESET_FILE = """\
Configured:
  modify:
    weather:
      clouds:
        base: 1000
Fresh:
  modify:
    weather:
      clouds:
        base: 500
"""

TEMP_DOCUMENT = {"modify": {"weather": {"clouds": {"base": 250}}}}


def build_launch(mission_file, *, pool, status, extensions=None):
    """A console-facing server over the REAL node code, with the maintenance flag left alone."""
    return stub_startup_path(console_target(
        build_server(mission_file, pool=pool, status=status, extensions=extensions), status=status))


def presets_at(tmp_path):
    presets = tmp_path / "presets.yaml"
    presets.write_text(PRESET_FILE, encoding="utf-8")
    return {"MizEdit": build_mizedit(presets)}


# ------------------------------- 1. a pick with the extensions UNTICKED is never recorded

@pytest.mark.asyncio
async def test_a_startup_pick_without_extensions_is_refused_and_not_recorded(tmp_path, mission):
    """RED on the unfixed tree: the picked NAME lands on the row though no extension ran."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.SHUTDOWN,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.startup_server(ctx_for(srv), SERVER, modify_mission=False,
                                                  preset="Fresh", maintenance=False)

    assert record_writes(pool) == [], record_writes(pool)
    assert label_of(pool.row) is not None, "the row still describes the load BEFORE this request"
    assert pool.row.preset == "Summer"
    assert result.success is False, "the contradictory request is refused, not silently honoured"
    assert result.message == mission_actions.WEATHER_NEEDS_EXTENSIONS
    assert srv.status == Status.SHUTDOWN, "a refused request starts nothing"


@pytest.mark.asyncio
async def test_a_startup_temporary_document_without_extensions_is_refused_and_not_recorded(
        tmp_path, mission):
    """The DOCUMENT shape of the same hole: ``(None, True)`` would render the temporary wording."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.SHUTDOWN,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.startup_server(ctx_for(srv), SERVER, modify_mission=False,
                                                  preset=TEMP_DOCUMENT, maintenance=False)

    assert record_writes(pool) == [], record_writes(pool)
    assert pool.row.preset == "Summer" and pool.row.temporary is False
    assert result.success is False
    assert result.message == mission_actions.WEATHER_NEEDS_EXTENSIONS


@pytest.mark.asyncio
async def test_a_load_pick_without_extensions_is_refused_and_not_recorded(tmp_path, mission):
    """The Missions tab's Load sends the same pick; with the extensions off it must not record either."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.RUNNING,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.load_mission(ctx_for(srv), SERVER, 0, modify_mission=False,
                                                use_orig=False, preset="Fresh")

    assert record_writes(pool) == [], record_writes(pool)
    assert pool.row.preset == "Summer"
    assert result.success is False
    assert result.message == mission_actions.WEATHER_NEEDS_EXTENSIONS


# --------------------------------- 2. positive controls: a launch that DOES apply still records

@pytest.mark.asyncio
async def test_a_load_pick_with_extensions_records_the_name(tmp_path, mission, no_write):
    """The refusal must not swallow the case it exists to protect."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.RUNNING,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.load_mission(ctx_for(srv), SERVER, 0, modify_mission=True,
                                                use_orig=False, preset="Fresh")

    assert result.success is True
    assert record_writes(pool)[-1][1] == ("Fresh", False, SERVER)
    assert label_of(pool.row) is not None and pool.row.preset == "Fresh"
    assert "weather preset 'Fresh' requested" in result.message


@pytest.mark.asyncio
async def test_a_load_temporary_document_with_extensions_records_it(tmp_path, mission, no_write):
    """The other shape of the same positive control."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.RUNNING,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.load_mission(ctx_for(srv), SERVER, 0, modify_mission=True,
                                                use_orig=False, preset=TEMP_DOCUMENT)

    assert result.success is True
    assert record_writes(pool)[-1][1] == (None, True, SERVER)
    assert pool.row.preset is None and pool.row.temporary is True
    assert "temporary weather preset" in result.message


@pytest.mark.asyncio
async def test_a_startup_pick_with_extensions_records_the_name(tmp_path, mission, no_write):
    """The Startup dialog's own pick, with its box ticked — the pre-existing behaviour, unchanged."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.SHUTDOWN,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.startup_server(ctx_for(srv), SERVER, modify_mission=True,
                                                  preset="Fresh", maintenance=False)

    assert result.success is True
    assert record_writes(pool)[-1][1] == ("Fresh", False, SERVER)
    assert srv.status != Status.SHUTDOWN, "the launch itself ran"
    assert "weather preset 'Fresh' requested" in result.message


@pytest.mark.asyncio
async def test_a_startup_without_a_pick_still_runs_and_clears(tmp_path, mission):
    """No pick, extensions off: the launch runs and the record is cleared, exactly as before."""
    pool = RecordingPool(RowState("Summer", False))
    srv = build_launch(mission, pool=pool, status=Status.SHUTDOWN,
                       extensions=presets_at(tmp_path))

    result = await mission_actions.startup_server(ctx_for(srv), SERVER, modify_mission=False,
                                                  preset="", maintenance=False)

    assert result.success is True
    assert record_writes(pool)[-1][1] == (None, False, SERVER)
    assert label_of(pool.row) is None
    assert srv.status != Status.SHUTDOWN


# --------------------------- 3. the node's meaning of ``preset`` off the console is unchanged

@pytest.mark.asyncio
async def test_the_scheduler_launch_shape_still_records_what_it_applied(mission):
    """``ServerImpl.startup(modify_mission=False, preset=...)`` is the scheduler's statement.

    The scheduler applies the configured presets to the mission file itself and then states their
    names here; the node must keep recording them, so the page shows the weather the launch really
    applied rather than the previous one's.
    """
    pool = RecordingPool(RowState())
    srv = stub_startup_path(build_server(mission, pool=pool, status=Status.SHUTDOWN))

    await ServerImpl.startup(srv, modify_mission=False, use_orig=False, preset=["Configured", "Pair"])

    assert record_writes(pool)[-1][1] == ("Configured, Pair", False, SERVER)


@pytest.mark.asyncio
async def test_a_bare_launch_with_no_preset_clears_the_record(mission):
    """The other half of the node's meaning: stating no preset clears, it does not preserve."""
    pool = RecordingPool(RowState("Summer", False))
    srv = stub_startup_path(build_server(mission, pool=pool, status=Status.SHUTDOWN))

    await ServerImpl.startup(srv, modify_mission=False, use_orig=False)

    assert record_writes(pool)[-1][1] == (None, False, SERVER)
    assert label_of(pool.row) is None


# ------------------------------------------------- 4. the console's own signature is the contract

def test_the_console_launch_parameters_are_unchanged():
    """The dialog posts these names, and ``modify_mission`` still defaults ON: no route changed."""
    for qualname in ("startup_server", "load_mission"):
        params = inspect.signature(getattr(mission_actions, qualname)).parameters
        assert params["modify_mission"].default is True, qualname
        assert params["preset"].default == "", qualname
        assert params["announce"].default is False, qualname


# ------------------------------------- 5. the route hands the refusal to the page, not a launch

import gettext  # noqa: E402
from pathlib import Path  # noqa: E402

from services.webservice import i18n  # noqa: E402
from services.webservice.pages import actions as actions_page  # noqa: E402
from test_webui_missions_load_form import (  # noqa: E402 - harness doubles, imported late
    LOAD, SERVER_A, action_by_key, dialog_hidden, load_fleet, open_load_dialog,
    post_dialog,
)
from test_webui_missions_tab import missions_page  # noqa: E402
from test_webui_startup_weather_picker import (  # noqa: E402 - harness doubles, imported late
    PRESET_FIELD, STARTUP, PresetNode, node_fleet, open_dialog,
)
from test_webui_write_pause import (  # noqa: E402 - fixtures imported on purpose
    build, clean_registry, client, config_dir, open_page, recording_bot, sign_in, token_of,
)


def test_the_route_refusal_states_the_same_condition_as_the_action():
    """The route's wrapped refusal and the action's backstop are ONE sentence and ONE predicate.

    The route must not drift from the action: the operator reads the same wording whichever surface
    refuses, and the CONDITION is the plugin's own predicate (``states_unapplied_weather``), which the
    route reaches rather than re-stating the boolean. A launch with a pick and the extensions ON is
    never refused; the same pick with the extensions OFF is.
    """
    assert actions_page.REFUSAL_WEATHER_NEEDS_EXTENSIONS == mission_actions.WEATHER_NEEDS_EXTENSIONS, \
        "the route's refusal and the action's backstop drifted into two sentences"
    # the route's availability gate is honest: register the plugin the way the console's own install
    # does, so the helper reaches the plugin's predicate (it refuses to import an unloaded plugin)
    from core.actions import discover_actions
    discover_actions(["mission"])
    for action in (action_by_key("startup"), action_by_key("mission_load")):
        assert actions_page.launch_weather_refusal(
            action, {actions_page.MODIFY_MISSION_FIELD: True,
                     actions_page.PRESET_FIELD: "Fresh"}) is None, action.key
        assert actions_page.launch_weather_refusal(
            action, {actions_page.MODIFY_MISSION_FIELD: False,
                     actions_page.PRESET_FIELD: "Fresh"}) == \
            actions_page.REFUSAL_WEATHER_NEEDS_EXTENSIONS, action.key


def test_the_route_refusal_is_translated_in_every_shipped_catalog():
    """The runtime reads the ``.mo``: the operator reads this refusal in their language, not English.

    Checked the way the runtime reads it — ``gettext.translation(...).gettext(msgid)`` must differ from
    the msgid — never by reading the ``.po``. RED before the fix (the sentence had no catalog entry, so
    gettext fell back to the msgid in all four), GREEN after.
    """
    repo = Path(__file__).resolve().parent.parent
    msgid = actions_page.REFUSAL_WEATHER_NEEDS_EXTENSIONS
    for language in ("cn", "de", "es", "ru"):
        translations = gettext.translation("webui", localedir=str(repo / "locale"),
                                           languages=[language], fallback=True)
        translated = translations.gettext(msgid)
        assert translated != msgid, \
            f"the refusal falls back to the English msgid in {language}"


@pytest.mark.asyncio
async def test_the_startup_route_refuses_a_pick_with_the_extensions_unticked(
        config_dir, clean_registry, recording_bot):
    """The dialog's own POST: unticked extensions + a pick is refused by the ROUTE, wrapped.

    Clearing a box sends the declaration's hidden OFF companion (``"0"``), so this body is exactly
    what a browser posts for "pick a preset, then untick Run extensions on the mission". The route
    raises the console's own sentence (translated for the request) before any action runs — the
    action's backstop is never reached on this path.
    """
    (config_dir / "presets.yaml").write_text(PRESET_FILE, encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(dialog.text),
                                               "modify_mission": "0",
                                               PRESET_FIELD: "Fresh"})
        assert reply.status_code == 400, reply.text
        assert actions_page.REFUSAL_WEATHER_NEEDS_EXTENSIONS in reply.json()["detail"]
        # the refusal is stored for the page the person lands on, in the request's language
        http.cookies.set(i18n.COOKIE_NAME, "de")
        page = await open_page(http)

    assert cluster.servers[0].call_args == [], "a launch that applies nothing must not receive a pick"
    assert recording_bot.calls == [], "the route refused before any action ran"
    assert "ein Wetter-Preset wurde gewählt" in page, \
        "the refusal is not rendered in the request's language"


@pytest.mark.asyncio
async def test_the_load_route_refuses_a_temporary_document_with_the_extensions_unticked(
        config_dir, clean_registry, recording_bot):
    """The Missions tab's Load: its temporary-DOCUMENT shape is refused with the extensions OFF.

    The dialog's own POST carries the extensions box off (the OFF companion) and a filled weather row,
    so the load states a DOCUMENT its extensions would not apply. The route refuses it wrapped, exactly
    as the Startup route refuses a pick, and nothing loads or restarts.
    """
    cluster = load_fleet()
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)
        key = actions_page.TEMPORARY_KEYS[0]
        # the dialogs no longer produce this pair: the POST is a caller's, so it states no mode
        body = {"server": SERVER_A, **dialog_hidden(dialog.text), key: "777",
                "modify_mission": "0"}
        body.pop("weather_mode", None)
        reply = await http.post(LOAD, data=body)
        after = await missions_page(http)
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].loads == [] and cluster.servers[0].restarts == [], \
        "a refused load must neither load nor restart"
    assert recording_bot.calls == [], "the route refused before any action ran"
    # the page renders the sentence with its quotes HTML-escaped, so match a fragment without them
    assert "a weather preset was picked and the extensions are off" in after, after[:400]

