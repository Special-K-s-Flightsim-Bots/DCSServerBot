"""The server row icon strip: startup / start / stop / restart / shutdown, three confirm dialogs.

One section per acceptance criterion: the DISCOVERY WIRING (``startup_server`` / ``shutdown_server``
must be reachable through the web shell's own install, or every control is silently omitted while the
suite stays green); the STATE TABLE (which status offers which control, on BOTH screens, rendered from
ONE implementation); the ``${status} -> control`` pairs being ABSENT rather than disabled, asserted on
the RENDERED markup; the icons (one glyph per control, no visible label, a native ``title`` AND an
``aria-label`` naming the target); the CAPABILITY and the SCOPE (a ``DCS`` member is offered nothing, a
manager only their own servers, and a crafted POST for a foreign server is refused indistinguishably
from a nonexistent one); the CONFIRM STEP (a POST that skipped its dialog is REFUSED; the dialog's
wording is asserted so it cannot drift, and its numbers come from the resolved server); CSRF; and the
AUDIT. Driven through the real app and a stub cluster; harness reused from ``test_webui_write_pause.py``."""
from __future__ import annotations

import os

import re
from urllib.parse import urljoin
import types
from pathlib import Path

import pytest

from conftest import asgi_client
import core.actions as actions_module
from core import Status
from core.actions import action_available
from services.webservice.pages import actions as actions_page
from services.webservice.pages import servers as servers_page
from test_webui_write_pause import (  # noqa: F401 - fixtures are imported on purpose
    BOT_YAML, PASSWORD, Cluster, build, clean_registry, client, config_dir, instance, open_page,
    recording_bot, sign_in, token_of,
)

STARTUP = "/actions/server/startup"
SHUTDOWN = "/actions/server/shutdown"
START = "/actions/server/start"
STOP = "/actions/server/stop"
RESTART = "/actions/server/restart"
PAUSE = "/actions/server/pause"
UNPAUSE = "/actions/server/unpause"
#: the SERVER message pair — the two controls that joined the RUNNING/PAUSED rows, built from the
#: constants so a rename cannot leave a test pointing at a route that no longer exists.
CHAT = actions_page.SERVER_CHAT_PATH
POPUP = actions_page.SERVER_POPUP_PATH

#: the dialog paths, built from the constant so a rename cannot leave a test pointing at a route
#: that no longer exists.
CONFIRM = actions_page.CONFIRM_SUFFIX


class FakeMission:
    """The mission double: pause/unpause are awaitable and flip the server's own status."""

    def __init__(self, server):
        self.server = server

    async def pause(self):
        self.server.status = Status.PAUSED

    async def unpause(self):
        self.server.status = Status.RUNNING


