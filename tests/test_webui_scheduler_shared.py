"""The shared DEFAULT section: who may open it, what it renders, and what a save writes.

A hoster states a regular nightly restart ONCE, in the file's ``DEFAULT`` block, and every server
that does not set its own value follows it. The switch that reaches that block from the wizard is the
Admin's alone — a manager owns their own server's schedule and nothing wider — so the two halves are
pinned separately: the RENDER (the switch exists for an Admin, on the three steps whose values are
worth sharing, and never for anyone else) and the WRITE (a flagged save moves ``DEFAULT`` and leaves
every server's own block byte-identical).
"""
from __future__ import annotations

import pytest

from services.webservice import scheduler_plan
from services.webservice.pages import settings as settings_page
from test_webui_scheduler_guard import (  # noqa: F401 - fixtures imported on purpose
    ADMIN, INSTANCE_A, MANAGER, SERVER_A, app_for, cluster, clean_registry, config_dir, digest,
    node_data, post_save, scheduler_path, scope_dir, token,
)
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures imported on purpose
    SCHEDULER_YAML, build, client, install_plugin, scheduler_file, sign_in, step_page, tab_body,
)


def plan_for_server() -> dict:
    """What the plugin answers for SERVER_A: its own block, merged — never ``DEFAULT``'s values."""
    return {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY", "12-24": "NNNNNNN"}}


async def open_step(http, step: str, extra: str = ""):
    return tab_body(await step_page(http, step, extra))


# ============================================================== 1. the switch, step by step

@pytest.mark.asyncio
@pytest.mark.parametrize("step, drawn", [("1", True), ("3", True), ("4", True), ("2", False)])
async def test_the_switch_is_drawn_on_the_three_shared_steps_and_not_on_startup(
        config_dir, clean_registry, monkeypatch, step, drawn):
    """Week schedule, actions, warnings — the values a hoster sets once. Startup stays per server."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_for_server())
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        body = await open_step(http, step)

    assert (scheduler_plan.SHARED_LABEL in body) is drawn, step
    if drawn:
        assert (f'name="{scheduler_plan.SHARED_TOGGLE_FIELD}" '
                f'value="{scheduler_plan.SHARED_ON}"') in body, "a submit, not a checkbox"
        assert scheduler_plan.SHARED_HINT in body, "the consequence is stated where the press is"


@pytest.mark.asyncio
async def test_switching_to_the_shared_default_shows_the_default_block_not_the_servers(
        config_dir, clean_registry, monkeypatch):
    """The press re-seeds the plan from ``DEFAULT``: the server's own timezone must not appear."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_for_server())
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        body = await open_step(http, "1", extra=f"{scheduler_plan.SHARED_TOGGLE_FIELD}="
                                                f"{scheduler_plan.SHARED_ON}")

    assert "Editing <b>DEFAULT</b>" in body, "the bar says which section is being edited"
    assert 'name="_shared_default" value="1"' in body, "and the target rides every later step"
    assert '<option value="Europe/Berlin" selected' not in body, \
        "the server's own values are not what DEFAULT states"


@pytest.mark.asyncio
async def test_the_review_step_warns_that_the_save_goes_to_every_server(
        config_dir, clean_registry, monkeypatch):
    """The lead sentence is the last place to notice a hoster is about to move every server."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_for_server())
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, ADMIN)
        body = tab_body(await step_page(http, "5", f"{scheduler_plan.SHARED_FIELD}=1"))

    assert "This saves to DEFAULT" in body
    assert 'name="_shared_default" value="1"' in body, "the review form carries the same target"


# ============================================================== 2. the write

@pytest.mark.asyncio
async def test_a_shared_save_writes_the_default_block_and_leaves_every_server_alone(
        scope_dir, node_data, clean_registry):
    """One press, one block: ``DEFAULT`` changes, both servers keep exactly what they stated."""
    app = app_for(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "_shared_default": "1",
                                       "timezone": "Europe/Paris"})
    assert reply.status_code == 303, reply.text[:300]
    text = scheduler_path(scope_dir).read_text(encoding="utf-8")
    shared, _, rest = text.partition(f"{INSTANCE_A}:")
    assert "timezone: Europe/Paris" in shared, "the DEFAULT block took the value"
    assert "timezone: Europe/Berlin" in rest, "the server's own block is untouched"
    assert "# the install's scheduler configuration" in text, "the header comment survives"
    assert "startup_delay: 10" in text, "a key this tab does not own survives in DEFAULT"


@pytest.mark.asyncio
async def test_a_plain_save_still_writes_only_the_servers_block(scope_dir, node_data,
                                                              clean_registry):
    """The unflagged door is unchanged: the DEFAULT block keeps its own values."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "timezone": "Europe/Paris"})
    assert reply.status_code == 303, reply.text[:300]
    text = scheduler_path(scope_dir).read_text(encoding="utf-8")
    shared, _, rest = text.partition(f"{INSTANCE_A}:")
    assert "Europe/Paris" not in shared, "DEFAULT was not touched"
    assert "timezone: Europe/Paris" in rest, "the server's block took the value"
    assert digest(scope_dir) != before


@pytest.mark.asyncio
async def test_the_shared_switch_is_refused_for_a_manager(scope_dir, node_data, clean_registry):
    """The capability, not the UI: a manager who posts the flag writes nothing."""
    app = app_for(scope_dir)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, MANAGER)
        token_text = await token(http)
        reply = await post_save(http, {"_csrf_token": token_text, "_scope": SERVER_A,
                                       "_shared_default": "1", "timezone": "Europe/Paris"})
    assert reply.status_code == 303, "a refusal is a notice, never a raw 403"
    assert digest(scope_dir) == before, "nothing was written"

