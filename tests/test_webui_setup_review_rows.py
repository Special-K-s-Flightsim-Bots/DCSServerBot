"""The Review screen's ROWS: a seeded plugin file gets one, and ``nodes.yaml`` gets exactly one.

Two rendering rules of step 10, both driven through the shipped setup application over a real ASGI
request (no hand-built HTML, no stub screen):

* the RESULTS list (:func:`services.webservice.setup.tour._review_results`) is one row per report
  entry — a per-plugin seed carries the dynamic ``plugin:<name>`` key, so it needs its own row with
  the reason the write gave, exactly like a static target's;
* the "will be written" list (:func:`services.webservice.setup.tour._will_write`) is one row per
  FILE. The commit plans ``nodes.yaml`` twice (the node document, then the nested extension merge),
  so the screen folds the second entry into the node's row and says so — and the node row is still
  there when there are no extension answers at all (the control below).

UNVERIFIED here: the rendered screen in a browser (layout, the row glyphs' colour). Every string
and every path this module asserts on is the shipped one.
"""
from __future__ import annotations

import pathlib
import re
from html import unescape

import pytest

from conftest import asgi_client
from core.utils import configwrite
from services.webservice.setup import apps as apps_step
from services.webservice.setup import review as review_step
from services.webservice.setup.plugins import RADIO_SRS, load_templates
from services.webservice.setup.tour import STEP_PATH
from test_webui_setup_review import (  # noqa: F401 - the autouse fixture is used by name
    _node_arguments, build, complete_draft, enter, seed_secrets, token_of)

NODES_PATH = "config/nodes.yaml"
MERGED = "the extensions are merged into this file"


def paths_of(html: str) -> list[str]:
    """Every path the screen lists, in the order it shows them."""
    return re.findall(r'<span class="path">([^<]+)</span>', html)


async def review_screen(tmp_path) -> str:
    """Step 10 as the operator sees it, for the draft already planted in *tmp_path*."""
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        return unescape((await client.get(STEP_PATH, params={"n": "10"})).text)


@pytest.mark.asyncio
async def test_a_seeded_plugin_file_gets_its_results_row_with_the_reason(tmp_path):
    """A ``plugin:<name>`` report entry names ``config/plugins/<name>.yaml`` and why it did not land.

    Both reasons a seed can have: a refused write (its ``error``) and a file the operator already
    had (its ``note``). An unknown key still renders nothing.
    """
    draft = complete_draft(tmp_path)
    draft.review = {"written": False, "results": [
        {"key": "bot", "ok": True, "changed": True, "skipped": False, "error": "", "note": ""},
        {"key": "plugin:mission", "ok": False, "changed": False, "skipped": False,
         "error": "config/plugins/mission.yaml refused by the test", "note": ""},
        {"key": "plugin:playerstats", "ok": False, "changed": False, "skipped": True, "error": "",
         "note": "config/plugins/playerstats.yaml already exists — left untouched"},
        {"key": "no-such-target", "ok": True, "changed": False, "skipped": False, "error": "",
         "note": ""},
        {"key": "plugin:", "ok": False, "changed": False, "skipped": True, "error": "", "note": ""},
        {"key": "main", "ok": False, "changed": False, "skipped": True, "error": "", "note": ""},
        {"key": "extensions", "ok": True, "changed": True, "skipped": False, "error": "",
         "note": "DCS.openbeta: SRS was not found — no port allocated"},
    ]}
    draft.save(tmp_path)
    html = await review_screen(tmp_path)
    listed = paths_of(html)

    assert "config/plugins/mission.yaml" in listed
    assert "config/plugins/mission.yaml refused by the test" in html
    assert "config/plugins/playerstats.yaml" in listed
    assert "config/plugins/playerstats.yaml already exists — left untouched" in html
    # a static target's row is untouched by the dynamic ones
    assert "config/services/bot.yaml" in listed
    assert "config/main.yaml" in listed
    # a write that succeeded keeps the fact it left something out
    assert "DCS.openbeta: SRS was not found — no port allocated" in html
    # genuinely unknown keys are still skipped, and a bare ``plugin:`` names no file
    assert "no-such-target" not in html
    assert "config/plugins/.yaml" not in html


