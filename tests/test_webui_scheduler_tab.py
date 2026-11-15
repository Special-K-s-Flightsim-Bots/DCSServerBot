"""The Scheduler tab on the server page: its place in the strip, its per-server gate, its body.

The tab is one server's own block of ``config/plugins/scheduler.yaml``, so its audience is per server:
``scheduler.view`` (``Admin``/``DCS Admin`` by role, a manager by scope grant) decides the tab, and
``settings.scheduler_access`` narrows that to the server in hand through the console's own
``scope.server_in_scope``. Pinned here: the capability pair and the expert door as declared; the tab's
place in the strip on BOTH tuples and in the RENDERED strip; a DCS Admin's read+write on a server in
scope and on an untagged server; a manager's read-only landing; a server outside the caller's scope is
absent AND refused without disclosing that it exists; and the landing offers no write control to a
reader.

Driven through the real app with the real local login backend and a stub cluster; the scheduler
plugin is a double installed on ``server_detail._scheduler``, since the plan READ is what the page
consumes and the plugin is not this module's subject.
"""
from __future__ import annotations

import html
import re
import types

import pytest

from conftest import permissions
from services.webservice import scheduler_plan
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, server, sign_in,
    _hash,
)

SERVER_A = "SRS-1"
#: the INSTANCE name — the key the scheduler file's blocks are written under. It deliberately differs
#: from the server's name, so a test cannot pass by keying the file with the page's title.
INSTANCE_A = "Host-A-Instance"


def cluster():
    """Server A (hosted by Host A) and server B — the page's own two sides."""
    a = server(SERVER_A, ["Host A"])
    b = server("SRS-B", ["Host B"], node_name="dcs-b-01", instance="Host-B-Instance")
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    return Cluster([a, b], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance(INSTANCE_A, a),
                              instance("Host-B-Instance", b)])


SCHEDULER_YAML = """\
# the install's scheduler configuration
DEFAULT:
  startup_delay: 10                                # shared by every server
  warn:
    message: '!!! {item} will {what} in {when} !!!'
    times:
    - 600
    - 60
Host-A-Instance:
  timezone: Europe/Berlin
  schedule:
    00-12: YYYYYYY
    12-24: NNNNNNN
  action:
    times:
    - 04:00
    method: rotate
    populated: false
"""


def scheduler_file(config_dir, body: str = SCHEDULER_YAML):
    """Write the install's scheduler file where ``settings.discover_files`` looks for it."""
    plugins = config_dir / "plugins"
    plugins.mkdir(exist_ok=True)
    path = plugins / "scheduler.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class FakeScheduler:
    """The plugin double: ``get_config`` is the ONE call the tab makes on it."""

    def __init__(self, plan):
        self.plan = plan
        self.calls: list[object] = []

    def get_config(self, server_obj=None, **_kwargs):
        self.calls.append(server_obj)
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan


def install_plugin(monkeypatch, plan):
    """Point the page module's plugin lookup at a double — the seam its own docstring names."""
    fake = FakeScheduler(plan)
    monkeypatch.setattr(detail_page, "_scheduler", lambda: fake)
    return fake


def tab_body(html: str) -> str:
    """The Scheduler tab's OWN markup — everything inside the body's one wrapper.

    The page around it carries chrome of its own (the sign-out form, the tab strip), so a pin that
    reads the whole document cannot tell the tab's markup from the shell's.
    """
    start = html.index('<div class="sw-body"')
    depth = 1
    index = html.index(">", start) + 1
    while depth:
        open_at = html.find("<div", index)
        close_at = html.find("</div>", index)
        if close_at == -1:
            break
        if open_at != -1 and open_at < close_at:
            depth += 1
            index = open_at + 4
        else:
            depth -= 1
            index = close_at + 6
    return html[start:index]


async def scheduler_page(http, server=SERVER_A, *, expect=200):
    response = await http.get(f"/fleet/{server}?tab=scheduler")
    assert response.status_code == expect, (response.status_code, response.text[:300])
    return response.text


# ------------------------------------------------------------------ 1. the capability, one predicate

def test_the_two_capabilities_and_the_expert_door_are_declared_once(config_dir):
    """``scheduler.view``/``scheduler.write``/``scheduler.expert``: the roles and the grants.

    Both per-server halves admit a manager whose scope holds the server — owning a server means
    deciding when it runs, so the write follows the read. The installation-wide door (Expert Mode,
    and the shared ``DEFAULT`` section that rides the same decision) is ``Admin`` only.
    """
    assert detail_page.SCHEDULER_CAPABILITY == settings_page.SCHEDULER_VIEW_CAPABILITY
    assert detail_page.SCHEDULER_CAPABILITY == "scheduler.view"
    assert detail_page.SCHEDULER_WRITE_CAPABILITY == "scheduler.write"
    assert detail_page.SCHEDULER_TAB == "scheduler"
    assert permissions.roles_for_capability("scheduler.view") == ("Admin", "DCS Admin")
    assert permissions.scope_grants("scheduler.view") is True
    assert permissions.roles_for_capability("scheduler.write") == ("Admin", "DCS Admin")
    assert permissions.scope_grants("scheduler.write") is True
    assert permissions.roles_for_capability("scheduler.expert") == ("Admin",)
    assert permissions.scope_grants("scheduler.expert") is False


def test_the_tab_sits_between_missions_and_mods_on_both_tuples(config_dir):
    """The ORDER is the declaration's: missions, then scheduler, then mods — on BOTH tuples.

    ``OPTIONAL_TAB_ORDER`` drives the strip and ``TAB_ORDER`` is the set ``normalise_tab`` accepts;
    a key present in one and not the other is a tab that either cannot be reached or cannot be
    normalised, so both are pinned by position rather than by membership.
    """
    for order in (detail_page.TAB_ORDER, detail_page.OPTIONAL_TAB_ORDER):
        assert (order.index(detail_page.MISSIONS_TAB)
                < order.index(detail_page.SCHEDULER_TAB)
                < order.index(detail_page.MODS_TAB)), order
    assert detail_page.TAB_LABELS[detail_page.SCHEDULER_TAB] == "Scheduler"


# --------------------------------------------------------------- 2. the strip, offered or withheld

def untagged_cluster():
    """One server that declares NO ``managed_by`` — the shape a DCS Admin keeps."""
    a = server(SERVER_A)
    node_a = types.SimpleNamespace(name="dcs-a-01", master=True,
                                   instances={INSTANCE_A: instance(INSTANCE_A, a)})
    return Cluster([a], nodes={"dcs-a-01": node_a}, node=node_a,
                   instances=[instance(INSTANCE_A, a)])


#: a DCS Admin whose scope the local backend never narrows: the untagged-server shape.
DCSSB_ADMIN_USERS = (
    {"username": "specialk", "roles": ["Admin"]},
    {"username": "dcsadmin", "roles": ["DCS Admin"]},
    {"username": "hoster", "roles": ["DCS"], "scope": ["Host A"]},
)


@pytest.mark.asyncio
async def test_an_admin_is_offered_the_tab_between_missions_and_mods(config_dir, clean_registry,
                                                                    monkeypatch):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin"})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}")

    assert "?tab=scheduler" in page, "the tab must be offered to a holder of scheduler.view"
    rendered = re.findall(r'href="[^"]*tab=([a-z]+)"', page)
    assert detail_page.SCHEDULER_TAB in rendered
    assert (rendered.index("missions") < rendered.index(detail_page.SCHEDULER_TAB)
            < rendered.index("mods")), rendered


@pytest.mark.asyncio
async def test_a_dcs_admin_reads_and_writes_the_server_its_scope_holds(config_dir, clean_registry,
                                                                      monkeypatch):
    """A DCS Admin on a server tagged with their own role: tab present AND the wizard link."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin",
                                 "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "mod1")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        body = tab_body(await scheduler_page(http))

    assert "?tab=scheduler" in page, "a DCS Admin must be offered the tab on its own server"
    assert "Change the plan" in body, "and may change it"
    assert re.search(r'href="[^"]*tab=scheduler&amp;step=1"', body)


@pytest.mark.asyncio
async def test_a_dcs_admin_keeps_an_untagged_server(config_dir, clean_registry, monkeypatch):
    """A DCS Admin honours ``managed_by`` AND keeps every untagged server (read + write)."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin",
                                 "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, untagged_cluster(), plugins=("mission", "mods", "scheduler"),
                users=[dict(user, password_hash=_hash()) for user in DCSSB_ADMIN_USERS])
    async with client(app) as http:
        await sign_in(http, "dcsadmin")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        body = tab_body(await scheduler_page(http))

    assert "?tab=scheduler" in page
    assert "Change the plan" in body