class FakeServer:
    """The ``Server`` double: every domain method the strip's actions call, counting its calls.

    ``startup`` / ``start`` flip the status to RUNNING and ``shutdown`` / ``stop`` to STOPPED, exactly
    like the real process would after the DCS broadcast: a double that left the status alone could not
    tell "the page reflects the new state" from "the page never changed".
    """

    def __init__(self, name, status=Status.RUNNING, managed_by=None, players=0):
        self.name = name
        self.display_name = name
        self.status = status
        self.node = types.SimpleNamespace(name="dcs-a-01")
        self.instance = types.SimpleNamespace(name="Host-A-Instance")
        self.port = 10309
        self.locals = {"managed_by": list(managed_by)} if managed_by else {}
        self.players = {f"ucid{i}": object() for i in range(players)}
        self.calls: list[str] = []
        #: WHAT each call was made with — the console's dialogs carry options now (Startup's
        #: ``modify_mission``/``use_orig``), and "the action ran" cannot tell a posted default from a
        #: posted choice. Kept beside ``calls`` so a test can assert both.
        self.call_args: list[dict] = []
        self.current_mission = FakeMission(self)

    def get_active_players(self):
        return list(self.players.values())

    #: THE SERVER'S OWN ROTATION — the four reads the mission-list action makes, so a dialog opened on
    #: this double renders the mission pick and the weather it qualifies. Two missions, the first the
    #: configured start one: enough to tell a pick from a single-entry rotation and a default from a
    #: choice.
    #: host-spelled: the dialog joins and normalises it, so a raw POSIX root never matches
    missions_dir = os.path.abspath("/srv/missions")

    async def get_missions_dir(self):
        return self.missions_dir

    async def getMissionList(self):
        return [os.path.join(self.missions_dir, "Training.miz"),
                os.path.join(self.missions_dir, "Rotated.miz")]

    async def getAllMissionFiles(self):
        return [(f"{name}", f"{self.missions_dir}/{name}")
                for name in ("Training.miz", "Rotated.miz")]

    async def get_current_mission_file(self):
        return f"{self.missions_dir}/Training.miz"

    async def getStartIndex(self):
        return 1

    async def setStartIndex(self, mission_id):
        self.calls.append("setStartIndex")
        self.call_args.append({"mission": mission_id})

    async def startup(self, **kwargs):
        self.calls.append("startup")
        self.call_args.append(dict(kwargs))
        self.status = Status.RUNNING

    async def shutdown(self, **kwargs):
        self.calls.append("shutdown")
        self.call_args.append(dict(kwargs))
        self.status = Status.STOPPED

    async def start(self, **kwargs):
        self.calls.append("start")
        self.call_args.append(dict(kwargs))
        self.status = Status.RUNNING

    async def stop(self, **kwargs):
        self.calls.append("stop")
        self.call_args.append(dict(kwargs))
        self.status = Status.STOPPED


def fleet(status=Status.RUNNING, players=0):
    """A (hosted by ``Host A``) and B (hosted by ``Host B``) — the scope's two sides."""
    a = FakeServer("SRS-1", status=status, managed_by=["Host A"], players=players)
    b = FakeServer("SRS-B", status=status, managed_by=["Host B"], players=players)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", a), instance("Host-B-Instance", b)])


async def screens(http, dashboard: str = "/"):
    """Both screens, rendered for the signed-in identity."""
    return {"servers": await open_page(http, servers_page.SERVERS_PATH),
            "dashboard": await open_page(http, dashboard)}


def offered(html: str) -> set[str]:
    """The control paths the page actually RENDERS — the strip and the overflow menu."""
    return set(re.findall(r'action="(/actions/server/[^"]+)"', html))


# ------------------------------------------------------------------ 1. the discovery wiring

@pytest.mark.asyncio
async def test_the_web_shell_discovers_the_server_actions_in_its_own_process(config_dir,
                                                                             clean_registry):
    """``action_available`` for every action the strip wires, through the shell's own install.

    The actions are ``plugins/mission/actions.py``'s: without the shell's discovery they are not in the
    registry and this file's other assertions would pass over a strip that renders nothing.
    """
    for qualname in ("startup_server", "shutdown_server", "start_server", "stop_server",
                     "restart_server", "pause_mission", "unpause_mission"):
        assert action_available(qualname) is False, "the registry must start EMPTY for this test"
    build(config_dir, fleet())
    for qualname in ("startup_server", "shutdown_server", "start_server", "stop_server",
                     "restart_server"):
        assert action_available(qualname) is True, qualname


# --------------------------------------------------------------------------- 2. the state table

