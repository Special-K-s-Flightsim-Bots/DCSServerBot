"""The dialog page's inline script must PARSE — it did not, so it never ran.

``templates/confirm.html`` is the only template carrying an INLINE ``<script>`` (every other
``<script>`` in ``templates/`` loads an external ``src``). That one block is the dialog's keyboard
contract — Escape dismisses the dialog, Tab cannot leave it. It was written

    (function  {
      "use strict";

with no parameter list, which is a JavaScript syntax error: the browser discards the whole block, so
the contract has never run on any dialog page, and no Python-level assertion could see it (the file
is valid Jinja, valid HTML and a green suite).

THE PIN is at the level of the defect: the rendered page's inline scripts are extracted and handed to
a real JavaScript parser (``inline_script_parse_harness.js``, run by ``node``). A string assertion on
``(function () {`` would pin one spelling of the fix; parsing pins that the block parses at all.
``test_the_parser_rejects_the_defect`` is the instrument's own positive control: the harness must
report the pre-fix spelling as a syntax error, or the pin could pass on a parser that never objects.

THE CENSUS (three halves):

* every inline ``<script>`` block in ``services/webservice/templates/`` — ``confirm.html`` must be
  the only one, so a future inline script cannot skip this pin by living in a page nobody renders
  here;
* the inline script the console BUILDS IN PYTHON on a refusal page (``shell.retryable_page``) — the
  same class of defect outside ``templates/``, so it is rendered and parsed too;
* a SHAPE census over every other inline-script producer (the guided setup tour's step scripts, which
  build their HTML as Python strings and cannot be rendered without walking the whole tour): each
  ``(function`` in them must carry its parameter list. This half is a string census, not a parser —
  it exists to catch the defect's spelling where no render is available, and the shape is byte-for-byte
  the same one the dialog carried.
"""
from __future__ import annotations

import json
import re

import httpx
import shutil
import subprocess
from pathlib import Path

import pytest

from services.webservice.pages import actions as actions_page
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"

# the console write suites' own harness, reused rather than copied
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    build, clean_registry, client, config_dir, sign_in, token_of,
)
from test_webui_write_message import UCID_A, message_fleet  # noqa: F401  (the doubles are used)

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
TEMPLATES = REPO / "services" / "webservice" / "templates"
WEBSERVICE = REPO / "services" / "webservice"
HARNESS = TESTS / "inline_script_parse_harness.js"

SERVER = "SRS-1"

#: an inline script: ``<script>`` with no ``src`` attribute, body captured.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)

#: the defect itself, in the spelling the tree carried — the harness's positive control.
DEFECT = '(function  {\n  "use strict";\n})();\n'

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: no JavaScript parser to pin with")


