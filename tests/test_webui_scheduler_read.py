"""The Scheduler tab's READ model: the effective plan, and where each key's value came from.

Two reads, never a third merge. The EFFECTIVE plan is the plugin's own ``get_config`` — the merge the
bot applies — and the FILE is read a second time only to say which keys the server owns and which it
inherits from ``DEFAULT``. The pins here are exactly that split:

* the shapes (``block_path`` / ``raw_block`` / ``key_states`` / ``schedule_rows`` / ``action_rows`` /
  ``warn_view`` / ``startup_rows`` / ``command_rows``) are pure and are checked without an
  application;
* the rendered landing is checked for the four states — a server with its own block, one that
  inherits everything, a missing plugin and a missing file — through the real app;
* and one pin proves the page shows the PLUGIN's answer rather than a parse of the file: the double's
  plan disagrees with the file on purpose, and the page must show the double's value.
"""
from __future__ import annotations

import re
from html import unescape

import pytest

from services.webservice import scheduler_plan as plan
from services.webservice.pages import server_detail as detail_page
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures imported on purpose
    INSTANCE_A, SERVER_A, SCHEDULER_YAML, FakeScheduler, cluster, install_plugin, scheduler_file,
    tab_body,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, open_page, sign_in,
)

NODE_A = "dcs-a-01"

DOCUMENT = {
    "DEFAULT": {"startup_delay": 10,
                "warn": {"message": "!!! {item} will {what} in {when} !!!", "times": [600, 60]}},
    INSTANCE_A: {"timezone": "Europe/Berlin",
                 "schedule": {"timezone": "Europe/Berlin", "00-12": "YYYYYYY", "12-24": "NNNNNNN"},
                 "action": {"times": ["04:00"], "method": "rotate", "populated": False}},
}


# ------------------------------------------------------------------------ 1. which block, and from where

def test_the_block_path_is_the_one_the_plugin_reads():
    """``(instance,)`` for a flat file, ``(node, instance)`` when the file nests under a node key."""
    assert plan.block_path(DOCUMENT, NODE_A, INSTANCE_A) == (INSTANCE_A,)
    nested = {"DEFAULT": {}, NODE_A: {INSTANCE_A: {"timezone": "UTC"}}}
    assert plan.block_path(nested, NODE_A, INSTANCE_A) == (NODE_A, INSTANCE_A)
    # no block at all: the server inherits everything
    assert plan.block_path({"DEFAULT": {}}, NODE_A, "someone-else") == ()
    # a document that is not a mapping is a miss, never a crash
    assert plan.block_path(None, NODE_A, INSTANCE_A) == ()


def test_the_raw_and_default_blocks_are_the_file_s():
    assert plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A)["timezone"] == "Europe/Berlin"
    assert plan.default_block(DOCUMENT)["startup_delay"] == 10
    assert plan.raw_block(DOCUMENT, NODE_A, "nobody") == {}
    assert plan.default_block({}) == {}


def test_every_element_key_is_classified_own_inherited_or_unset():
    """The landing's central fact: per key, the server's own block or ``DEFAULT``, never a guess."""
    effective = plan.default_block(DOCUMENT) | plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A)
    states = {row["key"]: row["state"] for row in
              plan.key_states(plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A),
                              plan.default_block(DOCUMENT))}

    assert set(states) == set(plan.ELEMENT_KEYS), "every schema key must be classified"
    assert states["timezone"] == plan.OWN
    assert states["schedule"] == plan.OWN
    assert states["warn"] == plan.INHERITED
    assert states["startup_delay"] == plan.INHERITED
    # neither block declares it, and the merge has nothing: no chip claims a source
    assert states["onShutdown"] == plan.UNSET


def test_a_server_with_no_block_of_its_own_inherits_every_key_it_has():
    effective = plan.default_block(DOCUMENT)
    states = plan.key_states({}, plan.default_block(DOCUMENT))
    assert [row["state"] for row in states if row["key"] in effective] == [plan.INHERITED] * 2
    view = plan.plan_view(effective, {}, plan.default_block(DOCUMENT))
    assert view["all_inherited"] is True, "a wholly inherited plan is the flag's one job"
    assert view["inherited_count"] == 2


# ---------------------------------------------------------------------------------- 2. the shapes

def test_the_week_grid_skips_the_timezones_own_key():
    """``timezone`` may sit INSIDE the ``schedule`` block; it is not a range and is not drawn as one."""
    rows = plan.schedule_rows(DOCUMENT[INSTANCE_A]["schedule"])
    assert [row["period"] for row in rows] == ["00-12", "12-24"]
    first = rows[0]
    assert len(first["cells"]) == len(plan.WEEKDAYS) == 7
    assert [(cell["letter"], cell["state"]) for cell in first["cells"][:2]] == [("Y", "y"), ("Y", "y")]
    assert rows[1]["cells"][0]["state"] == "n"


