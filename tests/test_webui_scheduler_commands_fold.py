"""The four event commands are folded shut on the Actions step, and still post.

A bot command is expert material: a hoster opening the step to move a restart should not meet four
command fields first. ``<details>`` is presentation only — every field stays inside the form, so the
values a file states survive a save whether or not the fold was ever opened. Pinned together,
because folding a card by moving its fields OUT of the form would look identical on the page and
silently drop every command on the next save.
"""
from __future__ import annotations

import pytest

from services.webservice import scheduler_plan
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures imported on purpose
    SERVER_A, build, clean_registry, client, cluster, config_dir, install_plugin, scheduler_file,
    sign_in, step_page, tab_body,
)


def plan_with_commands() -> dict:
    return {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"},
            "onSimulationStart": {"command": "load:Scripts/net/start.lua", "sleep": 30}}


@pytest.mark.asyncio
async def test_the_commands_card_is_folded_shut_and_still_carries_its_fields(
        config_dir, clean_registry, monkeypatch):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_with_commands())
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    assert '<details class="card sw-fold">' in body, "a fold, not a card"
    assert "<details class=\"card sw-fold\" open" not in body, "shut until the operator opens it"
    assert "Commands on events" in body
    assert f'name="onSimulationStart.command" value="load:Scripts/net/start.lua"' in body, \
        "the field is inside the fold AND inside the form, so it posts either way"
    assert 'name="onSimulationStart.sleep" value="30"' in body, "the delay rides with it"


@pytest.mark.asyncio
async def test_the_folded_header_says_whether_anything_is_stated(
        config_dir, clean_registry, monkeypatch):
    """The count is why the fold can be shut: an operator still sees what is behind it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_with_commands())
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        stated = tab_body(await step_page(http, "3"))

    install_plugin(monkeypatch, {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY"}})
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        empty = tab_body(await step_page(http, "3"))

    assert "1 set" in stated
    assert "none set" in empty
    assert scheduler_plan.COMMAND_KEYS[0] in stated