@pytest.mark.asyncio
async def test_a_manager_reads_and_edits_their_servers_plan_but_never_the_shared_default(
        config_dir, clean_registry, monkeypatch):
    """A manager (a DCS role whose scope holds the server) owns its schedule — and nothing wider.

    The wizard is offered, the step serves, and the switch that would write ``DEFAULT`` for every
    server is not drawn: that section is the hoster's, and one press of it would move servers the
    manager does not own.
    """
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin",
                                 "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "hoster")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        body = tab_body(await scheduler_page(http))
        step = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=1")

    assert "?tab=scheduler" in page, "a manager reads the schedule of their own server"
    assert "sw-week" in body
    assert "Change the plan" in body, "a manager owns its server's schedule"
    assert step.status_code == 200, "the step serves"
    assert scheduler_plan.SHARED_LABEL not in step.text, \
        "the shared DEFAULT switch is the hoster's, so a manager never sees it"


@pytest.mark.asyncio
async def test_a_server_outside_the_scope_is_absent_and_refused_without_disclosure(
        config_dir, clean_registry, monkeypatch):
    """A manager of another host: no tab anywhere, and the same 403 a missing name answers."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin"})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "hoster-b")
        out_of_scope = await http.get(f"/fleet/{SERVER_A}?tab=scheduler")
        missing = await http.get("/fleet/No-Such-Server")

    assert detail_page.SCHEDULER_TAB not in detail_page.tabs_for(
        ("DCS",), scheduler_view=False), "an unreadable server's tab is never built"
    assert out_of_scope.status_code == 403
    assert missing.status_code == 403
    assert out_of_scope.json() == missing.json(), "the refusal must disclose nothing"


@pytest.mark.asyncio
async def test_the_strip_and_the_refusal_read_the_one_access_function(config_dir, clean_registry,
                                                                     monkeypatch):
    """Deny at the ONE function and BOTH the strip and the crafted request follow.

    A second spelling of the rule would leave one of the two answering the old way.
    """
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin"})
    monkeypatch.setattr(settings_page, "scheduler_access",
                        lambda request, server=None: settings_page.SchedulerAccess(
                            view=False, write=False, expert=False))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}")
        refused = await http.get(f"/fleet/{SERVER_A}?tab=scheduler")

    assert "?tab=scheduler" not in page
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "Not authorized (needs one of: "
        + permissions.requirement_text(detail_page.SCHEDULER_CAPABILITY) + ").")


# ------------------------------------------------------------- 3. the body writes nothing at all

@pytest.mark.asyncio
async def test_the_landing_renders_no_write_control(config_dir, clean_registry, monkeypatch):
    """The landing is READ-ONLY: no form, no POST — the only way off it is a GET step link."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin",
                                 "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await scheduler_page(http)

    body = tab_body(html)
    assert '<div class="sw-week">' in body, "the week grid is the landing's own evidence"
    assert "<form" not in body, "the read-only landing must render no form"
    assert 'method="post"' not in body, "the read-only landing must post nothing"
    assert re.search(r'href="[^"]*tab=scheduler&amp;step=1"', body), \
        "the wizard is entered by a GET link, never a write control"


@pytest.mark.asyncio
async def test_the_landing_renders_in_the_visitors_language(config_dir, clean_registry, monkeypatch):
    """Every string on the tab is wrapped: the German render shows the catalog's words, not the msgids."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    # a trigger with a placeholder, so the pin also covers the ONE interpolation site (``say``)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin",
                                 "action": {"times": ["04:00"], "method": "rotate"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = await scheduler_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = await scheduler_page(http)

    assert "Where each value comes from" in english
    assert "Woher die einzelnen Werte kommen" in german
    assert "Where each value comes from" not in german
    # a sentence composed in Python goes through the same catalogs as the template's own strings
    assert detail_page.SCHEDULER_SETTINGS_LINK in english
    assert "In den Einstellungen öffnen" in german
    # and its placeholder is INTERPOLATED in both languages — a raw ``{value}`` on the page is the
    # defect this assertion exists for
    assert "at 04:00" in english and "{value}" not in english
    assert "um 04:00" in german and "{value}" not in german


@pytest.mark.asyncio
async def test_the_page_ships_the_tabs_stylesheet(config_dir, clean_registry, monkeypatch):
    """The `sw-` rules ride on the page — a tab cannot declare a sheet only for itself."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin"})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await open_page(http, f"/fleet/{SERVER_A}")

    assert 'href="/static/scheduler.css"' in page
    css = (detail_page.Path(__file__).parent.parent
           / "services" / "webservice" / "static" / "scheduler.css")
    assert ".sw-week" in css.read_text(encoding="utf-8")


# ------------------------------------------------------- 4. the wizard's steps, all GET on this route

#: the plan the step tests render: one of each shape the steps own.
WIZARD_PLAN = {
    "timezone": "Europe/Berlin",
    "schedule": {"00-12": "YYYYYYY", "12-24": "NNNNNNN"},
    "warn": {"message": "restarting", "times": [600, 60]},
    "action": [{"times": ["04:00"], "method": "rotate", "populated": False}],
    "startup": {"mission_id": 3},
    "onShutdown": "run:shutdown /r",
}


def hidden_inputs(body: str) -> dict[str, str]:
    """Every hidden input in one step's form, name -> value."""
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', body))


async def step_page(http, step, extra: str = "", server: str = SERVER_A) -> str:
    """The wizard at *step*, as the browser would request it."""
    query = f"?tab=scheduler&step={step}"
    if extra:
        query += "&" + extra
    response = await http.get(f"/fleet/{server}{query}")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


@pytest.mark.asyncio
async def test_every_step_is_one_get_form_with_the_step_as_a_button(config_dir, clean_registry,
                                                                    monkeypatch):
    """Script off: each step is ONE GET form and its Next/Back are submit buttons named ``step``."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = {step: tab_body(await step_page(http, step)) for step in ("1", "2", "3", "4")}

    for step, body in pages.items():
        assert '<form method="get" action="/fleet/SRS-1"' in body, f"step {step}: one GET form"
        assert 'method="post"' not in body, f"step {step}: no write control"
        assert re.search(r'<button type="submit" name="step" value="\d"', body), \
            f"step {step}: the step travels as a button, so the browser builds the URL"
        assert hidden_inputs(body).get("tab") == "scheduler", f"step {step}: the tab is carried"
        # script off: nothing is hidden by the server, so every control is on screen and submits
        assert " hidden" not in body and "display:none" not in body.replace(" ", ""), \
            f"step {step}: no control may be hidden by the render itself"
    assert 'name="week_days.0"' in pages["1"]
    assert 'name="onShutdown.command"' in pages["3"], \
        "the four event commands are edited on the actions step"
    assert 'name="onShutdown.command"' not in pages["2"], "and not on the startup step"
    assert 'name="action.0.method"' in pages["3"]
    assert 'name="warn.message"' in pages["4"]


@pytest.mark.asyncio
async def test_a_step_carries_the_values_it_does_not_own(config_dir, clean_registry, monkeypatch):
    """Step 4 edits the warn block only — the week and the action entry ride along hidden."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "4"))

    carried = hidden_inputs(body)
    assert carried["schedule.00-12"] == "YYYYYYY"
    assert carried["action.0.method"] == "rotate"
    assert carried["action.0.populated"] == "false", "a false flag survives as false"
    assert carried["startup.mission_id"] == "3"
    assert carried["onShutdown"] == "run:shutdown /r"
    assert carried["shape.action"] == "seq", "the file's action shape is carried, never re-chosen"
    assert "warn.message" not in carried, "the step's OWN field is a control, not a hidden input"


@pytest.mark.asyncio
async def test_an_unusable_step_is_the_first_step(config_dir, clean_registry, monkeypatch):
    """``?step=99`` and ``?step=`` land on step 1; an absent step stays on the landing."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        for value in ("99", "", "week"):
            body = tab_body(await step_page(http, value))
            assert 'name="week_days.0"' in body, value
            assert "step 1 of 5" in body, value
        landing = tab_body(await scheduler_page(http))
    assert 'class="sw-week"' in landing, "no step means the plan as it stands"
    assert "step 1 of " not in landing


@pytest.mark.asyncio
async def test_a_value_in_the_url_is_read_back_by_the_step(config_dir, clean_registry, monkeypatch):
    """The step reads its own posted plan: a refused cron expression is the page's own sentence."""
    from services.webservice import scheduler_plan

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3", "action.0.cron=0+0+30+2+*"))

    assert scheduler_plan.CRON_NEVER_FIRES in body
    assert "Next:" not in body, "a never-firing expression must not read as having a next run"
    assert 'value="0 0 30 2 *"' in body, "the typed value comes back into its control"