def parse(sources: dict[str, str], tmp_path: Path) -> dict[str, dict]:
    """Hand ``{name: source}`` to the real JavaScript parser; return its verdict per name."""
    paths = {}
    for name, source in sources.items():
        path = tmp_path / f"{name}.js"
        path.write_text(source, encoding="utf-8")
        paths[name] = str(path)
    done = subprocess.run(["node", str(HARNESS), *paths.values()],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return {name: verdict for name, verdict
            in zip(paths, json.loads(done.stdout))}


def assertions_of(verdicts: dict[str, dict]) -> None:
    """Fail naming the block and the parser's own message — a syntax error must be readable."""
    broken = {name: verdict.get("error") for name, verdict in verdicts.items()
              if not verdict["ok"]}
    assert not broken, "inline script(s) that a browser would discard:\n" + "\n".join(
        f"  {name}: {error}" for name, error in broken.items())


# ------------------------------------------------------------------ the instrument's positive control

@requires_node
def test_the_parser_rejects_the_defect(tmp_path):
    """The pre-fix spelling MUST be reported as a syntax error, or the pin proves nothing."""
    verdict = parse({"defect": DEFECT}, tmp_path)["defect"]
    assert verdict["ok"] is False, "the parser accepted a block that is a syntax error"
    assert "SyntaxError" in str(verdict.get("error")), verdict


# ------------------------------------------------------------------ 1. the rendered dialog page

async def dialog_html(config_dir, clean_registry) -> str:
    """Press the chat glyph on the players page: the row posts to its dialog, which renders."""
    app = build(config_dir, message_fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await http.get(ROSTER)
        assert page.status_code == 200, page.text
        reply = await http.post(actions_page.CHAT_PATH + actions_page.CONFIRM_SUFFIX,
                                data={"server": SERVER, "ucid": UCID_A,
                                      "_csrf_token": token_of(page.text)})
        assert reply.status_code == 200, reply.text
        return reply.text


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_dialog_inline_script_parses(config_dir, clean_registry, tmp_path):
    """ACCEPTANCE — the dialog's keyboard script parses, so the browser runs it."""
    html = await dialog_html(config_dir, clean_registry)
    scripts = INLINE_SCRIPT.findall(html)
    assert scripts, "the dialog rendered no inline script at all — the pin would pass vacuously"
    assertions_of(parse({f"dialog-{index}": source for index, source in enumerate(scripts)},
                        tmp_path))


# ------------------------------------------------------------------ 2. the census

def inline_scripts_in_templates() -> dict[str, str]:
    """Every inline ``<script>`` block under ``templates/``, keyed by template name."""
    found = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        for index, source in enumerate(INLINE_SCRIPT.findall(path.read_text(encoding="utf-8"))):
            found[f"{path.name}:{index}"] = source
    return found


#: THE TEMPLATES ALLOWED AN INLINE SCRIPT, each with the render step below that parses it:
#: ``confirm.html`` (the dialog's keyboard contract) and ``dcs_update.html`` (the DCS update dialog's
#: version read, which extends it), the two ModManager dialogs, and ``scheduler_body.html`` (the
#: wizard's progressive enhancement: the hard cap is hidden while its two conditions do not hold).
#: A NEW entry here must come with its own render, or the census is just a list of pages nobody
#: checks.
COVERED_TEMPLATES = {"confirm.html": "test_the_rendered_dialog_inline_script_parses",
                     "dcs_update.html": "test_the_rendered_dcs_dialog_inline_script_parses",
                     "mods_install.html": "test_the_rendered_mods_pick_dialog_inline_script_parses",
                     "mods_download.html": "test_the_rendered_mods_download_dialog_inline_script_parses",
                     "scheduler_body.html":
                         "test_the_rendered_scheduler_step_inline_script_parses"}


def test_templates_carry_no_other_inline_script():
    """CENSUS — every template with an inline script is one this pin PARSES elsewhere.

    A new inline script is meant to be seen: it must be added here (and to the render step that
    parses the page it belongs to), not slipped in behind a suite that only looks at one page.
    """
    found = inline_scripts_in_templates()
    assert found, "no inline script found at all — the census is looking at the wrong directory"
    names = {key.split(":")[0] for key in found}
    unknown = sorted(names - set(COVERED_TEMPLATES))
    assert not unknown, (
        f"inline script(s) in templates that this pin does not cover: {unknown} — extend the render "
        f"step to parse those pages")
    # and every entry claimed above is really a template that carries one (no stale coverage)
    stale = sorted(set(COVERED_TEMPLATES) - names)
    assert not stale, f"covered template(s) with no inline script: {stale}"


@requires_node
def test_the_login_background_script_is_external_and_parses(tmp_path):
    """CENSUS — the login page's canvas background is a static file, and it still parses.

    The census above forbids an inline block in ``login.html``; moving the script out of the template
    must not move it out of the parser's reach, so the shipped asset is handed to the same harness.
    """
    template = (TEMPLATES / "login.html").read_text(encoding="utf-8")
    assert "/static/login-stars.js" in template, "the page no longer loads the background asset"
    assert not INLINE_SCRIPT.search(template), "the login page still carries an inline script"
    asset = (WEBSERVICE / "static" / "login-stars.js").read_text(encoding="utf-8")
    assert "dcs-tactical-canvas" in asset, "the asset no longer draws the canvas the page ships"
    assertions_of(parse({"login-stars": asset}, tmp_path))


async def dcs_dialog_html(config_dir, monkeypatch) -> str:
    """Open the DCS update dialog on a node that declares a DCS installation, and return its page."""
    from test_webui_node_dcs_update import NODE, Fleet, app_with

    fleet = Fleet(monkeypatch).declare(NODE)
    app = app_with(config_dir, fleet.cluster())
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get("/nodes")
        assert page.status_code == 200, page.text
        reply = await http.post(f"{actions_page.NODE_DCS_UPDATE_PATH}{actions_page.CONFIRM_SUFFIX}",
                                data={"node": NODE, "_csrf_token": token_of(page.text)})
        assert reply.status_code == 200, reply.text
        return reply.text


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_dcs_dialog_inline_script_parses(config_dir, monkeypatch, tmp_path):
    """The DCS update dialog's own script parses — plus the keyboard contract it inherits."""
    html = await dcs_dialog_html(config_dir, monkeypatch)
    scripts = INLINE_SCRIPT.findall(html)
    assert scripts, "the DCS update dialog rendered no inline script at all — vacuous pin"
    # the page EXTENDS confirm.html, so both blocks are on it: the inherited keyboard contract and
    # the version read this template adds
    assert len(scripts) >= 2, f"expected the inherited and the page's own script, got {len(scripts)}"
    assertions_of(parse({f"dcs-dialog-{index}": source for index, source in enumerate(scripts)},
                        tmp_path))


@requires_node
def test_the_retryable_refusal_page_inline_script_parses(tmp_path):
    """CENSUS — the other inline script the console builds in Python (``shell.retryable_page``)."""
    from services.webservice import permissions, shell

    html = shell.retryable_page(permissions.RETRYABLE_STATUS, retry_after=7)
    scripts = INLINE_SCRIPT.findall(html)
    assert scripts, "the retryable page rendered no inline script at all"
    assertions_of(parse({f"retryable-{index}": source for index, source in enumerate(scripts)},
                        tmp_path))


# ------------------------------------------------------ the scheduler wizard's own enhancement
#
# The wizard's actions step carries TWO inline scripts: the SHARED pick-chips enhancement (one block
# for every step that draws a pick) and the hard-cap line, which is hidden while the entry has no
# Mission time or does not wait for an empty server. Both are progressive enhancement, so a script a
# browser discards would leave the line visible and the chips missing — legal, but silently
# unpolished. They are parsed here like every other inline block, and the render step below is the
# census's evidence.

async def scheduler_step_html(config_dir, monkeypatch) -> str:
    """Render the wizard's actions step — the page that carries the hard cap's script."""
    from test_webui_scheduler_tab import (cluster, install_plugin, scheduler_file,
                                          SERVER_A, WIZARD_PLAN)

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=3")
        assert page.status_code == 200, page.text
        return page.text


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_scheduler_step_inline_script_parses(config_dir, clean_registry,
                                                               monkeypatch, tmp_path):
    """The wizard's actions step carries the pick chips and the hard cap, and both parse."""
    html = await scheduler_step_html(config_dir, monkeypatch)
    scripts = INLINE_SCRIPT.findall(html)
    assert len(scripts) == 2, (
        f"the actions step must carry the pick chips and the hard cap, got {len(scripts)}")
    assertions_of(parse({f"scheduler-step-{index}": source
                         for index, source in enumerate(scripts)}, tmp_path))


async def scheduler_week_step_html(config_dir, monkeypatch) -> str:
    """Render the wizard's FIRST step — the page that carries the day buttons' script."""
    from test_webui_scheduler_tab import (cluster, install_plugin, scheduler_file,
                                          SERVER_A, WIZARD_PLAN)

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await http.get(f"/fleet/{SERVER_A}?tab=scheduler&step=1")
        assert page.status_code == 200, page.text
        return page.text


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_scheduler_week_step_inline_script_parses(config_dir, clean_registry,
                                                                     monkeypatch, tmp_path):
    """The wizard's week step carries exactly one script, and it parses."""
    html = await scheduler_week_step_html(config_dir, monkeypatch)
    scripts = INLINE_SCRIPT.findall(html)
    assert len(scripts) == 1, f"the week step must carry its one script, got {len(scripts)}"
    assertions_of(parse({"scheduler-week-0": scripts[0]}, tmp_path))


#: the defect's spelling: ``(function`` with no parameter list before the body.
NO_PARAMS = re.compile(r"\(function\s*\{")

#: the producers the shape census must see, so an empty walk cannot pass for a clean one.
KNOWN_PRODUCERS = {"templates/confirm.html", "setup/tour.py", "shell.py"}


def test_every_inline_script_producer_gives_its_function_a_parameter_list():
    """CENSUS — the tour's step scripts build their HTML as Python strings and are checked by shape.

    They cannot be parsed here without walking the entire guided tour, so this half is a string
    census: it catches the exact spelling the dialog carried, anywhere an inline script is authored.
    """
    sources = [path for path in sorted(WEBSERVICE.rglob("*.html"))
               if path.is_relative_to(TEMPLATES)]
    sources += sorted(WEBSERVICE.rglob("*.py"))
    producers = {path.relative_to(WEBSERVICE).as_posix(): path.read_text(encoding="utf-8")
                 for path in sources
                 if "<script>" in path.read_text(encoding="utf-8")}
    assert set(producers) >= KNOWN_PRODUCERS, (
        f"the census found {sorted(producers)}, missing {sorted(KNOWN_PRODUCERS - set(producers))}"
        " — it is no longer looking where the inline scripts are authored")
    broken = {name: NO_PARAMS.findall(source) for name, source in producers.items()
              if NO_PARAMS.search(source)}
    assert not broken, (
        "inline script authorship with a function but no parameter list "
        f"(a JavaScript syntax error at render time): {sorted(broken)}")

# ------------------------------------------------- the ModManager dialogs' own keyboard contract
#
# The two mods dialogs carry the SAME keyboard contract ``confirm.html`` does (Esc goes back without
# acting, Tab cannot leave the dialog), because they are pages of the same kind. The two render steps
# below open them through the real application, so an entry in the census above is a page somebody
# actually parses.

async def _mods_dialog_html(config_dir, monkeypatch, verb: str) -> str:
    """Open ONE of the mods dialogs and return its page."""
    from core.services.registry import ServiceRegistry
    from services.modmanager import ModManagerService
    from services.webservice.pages import mods as mods_page
    from test_webui_mods_pages import ROOT_ROWS, RecordingService, fleet
    from test_webui_mods_pages import build as mods_build

    monkeypatch.setitem(ServiceRegistry._singletons, ModManagerService,
                        RecordingService(rows=ROOT_ROWS))
    app = mods_build(config_dir, fleet())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as http:
        await sign_in(http, "specialk")
        page = await http.get(mods_page.node_page_url("node-01"))
        assert page.status_code == 200, page.text
        reply = await http.post(mods_page.dialog_url("node", "node-01", verb),
                                data={"_csrf_token": token_of(page.text)})
        assert reply.status_code == 200, reply.text
        return reply.text


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_mods_pick_dialog_inline_script_parses(config_dir, monkeypatch, tmp_path):
    """The install dialog's inline script parses, so the browser runs it."""
    html = await _mods_dialog_html(config_dir, monkeypatch, "install")
    scripts = INLINE_SCRIPT.findall(html)
    assert scripts, "the dialog rendered no inline script at all — the pin would pass vacuously"
    assertions_of(parse({f"mods-pick-{index}": source for index, source in enumerate(scripts)},
                        tmp_path))


@requires_node
@pytest.mark.asyncio
async def test_the_rendered_mods_download_dialog_inline_script_parses(config_dir, monkeypatch,
                                                                     tmp_path):
    """The download dialog's inline script parses, so the browser runs it."""
    html = await _mods_dialog_html(config_dir, monkeypatch, "download")
    scripts = INLINE_SCRIPT.findall(html)
    assert scripts, "the dialog rendered no inline script at all — the pin would pass vacuously"
    assertions_of(parse({f"mods-download-{index}": source for index, source in enumerate(scripts)},
                        tmp_path))
