"""The row says a target is RUNNING, from the action seam's own in-flight guard.

The marker is read SERVER-SIDE at render time (no second flag, no client timer, no click handler),
so a reload and a second viewer see the same thing and it clears when the guard clears. The guard
keys the target and cannot name the action, so the fact belongs to the ROW (a ``busy`` class and
``data-busy="true"`` on the strip container, static, never a pulsing glyph). The probe is put in
flight under a padded, odd-cased name to pin the key normalisation (``core.actions._normalise_target``)."""
from __future__ import annotations

import asyncio
import contextlib
import re
import types
from pathlib import Path

import pytest

from core.action_results import ActionResult
from core.actions import ActionContext, call_action, action, reset_in_flight
from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    BOT_YAML, PASSWORD, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, sign_in, token_of,
)

#: the two signals, asserted separately so a regression names which one broke: the CONTROL's pulse
#: (the actor's own expectation) and the ROW marker (the seam's in-flight half).
BUSY_ATTRIBUTE = 'aria-busy="true"'
BUSY_CLASS = "busy"
ROW_MARKER = 'data-busy="true"'


def running_fleet():
    """A (hosted by ``Host A``) and B (hosted by ``Host B``), both RUNNING — the scope's two sides."""
    a = _server("SRS-1", ["Host A"])
    b = _server("SRS-B", ["Host B"], node_name="dcs-b-01", instance_name="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


def _server(name, managed_by=None, *, status=Status.RUNNING, node_name="dcs-a-01",
            instance_name="Host-A-Instance"):
    """A server double shaped like the pause harness's, with the status the state table needs.

    RUNNING is the interesting case: it is the ONLY state whose strip carries more than one control
    (Pause in the strip, Stop in the overflow menu).
    """
    mission = types.SimpleNamespace(pause=None, unpause=None)
    srv = types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=instance_name), port=10309,
        current_mission=mission, players={}, is_remote=False,
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )
    return srv


@contextlib.asynccontextmanager
async def in_flight(server_name: str):
    """Put a target IN FLIGHT through the REAL seam, and hold it there for the block.

    The guard is process state and forbidden a second copy, so the honest way is the production one:
    register an action, call it with :func:`core.actions.call_action` and block inside it.
    ``call_action`` adds the normalised key BEFORE it awaits the action, so the moment the action signals
    ``started`` the set is exactly "an action is running on this target right now".
    """
    started = asyncio.Event()
    release = asyncio.Event()

    @action
    async def _hold(ctx, server_name: str):  # noqa: ARG001 - the target is the whole point
        started.set()
        await release.wait()
 # ``data["audit"]`` silences the seam's "this write has no trail" warning: the probe is not
 # an operation, and the warning is not what this file is measuring.
        return ActionResult(success=True, message="held", data={"audit": "recorded"})

    task = asyncio.create_task(
        call_action(_hold.__qualname__,
                    ActionContext(node=types.SimpleNamespace(name="dcs-a-01"),
                                  bus=types.SimpleNamespace(servers={})),
                    server_name=server_name))
    try:
        await asyncio.wait_for(started.wait(), 5)
        yield
    finally:
        release.set()
        await asyncio.wait_for(task, 5)


# 1. the mechanism

@pytest.mark.asyncio
async def test_no_control_is_busy_at_rest(config_dir, clean_registry):
    """The control: with nothing in flight the page carries NO busy signal of any kind.

    Without this half, "the row marks itself when a target is in flight" could pass over a template
    that renders the marker unconditionally — a signal that is always on says nothing.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)
    assert BUSY_ATTRIBUTE not in html
    assert ROW_MARKER not in html
    assert not re.search(r'class="act-btn[^\"]*\bbusy\b', html)
    assert not re.search(r'class="mi[^\"]*\bbusy\b', html)
    assert "is running; this can take a moment" not in html


@pytest.mark.asyncio
async def test_an_in_flight_target_marks_the_row_and_lights_no_glyph(config_dir, clean_registry):
    """A target in flight marks the ROW, not a control.

    The seam keys the TARGET and cannot say which action runs, so the fact is rendered where it is honest
 -- the strip container -- and NO control claims it. Read from the seam's own set at render time, so
    nothing here clicks anything, and it is already gone the instant the guard clears.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")

        rest = await open_page(http)
        assert ROW_MARKER not in rest, "the pre-flight render must be clean"

        async with in_flight("SRS-1"):
            flying = await open_page(http)
            assert ROW_MARKER in flying, "a running action must mark its row"
            assert re.search(r'class="act-strip[^\"]*\bbusy\b', flying), \
                "the marker is the strip container's class"
            assert BUSY_ATTRIBUTE not in flying, \
                "no control claims it: the seam cannot name the action that is running"
            assert not re.search(r'class="act-btn[^\"]*\bbusy\b', flying)
            assert not re.search(r'class="mi[^\"]*\bbusy\b', flying)
            assert "is running; this can take a moment" not in flying

        ended = await open_page(http)
        assert ROW_MARKER not in ended, "the guard cleared, so the marker must be gone"
    assert reset_in_flight() is None


# ------------------------------------------------------------------- 2. the key is the guard's