@pytest.mark.asyncio
@pytest.mark.parametrize("status, shown, hidden", [
    (Status.RUNNING, {PAUSE, RESTART + CONFIRM, SHUTDOWN + CONFIRM, STOP + CONFIRM,
                      CHAT + CONFIRM, POPUP + CONFIRM},
     {UNPAUSE, START, STARTUP + CONFIRM}),
    (Status.PAUSED, {UNPAUSE, RESTART + CONFIRM, SHUTDOWN + CONFIRM, STOP + CONFIRM,
                     CHAT + CONFIRM, POPUP + CONFIRM},
     {PAUSE, START, STARTUP + CONFIRM}),
    (Status.STOPPED, {START}, {PAUSE, UNPAUSE, STARTUP + CONFIRM, RESTART, SHUTDOWN, STOP, CHAT,
                              POPUP}),
    (Status.SHUTDOWN, {STARTUP + CONFIRM}, {PAUSE, UNPAUSE, START, STOP, RESTART, SHUTDOWN, CHAT,
                                            POPUP}),
    (Status.LOADING, {STARTUP + CONFIRM}, {PAUSE, UNPAUSE, START, STOP, RESTART, SHUTDOWN,
                                           CHAT, POPUP}),
    (Status.SHUTTING_DOWN, set(), {PAUSE, UNPAUSE, START, STARTUP + CONFIRM, STOP, RESTART,
                                   SHUTDOWN, CHAT, POPUP}),
], ids=["running", "paused", "stopped", "shutdown", "loading", "shutting-down"])
async def test_the_state_decides_the_controls_on_both_screens(config_dir, clean_registry, status,
                                                              shown, hidden):
    """One state, one set of controls -- rendered on the dashboard AND on /fleet.

    The two screens are two renderings of ONE component, so a control the state table gives must appear
    on both and one that may not act on neither. ``SHUTTING_DOWN`` is the state with NO control at all.
    ``LOADING`` carries ONE glyph, and it is NOT an offer: the launch control, busy and DISABLED, is the
    boot's own marker — a server can be started from Discord or the scheduler, so the row states that it
    is coming up with no press behind it. THIS TABLE IS THE PROCESS-AND-MISSION SET for the install these tests build
    (``build()`` loads the ``mission`` plugin only); the MAINTENANCE flag pair is not discovered and is
    correctly omitted -- its own state table is in ``tests/test_webui_server_maintenance.py``. STARTUP
    POSTS TO ITS DIALOG: the maintenance option rides on Shutdown/Startup, so the SHUTDOWN row's control
    posts to ``/actions/server/startup/confirm``; both are ABSENT from every other status. THE SERVER
    MESSAGE PAIR (Chat/Popup) joins the RUNNING and PAUSED rows alone: it acts on a server whose
    process can receive the message, and it confirms through its own dialog like the player pair.
    """
    app = build(config_dir, fleet(status))
    async with client(app) as http:
        await sign_in(http, "mod1")
        pages = await screens(http)
    for where, html in pages.items():
        rendered = offered(html)
        assert rendered == shown, f"{where} at {status.value}: {sorted(rendered)}"
        assert "act-strip" not in html or shown
        if status is Status.LOADING:
            # the ONE glyph a booting row carries is a STATEMENT: busy, and not pressable
            form = re.search(r'<form[^>]*action="' + re.escape(STARTUP + CONFIRM) + r'".*?</form>',
                             html, re.S)
            assert form, f"{where}: the booting row drew no launch glyph"
            assert 'aria-busy="true"' in form.group(0), f"{where}: the boot glyph is not pulsing"
            assert "disabled" in form.group(0), f"{where}: the boot glyph invites a second press"
        if status is Status.SHUTDOWN:
            # the SAME control on a SHUTDOWN row IS the offer — nothing disabled about it
            form = re.search(r'<form[^>]*action="' + re.escape(STARTUP + CONFIRM) + r'".*?</form>',
                             html, re.S)
            assert form and "disabled" not in form.group(0), f"{where}: the offer was drawn inert"


@pytest.mark.asyncio
async def test_a_row_with_one_control_keeps_its_glyph_and_no_trigger(config_dir, clean_registry):
    """Folding one icon into a one-item menu would add a click for nothing."""
    app = build(config_dir, fleet(Status.STOPPED))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)
    assert f'action="{START}"' in html
    assert "act-btn more" not in html, "a single-control row must not carry the overflow trigger"


