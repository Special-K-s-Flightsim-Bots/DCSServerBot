"""The busy state of the tour's two slow submits — the Database step and the Discord fetch.

The wait the operator reported ("Continue takes a bit") is made VISIBLE: while a submit is in
flight the pressed button says what it is doing (a translated label — "Creating the database…",
"Testing the connection…", "Continuing…", "Connecting to Discord…") and every submit control of the
form is disabled, so a double press cannot put two requests on the wire. The state is a CLIENT-SIDE
enhancement over a plain form POST (the setup tour has no server-side action seam to read), it fakes
nothing, and with scripting off the served page is exactly the one it always was.

Two layers, both over the shipped code:

* the VIEW: real ASGI requests against the shipped setup application, asserting the ``data-busy``
  attribute each submit carries (translated) and that the page claims no busy state of its own;
* the SCRIPT: the shipped inline text driven against a minimal DOM (``busy_submit_harness.js``), so
  the label change, the form lock, the double-submit refusal and the value mirror are the shipped
  behaviour and not a description of it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import asgi_client, run_node_harness
from services.webservice.setup import tour as tour_step
from services.webservice.setup.tour import LANGUAGE_PATH, STEP_PATH
from test_webui_setup_database import build, enter, seed_step_three, token_of  # noqa: F401
from test_webui_setup_discord import seed as seed_discord

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "busy_submit_harness.js"


def _busy_body() -> str:
    """The shipped inline busy script, without its ``<script>`` tags (the harness runs the body)."""
    script = tour_step._busy_submit_script()
    assert script.startswith("<script>") and script.endswith("</script>")
    return script[len("<script>"):-len("</script>")]


def _busy_button(html: str, label: str) -> str:
    """The opening tag of the submit button that carries ``data-busy`` *label*."""
    found = re.search(r'<button[^>]*data-busy="' + re.escape(label) + r'"[^>]*>', html)
    assert found, f"no submit button carries data-busy={label!r}"
    return found.group(0)


# --------------------------------------------------------------------------- the Database step

@pytest.mark.asyncio
async def test_the_create_paths_test_button_wears_the_create_busy_label(tmp_path):
    seed_step_three(tmp_path)                       # the create path is the default
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    tag = _busy_button(html, "Creating the database…")
    assert 'value="test"' in tag, tag


@pytest.mark.asyncio
async def test_the_existing_paths_test_button_wears_the_test_busy_label(tmp_path):
    seed_step_three(tmp_path, path="existing")
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    tag = _busy_button(html, "Testing the connection…")
    assert 'value="test"' in tag, tag


@pytest.mark.asyncio
async def test_the_continue_button_wears_its_own_busy_label(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    tag = _busy_button(html, "Continuing…")
    assert 'value="continue"' in tag, tag


# --------------------------------------------------------------------------- the Discord step

@pytest.mark.asyncio
async def test_the_discord_continue_wears_the_fetch_busy_label(tmp_path):
    seed_discord(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "5"})).text
    tag = _busy_button(html, "Connecting to Discord…")
    assert tag.startswith('<button type="submit"'), tag


# --------------------------------------------------------------------------- translated

@pytest.mark.asyncio
async def test_the_busy_labels_are_translated(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        await client.get(LANGUAGE_PATH, params={"lang": "de", "next": f"{STEP_PATH}?n=3"})
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert 'data-busy="Datenbank wird erstellt…"' in html
    assert 'data-busy="Fortfahren…"' in html


# --------------------------------------------------------------------------- the served markup

@pytest.mark.asyncio
async def test_the_page_serves_the_very_script_the_harness_drives(tmp_path):
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    assert tour_step._busy_submit_script() in html


@pytest.mark.asyncio
async def test_the_served_page_claims_no_busy_state_of_its_own(tmp_path):
    """With scripting off the page is the one it always was: no server-side busy claim, no bar.

    The busy state is created by the script; the markup only carries the label as a ``data-busy``
    attribute (inert without a script) plus ordinary, enabled submits — so a JS-off browser shows
    the plain form and its own in-flight state, nothing faked.
    """
    seed_step_three(tmp_path)
    app, _ = build(tmp_path)
    async with asgi_client(app) as client:
        await enter(client)
        html = (await client.get(STEP_PATH, params={"n": "3"})).text
    # with the scripts stripped (what a JS-off reader gets) nothing claims a busy state
    markup = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    assert "aria-busy" not in markup
    assert "<progress" not in markup
    for label in ("Creating the database…", "Continuing…"):
        assert "%" not in _busy_button(html, label)
    # the Test button is an ordinary enabled submit (the Continue carries its own, legitimate gate)
    assert "disabled" not in _busy_button(html, "Creating the database…")


# --------------------------------------------------------------------------- the shipped script

def test_the_shipped_script_locks_the_form_and_refuses_a_double_submit(tmp_path):
    """The shipped inline script against a minimal DOM: label, lock, refusal and value mirror."""
    body = tmp_path / "busy.js"
    body.write_text(_busy_body(), encoding="utf-8")
    done = run_node_harness(["node", str(HARNESS), str(body)])
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    # a press: the label changes, the button is busy, the form locks, the value is mirrored first
    assert data["press"] == {
        "prevented": False,
        "label": "Testing the connection…",
        "ariaBusy": "true",
        "disabled": [True, True],
        "locked": True,
        "mirrored": [{"name": "action", "value": "test", "type": "hidden"}],
    }
    # a second submit while the first is in flight is refused, with no second mirror
    assert data["double"] == {"prevented": True, "appended": 1, "continueLabel": "continue"}
    # a form without a data-busy button is left completely alone
    assert data["untouched"] == {"prevented": False, "disabled": [False], "locked": False,
                                 "appended": 0}
    # an implicit submission (no pressed control) still locks the form and mirrors nothing
    assert data["implicit"] == {"disabled": [True, True], "appended": []}