@pytest.mark.asyncio
async def test_the_steps_read_schedule_startup_actions_warnings(config_dir, clean_registry,
                                                               monkeypatch):
    """The rail's order AND each step's own heading: the wizard follows the order of the plan."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        bodies = {step: tab_body(await step_page(http, step)) for step in ("1", "2", "3", "4")}

    rail = bodies["1"]
    order = [rail.find(label) for label in ("When it runs", "Startup",
                                            "Actions (restart, rotate, etc.)", "Warnings",
                                            "Review &amp; save")]
    assert all(at >= 0 for at in order), order
    assert order == sorted(order), "the rail must list the steps in the plan's own order"
    assert "Startup &amp; events" not in rail, "the step states no events any more"
    assert "mission, presets" in rail and "mission, presets, commands" not in rail
    assert "one trigger, options by method" in rail
    assert "When should the server run?" in bodies["1"]
    heading = re.search(r"<h1>(.*?)</h1>", bodies["2"], re.S).group(1)
    assert heading.strip() == "Startup", heading
    assert "How does the server start, and what runs on its events?" not in bodies["2"]
    heading = re.search(r"<h1>(.*?)</h1>", bodies["3"], re.S).group(1)
    assert heading.strip() == "Actions (restart, rotate, etc.)", heading
    assert "What should happen while it runs?" not in bodies["3"]
    assert "Restarts &amp; missions" not in rail
    assert "How is a restart announced?" in bodies["4"]
    # the review step follows the LAST content step, which is the warnings step
    assert "The review step and the save follow" in bodies["4"]
    assert "The review step and the save follow" not in bodies["2"]


@pytest.mark.asyncio
async def test_max_mission_time_renders_only_with_the_mission_time_trigger(config_dir, clean_registry,
                                                                          monkeypatch):
    """The hard cap belongs to the mission-time trigger: drawn there, and nowhere else."""
    scheduler_file(config_dir)
    plan = dict(WIZARD_PLAN, action=[{"mission_time": 480, "max_mission_time": 600,
                                      "method": "restart", "populated": False}])
    install_plugin(monkeypatch, plan)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        minutes = tab_body(await step_page(http, "3"))

    assert 'name="action.0.mission_time"' in minutes
    assert 'name="action.0.max_mission_time"' in minutes, "the cap renders with mission time"
    assert "At most" in minutes
    assert "The hard cap: at that many minutes of mission time the mission restarts even with " \
           "players on. The bot applies it only while this entry waits until nobody is flying." \
           in minutes
    # it is NOT hidden behind a script any more: the control is drawn, so no `hidden` dance
    assert "data-max-mission-time" not in minutes
    assert "data-mission-time" not in minutes

    times = dict(WIZARD_PLAN, action=[{"times": ["04:00"], "max_mission_time": 600,
                                       "method": "restart"}])
    install_plugin(monkeypatch, times)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    assert 'name="action.0.max_mission_time"' not in body, (
        "a times entry draws no mission-time control, so a cap cannot ride along")


@pytest.mark.asyncio
async def test_wait_for_nobody_is_two_radios_of_one_name(config_dir, clean_registry, monkeypatch):
    """``populated`` is a two-value choice: the default states NO key, ``false`` states the wait."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        waiting = tab_body(await step_page(http, "3"))
        acting = tab_body(await step_page(http, "3", "action.0.method=restart"))

    radios = re.findall(r'<input type="radio" name="action\.0\.populated" value="([^"]*)"'
                        r'([^>]*)>', waiting)
    assert [value for value, _ in radios] == ["", "false"], "one name, two exclusive values"
    assert "checked" not in radios[0][1], "an entry that waits renders the wait as the choice"
    assert "checked" in radios[1][1]
    assert 'type="checkbox" name="action.0.populated"' not in waiting
    # every OTHER boolean of the entry stays a checkbox
    assert 'type="checkbox" name="action.0.mission_end"' in waiting
    # an entry that does not wait carries the DEFAULT: the empty value is the checked one
    default = re.findall(r'<input type="radio" name="action\.0\.populated" value="([^"]*)"'
                         r'([^>]*)>', acting)
    assert "checked" in default[0][1], "act even with players on is the default"


@pytest.mark.asyncio
async def test_step_1_offers_a_timezone_picker_of_every_zone(config_dir, clean_registry,
                                                             monkeypatch):
    """A CLOSED list from the interpreter — a select, the empty option first, no free-text box."""
    from zoneinfo import available_timezones

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    select = re.search(r'<select[^>]*id="sw-timezone"[^>]*>(.*?)</select>', body, re.S)
    assert select, "the timezone control must be a select"
    assert 'type="text"' not in select.group(0)
    options = re.findall(r'<option value="([^"]*)"', select.group(1))
    assert options[0] == "", "the first option is the machine's own local time"
    assert set(available_timezones()) <= set(options)
    assert 'value="Europe/Berlin" selected' in select.group(1), "the plan's zone is the choice"


@pytest.mark.asyncio
async def test_a_timezone_the_list_does_not_carry_stays_selected(config_dir, clean_registry,
                                                                 monkeypatch):
    """An old or aliased name is added as its own option, so the step never silently drops it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin_Old",
                                 "schedule": {"00-24": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    assert 'value="Europe/Berlin_Old" selected' in body


@pytest.mark.asyncio
async def test_a_plan_with_no_schedule_renders_one_whole_day_row(config_dir, clean_registry,
                                                                 monkeypatch):
    """No schedule at all: ONE row, 00-24, every day on — the default proposal."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "Europe/Berlin"})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    rows = re.findall(r'<div class="sw-wrow"[^>]*>', body)
    assert len(rows) == 1, rows
    assert 'name="week_start.0" value="00:00"' in body
    assert 'name="week_end.0" value="00:00"' in body, "midnight is entered as 00:00"
    assert 'name="week_days.0" value="YYYYYYY"' in body
    assert "00:00-24:00" not in body, "the pickers state the range, never a second label beside them"


@pytest.mark.asyncio
async def test_a_week_row_states_its_range_only_through_its_pickers(config_dir, clean_registry,
                                                                   monkeypatch):
    """A stated row: no range label, and the two time pickers carry that row's own start and end."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    first = body.split('class="sw-wrow"', 1)[1]
    assert "sw-erk" not in body, "the duplicate range label is gone, class and markup both"
    assert 'name="week_start.0" value="00:00"' in first
    assert 'name="week_end.0" value="12:00"' in first, "the picker carries the row's own end"
    # same columns in the same order on every row, so a list of rows reads as a column
    order = [first.index(f'name="{name}.0"') for name in ("week_start", "week_end", "week_days")]
    assert order == sorted(order), order
    assert first.index('name="week_days.0"') < first.index('name="removerow" value="0"')
    css = stylesheet()
    assert ".sw-erk" not in css, "the stylesheet drops the label with the markup"


@pytest.mark.asyncio
async def test_a_row_added_with_the_add_control_starts_with_empty_pickers(config_dir,
                                                                         clean_registry,
                                                                         monkeypatch):
    """A new row CONTINUES the week: it starts where the row above ends, both a whole hour."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    rows = "week_start.0=00%3A00&week_end.0=12%3A00&week_days.0=YYYYYYY"
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1", rows + "&addrow=1"))

    added = body.split('class="sw-wrow"')[2].split("</div>", 1)[0]
    assert 'name="week_start.1" value="12:00"' in added, "the row above's end, not a dash"
    assert 'name="week_end.1" value="13:00"' in added, "one hour on, minutes never empty"
    assert "—" not in added, "no placeholder text stands in for the times"
    # nothing VISIBLE stands before the first picker: the row's only other element is its hidden key
    before_first_control = re.sub(r"<input[^>]*>", "",
                                  added.split("<label", 1)[0].split(">", 1)[1])
    assert before_first_control.strip() == "", "nothing is printed before the first picker"
    assert 'name="week_key.1" value=""' in added, \
        "a new row carries no key, so its range composes from its own times"
    order = [added.index(f'name="{name}.1"') for name in ("week_start", "week_end", "week_days")]
    assert order == sorted(order), order
    assert added.index('name="week_days.1"') < added.index(f'name="removerow" value="1"')