def test_a_day_the_pattern_does_not_cover_is_not_scheduled():
    rows = plan.schedule_rows({"00-12": "YN"})
    states = [cell["state"] for cell in rows[0]["cells"]]
    assert states[:2] == ["y", "n"]
    assert states[2:] == ["x"] * 5
    assert plan.NOT_SCHEDULED in [cell["word"] for cell in rows[0]["cells"]]


def test_an_action_entry_keeps_the_shape_the_file_holds():
    """One ``action_map`` is ONE entry; a sequence is one entry per item — never normalised."""
    one = plan.action_rows({"times": ["04:00", "08:00"], "method": "rotate"})
    assert len(one) == 1
    assert one[0]["method"] == "Rotate to the next mission"
    assert one[0]["trigger"]["params"] == {"value": "04:00, 08:00"}

    seq = plan.action_rows([{"method": "restart", "mission_time": 480, "populated": False},
                            {"method": "stop", "idle_time": 30, "populated": True}])
    assert [row["method"] for row in seq] == ["Restart the mission", "Stop the server"]
    assert seq[0]["trigger"]["params"] == {"value": "480"}
    assert seq[0]["conditions"] == ["only when nobody is flying"]
    assert seq[1]["conditions"] == [], "``populated: true`` is not a condition, it is the default"
    assert plan.action_rows(None) == []


def test_an_action_entry_reports_its_extra_flags_and_cron_trigger():
    rows = plan.action_rows({"cron": "0 */6 * * *", "method": "restart", "shutdown": True,
                             "use_orig": True})
    assert rows[0]["trigger"]["params"] == {"value": "0 */6 * * *"}
    assert "on the cron schedule" in rows[0]["trigger"]["msgid"]
    assert rows[0]["extras"] == ["shut the computer down afterwards",
                                 "use the original mission file"]


def test_the_warning_ladder_reads_both_shapes_of_times():
    """``times`` is ``seq_or_map``: a list of seconds, or second -> its own message."""
    seq = plan.warn_view({"message": "m", "times": [600, 60], "sound": "alarm.ogg"})
    assert [step["seconds"] for step in seq["ladder"]] == ["600", "60"]
    assert seq["ladder"][0]["message"] == ""
    assert seq["sound"] == "alarm.ogg"

    keyed = plan.warn_view({"times": {600: "ten minutes", 60: "one minute"}})
    assert [(step["seconds"], step["message"]) for step in keyed["ladder"]] == [
        ("600", "ten minutes"), ("60", "one minute")]

    assert plan.warn_view({"countdown": {"time": 10, "message": "10"}})["countdown"]["time"] == "10"
    assert plan.warn_view(None) is None
    assert plan.warn_view({}) is None


def test_the_startup_summary_and_the_four_event_commands():
    rows = plan.startup_rows({"mission_id": 3, "clear_maintenance": True, "presets": ["a", "b"]})
    assert [row["key"] for row in rows] == ["mission_id", "presets", "clear_maintenance"], \
        "the summary follows the schema's key order, not the file's"
    assert rows[0]["value"] == "3"
    assert rows[1]["value"] == "a, b"
    assert rows[2]["value"] == "yes"

    commands = plan.command_rows({"onSimulationStart": "load:Scripts/net/start.lua",
                                  "onShutdown": {"command": "run:shutdown /r", "sleep": 10}})
    assert [row["key"] for row in commands] == ["onSimulationStart", "onSimulationStop",
                                                "onMissionEnd", "onShutdown"]
    assert commands[0]["command"] == "load:Scripts/net/start.lua"
    assert commands[1]["command"] == "", "an event with nothing set is READ as nothing set"
    assert (commands[3]["command"], commands[3]["sleep"]) == ("run:shutdown /r", "10")


def test_an_empty_plan_is_the_empty_test():
    assert plan.has_plan({}) is False
    assert plan.has_plan(None) is False
    assert plan.has_plan({"timezone": "UTC"}) is True


# ------------------------------------------------------------------- 3. the rendered landing's states

def rendered(body: str) -> str:
    """The body as the BROWSER reads it: Jinja escapes an apostrophe, the sentence does not have one."""
    return unescape(body)


async def landing(http, server=SERVER_A):
    response = await http.get(f"/fleet/{server}?tab=scheduler")
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


