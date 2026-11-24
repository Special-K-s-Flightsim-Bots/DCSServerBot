"""The shared DEFAULT state must be impossible to be in by accident, or to be stuck in.

Two failures this pins: an operator who pressed the shared button and cannot find the way back to the
server's own block, and a state that only whispers — a line of grey hint text where the consequence
is that every server that inherits now restarts at the time being edited.
"""
from __future__ import annotations

import pytest

from services.webservice import scheduler_plan
from test_webui_scheduler_shared import plan_for_server
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, cluster, config_dir, install_plugin, scheduler_file, sign_in,
    step_page, tab_body,
)

SHARED_ON = f"{scheduler_plan.SHARED_FIELD}=1"


async def page_at(http, step: str, extra: str = "") -> str:
    return tab_body(await step_page(http, step, extra))


@pytest.fixture
def app_with_file(config_dir, clean_registry, monkeypatch):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan_for_server())
    return build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["1", "2", "3", "4"])
async def test_a_shared_step_states_it_in_an_alert_and_offers_the_way_back(app_with_file, step):
    """EVERY step, startup included: its values reach DEFAULT through the same save."""
    async with client(app_with_file) as http:
        await sign_in(http, "specialk")
        body = await page_at(http, step, SHARED_ON)

    assert 'class="sw-shared-alert"' in body, "a band, not a grey hint"
    assert scheduler_plan.SHARED_LABEL in body, "it names the state"
    assert scheduler_plan.SHARED_HINT in body, "and what the state does"
    assert f'value="{scheduler_plan.SHARED_OFF}"' in body, "the way back is on this page"
    assert body.count(f'value="{scheduler_plan.SHARED_OFF}"') == 1, "exactly one way back"


@pytest.mark.asyncio
async def test_the_way_back_is_the_same_control_that_led_in(app_with_file):
    """Unshared: the button that enters the state. Shared: the same worth of button, going back."""
    async with client(app_with_file) as http:
        await sign_in(http, "specialk")
        plain = await page_at(http, "1")
        shared = await page_at(http, "1", SHARED_ON)

    assert 'class="sw-shared-alert"' not in plain, "the unshared step raises no alarm"
    assert f'value="{scheduler_plan.SHARED_ON}"' in plain and \
           scheduler_plan.SHARED_LABEL in plain, "and offers the way in"
    assert f'value="{scheduler_plan.SHARED_ON}"' not in shared, \
        "once shared, the way in is not offered again"


@pytest.mark.asyncio
async def test_the_review_step_keeps_the_alert_and_links_back(app_with_file):
    """The review form POSTs to the save route, so leaving it must be a LINK, never a submit."""
    async with client(app_with_file) as http:
        await sign_in(http, "specialk")
        body = await page_at(http, "5", SHARED_ON)

    assert 'class="sw-shared-alert"' in body
    assert "This saves to DEFAULT" in body, "the sentence the alert carries on this step"
    assert 'class="st-btn warn" href="' in body, "a link out of the alarm, not a button"
    assert f'value="{scheduler_plan.SHARED_OFF}"' not in body, \
        "a submit here would be read as a save of the section it left"