@pytest.mark.asyncio
async def test_step_1_renders_the_letter_field_not_only_day_buttons(config_dir, clean_registry,
                                                                    monkeypatch):
    """Script off: the 7-letter field IS the row's control, so a row is never buttons-only."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    field = re.search(r'<input class="sw-inp mono" type="text" name="week_days\.0"'
                      r' value="([YNP]{7})" maxlength="7" pattern="\[YNP\]\{7\}">', body)
    assert field, "the no-script control must be rendered by the server"
    assert field.group(1) == WIZARD_PLAN["schedule"]["00-12"]
    assert 'class="sw-day' not in body, "the buttons are the script's, never the only control"


@pytest.mark.asyncio
async def test_a_row_op_round_trips_on_the_same_get_form(config_dir, clean_registry, monkeypatch):
    """`addrow` appends one empty row and `removerow` drops that row — both without a script."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    rows = "week_start.0=00%3A00&week_end.0=12%3A00&week_days.0=YYYYYYY" \
           "&week_start.1=12%3A00&week_end.1=00%3A00&week_days.1=NNNNNNN"
    async with client(app) as http:
        await sign_in(http, "specialk")
        added = tab_body(await step_page(http, "1", rows + "&addrow=1"))
        removed = tab_body(await step_page(http, "1", rows + "&removerow=0"))

    assert added.count('class="sw-wrow"') == 3, "one more row"
    assert 'name="week_days.2" value="YYYYYYY"' in added
    assert removed.count('class="sw-wrow"') == 1, "that row is gone"
    assert 'name="week_days.0" value="NNNNNNN"' in removed
    # the step itself rides in a hidden field, so a row op re-renders step 1 and not the landing
    assert '<input type="hidden" name="step" value="1">' in added
    assert "step 1 of 5" in added


@pytest.mark.asyncio
async def test_an_overlapping_pair_is_refused_on_the_page(config_dir, clean_registry, monkeypatch):
    """Two ranges that overlap are each their own sentence naming the row — the plan writes neither."""
    from services.webservice import scheduler_plan

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    rows = "week_start.0=00%3A00&week_end.0=12%3A00&week_days.0=YYYYYYY" \
           "&week_start.1=10%3A00&week_end.1=14%3A00&week_days.1=NNNNNNN"
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1", rows))

    assert scheduler_plan.WEEK_RANGE_OVERLAP in body
    assert "Row 2:" in body, "the sentence names the row it refuses"


@pytest.mark.asyncio
async def test_a_step_renders_in_the_visitors_language(config_dir, clean_registry, monkeypatch):
    """Every sentence on a step is wrapped: the German render shows the catalog's words."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = tab_body(await step_page(http, "1"))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = tab_body(await step_page(http, "1"))

    assert "When should the server run?" in english
    assert "Wann soll der Server laufen?" in german
    assert "When should the server run?" not in german
    assert "Die Woche" in german, "the step's own headings are translated too"
    # the week editor's own new strings, from the catalogs rather than the msgids
    for english_string, german_string in (("From", "Von"), ("To", "Bis"), ("Days", "Tage"),
                                          ("Remove row", "Zeile entfernen"),
                                          ("Add a row", "Zeile hinzufügen"),
                                          ("the machine's own local time",
                                           "die eigene Ortszeit des Rechners")):
        assert english_string in english, english_string
        assert german_string in german, german_string
    # the translated day names ride in the grid's attribute the buttons are built from
    assert 'data-week-days="Mo|Di|Mi|Do|Fr|Sa|So"' in german, "the button labels are translated"


# ------------------------------------------------- 6. the layout: row widths, glyphs, step 4's shapes

#: the plan step 4 renders with every optional part STATED, so the disclosures have something to show.
STATED_WARN_PLAN = {
    "timezone": "UTC",
    "warn": {"message": "!!! {item} will {what} in {when} !!!",
             "sound": "alarm.ogg",
             "times": [600, 60],
             "countdown": {"time": 10, "message": "{item} will {what} in {when}"}},
}


def row_chunk(body: str, css_class: str) -> str:
    """The FIRST element carrying *css_class* and everything up to the next such element."""
    chunks = body.split(f'class="{css_class}"')
    assert len(chunks) > 1, f"no {css_class} rendered"
    return chunks[1].split(f'class="{css_class}"')[0]


def trash_paths(html_text: str) -> list[str]:
    """Every path of the trashcan glyph inside the FIRST ``st-trash`` control of *html_text*."""
    head = html_text.split('class="st-trash', 1)
    assert len(head) > 1, "no trashcan control"
    svg = head[1].split("</button>")[0] if "</button>" in head[1] else head[1].split("</a>")[0]
    return re.findall(r'd="([^"]+)"', svg)


def stylesheet() -> str:
    return (detail_page.Path(__file__).parent.parent / "services" / "webservice" / "static"
            / "scheduler.css").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_step_1_gives_the_times_a_narrow_width_and_the_days_the_rest(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch):
    """One row, one line: two narrow-time controls, the day control taking the space left over."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "1"))

    row = row_chunk(body, "sw-wrow")
    assert row.count('class="sw-wlab sw-time"') == 2, \
        "both times carry the narrow class, so they cannot take the row's width"
    assert row.count('class="sw-wlab sw-days-col"') == 1, \
        "the day control carries the class that claims the remaining space"
    # the week's own fields ride in those two blocks, never the other way round
    for name in ('name="week_start.0"', 'name="week_end.0"'):
        assert name in row.split('class="sw-wlab sw-days-col"')[0], name
    assert 'name="week_days.0"' in row.split('class="sw-wlab sw-days-col"')[1], \
        "the 7-letter field is the control that gets the space"
    # the remove control is the row's LAST cell
    assert row.index("sw-days-col") < row.index("sw-rm")
    css = stylesheet()
    assert re.search(r"\.sw-wrow \.sw-time \{[^}]*flex: 0 0 \d+px", css), \
        "the narrow time column must be a FIXED width (no grow, no shrink)"
    assert re.search(r"\.sw-days \{[^}]*flex-wrap: nowrap", css), \
        "the seven day buttons may never split across two lines"


@pytest.mark.asyncio
async def test_every_row_removal_is_the_console_trashcan(config_dir, clean_registry, monkeypatch):
    """A row's removal is the glyph the tile foot uses: a submit button, named only in title/aria."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(STATED_WARN_PLAN,
                                     action=[{"times": ["04:00"], "method": "rotate"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        week = tab_body(await step_page(http, "1"))
        entries = tab_body(await step_page(http, "3"))
        ladder = tab_body(await step_page(http, "4"))

    for body in (week, entries, ladder):
        assert '<button type="submit" class="st-trash sw-rm"' in body
    # one per row, still a submit button of the SAME GET form, named in title and aria-label
    assert re.search(r'class="st-trash sw-rm" name="removerow" value="0" '
                     r'title="Remove row" aria-label="Remove row"', week)
    assert re.search(r'class="st-trash sw-rm" name="removerow" value="0" '
                     r'title="Remove row" aria-label="Remove row"', ladder)
    assert re.search(r'class="st-trash sw-rm" name="removeentry" value="0" '
                     r'title="Remove entry 1" aria-label="Remove entry 1"', entries)
    # NO visible remove wording: the word lives in the two attributes only
    assert ">Remove row<" not in week and ">Remove row<" not in ladder
    assert ">Remove entry 1<" not in entries
    # the drawing is the tile foot's own, path for path
    tiles = (detail_page.Path(__file__).parent.parent / "services" / "webservice" / "templates"
             / "_tiles.html").read_text(encoding="utf-8")
    macro = tiles.split("{% macro trash(url, title) -%}")[1].split("{%- endmacro %}")[0]
    assert trash_paths(week) == re.findall(r'd="([^"]+)"', macro), \
        "the row removal must wear the console's one trashcan, not a second drawing"
    # the stylesheet reuses the shipped affordance instead of restating it
    assert re.search(r"\.sw-rm \{[^}]*cursor: pointer", stylesheet())


@pytest.mark.asyncio
async def test_step_4_puts_the_countdown_on_one_line(config_dir, clean_registry, monkeypatch):
    """Seconds narrow, message taking the rest — one line, and the same help + preview as the message."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, STATED_WARN_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "4"))

    row = row_chunk(body, "sw-cdrow")
    assert 'name="warn.countdown.time"' in row, "the seconds box is part of the one countdown line"
    assert 'name="warn.countdown.message"' in row, "the message box is part of the same line"
    assert row.index('name="warn.countdown.time"') < row.index('name="warn.countdown.message"')
    css = stylesheet()
    assert re.search(r"\.sw-cdrow \{[^}]*grid-template-columns: \d+px minmax\(0, 1fr\)", css), \
        "a fixed narrow first column and a message column that takes the rest"
    # the SAME placeholder help and the SAME server-rendered preview as the warning message
    assert body.count('class="sw-ph" data-warn-chips') == 2
    assert 'data-warn-target="sw-warn-cdmsg"' in body
    assert body.count("Shown to every player; the bot fills the three names in.") == 2
    assert body.count('class="sw-prev-lab"') == 2, "both fields say what a player reads"
    assert "server will restart in 10 minutes" in body.split('class="sw-cdrow"')[1]