@pytest.mark.asyncio
async def test_the_pulse_keys_off_the_guards_own_normalisation(config_dir, clean_registry):
    """A padded, odd-cased probe target lights the CORRECTLY-named row.

    ``call_action`` normalises the target once (strip + case-fold) and keys the guard with it; this
    proves ``pages.actions.target_key`` reproduces that key rather than inventing its own, so the
    marker and the refusal can never disagree (: "do not add a second flag").
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        async with in_flight("  srs-1  "):
            html = await open_page(http)
    assert ROW_MARKER in html, "the guard folded '  srs-1  ' onto the SRS-1 row's key"


def test_the_key_shape_is_pinned_to_the_guard():
    """The key itself, named — a fast pin on the shape the live test above exercises end to end."""
    assert actions_page.target_key("server", "SRS-1") == "server:srs-1"
    assert actions_page.target_key("server", "  SRS-1  ") == "server:srs-1"
    assert actions_page.target_key("node", "DCS-A-01") == "node:dcs-a-01"
    assert actions_page.target_key("server", "") == "server:"
    assert actions_page.target_key("server", None) == "server:"


# ----------------------------------------------------------------------- 3. the scope rule

@pytest.mark.asyncio
async def test_a_viewer_cannot_learn_that_an_out_of_scope_server_is_busy(config_dir, clean_registry):
    """A manager of ``Host B`` sees NOTHING about ``SRS-1`` being mid-action.

    The marker is a property of a row the viewer ALREADY has — the scoped source decides which rows
    exist — so there is no rendered output that can disclose a server outside the scope, busy or not.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "hoster-b")                 # manages Host B only
        async with in_flight("SRS-1"):                  # somebody else's server is mid-action
            page = await open_page(http)
        assert ROW_MARKER not in page and "busy" not in page
        assert "SRS-1" not in page, "the out-of-scope row is not rendered at all"
        assert 'value="SRS-B"' in page, "their own row is still there"

 # the same fact, from the in-scope side: their OWN busy server does show it
        async with in_flight("SRS-B"):
            own = await open_page(http)
        assert ROW_MARKER in own
        assert "SRS-1" not in own


# -------------------------------------------------------------------- 4. one strip, two screens

@pytest.mark.asyncio
async def test_both_screens_render_the_pulse(config_dir, clean_registry):
    """The dashboard's Servers tab and ``/fleet`` are ONE component — both mark, from one read."""
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        async with in_flight("SRS-1"):
            dashboard = await open_page(http, "/")
            servers = await open_page(http, servers_page.SERVERS_PATH)
    assert ROW_MARKER in dashboard and ROW_MARKER in servers


@pytest.mark.asyncio
async def test_the_busy_control_is_not_disabled_or_hidden(config_dir, clean_registry):
    """The signal is informational: the control stays a real, focusable button.

    The guard is the authority that refuses a second press (with its own typed refusal), so the
    console must not disable the button and invent a second, silent refusal path.
    """
    app = build(config_dir, running_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        async with in_flight("SRS-1"):
            html = await open_page(http)
    for button in re.findall(r"<button class=\"act-btn[^\"]*\"[^>]*>", html):
        assert "disabled" not in button, button
    for button in re.findall(r"<button class=\"mi[^\"]*\"[^>]*>", html):
        assert "disabled" not in button, button


# ------------------------------------------------------------------------- 5. the stylesheet

def test_the_stylesheet_animates_the_class_and_opts_out_of_motion():
    """The animation belongs to the pressed CONTROL (the ``busy`` class the expectation renders).

    The ROW marker is deliberately STATIC -- it is a marker, not a pulse, so it carries no animation and
    needs no opt-out. Both are pinned so a future change cannot give the row a pulse nobody asked for.
    """
    css = Path(actions_page.__file__).parent.parent.joinpath("static", "shell.css").read_text(
        encoding="utf-8")
    keyframes = re.findall(r"@keyframes\s+([\w-]+)", css)
    # TWO animations: the pressed control's pulse and the music player's loading dot. Each one ships
    # with its own reduced-motion opt-out, right below its rule.
    assert keyframes == ["act-busy", "dot-spin"], f"the console's animations: {keyframes}"
    assert re.search(r"\.act-btn\.busy[^{]*\{[^}]*animation:\s*act-busy", css), \
        "the busy class must run the keyframes"
    assert re.search(r"\.mi\.busy[^{]*\{[^}]*animation:\s*act-busy", css), \
        "the menu item is the same control and must animate too"
 # the opt-out: NO animation for a reader who asked for less motion, and the selectors are the
 # same ones, so the opt-out cannot miss a rule that animates.
    opt_out = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}",
                        css, re.S)
    assert opt_out, "this is the console's first animation — the opt-out ships with it"
    assert ".act-btn.busy" in opt_out.group(1) and ".mi.busy" in opt_out.group(1)
    assert "animation: none" in opt_out.group(1)
    # the loading dot is the second animation and carries the same opt-out, on its own selector
    assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.dot\.spin\s*"
                     r"\{\s*animation:\s*none", css), "the loading dot must opt out of motion"
 # THE ROW MARKER IS STATIC: the row carries a marker, never a pulse.
    marker = re.search(r"td\.acts \.act-strip\.busy[^{]*\{([^}]*)\}", css)
    assert marker, "the row marker needs its own rule"
    assert "animation" not in marker.group(1), "the ROW marker must not pulse"


def test_the_accessible_signal_is_markup_not_css():
    """``aria-busy`` and the wording are on the BUTTON, so they survive with CSS (or motion) off.

    The pulse is decoration; the statement a screen reader reads is the attribute, and the statement
    a reduced-motion reader reads is the title/aria — neither is defined in the stylesheet.
    """
    source = Path(actions_page.__file__).parent.parent.joinpath("templates",
                                                                "_strip.html").read_text(
        encoding="utf-8")
    assert "aria-busy" in source, "the strip template must carry the accessible signal"