@pytest.mark.asyncio
async def test_nodes_yaml_is_listed_once_when_the_extensions_are_merged_into_it(tmp_path, monkeypatch):
    """Step 9's extension answers plan a second ``nodes.yaml`` write — the screen shows ONE row."""
    monkeypatch.setattr(apps_step, "detect_app",
                        lambda spec: "/opt/tacview" if spec.key == "Tacview" else None)
    draft = complete_draft(tmp_path)
    draft.plugins = {"templates": [], "radio": {"DCS.openbeta": RADIO_SRS},
                     "tacview": True, "lotatc": False}
    draft.save(tmp_path)
    app, _ = build(tmp_path)
    # the PLAN really does carry the node document and the extension merge
    keys = [item.target.key for item in review_step.plan(draft, app=app)]
    assert keys.count("nodes") == 1 and keys.count("extensions") == 1

    html = await review_screen(tmp_path)
    assert paths_of(html).count(NODES_PATH) == 1
    assert html.count(f"this node, DCS — {MERGED}") == 1


@pytest.mark.asyncio
async def test_nodes_yaml_is_still_listed_when_there_are_no_extension_answers(tmp_path):
    """The control: a de-dupe that removes the node row entirely fails here."""
    draft = complete_draft(tmp_path)
    app, _ = build(tmp_path)
    keys = [item.target.key for item in review_step.plan(draft, app=app)]
    assert "extensions" not in keys

    html = await review_screen(tmp_path)
    assert paths_of(html).count(NODES_PATH) == 1
    assert "this node, DCS" in html
    assert MERGED not in html


@pytest.mark.asyncio
async def test_a_half_written_commit_reports_the_seeded_plugin_row_and_its_note(tmp_path, monkeypatch):
    """The real half-write: a seed the operator already had, an extension that allocated no port.

    Both are facts the report carries and the screen must show — the skipped seed with its own
    note (never the generic "not attempted" sentence, which would name the wrong reason), and the
    written ``nodes.yaml`` row with the sentence about what the commit left out (the extension merge
    has nothing to add here, so that sentence travels on the node write).
    """
    monkeypatch.setattr(apps_step, "detect_app", lambda spec: None)   # no SRS on this machine
    template = load_templates()[0]
    seeded = template.plugins[0]

    draft = complete_draft(tmp_path)
    draft.plugins = {"templates": [template.id], "radio": {"DCS.openbeta": RADIO_SRS},
                     "tacview": False, "lotatc": False}
    draft.save(tmp_path)
    mine = tmp_path / "plugins" / f"{seeded}.yaml"
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text("DEFAULT: {}\n", encoding="utf-8")

    real = configwrite.save_config_file

    def wrapper(path, data, **kwargs):
        if pathlib.Path(path).name == "servers.yaml":
            raise configwrite.ConfigWriteError("servers.yaml refused by the test", path=path)
        return real(path, data, **kwargs)

    monkeypatch.setattr(configwrite, "save_config_file", wrapper)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        screen = await client.get(STEP_PATH, params={"n": "10"})
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                                      "n": "10", "action": "write"})

    html = unescape(response.text)
    listed = paths_of(html)
    assert f"config/plugins/{seeded}.yaml" in listed
    assert f"plugins/{seeded}.yaml already exists — left untouched" in html
    # the written extension merge keeps the fact that an SRS instance got no port
    assert "DCS.openbeta: SRS was not found — no port allocated" in html
    # the half-write is named for what it is
    assert "config/main.yaml" in listed
    assert "not attempted — an earlier file was not written" in html


@pytest.mark.asyncio
async def test_the_no_port_note_is_written_in_the_console_language(tmp_path, monkeypatch):
    """The sentences the write reports are translated: a German console gets the German sentence.

    The note is built during the commit (``review._sentence``), so this drives the real POST and reads
    what the Review screen printed back — the sentence has to reach BOTH a catalog and the page.
    """
    monkeypatch.setattr(apps_step, "detect_app", lambda spec: None)   # no SRS on this machine
    draft = complete_draft(tmp_path)
    draft.language = "de"
    draft.plugins = {"templates": [], "radio": {"DCS.openbeta": RADIO_SRS},
                     "tacview": False, "lotatc": False}
    draft.save(tmp_path)

    real = configwrite.save_config_file

    def wrapper(path, data, **kwargs):
        if pathlib.Path(path).name == "servers.yaml":
            raise configwrite.ConfigWriteError("servers.yaml refused by the test", path=path)
        return real(path, data, **kwargs)

    monkeypatch.setattr(configwrite, "save_config_file", wrapper)
    app, _ = build(tmp_path)
    seed_secrets(app)
    async with asgi_client(app) as client:
        await enter(client)
        screen = await client.get(STEP_PATH, params={"n": "10"})
        response = await client.post(STEP_PATH, data={"_csrf_token": token_of(screen.text),
                                                      "n": "10", "action": "write"})

    html = unescape(response.text)
    assert "DCS.openbeta: SRS wurde nicht gefunden — kein Port vergeben" in html
    # the English sentence is not what a German console shows
    assert "SRS was not found" not in html