@pytest.mark.asyncio
async def test_step_4_hides_the_optional_parts_behind_the_shared_disclosure(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch):
    """Sound and Countdown are the website's OWN block (``editor.panel``), and a stated value opens it.

    The shared macro renders `<details class="st-block">` with a chevron glyph and a body in
    `.bd`, so an optional part opens the way a config section does on the other edit pages; a native
    disclosure keeps it working with the script off.
    """
    scheduler_file(config_dir)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        install_plugin(monkeypatch, STATED_WARN_PLAN)
        stated = tab_body(await step_page(http, "4"))
        install_plugin(monkeypatch, {"timezone": "UTC", "warn": {"message": "soon",
                                                                "times": [600]}})
        bare = tab_body(await step_page(http, "4"))

    for body in (stated, bare):
        assert body.count('<details class="st-block"') == 2, "one per optional part"
        heads = re.findall(r"<summary>(.*?)</summary>", body, re.S)
        assert len(heads) == 2, heads
        for head, word in zip(heads, ("Sound", "Countdown")):
            assert 'class="chev"' in head, "the shared macro's chevron"
            assert word in head, word
            assert "optional" in head, "the summary carries the optional marker"
        assert 'name="warn.sound"' in body and 'name="warn.countdown.time"' in body
        # the body sits in the macro's own wrapper, so the block is the shared one and not a lookalike
        assert body.count('<div class="bd">') >= 2
    # a stated value leaves nothing hidden
    assert stated.count('<details class="st-block" open>') == 2, "a stated part renders open"
    assert "open>" not in bare.split('<details class="st-block"')[1][:40], \
        "an absent part starts collapsed, and the summary is the control with the script off"

    css = stylesheet()
    assert ".sw-opt" not in css, "the wizard's own disclosure look is gone"


@pytest.mark.asyncio
async def test_step_4_renders_no_fill_control(config_dir, clean_registry, monkeypatch):
    """The one-click ladder is gone from the page entirely, with no field name left behind."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "UTC", "warn": {"message": "soon", "times": [600]}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "4"))

    assert "Fill 10, 5 and 1 minute" not in body
    assert not re.search(r'name="typical"', body)
    assert "600, 300, 60" not in body
    # the rows it would have filled are still added the ordinary way
    assert re.search(r'class="st-btn" name="addrow" value="1"', body)


# ------------------------------------- 7. step 2: the startup, optional and never two mission picks

def startup_card(body: str) -> str:
    """The startup card's own markup: from the switch to the end of its body."""
    start = body.index('<div class="sw-start">')
    return body[start:body.index('<aside class="st-aside">')]


def switch_of(body: str) -> str:
    """The ONE checkbox of step 2, tag and attributes — the control the step is built around."""
    match = re.search(r'<input type="checkbox" name="startup_on"[^>]*>', body)
    assert match, "the step must carry its one switch"
    return match.group(0)


def pick_wrapper(body: str, kind: str) -> str:
    """One of the two mission pickers, its wrapper included."""
    start = re.search(r'<div class="sw-pick[^"]*"[^>]*data-pick="' + kind + r'">', body)
    assert start, f"no picker for {kind}"
    tail = body[start.end():]
    stops = [tail.find(marker) for marker in ('<div class="sw-pick', 'data-preset-pick')]
    stops = [at for at in stops if at > -1]
    return body[start.start():start.end() + (min(stops) if stops else len(tail))]


@pytest.mark.asyncio
async def test_step_2_is_the_startup_and_nothing_else(config_dir, clean_registry, monkeypatch):
    """The rail, its blurb and the heading all read ``Startup`` — the events left this step."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        startup = tab_body(await step_page(http, "2"))
        actions = tab_body(await step_page(http, "3"))

    heading = re.search(r"<h1>(.*?)</h1>", startup, re.S).group(1).strip()
    assert heading == "Startup", heading
    rail = re.search(r'<div class="st-tabs">(.*?)</div>\s*<div class="st-bar">', startup, re.S).group(1)
    assert re.search(r'<span class="n">2</span>Startup<span', rail), rail
    assert "Startup &amp; events" not in rail, "the step states no events any more"
    assert "mission, presets" in rail and "commands" not in rail
    assert "Commands on events" not in startup, "the event card left this step"
    assert "Commands on events" in actions, "and is the actions step's own card"


@pytest.mark.asyncio
async def test_the_switch_is_one_checkbox_of_the_steps_own_form(config_dir, clean_registry,
                                                               monkeypatch):
    """One switch, its sentence, and the two consequences said in words — then the fields."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 3}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    card = startup_card(body)
    assert switch_of(card).count("checked") == 1, "the block states a value, so the box is checked"
    # the hidden 0 comes FIRST, so the browser posts the checkbox's own value last
    hidden_at = card.index('<input type="hidden" name="startup_on" value="0">')
    assert hidden_at < card.index('name="startup_on" value="1"')
    assert "Start a fixed mission every time this server starts" in card
    assert "Left off, the server starts the mission that ran last." in card
    assert "Saving with this off removes the startup keys" in card and "DEFAULT" in card, \
        "the deletion is stated, because that is what saving does"
    # every field the step would write sits BELOW the switch, inside the body it hides
    head, _, tail = card.partition('<div class="sw-startbody">')
    assert 'name="startup.' not in head, "no startup control escapes the switch"
    for name in ('name="startup.mission_id"', 'name="startup.settings"',
                 'name="startup.clear_maintenance"'):
        assert name in tail, name
    assert 'name="startup.password"' not in card, \
        "the startup block draws no password control: that field belongs to an action entry"


@pytest.mark.asyncio
async def test_a_startup_block_that_states_a_password_carries_it_without_a_control(config_dir,
                                                                                  clean_registry,
                                                                                  monkeypatch):
    """A stated ``startup.password`` rides hidden: no control, and its value still posts on a save."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 3,
                                                           "password": "carried-secret"}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    card = startup_card(body)
    assert 'name="startup.password"' in card, "the value is carried as a hidden field"
    assert 'type="text" name="startup.password"' not in card, "but never offered as an input"
    assert "Password set" not in card, "and no label stands beside it"
    assert re.search(r'<input type="hidden" name="startup\.password" value="carried-secret">', card)


@pytest.mark.asyncio
async def test_the_switch_opens_off_when_the_block_states_nothing(config_dir, clean_registry,
                                                                 monkeypatch):
    """No block = nothing ticked, and the fields stay outside the visible page."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {"timezone": "UTC", "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    card = startup_card(body)
    assert "checked" not in switch_of(card), "an absent block must not tick the box"
    # the browser's half: the stylesheet hides the body while the box is off, and the fields stay
    # in the DOM (a hidden field still posts its value) — which is why the ROUTE decides
    css = stylesheet()
    assert re.search(r"\.sw-start:not\(:has\(\.sw-startbox input:checked\)\) \.sw-startbody "
                     r"\{ display: none; \}", css), \
        "the step's body must be hidden by the shipped stylesheet, not by the render"
    assert '<div class="sw-startbody">' in card, "a script-less page must still hold the fields"