@pytest.mark.asyncio
async def test_a_server_with_its_own_block_marks_own_and_inherited_keys(config_dir, clean_registry,
                                                                      monkeypatch):
    scheduler_file(config_dir)
    # the plugin's answer: the merge of DEFAULT and the server's block, as the bot computes it
    effective = plan.default_block(DOCUMENT) | plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A)
    install_plugin(monkeypatch, effective)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    own = set(re.findall(r'data-own="([^"]+)"', body))
    inherited = set(re.findall(r'data-inherited="([^"]+)"', body))
    assert own == {"timezone", "schedule", "action"}
    assert inherited == {"warn", "startup_delay"}
    assert not own & inherited, "a key cannot be both the server's own and inherited"
    assert "no block of its own" not in body, "this server HAS a block"


@pytest.mark.asyncio
async def test_a_server_that_inherits_everything_marks_every_key_inherited(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch):
    """SRS-B has no block: the plan is ``DEFAULT``'s, and every key it carries says so."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan.default_block(DOCUMENT))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http, "SRS-B")

    body = tab_body(html)
    assert set(re.findall(r'data-own="([^"]+)"', body)) == set()
    assert set(re.findall(r'data-inherited="([^"]+)"', body)) == {"warn", "startup_delay"}
    assert "no block of its own" in body, "the bar must say the server owns no block"


@pytest.mark.asyncio
async def test_the_page_shows_the_plugins_merge_not_a_parse_of_the_file(config_dir, clean_registry,
                                                                       monkeypatch):
    """The double's plan DISAGREES with the file: the page must show the double's value.

    This is the pin that separates the two reads. The file says ``Europe/Berlin``; the plugin's own
    answer says ``UTC``. A page that parsed the YAML itself would show Berlin and pass every other
    assertion in this module.
    """
    scheduler_file(config_dir)
    effective = plan.default_block(DOCUMENT) | plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A)
    effective = dict(effective) | {"timezone": "UTC"}
    install_plugin(monkeypatch, effective)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert "UTC" in body
    assert "Europe/Berlin" not in body


@pytest.mark.asyncio
async def test_the_missing_plugin_explains_itself_and_offers_no_plan(config_dir, clean_registry,
                                                                    monkeypatch):
    scheduler_file(config_dir)
    monkeypatch.setattr(detail_page, "_scheduler", lambda: None)
    app = build(config_dir, cluster(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert detail_page.SCHEDULER_NO_PLUGIN in rendered(body)
    assert '<div class="sw-week">' not in body, "a state with no plan must draw no plan"
    assert detail_page.SCHEDULER_SETTINGS_LINK in html
    assert f'href="/settings/plugin/scheduler"' in body


@pytest.mark.asyncio
async def test_the_missing_file_explains_itself_and_links_to_settings(config_dir, clean_registry,
                                                                     monkeypatch):
    """No scheduler file on the install: the Settings area's own 404 becomes a sentence, not a page."""
    install_plugin(monkeypatch, {"timezone": "UTC"})
    app = build(config_dir, cluster(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert detail_page.SCHEDULER_NO_FILE in rendered(body)
    assert '<div class="sw-week">' not in body
    assert 'href="/settings"' in body, "the way to Settings is the whole point of the state"


@pytest.mark.asyncio
async def test_a_plugin_that_cannot_answer_is_an_empty_state_not_a_500(config_dir, clean_registry,
                                                                      monkeypatch):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, RuntimeError("the plan exploded"))
    app = build(config_dir, cluster(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert detail_page.SCHEDULER_NO_PLAN in rendered(body)
    assert '<div class="sw-week">' not in body


@pytest.mark.asyncio
async def test_an_unreadable_file_claims_no_source_for_any_value(config_dir, clean_registry,
                                                                monkeypatch):
    """A file that cannot be parsed costs the PROVENANCE, not the plan — and no chip is invented.

    "No block of its own" is an observation about a file that was read; with an unreadable file the
    absence of a block is unknown, so the bar must not say it and no key may be marked inherited.
    """
    scheduler_file(config_dir, "DEFAULT: [this is not a map\n  bad: {\n")
    effective = plan.default_block(DOCUMENT) | plan.raw_block(DOCUMENT, NODE_A, INSTANCE_A)
    install_plugin(monkeypatch, effective)
    app = build(config_dir, cluster(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert '<div class="sw-week">' in body, "the plan itself must still render"
    assert "could not be read a second time for provenance" in rendered(body)
    assert 'data-inherited="' not in body and 'data-own="' not in body
    assert "no block of its own" not in rendered(body)


@pytest.mark.asyncio
async def test_a_plan_with_nothing_in_it_says_so(config_dir, clean_registry, monkeypatch):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, {})
    app = build(config_dir, cluster(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await landing(http)

    body = tab_body(html)
    assert detail_page.SCHEDULER_EMPTY in rendered(body)
    assert '<div class="sw-week">' not in body