@pytest.mark.asyncio
async def test_a_multi_control_row_carries_the_trigger_and_the_menu(config_dir, clean_registry):
    """The rarer control lives in the row's overflow menu, with its label written out."""
    app = build(config_dir, fleet(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)
    assert 'popovertarget="act-menu-0"' in html
    assert 'class="mi danger"' in html, "Stop is the menu's item and is drawn destructive"
    assert ">Stop<" in html and ">Start<" not in html
    assert "players are disconnected" in html


# ----------------------------------------------------------------------------- 3. the glyphs

@pytest.mark.asyncio
async def test_every_control_is_a_glyph_with_a_title_and_an_accessible_name(config_dir,
                                                                           clean_registry):
    """One inline SVG per control, a native tooltip AND an ``aria-label`` naming the target.

    No visible label in the strip: the word is the ACCESSIBLE name (and the tooltip sentence), which
    is what makes the icon-only strip checkable instead of merely intended. The glyph set itself is
    ``templates/_icons.html`` (one partial, two readers).
    """
    app = build(config_dir, fleet(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        html = await open_page(http)
    for title, aria in (
        ("Pause — freeze the mission; players stay connected", "Pause the mission on SRS-1"),
        ("Restart — players currently flying lose their sortie", "Restart server SRS-1"),
        ("Shutdown — stop the DCS server; everyone on it is disconnected",
         "Shutdown server SRS-1"),
        ("Stop — stop the server process; players are disconnected", "Stop server SRS-1"),
    ):
        assert f'title="{title}"' in html, title
        assert f'aria-label="{aria}"' in html, aria
    assert html.count('<svg class="ic24"') >= 4
 # THE STRIP'S buttons carry a glyph and NO text; the menu is where a control's word is written
 # out, which is why the assertion is scoped to `act-btn`.
    for button in re.findall(r'<button class="act-btn[^"]*"[^>]*>(.*?)</button>', html, re.S):
        assert not re.search(r">[A-Za-z][^<]*<", button), f"a visible label in the strip: {button}"


def test_the_glyph_set_carries_the_four_approved_fixes():
    """The glyph table itself, per name, pinned on the partial every reader imports.

    The rule: ``startup`` (SHUTDOWN) and ``start`` (STOPPED) -- two disjoint "bring a down server up"
    states -- wear the SAME plain play triangle; ``unpause`` (PAUSED) is a different operation and keeps
    the bar+triangle drawing; ``shutdown`` is the only power arc (shared with the node row); ``popup`` is
    the FILLED bubble while ``chat`` keeps the outlined one; ``kick`` is the simplified arrow leaving a
    barrier; and the node row's ``restart``/``shutdown`` are shared with the server row while
    ``online``/``offline`` belong to the maintenance pair -- no SERVER control may reach for either.
    """
    source = Path(actions_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = dict(re.findall(r"'([a-z_]+)':\s*'([^']*)'", source.read_text(encoding="utf-8")))
    assert {"startup", "shutdown", "pause", "unpause", "restart", "start", "stop", "kick",
            "ban", "chat", "popup", "mute", "more", "warn"} <= set(glyphs), sorted(glyphs)

 # 1. THE PAIR (two disjoint states, one plain play triangle): the
 # play triangle means "bring a DOWN server up", so startup (SHUTDOWN) and start (STOPPED) —
 # two disjoint states — wear it together; unpause (PAUSED) is a different operation.
    assert glyphs["startup"] == glyphs["start"], \
        "Startup and Start both mean 'bring a down server up' — the SAME play triangle"
    assert 'M7.6 4.8 19.6 12 7.6 19.2Z' in glyphs["startup"], "that drawing is the play triangle"
    assert "circle" not in glyphs["startup"], "the ring-with-stem startup glyph is deleted"
 # UNPAUSE is NOT the triangle: PAUSED is a frozen RUNNING server, not a down one, so it keeps
 # the mockup's own bar+triangle drawing --.
    assert glyphs["unpause"] not in (glyphs["startup"], glyphs["start"]), \
        "Unpause must NOT wear the play triangle: PAUSED is not a down server"
    assert 'M11.6 5.4 20.2 12l-8.6 6.6Z' in glyphs["unpause"], "Unpause is the bar+triangle's triangle"
    assert "<rect" in glyphs["unpause"], "Unpause carries the bar left of the triangle"
 # Shutdown is the power arc, and the ONLY power drawing in the set — the two-power-glyphs
 # ambiguity reported ("shows the 'power' button, the same as when it is started up and to
 # shut it down") is gone with the ring.
    assert "a8.4 8.4 0 1 1" in glyphs["shutdown"], "Shutdown is the power arc"
    assert [k for k, v in glyphs.items() if v == glyphs["shutdown"]] == ["shutdown"], \
        "shutdown must be the ONLY power arc drawing in the set"
 # THE INVARIANT the one-drawing decision rests on: a row is in exactly ONE state, so no single
 # state may offer two controls wearing the same drawing. A future card adding a second "bring
 # it up"-shaped control to a state that already has one breaks this — which is the point.
    for status in sorted({s for action in actions_page.SERVER_ACTIONS for s in action.statuses}):
        drawings = [glyphs[action.key] for action in actions_page.SERVER_ACTIONS
                    if status in action.statuses]
        assert len(drawings) == len(set(drawings)), \
            f"state {status} offers two controls with the same drawing"

 # 2. popup is FILLED (it carries the knock-out), chat is outlined
    assert 'fill-rule="evenodd"' in glyphs["popup"] and 'fill="currentColor"' in glyphs["popup"], \
        "the popup bubble must be filled"
    assert "fill-rule" not in glyphs["chat"], "chat keeps the OUTLINED bubble"

 # 3. kick is the simplified arrow past a barrier — no person circle to merge at 16px
    assert "circle" not in glyphs["kick"], "the mockup's person glyph merges into a blob at 16px"
    assert glyphs["kick"].count("<path") <= 3, "kick must stay a few strokes to read at 16px"

 # 4. RESTATED: this item used to assert that NO node glyph was in this
 # table: the node row's ``online``/``offline`` pair is the maintenance operation (it touches
 # only the servers on the node), so no SERVER control reaches for either.
 # the node row), so the pair is DRAWN now, and what this pin protects instead is that the two
 # belong to the NODE row: one box with a mirrored arrow each, and no server control borrows them.
    assert {"online", "offline"} <= set(glyphs), "the maintenance pair draws its own glyphs"
    assert glyphs["online"] != glyphs["offline"], "two words, two shapes"
    assert "rect" in glyphs["online"] and "rect" in glyphs["offline"], \
        "one box, one mirrored arrow — the mockup's reviewed pair"
    assert not {"online", "offline"} & {action.key for action in actions_page.SERVER_ACTIONS}, \
        "the node pair belongs to the node row; no server control may name it"

 # 5. NEW: the SERVER row gained the MAINTENANCE FLAG pair —
 # ``maintenance`` / ``end_maintenance``. Two shapes are required of them and both are asserted
 # here:
 # * they are TWO drawings, one per half (a pair that wore one shape would read as one control);
 # * they differ from EVERY other drawing in the set — the flag pair applies in EVERY server
 # state (a flag is not a power state), so on some row it is drawn
 # beside each of the process controls. That is the invariant above, spelled out for the
 # state-less pair the loop over ``statuses`` cannot see.
    assert {"maintenance", "end_maintenance"} <= set(glyphs), \
        "the maintenance flag pair draws its own glyphs"
    assert glyphs["maintenance"] != glyphs["end_maintenance"], "two halves, two shapes"
    taken = [key for key, drawing in glyphs.items()
             if key not in ("maintenance", "end_maintenance")]
    for key in ("maintenance", "end_maintenance"):
        assert glyphs[key] not in {glyphs[other] for other in taken}, \
            f"'{key}' reuses a drawing another control already wears"
    for status in ("RUNNING", "PAUSED", "STOPPED", "SHUTDOWN", "LOADING", "SHUTTING_DOWN"):
        drawings = [glyphs[action.key] for action in actions_page.SERVER_ACTIONS
                    if not action.statuses or status in action.statuses]
        assert len(drawings) == len(set(drawings)), \
            f"state {status} offers two controls with the same drawing"


def test_every_server_control_points_at_a_glyph_that_exists():
    """No control may name a glyph the partial does not draw: the alternative is an EMPTY ``<svg>``
    inside a perfectly normal-looking button, which no other test would notice.

    RESTATED: the key regex accepts an UNDERSCORE, because the maintenance
    flag pair's own key is ``end_maintenance``. The pin is unchanged — every ``SERVER_ACTIONS`` key
    must exist in the table — and it is exactly what would have caught a renamed drawing.
    """
    source = Path(actions_page.__file__).parent.parent.joinpath("templates", "_icons.html")
    glyphs = set(re.findall(r"'([a-z_]+)':\s*'", source.read_text(encoding="utf-8")))
    missing = sorted({action.key for action in actions_page.SERVER_ACTIONS} - glyphs)
    assert missing == [], f"control(s) with no glyph: {missing}"


# ------------------------------------------------------------ 4. capability and scope

@pytest.mark.asyncio
async def test_a_dcs_member_is_offered_no_control_and_cannot_post(config_dir, clean_registry,
                                                                 recording_bot):
    """A ``DCS`` member: no console at all, no control in any markup, and the gate refuses first."""
    app = build(config_dir, fleet(Status.SHUTDOWN))
    server = fleet().servers[0]
    async with client(app) as http:
        await sign_in(http, "plain")
        page = await http.get(servers_page.SERVERS_PATH)
        assert page.status_code == 403
        assert "act-strip" not in page.text and "act-btn" not in page.text
        reply = await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": "x" * 43})
        assert reply.status_code == 403, "the gate refuses before the handler"
        assert "500" not in reply.text and "Internal Server Error" not in reply.text
    assert server.calls == []
    assert recording_bot.calls == []


@pytest.mark.asyncio
async def test_a_manager_sees_only_their_own_row_and_a_foreign_post_is_indistinguishable(
        config_dir, clean_registry, recording_bot):
    """The scope, on the strip and on the route: no control for somebody else's server, and the same
    bare 403 for a real out-of-scope name and for a name that does not exist — no enumeration."""
    app = build(config_dir, fleet(Status.SHUTDOWN))
    async with client(app) as http:
        await sign_in(http, "hoster-b")             # manages Host B only
        page = await open_page(http)
        assert 'value="SRS-B"' in page, "their own row keeps its control"
        assert 'value="SRS-1"' not in page, "no control on somebody else's row"
        token = token_of(page)
        foreign = await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token})
        missing = await http.post(STARTUP, data={"server": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text, "the two refusals must be indistinguishable"
    assert "SRS-1" not in foreign.text
 # NAMED BOUNDARY:
 # ``test_webui_write_pause.py:401-404``): a SCOPE/PERMISSION refusal is the ROUTE's answer and
 # happens before any action, so it carries no ActionResult and leaves no audit entry — the trail
 # records the ACTION's own refusals instead (see
 # ``test_the_audit_records_the_success_and_the_action_s_own_refusal``). Auditing a bare 403 would
 # be a second audit implementation inside the gate, writable by any caller that hits the route.
    assert recording_bot.calls == [], "a refused request never reached an action, so audits nothing"


# ------------------------------------------------------------------------ 5. the confirm step

@pytest.mark.asyncio
async def test_a_confirm_required_route_refuses_a_post_that_skipped_its_dialog(config_dir,
                                                                              clean_registry,
                                                                              recording_bot):
    """A confirmation curl can skip is decoration.

    The row's control posts to the DIALOG, the dialog's form carries the one-shot token, and the action's
    own route refuses everything else -- asserted by the 403 with the console's wording and by the domain
    double never being called.
    """
    cluster = fleet(Status.RUNNING)
    app = build(config_dir, cluster)
    server = cluster.servers[0]
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http)

        direct = await http.post(RESTART, data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert direct.status_code == 403, "a POST that never opened the dialog must be refused"
        assert server.calls == [], "nothing may run without the confirmation"

        dialog = await http.post(RESTART + CONFIRM,
                                 data={"server": "SRS-1", "_csrf_token": token_of(page)})
        assert dialog.status_code == 200
        assert "Players currently flying lose their sortie." in dialog.text
        token = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
        assert token, "the dialog carries no confirm token"

        accepted = await http.post(RESTART, data={"server": "SRS-1", "_csrf_token": token_of(page),
                                                  "_confirm_token": token.group(1)})
        assert accepted.status_code == 303
        assert server.calls == ["stop", "start"], "the action ran after the confirmation"

 # the token is SPENT: a replayed dialog form must not run the action a second time
        replay = await http.post(RESTART, data={"server": "SRS-1", "_csrf_token": token_of(page),
                                                "_confirm_token": token.group(1)})
        assert replay.status_code == 403
    assert server.calls == ["stop", "start"]


@pytest.mark.asyncio
async def test_the_dialog_wording_names_the_consequence_and_counts_the_players(config_dir,
                                                                              clean_registry,
                                                                              recording_bot):
    """The copy is asserted, so it cannot drift into "are you sure?" — and the figure is the
    RESOLVED server's, never a field the browser sent."""
    cluster = fleet(Status.RUNNING, players=3)
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        restart = await http.post(RESTART + CONFIRM,
                                  data={"server": "SRS-1", "_csrf_token": token})
        shutdown = await http.post(SHUTDOWN + CONFIRM,
                                   data={"server": "SRS-1", "_csrf_token": token})
        stop = await http.post(STOP + CONFIRM, data={"server": "SRS-1", "_csrf_token": token})

    assert "Players currently flying lose their sortie." in restart.text
    assert "mid-mission progress is gone" in restart.text
    assert "Restart server" in restart.text
    assert "Restart SRS-1 — players flying lose their sortie" in restart.text

    assert "<b>3 player(s) connected right now lose their connection</b>" in shutdown.text, \
        "the warning is present tense: the players are still connected when it is read"
    assert "nobody can join until somebody starts the server up again" in shutdown.text
    assert "Shut down" in shutdown.text

    assert "<b>3 player(s) connected right now lose their connection.</b>" in stop.text
    assert "Stop ends the server process" in stop.text
    assert "Stop server process" in stop.text

    for dialog in (restart, shutdown, stop):
        assert "are you sure" not in dialog.text.lower()
        assert f'name="{actions_page.CONFIRM_FIELD}"' in dialog.text
        assert f'action="/actions/server/' in dialog.text


@pytest.mark.asyncio
async def test_the_dialog_is_keyboard_dismissible_and_traps_its_focus(config_dir, clean_registry,
                                                                     recording_bot):
    """The dialog's keyboard contract, pinned on the RENDERED page.

    The confirmation is a dialog a keyboard can dismiss and whose focus it cannot lose. Markup alone
    gives neither, so the page carries a small script; this test asserts the page ships it (rather than a
    comment claiming it does), the JS-free fallback is there, and the destructive button stays off the
    default path.
    """
    app = build(config_dir, fleet(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        dialog = await http.post(RESTART + CONFIRM,
                                 data={"server": "SRS-1", "_csrf_token": token})

    assert 'role="dialog"' in dialog.text and 'aria-modal="true"' in dialog.text, \
        "assistive technology must be told this is a modal dialog"
    assert '"Escape"' in dialog.text, "Esc does not dismiss the dialog"
    assert "window.location.assign(back)" in dialog.text, "Esc must go back without acting"
    assert '"Tab"' in dialog.text and "preventDefault" in dialog.text, \
        "focus is not trapped between the dialog's controls"
 # CANCEL IS AN ANCHOR, NOT A GET FORM (M12). A GET form replaces the action's query with its own
 # (empty) serialised data and drops the tab; the anchor keeps ``back`` verbatim. Pin the shape so a
 # future edit cannot smuggle the form back in.
    assert 'method="get"' not in dialog.text, "Cancel must not be a GET form (M12)"
    cancel = re.search(r'<a class="btn" href="([^"]*)"[^>]*autofocus[^>]*>(.*?)</a>', dialog.text, re.S)
    assert cancel and cancel.group(2).strip() == "Cancel", \
        "without a script the default control must be Cancel, an autofocused anchor"
    assert urljoin(RESTART + CONFIRM, cancel.group(1)) == servers_page.SERVERS_PATH, \
        "Cancel's href must resolve to the back path, tab and all"
    assert 'class="btn danger" type="submit"' in dialog.text


@pytest.mark.asyncio
async def test_the_dialog_refuses_a_foreign_server_like_a_missing_one(config_dir, clean_registry,
                                                                     recording_bot):
    """The dialog answers the SAME refusals as its POST."""
    app = build(config_dir, fleet(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        token = token_of(await open_page(http))
        foreign = await http.post(RESTART + CONFIRM,
                                  data={"server": "SRS-1", "_csrf_token": token})
        missing = await http.post(RESTART + CONFIRM,
                                  data={"server": "NOPE-9", "_csrf_token": token})
    assert foreign.status_code == 403 and missing.status_code == 403
    assert foreign.text == missing.text
    assert "SRS-1" not in foreign.text


# ------------------------------------------------------------------------------- 6. the CSRF

@pytest.mark.asyncio
async def test_the_new_routes_refuse_a_post_with_no_session_token(config_dir, clean_registry,
                                                                 recording_bot):
    """Every new route carries the session's CSRF dependency, the dialog's loader included."""
    cluster = fleet(Status.SHUTDOWN)
    app = build(config_dir, cluster)
    browser = {"Accept": "text/html"}
    async with client(app) as http:
        await sign_in(http, "mod1")
        for path in (STARTUP, RESTART + CONFIRM):
            reply = await http.post(path, data={"server": "SRS-1"}, headers=browser)
            assert reply.status_code == 403, path
            assert "nothing was saved" in reply.text, path
            assert "SRS-1" not in reply.text, path
    assert cluster.servers[0].calls == []
    assert recording_bot.calls == []


# ------------------------------------------------------------------------------ 7. the audit

@pytest.mark.asyncio
async def test_the_audit_records_the_success_and_the_action_s_own_refusal(config_dir, clean_registry,
                                                                         recording_bot):
    """One entry per ATTEMPT, naming the web actor -- the success and the typed refusal.

    A PERMISSION refusal (the scope, the capability) is the ROUTE's answer and happens before any
    action, so it carries no ActionResult and is not audited here.
    """
    cluster = fleet(Status.SHUTDOWN)
    app = build(config_dir, cluster)
    server = cluster.servers[0]
    async with client(app) as http:
        await sign_in(http, "mod1")
        token = token_of(await open_page(http))
        first = await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token})
        assert first.status_code == 303
 # the double flips the status, so the SECOND attempt meets the action's own state guard
        token = token_of(await open_page(http))
        second = await http.post(STARTUP, data={"server": "SRS-1", "_csrf_token": token})
        assert second.status_code == 303
    assert server.calls == ["startup"], "the refused attempt ran no domain call"
    messages = [call[0] for call in recording_bot.calls]
    assert messages == ["Server 'SRS-1' started up.",
                        "Server 'SRS-1' is Running, not shut down."]


# ------------------------------------------------------------- 8. one implementation, two screens

@pytest.mark.asyncio
async def test_both_screens_render_the_same_strip(config_dir, clean_registry):
    """The dashboard's Servers tab and /fleet are two screens of ONE component: the same control
    paths, the same trigger markup — the partial and the map that fills it are shared."""
    app = build(config_dir, fleet(Status.RUNNING))
    async with client(app) as http:
        await sign_in(http, "mod1")
        pages = await screens(http)
    assert offered(pages["dashboard"]) == offered(pages["servers"])
    trigger = '<button class="act-btn more" type="button" popovertarget="act-menu-0"'
    assert trigger in pages["dashboard"] and trigger in pages["servers"]


@pytest.mark.asyncio
async def test_an_installation_without_the_mission_plugin_offers_nothing(config_dir, clean_registry):
    """The action layer absent: no strip on either screen, and a crafted POST is a typed refusal —
    never a 500, never a fake success."""
    app = build(config_dir, fleet(Status.SHUTDOWN))
    actions_module._ACTIONS.clear()       # the ``mission`` plugin is not on this install
    async with client(app) as http:
        await sign_in(http, "mod1")
        pages = await screens(http)
        for html in pages.values():
            assert "act-strip" not in html
        reply = await http.post(STARTUP, data={"server": "SRS-1",
                                               "_csrf_token": token_of(pages["servers"])})
        assert reply.status_code == 303
        after = await open_page(http)
        assert "not available" in after