@pytest.mark.asyncio
async def test_the_switch_reads_the_post_rather_than_the_script(config_dir, clean_registry,
                                                                monkeypatch):
    """A posted ``0`` opens the step off and a posted ``1`` on — the script is never required."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 3}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        off = tab_body(await step_page(http, "2", "startup_on=0&startup.mission_id=3"))
        on = tab_body(await step_page(http, "2", "startup_on=0&startup_on=1&startup.mission_id=3"))

    assert "checked" not in switch_of(off), "the post wins over what the plan still carries"
    assert 'name="startup.mission_id"' in startup_card(off), \
        "the control is still drawn — the server, not the render, decides what is written"
    assert switch_of(on).count("checked") == 1, "the last value of the repeated name wins"


@pytest.mark.asyncio
async def test_the_two_mission_picks_are_one_radio_and_only_the_chosen_one_submits(
        config_dir, clean_registry, monkeypatch):
    """Both pickers are drawn; the radio names the one that posts, the other renders disabled."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 3}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        by_id = tab_body(await step_page(http, "2"))
        by_file = tab_body(await step_page(http, "2", "startup_pick=mission_file"))

    for body, chosen in ((by_id, "mission_id"), (by_file, "mission_file")):
        radios = re.findall(r'<input type="radio" name="startup_pick" value="([^"]*)"([^>]*)>', body)
        assert [value for value, _rest in radios] == ["mission_id", "mission_file"]
        assert "checked" in dict(radios)[chosen], body
        assert "checked" not in dict(radios)["mission_file" if chosen == "mission_id"
                                           else "mission_id"]
        other = "mission_file" if chosen == "mission_id" else "mission_id"
        assert "sw-pick-off" in pick_wrapper(body, other), "the pick not chosen is marked off"
        assert " disabled" in pick_wrapper(body, other), \
            "and its control cannot post a value"
        assert "sw-pick-off" not in pick_wrapper(body, chosen)
        assert " disabled" not in pick_wrapper(body, chosen), "the chosen control submits"
    assert "by mission ID" in by_id and "by mission file" in by_id
    assert "never both" in by_id, "the exclusivity is stated on the step"


@pytest.mark.asyncio
async def test_the_event_commands_ride_on_the_actions_step(config_dir, clean_registry, monkeypatch):
    """The four ``on*`` controls are edited on the actions step and carried hidden elsewhere."""
    from services.webservice import scheduler_plan

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pages = {step: tab_body(await step_page(http, step)) for step in ("1", "2", "3", "4")}

    for key in scheduler_plan.COMMAND_KEYS:
        assert f'name="{key}.command"' not in pages["2"], f"{key} left step 2"
        assert f'name="{key}.command"' in pages["3"], f"{key} is the actions step's control"
    # and they ride along hidden on the steps that do not own them
    for step in ("1", "2", "4"):
        carried = hidden_inputs(pages[step])
        assert carried.get("onShutdown") == "run:shutdown /r", step
        assert carried.get("shape.onShutdown") == "str", step


@pytest.mark.asyncio
async def test_the_startup_step_renders_in_the_visitors_language(config_dir, clean_registry,
                                                                monkeypatch):
    """The switch, its two consequences, the pick labels and the events lead come from the catalog."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 3}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = tab_body(await step_page(http, "2"))
        english_events = tab_body(await step_page(http, "3"))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = tab_body(await step_page(http, "2"))
        german_events = tab_body(await step_page(http, "3"))

    assert "Hochfahren" in german, "the step's own label is translated"
    for english_string, german_string in (
            ("Mission and presets", "Mission und Presets"),
            ("Start a fixed mission every time this server starts",
             "Starte bei jedem Start dieses Servers eine feste Mission"),
            ("Left off, the server starts the mission that ran last.",
             "Bleibt der Haken aus, startet der Server die Mission, die zuletzt lief."),
            ("Saving with this off removes the startup keys",
             "Beim Speichern mit ausgeschaltetem Haken"),
            ("by mission ID", "über die Missions-ID"),
            ("by mission file", "über die Missionsdatei"),
            ("never both", "nie auf beide")):
        assert english_string in english, english_string
        assert german_string in german, german_string
        assert english_string not in german, english_string
    assert "Each command runs when that event happens" in english_events
    assert "Jeder Befehl läuft, wenn dieses Ereignis eintritt" in german_events


# ------------------------------------- 3. one trigger, times as ROWS, options that follow the method

#: the ONE field each trigger's control posts. ``times`` posts rows, never a delimiter-separated
#: field, so its marker is the first row's own name.
TRIGGER_FIELDS = {
    "times": "action_time.0.0",
    "mission_time": "action.0.mission_time",
    "real_time": "action.0.real_time",
    "idle_time": "action.0.idle_time",
    "cron": "action.0.cron",
}

ONE_TRIGGER_PLANS = {
    "times": {"times": ["04:00"], "method": "restart"},
    "mission_time": {"mission_time": 480, "method": "restart"},
    "real_time": {"real_time": 240, "method": "restart"},
    "idle_time": {"idle_time": 30, "method": "restart"},
    "cron": {"cron": "0 4 * * *", "method": "restart"},
}


async def actions_body(config_dir, monkeypatch, entry, extra: str = "") -> str:
    """Step 3's own markup, with *entry* as the server's one action."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[dict(entry)]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        return tab_body(await step_page(http, "3", extra))


@pytest.mark.parametrize("chosen", list(TRIGGER_FIELDS))
@pytest.mark.asyncio
async def test_only_the_chosen_trigger_draws_a_control(config_dir, clean_registry, monkeypatch,
                                                       chosen):
    """Five radios of ONE name, and exactly one trigger's control on the page."""
    body = await actions_body(config_dir, monkeypatch, ONE_TRIGGER_PLANS[chosen])

    radios = re.findall(r'<input type="radio" name="action_trigger\.0" value="([^"]+)"([^>]*)>',
                        body)
    assert [value for value, _attrs in radios] == ["times", "mission_time", "real_time",
                                                   "idle_time", "cron"]
    assert [value for value, attrs in radios if "checked" in attrs] == [chosen]
    assert "When does this run?" in body
    assert f'name="{TRIGGER_FIELDS[chosen]}"' in body, chosen
    for other, field in TRIGGER_FIELDS.items():
        if other == chosen:
            continue
        assert f'name="{field}"' not in body, (chosen, other)
    # the delimiter-separated field is gone for every choice, and the row list is the times control
    assert 'name="action.0.times"' not in body
    assert ('type="time"' in body) is (chosen == "times")
    # the hard cap rides with mission time and with nothing else
    assert ('name="action.0.max_mission_time"' in body) is (chosen == "mission_time")


@pytest.mark.asyncio
async def test_times_are_one_row_per_time_and_never_a_delimiter_field(config_dir, clean_registry,
                                                                     monkeypatch):
    """A ``times`` entry draws one time input per row, each with its next fire, plus Add a time."""
    body = await actions_body(config_dir, monkeypatch,
                              {"times": ["04:00", "08:00"], "method": "restart"})

    rows = re.findall(r'<input class="sw-inp mono" type="time" name="(action_time\.0\.\d)"'
                      r'[^>]*value="([^"]*)"', body)
    assert rows == [("action_time.0.0", "04:00"), ("action_time.0.1", "08:00")]
    assert body.count('class="sw-trow"') == 2
    assert re.search(r'name="addtime"[^>]*value="0"', body), "a row is added from this step"
    assert re.search(r'name="removetime"[^>]*value="0\.\d"', body)
    fires = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", body)
    assert len(fires) >= 2, "each row still shows its next fire"
    assert 'name="action.0.times"' not in body, "never one comma-separated field"
    assert 'placeholder="04:00, 08:00"' not in body


@pytest.mark.asyncio
async def test_a_time_the_control_cannot_hold_and_a_time_it_refuses(config_dir, clean_registry,
                                                                   monkeypatch):
    """``24:00`` renders as ``00:00`` (the same instant), and a value that is no time says so."""
    midnight = await actions_body(config_dir, monkeypatch,
                                  {"times": ["24:00"], "method": "restart"})
    assert 'name="action_time.0.0"' in midnight
    assert 'value="00:00"' in midnight, "the control cannot hold 24:00"
    assert 'value="24:00"' not in midnight

    bad = await actions_body(config_dir, monkeypatch,
                             {"times": ["later"], "method": "restart"})
    assert "Row 1: " + scheduler_plan.TIME_NOT_A_TIME in bad


@pytest.mark.asyncio
async def test_adding_and_removing_a_time_row_round_trips(config_dir, clean_registry, monkeypatch):
    """``addtime`` appends an empty row, ``removetime`` drops the row it names — both on the GET form."""
    added = await actions_body(
        config_dir, monkeypatch, {"times": ["04:00"], "method": "restart"},
        extra="action_trigger.0=times&action.0.method=restart"
              "&action_time.0.0=04:00&addtime=0")
    rows = re.findall(r'name="(action_time\.0\.\d)"[^>]*value="([^"]*)"', added)
    assert rows == [("action_time.0.0", "04:00"), ("action_time.0.1", "")], rows

    removed = await actions_body(
        config_dir, monkeypatch, {"times": ["04:00"], "method": "restart"},
        extra="action_trigger.0=times&action.0.method=restart"
              "&action_time.0.0=04:00&action_time.0.1=&removetime=0.0")
    rows = re.findall(r'name="(action_time\.0\.\d)"[^>]*value="([^"]*)"', removed)
    assert rows == [("action_time.0.0", "")], rows


