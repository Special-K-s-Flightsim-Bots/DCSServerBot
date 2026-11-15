"""The Launch glyph on a row that is BOOTING — whichever door started the server.

A launch records an expectation only when THIS console issued it (``awaiting_change``), so a server
started from Discord, the scheduler or a node power operation had nothing to pulse: ``LOADING`` matches
no control's ``statuses`` and the row rendered an empty strip for the whole boot, which the operator
reads as "it does not notice that the server is starting". The pulse is read from the ROW'S OWN STATE
instead, so every door produces the same glyph.

Pins: a booting row draws the Launch control busy + disabled on both screens; the pulse owes nothing to
a recorded press; a server at rest carries none; a press from this console draws exactly ONE glyph (no
second copy); and a viewer without the launch capability gets no glyph at all.
"""
from __future__ import annotations

import re
import types

import pytest

from core import Status
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, server, sign_in,
)

#: the Launch control has a DIALOG (its maintenance box), so the row's form posts to ``/confirm``
LAUNCH_FORM = "/actions/server/startup/confirm"
DASHBOARD = "/"


def booting(status=Status.LOADING):
    """One server on the master node, in *status* — the only row the scope question needs."""
    srv = server("SRS-1", ["Host A"], status=status)
    node = types.SimpleNamespace(name="dcs-a-01", master=True,
                                 instances={"Host-A-Instance": instance("Host-A-Instance", srv)})
    return Cluster([srv], nodes={"dcs-a-01": node}, node=node,
                   instances=[instance("Host-A-Instance", srv)])


def form_markup(html: str, path: str) -> list[str]:
    """Every form on the page that posts to *path* — one per rendered control."""
    return re.findall(r'<form method="post" action="' + re.escape(path) + r'"[^>]*>.*?</form>',
                      html, flags=re.S)


def button_of(markup: str) -> str:
    """The ``<button>`` inside one rendered control's form."""
    match = re.search(r"<button[^>]*>", markup)
    assert match, f"the control rendered no button: {markup[:120]}"
    return match.group(0)


def pulses(markup: str) -> bool:
    """Whether one control wears the console's busy idiom (the accessible statement + the class)."""
    button = button_of(markup)
    return 'aria-busy="true"' in button and re.search(r'class="[^"]*\bbusy\b', button) is not None


# ------------------------------------------------- 1. a booting row shows the launch glyph

@pytest.mark.asyncio
async def test_a_booting_row_pulses_the_launch_glyph_on_both_screens(config_dir, clean_registry):
    """The dashboard's Servers tab and ``/fleet`` render ONE strip: both pulse it."""
    app = build(config_dir, booting(Status.LOADING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        screens = [await open_page(http, DASHBOARD), await open_page(http, servers_page.SERVERS_PATH)]
    for html in screens:
        forms = form_markup(html, LAUNCH_FORM)
        assert forms, "the booting row rendered no Launch control at all"
        assert len(forms) == 1, f"one row, one glyph: {len(forms)}"
        assert pulses(forms[0]), "the Launch glyph is not pulsing while the server boots"
        assert "disabled" in button_of(forms[0]), \
            "a booting row's Launch glyph is a statement, not an invitation to press it again"


@pytest.mark.asyncio
async def test_the_boot_pulse_owes_nothing_to_a_recorded_press(config_dir, clean_registry):
    """No write was issued from this console — the glyph comes from the ROW's state alone.

    This is the reported case: the server was started from Discord, so the store is empty and the
    pulse can only come from the status the row reads.
    """
    assert actions_page.awaiting_store() == {}, "the pin needs an empty expectation store"
    app = build(config_dir, booting(Status.LOADING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, DASHBOARD)
    assert actions_page.awaiting_store() == {}, "the render recorded an expectation of its own"
    forms = form_markup(html, LAUNCH_FORM)
    assert forms and pulses(forms[0]), "the external launch did not pulse the row"


# ------------------------------------------------- 2. the control: a server at rest is quiet

@pytest.mark.asyncio
async def test_a_server_at_rest_pulses_no_glyph(config_dir, clean_registry):
    """RUNNING offers Pause/Stop and the strip carries no busy signal — the pulse is not always-on."""
    app = build(config_dir, booting(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, DASHBOARD)
    assert not re.search(r'class="act-btn[^"]*\bbusy\b', html), "a row at rest wore the pulse"
    assert 'aria-busy="true"' not in html
    assert not form_markup(html, LAUNCH_FORM), "a RUNNING row is not booting"


# ------------------------------------------------- 3. one press, one glyph

@pytest.mark.asyncio
async def test_a_press_from_this_console_does_not_draw_a_second_glyph(config_dir, clean_registry):
    """The pending control and the boot glyph are the SAME control — never two on one row."""
    action = next(candidate for candidate in actions_page.server_row_actions()
                  if candidate.key == actions_page.BOOT_CONTROL_KEY)
    actions_page.remember_awaiting_change("server", "SRS-1", action, "SHUTDOWN", False)
    app = build(config_dir, booting(Status.LOADING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, DASHBOARD)
    forms = form_markup(html, LAUNCH_FORM)
    assert len(forms) == 1, f"the row drew {len(forms)} Launch controls"
    assert pulses(forms[0])


# ------------------------------------------------- 4. scope and availability still decide

@pytest.mark.asyncio
async def test_a_manager_outside_the_scope_never_sees_the_pulse(config_dir, clean_registry):
    """The glyph is a property of a row the viewer ALREADY has — no row, no pulse."""
    app = build(config_dir, booting(Status.LOADING))
    async with client(app) as http:
        await sign_in(http, "hoster-b")        # manages Host B; SRS-1 is hosted by Host A
        html = await open_page(http, servers_page.SERVERS_PATH)
    assert "SRS-1" not in html, "the out-of-scope row was rendered"
    assert not form_markup(html, LAUNCH_FORM)


@pytest.mark.asyncio
async def test_an_install_without_the_launch_action_shows_no_glyph(config_dir, clean_registry):
    """The control is only drawn when its action exists — the boot pulse cannot invent one."""
    app = build(config_dir, booting(Status.LOADING), plugins=())
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http, DASHBOARD)
    assert not form_markup(html, LAUNCH_FORM), "a missing action was rendered as a control"