#: method -> the controls it draws. Every other control must be absent from the card.
METHOD_CONTROLS = {
    "restart": {"action.0.settings", "action.0.run_extensions", "action.0.use_orig",
                "action.0.password"},
    "rotate": {"action.0.settings", "action.0.run_extensions", "action.0.use_orig",
               "action.0.password"},
    "load": {"action.0.mission_id", "action.0.settings", "action.0.run_extensions",
             "action.0.use_orig", "action.0.no_reload", "action.0.password"},
    "stop": set(),
    "shutdown": set(),
}
ALL_METHOD_CONTROLS = set().union(*METHOD_CONTROLS.values())


@pytest.mark.parametrize("method", sorted(METHOD_CONTROLS))
@pytest.mark.asyncio
async def test_the_method_selects_which_mission_and_setting_controls_render(
        config_dir, clean_registry, monkeypatch, method):
    """One table: the method decides which secondary keys the card offers, exhaustively."""
    body = await actions_body(config_dir, monkeypatch,
                              {"cron": "0 4 * * *", "method": method})
    for name in ALL_METHOD_CONTROLS:
        if name in METHOD_CONTROLS[method]:
            assert f'name="{name}"' in body, (method, name)
        else:
            assert f'name="{name}"' not in body, (method, name)
    # every method keeps the trigger, the two "when nobody is flying" radios and the conditions
    assert 'name="action.0.cron"' in body
    assert 'name="action.0.populated"' in body
    assert 'name="action.0.mission_end"' in body
    assert 'name="action.0.coalition_win"' in body
    assert "The options below follow this choice" in body


#: the methods that (re)start a mission: the bot sets the server password right after loading one.
PASSWORD_METHODS = ("restart", "rotate", "load")


@pytest.mark.parametrize("method", sorted(METHOD_CONTROLS))
@pytest.mark.asyncio
async def test_only_a_method_that_starts_a_mission_draws_the_one_password_control(
        config_dir, clean_registry, monkeypatch, method):
    """One password field for restart/rotate/load, none for stop/shutdown — with its own wording."""
    body = await actions_body(config_dir, monkeypatch,
                              {"cron": "0 4 * * *", "method": method, "password": "hunter2"})
    fields = re.findall(r'<input[^>]*name="action\.0\.password"[^>]*>', body)

    if method in PASSWORD_METHODS:
        assert len(fields) == 1, (method, fields)
        assert 'value="hunter2"' in fields[0], (method, fields[0])
        assert 'autocomplete="off"' in fields[0], "a stored browser credential must not fill it"
        assert "Server password" in body, method
        assert "The server password is set when the mission starts." in body, method
    else:
        assert fields == [], (method, fields)
        assert "Server password" not in body, method
        assert "The server password is set when the mission starts." not in body, method
    # the field is never a startup one: that block draws no password control at all
    assert 'name="startup.password"' not in body.replace("action.0.password", "")


@pytest.mark.asyncio
async def test_the_password_field_states_the_file_and_never_a_live_secret(
        config_dir, clean_registry, monkeypatch):
    """The control shows only the plan's value: the running server's own password never reaches it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN,
                                     action=[{"cron": "0 4 * * *", "method": "restart",
                                              "password": "from-the-file"}]))
    hosts = cluster()
    live = hosts.servers[0]
    # the running server's own settings, the secret a live read would hand the page
    live.settings = {"password": "live-secret"}
    live.password = "live-secret"
    app = build(config_dir, hosts, plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    field = re.search(r'<input[^>]*name="action\.0\.password"[^>]*>', body)
    assert field, body[:2000]
    assert 'value="from-the-file"' in field.group(0), field.group(0)
    assert "live-secret" not in body, "a value read from the server's live settings was rendered"

    # and a plan that states none leaves the field empty rather than filling it from the server
    install_plugin(monkeypatch, dict(WIZARD_PLAN,
                                     action=[{"cron": "0 4 * * *", "method": "restart"}]))
    async with client(build(config_dir, hosts, plugins=("mission", "mods", "scheduler"))) as http:
        await sign_in(http, "specialk")
        empty = tab_body(await step_page(http, "3"))
    field = re.search(r'<input[^>]*name="action\.0\.password"[^>]*>', empty)
    assert field, empty[:2000]
    assert 'value=""' in field.group(0), field.group(0)
    assert "live-secret" not in empty


@pytest.mark.asyncio
async def test_a_password_carried_into_another_step_still_posts(config_dir, clean_registry,
                                                                monkeypatch):
    """The value rides as a hidden control on a step that does not own the action entries."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN,
                                     action=[{"cron": "0 4 * * *", "method": "load",
                                              "mission_id": 3, "password": "hunter2"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "4"))

    assert re.search(r'<input type="hidden" name="action\.0\.password" value="hunter2">', body)
    assert 'name="startup.password"' not in body


@pytest.mark.asyncio
async def test_a_load_names_its_mission_one_of_two_ways(config_dir, clean_registry, monkeypatch):
    """The two pickers are exclusive: one is live, the other is disabled so it posts nothing."""
    both = await actions_body(config_dir, monkeypatch,
                              {"cron": "0 4 * * *", "method": "load",
                               "mission_id": 3, "mission_file": "Missions/night.miz"})
    checked = [value for value, attrs in re.findall(
        r'<input type="radio" name="action_pick\.0" value="([^"]+)"([^>]*)>', both)
        if "checked" in attrs]
    assert checked == ["mission_id"], checked
    assert "A load names its mission one of the two ways, never both" in both
    head, _, tail = both.partition('data-pick="mission_file"')
    assert "disabled" not in head.rsplit('data-pick="mission_id"', 1)[-1], "the id pick is live"
    assert "disabled" in tail.split("</div>")[0], "the file pick cannot post while the id is chosen"

    only_file = await actions_body(config_dir, monkeypatch,
                                   {"cron": "0 4 * * *", "method": "load",
                                    "mission_file": "Missions/night.miz"})
    head, _, tail = only_file.partition('data-pick="mission_file"')
    assert "disabled" in head.rsplit('data-pick="mission_id"', 1)[-1], "the id pick is shut"
    assert "disabled" not in tail.split("</div>")[0]


@pytest.mark.asyncio
async def test_a_file_carrying_two_triggers_renders_one_and_says_so(config_dir, clean_registry,
                                                                   monkeypatch):
    """The bot reads times of day before cron, so that is the trigger the step shows and keeps."""
    body = await actions_body(config_dir, monkeypatch,
                              {"times": ["04:00"], "cron": "0 5 * * *", "method": "restart"})

    checked = [value for value, attrs in re.findall(
        r'<input type="radio" name="action_trigger\.0" value="([^"]+)"([^>]*)>', body)
        if "checked" in attrs]
    assert checked == ["times"], checked
    assert 'name="action_time.0.0"' in body
    assert 'name="action.0.cron"' not in body
    assert "This entry states more than one trigger" in body
    assert "the bot reads times of day first, then mission time, then server runtime, then idle " \
           "time, and cron last" in body
    # the one-trigger sentence is NOT shown for an entry that states a single trigger
    single = await actions_body(config_dir, monkeypatch, {"cron": "0 5 * * *", "method": "restart"})
    assert "This entry states more than one trigger" not in single
    assert "An entry states ONE of these" in single


#: the card's read-only marker and the words it must carry, once per shipped sentence.
LEGACY_MARK = 'data-action-legacy'
LEGACY_LEAD = ("This key is deprecated and this tab does not write it, so this entry cannot be "
               "retriggered here.")

#: the file's block for the legacy pins: the deprecated pair lives in the FILE — no form field ever
#: posts it, so a plan built from a previous step no longer states it — and the entry rides a map,
#: the shape a one-entry block has.
LEGACY_FILE = """\
Host-A-Instance:
  timezone: Europe/Berlin
  schedule:
    00-12: YYYYYYY
  action:
@ENTRY@"""

LOCAL_TIMES_ENTRY = """\
    local_times:
    - 04:00
    - 08:00
    method: restart
"""

UTC_TIMES_ENTRY = """\
    utc_times:
    - 16:00
    method: rotate
"""

LEGACY_PAIR_ENTRY = """\
    local_times:
    - 04:00
    utc_times:
    - 02:00
    - 18:00
    method: restart
"""

LEGACY_AND_TIMES_ENTRY = """\
    times:
    - 04:00
    local_times:
    - 05:00
    method: restart
"""


async def legacy_actions_body(config_dir, monkeypatch, action_lines: str, entry: dict) -> str:
    """Step 3's markup for a server whose FILE holds *action_lines* as its one action entry."""
    scheduler_file(config_dir, LEGACY_FILE.replace("@ENTRY@", action_lines))
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[dict(entry)]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        return tab_body(await step_page(http, "3"))


@pytest.mark.asyncio
async def test_a_legacy_only_entry_reads_as_legacy_and_draws_no_trigger_control(
        config_dir, clean_registry, monkeypatch):
    """An entry whose only trigger is ``local_times``: READ-ONLY, and not one radio or input.

    The bot still reads that key and wins over any trigger beside it, so a radio here would let the
    operator add a second trigger the old key overrides. The card therefore states the key, its
    value and why it cannot be retriggered, and offers the entry's remaining controls as usual.
    """
    raw = await legacy_actions_body(config_dir, monkeypatch, LOCAL_TIMES_ENTRY,
                                    {"local_times": ["04:00", "08:00"], "method": "restart"})
    body = html.unescape(raw)

    assert LEGACY_MARK in body, "a legacy-only entry must draw the read-only block"
    assert "This entry's trigger is a deprecated key" in body
    assert "local_times: 04:00, 08:00" in body, "the key AND its value are stated"
    assert LEGACY_LEAD in body
    assert "Everything else about this entry stays editable" in body
    # NO trigger radio and NO trigger input of any kind — a save cannot add a second trigger
    assert 'name="action_trigger.0"' not in body
    assert "When does this run?" not in body
    assert "An entry states ONE of these" not in body
    for field in TRIGGER_FIELDS.values():
        assert f'name="{field}"' not in body, field
    assert 'name="action.0.times"' not in body
    assert 'type="time"' not in body
    # it does NOT read as trigger-less: the sentence for a brand-new entry is the wrong one here
    assert "Nothing happens for this entry until a trigger is stated" not in body
    # the rest of the entry is editable, exactly as for any other entry
    assert 'name="action.0.method"' in body
    assert 'name="action.0.populated"' in body
    assert 'name="action.0.mission_end"' in body
    assert 'name="action.0.coalition_win"' in body


@pytest.mark.asyncio
async def test_the_deprecated_utc_pair_and_both_keys_render_the_same_way(config_dir, clean_registry,
                                                                       monkeypatch):
    """``utc_times`` alone and the pair together: one read-only line per key, in schema order."""
    utc = await legacy_actions_body(config_dir, monkeypatch, UTC_TIMES_ENTRY,
                                    {"utc_times": ["16:00"], "method": "rotate"})
    assert LEGACY_MARK in utc
    assert "utc_times: 16:00" in utc
    assert 'name="action_trigger.0"' not in utc

    both = await legacy_actions_body(config_dir, monkeypatch, LEGACY_PAIR_ENTRY,
                                     {"local_times": ["04:00"], "utc_times": ["02:00", "18:00"],
                                      "method": "restart"})
    lines = re.findall(r'<p class="mono">(local_times|utc_times): ([^<]*)</p>', both)
    assert lines == [("local_times", "04:00"), ("utc_times", "02:00, 18:00")], lines


@pytest.mark.asyncio
async def test_a_supported_trigger_beside_a_legacy_key_keeps_todays_behaviour(
        config_dir, clean_registry, monkeypatch):
    """Both keys: the supported trigger is the one shown and editable, and no read-only block."""
    body = await legacy_actions_body(config_dir, monkeypatch, LEGACY_AND_TIMES_ENTRY,
                                     {"times": ["04:00"], "local_times": ["05:00"],
                                      "method": "restart"})

    checked = [value for value, attrs in re.findall(
        r'<input type="radio" name="action_trigger\.0" value="([^"]+)"([^>]*)>', body)
        if "checked" in attrs]
    assert checked == ["times"], checked
    assert 'name="action_time.0.0"' in body, "the supported trigger keeps its own control"
    assert LEGACY_MARK not in body, "the card is editable, not read-only"
    assert "local_times" not in body, "the legacy key is neither drawn nor offered"


@pytest.mark.asyncio
async def test_the_legacy_entry_is_read_through_the_step_before_it(config_dir, clean_registry,
                                                                  monkeypatch):
    """Coming from step 2, the plan no longer states the key — the card reads the FILE for it.

    No form field ever carries ``local_times`` (the save refuses it where it occurs), so a step-3 card
    built from the previous step's fields alone would read the entry as trigger-less again and offer
    the very radios this card exists to withhold.
    """
    from urllib.parse import urlencode

    from test_webui_scheduler_save import form_pairs

    scheduler_file(config_dir, LEGACY_FILE.replace("@ENTRY@", LOCAL_TIMES_ENTRY))
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[
        {"local_times": ["04:00"], "method": "restart"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        step_two = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=2")
        assert step_two.status_code == 200, step_two.text[:300]
        pairs = form_pairs(step_two.text)
        carried = [name for name, _value in pairs if name.startswith("action.0.local_times")]
        assert not carried, "the deprecated key is never carried between steps"
        page = await http.get(f"/fleet/{SERVER_A}?{urlencode(pairs)}&step=3")

    assert page.status_code == 200, page.text[:300]
    body = html.unescape(page.text)
    assert LEGACY_MARK in body, "the card must still read the entry the file states"
    assert "local_times: 04:00" in body
    assert 'name="action_trigger.0"' not in body


@pytest.mark.asyncio
async def test_the_legacy_block_renders_in_the_visitors_language(config_dir, clean_registry,
                                                                monkeypatch):
    """The read-only state's three sentences come from the catalog, not from the msgid."""
    from services.webservice import i18n

    scheduler_file(config_dir, LEGACY_FILE.replace("@ENTRY@", LOCAL_TIMES_ENTRY))
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[
        {"local_times": ["04:00"], "method": "restart"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = html.unescape(tab_body(await step_page(http, "3")))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = html.unescape(tab_body(await step_page(http, "3")))

    for english_string, german_string in (
            ("This entry's trigger is a deprecated key",
             "Der Auslöser dieses Eintrags ist ein veralteter Schlüssel"),
            (LEGACY_LEAD,
             "Dieser Schlüssel ist veraltet und dieser Tab schreibt ihn nicht, daher kann dieser "
             "Eintrag hier nicht neu ausgelöst werden."),
            ("Everything else about this entry stays editable",
             "Alles andere an diesem Eintrag bleibt bearbeitbar")):
        assert english_string in english, english_string
        assert german_string in german, english_string
    # the key's own name is operator data and stays as the file spells it in both languages
    assert "local_times: 04:00" in english
    assert "local_times: 04:00" in german


@pytest.mark.asyncio
async def test_the_actions_step_renders_in_the_visitors_language(config_dir, clean_registry,
                                                                monkeypatch):
    """The step's name, its trigger radios and its sentences come from the catalog."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[
        {"times": ["04:00"], "cron": "0 5 * * *", "method": "load", "mission_file": "a.miz"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = tab_body(await step_page(http, "3"))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = tab_body(await step_page(http, "3"))

    assert "Actions (restart, rotate, etc.)" in english
    assert "Aktionen (Neustart, Rotation usw.)" in german
    for english_string, german_string in (
            ("one trigger, options by method", "ein Auslöser, Optionen je Methode"),
            ("When does this run?", "Wann läuft das?"),
            ("Times of day", "Tageszeiten"),
            ("Minutes of mission time", "Minuten Missionszeit"),
            ("Minutes of server runtime", "Minuten Serverlaufzeit"),
            ("Minutes with nobody on", "Minuten ohne Spieler"),
            ("Cron expression", "Cron-Ausdruck"),
            ("Add a time", "Zeit hinzufügen"),
            ("An entry states ONE of these", "Ein Eintrag nennt GENAU EINES davon"),
            ("This entry states more than one trigger",
             "Dieser Eintrag nennt mehr als einen Auslöser"),
            ("The bot reads times of day first".lower(),
             "der Bot liest zuerst Tageszeiten"),
            ("A load names its mission one of the two ways, never both",
             "Ein Load nennt seine Mission auf eine der beiden Arten, nie auf beide"),
            ("The options below follow this choice", "Die Optionen unten folgen dieser Wahl")):
        assert english_string in english, english_string
        assert german_string in german, german_string
        assert english_string not in german, english_string
