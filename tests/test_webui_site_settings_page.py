"""The Site settings page, driven in a real browser: its Add controls and its preset tiles.

Both defects this pins are CLIENT-side, so a model test cannot see either:

* the three list blocks (``branding.footer_links``, ``nav``, ``tiles``) are drawn by the generated
  control renderer, and its ``+ Add`` / ``✕`` behaviour lives in ``static/configgen.js`` — a page
  that does not LOAD that script renders the controls and does nothing when they are pressed;
* a preset tile is a ``<label>`` around a radio inside the card's form, so pressing it must select
  the tile and do nothing else — no submit, no navigation, no blank document.

The page is served by the real application on a real socket (uvicorn in a thread), signed in through
the real username/password door, and driven by ``tests/site_settings_probe.py`` in its own process
(Playwright's sync API leaves an event loop behind, which would break the suite's ``asyncio.run``).
The probe prints what the PAGE did; this test asserts it, and reads ``site.yaml`` between the two
phases so "the added row reaches the file" is proven against the file, not the markup.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from conftest import REPO_ROOT
from services.webservice import mount, sites, theme_css
from services.webservice.auth import local as local_auth
from services.webservice.pages import site_config as site_page
from test_webui_site_pages import (PASSWORD, build, config_dir,  # noqa: F401 - fixtures
                                   door_config)

PROBE = REPO_ROOT / "tests" / "site_settings_probe.py"

#: the interpreter the probe runs under: the project's venv is the one that ships Playwright, and the
#: suite's own interpreter is deliberately a bare developer Python.
VENV_PYTHON = REPO_ROOT / "venv" / "bin" / "python"

ADMIN = "admin"
SITE_KEY = "root"

BOT_YAML = """\
roles:
  Admin:
    - admin
"""

SITE_YAML = """\
sites:
  root:
    slug: ""
    branding:
      title: Root Site
  group1:
    slug: group1
    group_role: 5th Guards
    branding:
      title: 5th Guards
"""

FOOTER_LINK_LABEL = "Imprint"
FOOTER_LINK_HREF = "/imprint"

#: the same two sites, with the GROUP site switched OFF — the card the switch drives.
SITE_YAML_OFF = """\
sites:
  root:
    slug: ""
    branding:
      title: Root Site
  group1:
    slug: group1
    group_role: 5th Guards
    enabled: false
    branding:
      title: 5th Guards
"""

#: the site the switch phase flips.
OFF_SITE = "group1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_config(tmp_path):
    """A config directory the console installs from: ``bot.yaml``, ``servers.yaml``, ``site.yaml``."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(
        "Root-1:\n  port: 10308\nGroup-1:\n  managed_by:\n    - 5th Guards\n", encoding="utf-8")
    (tmp_path / sites.SITE_FILE).write_text(SITE_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def served(live_config):
    """The real application on a real loopback port, with one local account that may sign in."""
    yield from _serve(live_config)


@pytest.fixture
def served_off(tmp_path):
    """The same application over a site file whose GROUP site is switched off."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(
        "Root-1:\n  port: 10308\nGroup-1:\n  managed_by:\n    - 5th Guards\n", encoding="utf-8")
    (tmp_path / sites.SITE_FILE).write_text(SITE_YAML_OFF, encoding="utf-8")
    yield from _serve(tmp_path)


def _serve(config_dir):
    """The real application on a real loopback port, signed in as ``ADMIN``; yields its base URL."""
    user = {"username": ADMIN, "roles": ["Admin"],
            "password_hash": local_auth.hash_password(PASSWORD,
                                                     iterations=local_auth.MIN_ITERATIONS)}
    app, _registrar = build(config_dir, config=door_config(users=[user]))
    port = _free_port()
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                          log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - a port that never bound is a broken harness, not a defect
        pytest.fail("the console never bound its port")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


#: the shipped sheet's transcription: the palette a site stores when it names no preset
SHEET_TEXT = site_page.expert_sheet().text
SHEET_DECLARATIONS = len([line for line in SHEET_TEXT.splitlines() if line.strip()])


def _sheet_site_yaml() -> str:
    """One site whose palette IS the sheet: a ``theme.expert`` block scalar of the transcription.

    ``|-`` keeps the scalar exactly as the page's own editor holds it (no trailing newline), so the
    never-clear pin can compare the stored value BYTE for byte rather than modulo a YAML artefact.
    """
    body = "".join(f"        {line}\n" for line in SHEET_TEXT.splitlines())
    return ("sites:\n  root:\n    slug: \"\"\n    branding:\n      title: Root Site\n"
            "    theme:\n      expert: |-\n" + body)


@pytest.fixture
def sheet_config(tmp_path):
    """A config directory whose ONE site stores an expert sheet."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(
        "Root-1:\n  port: 10308\nGroup-1:\n  managed_by:\n    - 5th Guards\n", encoding="utf-8")
    (tmp_path / sites.SITE_FILE).write_text(_sheet_site_yaml(), encoding="utf-8")
    return tmp_path


@pytest.fixture
def sheet_served(sheet_config):
    """The same application, on a config whose site already wears an expert sheet."""
    yield from _serve(sheet_config)


def _probe(base: str, phase: str, site_file: Path | None = None, site: str = SITE_KEY,
           extra: "tuple[str, ...]" = ()) -> dict:
    """Run the browser probe in its own process and answer what it printed.

    *site_file* is the ``site.yaml`` the probe may read between two Saves; when it is given the
    probe reports the bytes the FIRST Save left, because the second Save happens before the caller
    can read the file. Omitting it keeps every other phase's call unchanged. *site* is the card the
    phase works on — the page draws one card per site, so a phase that must run on a card other than
    the root site's names it here. *extra* is appended to the probe's argv for the phases that take
    their own arguments (the render paths).
    """
    interpreter = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    page_path = mount.url(site_page.SITE_CONFIG_PATH)
    argv = [interpreter, str(PROBE), base, ADMIN, PASSWORD, page_path, phase, site]
    argv.extend(extra)
    if site_file is not None:
        argv.append(str(site_file))
    finished = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=300)
    stdout = finished.stdout or ""
    stderr = finished.stderr or ""
    assert finished.returncode == 0, (
        f"the probe failed (exit {finished.returncode}) for phase {phase!r} / site {site!r}:\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}")
    assert stdout.strip(), (
        f"the probe printed nothing for phase {phase!r} / site {site!r} (exit 0): the run was "
        f"intercepted, or the probe died before it reported. stderr:\n{stderr}")
    if stderr.strip():
        print("PROBE_STDERR", stderr.strip(), file=__import__("sys").stderr)
    payload = json.loads(stdout.strip().splitlines()[-1])
    if isinstance(payload, dict) and payload.get("unavailable"):
        pytest.skip(payload["unavailable"])
    return payload


def _links(written: Path) -> list[dict]:
    """The footer links the site file now carries for the root site."""
    block = sites.load_site_block(written.parent) or {}
    return list(((block.get(SITE_KEY) or {}).get("branding") or {}).get("footer_links") or [])


def test_the_add_controls_work_and_reach_the_file(served, live_config):
    """THE OPERATOR'S SYMPTOM: pressing Add does nothing. Then: the row reaches the saved YAML."""
    written = live_config / sites.SITE_FILE
    assert not _links(written)

    added = _probe(served, "add")

    assert any("configgen.js" in (source or "") for source in added["scripts"]), (
        "the page renders the generated lists without the script that drives their Add controls: "
        f"{added['scripts']}")
    for name, (before, after) in added["add"].items():
        # each list may already carry rows and each press adds exactly ONE
        assert after == before + 1, f"pressing Add in {name} added {after - before} row(s)"
    # §C: the two closed sets carry no Add and no remove, and they list every id of the vocabulary
    for name, vocabulary in (("nav", sites.NAV_PAGE_IDS), ("tiles", sites.TILE_IDS)):
        controls = added["closed"][name]
        assert controls["add"] == 0, f"the {name} block draws an Add control"
        assert controls["remove"] == 0, f"the {name} block draws a remove control"
        assert controls["rows"] == len(vocabulary), (
            f"the {name} block draws {controls['rows']} rows, the vocabulary has {len(vocabulary)}")

    preset = added["preset"]
    assert preset["submits"] == [], f"a preset press submitted the card's form: {preset['submits']}"
    assert preset["after"]["url"] == preset["before"]["url"], "a preset press navigated away"
    assert preset["after"]["text"] == preset["before"]["text"], "a preset press blanked the document"
    assert preset["after"]["scrollY"] == preset["before"]["scrollY"], (
        "a preset press scrolled the WINDOW away from the page's content, leaving a blank viewport: "
        f"{preset['before']['scrollY']} -> {preset['after']['scrollY']}")
    assert preset["checked"] == ["brass"], "a preset press did not select its tile"
    assert not added["errors"], added["errors"]

    saved = added["saved"]
    assert saved is not None, "the Add control produced no row, so there was nothing to save"
    assert any("Saved" in notice for notice in saved["notices"]), saved["notices"]
    assert added["rows_after_save"] == 1, "the saved page does not render the row it just wrote"
    assert _links(written) == [{"label": FOOTER_LINK_LABEL, "href": FOOTER_LINK_HREF}], \
        f"the added row never reached {sites.SITE_FILE}"

    removed = _probe(served, "remove")

    assert removed["removed"]["found"], "the probe could not find the row it wrote"
    assert not removed["errors"], removed["errors"]
    assert not _links(written), "removing the row did not reach the file"


# ================================== §A · the branding card's binding, driven in a real browser (pin 5)

#: the name the probe types — the design's own example.
TYPED_NAME = "5th Guards"
#: the footer link the probe adds and fills (the same pair the ``add`` phase writes).
probe_label = "Imprint"
probe_href = "/imprint"


def _unexpected_requests(requests: list[str], base: str) -> list[str]:
    """Every request that reached the CONSOLE's own tree and was not the card's draft door.

    An edit posts the card's draft; the box then re-reads the site's OWN document, which pulls its own
    stylesheet, flags and scripts — all of that lives outside the console's mount and is the site's.
    Every address INSIDE the console's tree is a control's door (the save, the avatar, the sheet), so
    the one that may appear is the draft door and nothing else. The site file's own bytes are checked
    beside this, so "nothing was written" is proven against the file rather than against a URL list.
    """
    door = base + mount.url(site_page.SITE_CONFIG_DRAFT_PATH)
    console = base + mount.CONSOLE_PREFIX
    offenders = []
    for request in requests:
        method, _, url = request.partition(" ")
        if method == "POST" and url == door:
            continue
        if url == console or url.startswith(console + "/"):
            offenders.append(request)
    return offenders


def _draft_posts(requests: list[str], base: str) -> list[str]:
    door = base + mount.url(site_page.SITE_CONFIG_DRAFT_PATH)
    return [request for request in requests if request == f"POST {door}"]


def test_typing_changes_the_preview_and_writes_nothing_to_the_server(served, live_config):
    """PIN 5: typing repaints the box AND reaches the server only as a draft — Save is the only write.

    The card posts its own fields to the draft door so the box can follow values a browser cannot
    paint (the nav's list, the tile strip, a palette); that door writes nothing, so the file and every
    other address are untouched. The preview's own DOM still changes on the keystroke itself.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served, "bind")
    box = report["bind"]
    assert box["before"]["hero"] != TYPED_NAME, "the preview already showed the typed name"
    assert box["after"]["hero"] == TYPED_NAME, (
        f"typing changed nothing in the preview's own hero: {box['before']} -> {box['after']}")
    assert box["after"]["title"] == box["before"]["title"], "typing a community name moved the title"
    assert _unexpected_requests(box["requests"], served) == [], (
        f"typing talked to the server outside the draft door: {box['requests']}")
    assert box["links"] == [probe_label], (
        f"the footer's links are not rebuilt from the card's rows: {box['links']}")
    assert box["hrefs"] == [probe_href], f"the rebuilt anchor carries {box['hrefs']}"
    assert not report["errors"], report["errors"]
    assert written.read_bytes() == before, "typing wrote the site file"


def test_the_box_follows_a_discrete_edit_before_save(served, live_config):
    """THE DRAFT, in a real browser: uncheck a nav entry and the box re-renders it inside ~1 s.

    A nav entry is the discriminating field — the local paint can only HIDE an anchor, so an anchor
    the server never rendered proves the frame was re-read from the draft. The pin also proves the
    edit went NO further than the draft: the site file is byte-identical afterwards.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served, "draft")
    draft = report["draft"]
    assert draft["before"] == 1, "the box did not draw the site's leaderboard entry to begin with"
    assert draft["after"] == 0, "the box still carries the nav entry the form just removed"
    assert draft["elapsed_ms"] is not None and draft["elapsed_ms"] <= 1500, (
        f"the box took {draft['elapsed_ms']} ms to follow the form")
    assert "leaderboard" not in draft["checked_nav"], draft["checked_nav"]
    assert _unexpected_requests(draft["requests"], served) == [], (
        f"the edit talked to the server outside the draft door: {draft['requests']}")
    assert _draft_posts(draft["requests"], served), "the edit posted no draft"
    assert not report["errors"], report["errors"]
    assert written.read_bytes() == before, "the edit wrote the site file"
    print(f"the box followed the form in {draft['elapsed_ms']} ms")


def test_switching_a_site_on_mounts_the_box_and_switching_it_off_puts_it_back(served_off, tmp_path):
    """THE REPORTED SYMPTOM, in a real browser: flipping the switch now changes the pane.

    A switched-off site's card mounts no frame and states the off-state as the box's body. Flipping
    the switch posts the draft and mounts the frame, whose document carries the site's own words;
    flipping it back puts the server's off-state sentence there again. The site file never moves.
    """
    written = tmp_path / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served_off, "switch", site=OFF_SITE)
    switch = report["switch"]

    assert switch["off_before"]["frames"] == 0, "a switched-off site's card mounted a frame"
    assert switch["off_before"]["off"] == 1, "the box does not state the off-state"
    assert switch["on"]["frames"] == 1, "flipping the switch mounted no frame"
    assert switch["on"]["off"] == 0, "the off-state is still drawn beside the frame"
    assert (switch["on"]["title"] or "").strip() == "5th Guards", switch["on"]["title"]
    assert switch["on"]["elapsed_ms"] is not None and switch["on"]["elapsed_ms"] <= 3000, (
        f"the frame took {switch['on']['elapsed_ms']} ms to appear")
    assert switch["off_after"]["frames"] == 0, "switching the site off left the frame mounted"
    assert switch["off_after"]["off"] == 1, "switching the site off drew no off-state"
    assert (switch["off_after"]["words"] or "").strip() == switch["off_before"]["words"].strip(), (
        "the off-state reads differently after the round trip")
    assert _unexpected_requests(switch["requests"], served_off) == [], (
        f"the switch talked to the server outside the draft door: {switch['requests']}")
    assert _draft_posts(switch["requests"], served_off), "the switch posted no draft"
    assert not report["errors"], report["errors"]
    assert written.read_bytes() == before, "flipping the switch wrote the site file"
    print(f"the frame mounted in {switch['on']['elapsed_ms']} ms")


# ==================================== §B · a press rewrites the two lists, in a real browser (pins 1, 2)

#: the design's B1 sets, read back from the DOM after a press.
PVP_SECTIONS = ["community", "standings", "aircraft", "when_we_fly", "servers"]
PVP_METRICS = ["playtime", "air_targets", "kd_ratio", "pvp_kd_ratio"]


def test_pressing_a_character_rewrites_the_lists_and_posts_nothing(served, live_config):
    """PINS 1 and 2: the press rewrites all fourteen controls, the chip and the sentence.

    The press itself decides nothing on the server: it moves the switches, and the only call the card
    makes is its draft door (which writes nothing) plus the preview document that renders the draft.
    Then a switch moved by hand is NAMED as a divergence, and the next press clears the line.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served, "character")
    character = report["character"]

    pressed = character["pressed"]
    assert pressed["sections"] == PVP_SECTIONS, (
        f"the press left these sections on: {pressed['sections']}")
    assert pressed["metrics"] == PVP_METRICS, (
        f"the press left these metrics on: {pressed['metrics']}")
    assert pressed["count"] == "5 of 7 sections \u00b7 4 of 7 metrics", (
        f"the card's chip reads {pressed['count']!r}")
    assert pressed["chip"] == "PvP", f"the card's character chip reads {pressed['chip']!r}"
    assert "fight-night community" in (pressed["what"] or ""), (
        f"the card states no PvP sentence: {pressed['what']!r}")
    assert "co-op community" not in (pressed["what"] or ""), "the card states two sentences at once"
    assert "the season curve and the squadrons" in (pressed["whySections"] or ""), (
        f"the sections list states no reason: {pressed['whySections']!r}")
    assert "ships, air defence and ground targets" in (pressed["whyMetrics"] or ""), (
        f"the metrics list states no reason: {pressed['whyMetrics']!r}")
    assert pressed["changedHidden"] is True, "the divergence line shows before anything was edited"
    assert _unexpected_requests(character["requests"], served) == [], (
        f"a press talked to the server outside the draft door: {character['requests']}")
    assert _draft_posts(character["requests"], served), "a press posted no draft"
    assert written.read_bytes() == before, "a press wrote the site file"

    edited = character["edited"]
    assert edited["changedHidden"] is False, "a hand edit after a press is not named"
    assert "Edited by hand" in (edited["changed"] or ""), edited["changed"]
    assert "PvP" in (edited["changed"] or ""), (
        f"the divergence line does not name the pressed character: {edited['changed']!r}")
    assert "season" in edited["sections"], "the hand edit did not survive the press"

    again = character["again"]
    assert again["changedHidden"] is True, "pressing a character again did not clear the line"
    assert again["sections"] == PVP_SECTIONS, (
        f"the second press restored {again['sections']}")
    assert not report["errors"], report["errors"]


# ==================== §C · the rows move by mouse on the shared grip, in a real browser (pins 1, 2, 4)

#: the order after the probe's drag: row 3 (squadrons) taken above row 1 (overview).
AFTER_DRAG = ["squadrons", "overview", "leaderboard", "players"]
#: and after Alt+Down on the focused grip of the row that was first.
AFTER_KEY = ["overview", "squadrons", "leaderboard", "players"]


def test_a_drop_reorders_posts_nothing_and_save_writes_the_order(served, live_config):
    """PINS 1, 2 and 4: the mouse drag and the keyboard move the row; Save writes the order.

    A LOCAL list posts nothing itself, so the pin is the ORDER the DOM carries after the move, the
    values the form would post, the pixels of one row's order cell (the number is a CSS counter, so it
    is painted, not written), and then the file's own order once the card is saved. The only request
    the card may start is its draft door — which writes nothing — and the preview it re-reads.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served, "reorder")
    moved = report["reorder"]

    assert "reorder.js" in " ".join(moved["scripts"] if "scripts" in moved else report["scripts"])         or any("reorder.js" in (src or "") for src in report["scripts"]), report["scripts"]
    assert moved["before"]["ids"] == ["overview", "leaderboard", "squadrons", "players"], (
        f"the block starts in {moved['before']['ids']}")
    assert moved["after_drag"]["ids"] == AFTER_DRAG, (
        f"dragging row 3 above row 1 left {moved['after_drag']['ids']}")
    assert moved["after_key"]["ids"] == AFTER_KEY, (
        f"Alt+Down on the focused grip left {moved['after_key']['ids']}")
    assert _unexpected_requests(moved["requests"], served) == [], (
        f"a drop talked to the server outside the draft door: {moved['requests']}")
    # the LOCAL list still REPORTS each move, bubbling on the container: the page's own script is
    # told, it just has nothing to post
    assert moved["moves"] == [AFTER_DRAG, AFTER_KEY], (
        f"the container reported {moved['moves']}")
    assert moved["after_drag"]["status"] == 0 and moved["after_key"]["status"] == 0, (
        "the LOCAL list drew a status line for a write it never made")
    assert moved["after_drag"]["dragging"] == 0 and moved["after_key"]["dragging"] == 0, (
        "a row was left in its dragging state")
    # the posted values follow the DOM: the order IS the payload, with no field renamed
    assert moved["after_key"]["posted"] == AFTER_KEY, (
        f"the form would post {moved['after_key']['posted']}")
    # PIN 4: the number is the counter's, and the counter renumbers with the move
    for state in ("before", "after_drag", "after_key"):
        for cell in moved[state]["ord"]:
            assert cell["text"] == "", (
                f"the order number is written into the DOM: {cell['text']!r}")
            assert "counter(" in cell["content"], (
                f"the order cell draws {cell['content']!r}, not a counter")
    painted = [moved[state]["ord_squadrons"] for state in ("before", "after_drag", "after_key")]
    assert len(set(painted)) == 3, (
        f"the same row painted the same number in all three positions: {painted}")

    assert any("Saved" in notice for notice in moved["saved"]["notices"]), moved["saved"]["notices"]
    assert moved["after_save"]["ids"] == AFTER_KEY, (
        f"the reloaded page draws {moved['after_save']['ids']}, the saved order is {AFTER_KEY}")
    block = sites.load_site_block(live_config) or {}
    assert (block.get("root") or {}).get("nav") == AFTER_KEY, (
        f"site.yaml carries nav: {(block.get('root') or {}).get('nav')!r}")
    assert not report["errors"], report["errors"]


# ================================== §D · the expert sheet, in a real browser (pins 1–6)

#: the design's transcription, read from the page module: 30 declarations, 35 lines, 932 characters.
SHEET = site_page.expert_sheet()
SHELL_CSS = REPO_ROOT / "services" / "webservice" / "static" / "shell.css"
SCOPE_CSS = REPO_ROOT / "services" / "webservice" / "static" / "settings-scope.css"

#: the copy's own badge, count line and bound, exactly as the design states them.
COPY_BADGE = "copy of brass.css"
UNSTORED_BADGE = "copy of brass.css · not stored"
EDITED_BADGE = "edited"
COPY_WHEN = "30 declarations · 35 lines · linked after the site's own sheet"
UNSTORED_WHEN = ("30 declarations · 35 lines · shown, not stored — the shipped copy for reading; "
                 "it is kept only when you change it")
COPY_COUNT = "8 192 characters allowed · 932 used"
ACCEPTED = ("Accepted — 30 declarations, 932 characters. Saving writes them as this site's own sheet.")
REPLACE = "Replace this sheet with the 30 declarations of brass? This cannot be undone."

#: the line the probe falsifies (1-based 12) and the text it types into it.
REFUSED_LINE = 12
TYPED = "!important"


def test_the_sheet_is_the_shipped_transcription_and_its_check_is_accepted(served):
    """PIN 2: a fresh card's sheet IS the 30 declarations of brass, and Check accepts them.

    The transcription is read from the page module, never restated: 30 declarations, 35 lines and 932
    characters are the design's own numbers, and the validator accepts the text (the refusal the raw
    preset file would earn — its ``:root`` wrapper and its opening comment — is not reachable).
    """
    assert (SHEET.declarations, SHEET.lines, SHEET.characters) == (30, 35, 932), \
        f"the transcription is {SHEET.declarations}/{SHEET.lines}/{SHEET.characters}"

    report = _probe(served, "expert")
    expert = report["expert"]
    assert expert["shown"], "the admin's card draws no expert sheet"

    initial = expert["initial"]
    assert initial["text"] == SHEET.text, "the sheet does not open as the shipped sheet's transcript"
    assert initial["lines"] == SHEET.lines == 35, f"the sheet is {initial['lines']} lines"
    # a fresh card stores no sheet: the badge and the line say the transcription is SHOWN, not kept
    assert initial["badge"] == UNSTORED_BADGE, f"the bar's badge reads {initial['badge']!r}"
    assert initial["when"] == UNSTORED_WHEN, f"the bar's line reads {initial['when']!r}"
    assert initial["count"] == COPY_COUNT, f"the foot's bound reads {initial['count']!r}"
    assert initial["loadDisabled"] is True, "the restore control is live on an unedited copy"
    # §D1's measures: 460 px tall, a 52 px gutter
    assert initial["gutter"] == 52, f"the gutter measures {initial['gutter']} px"
    assert initial["height"] == 460, f"the sheet measures {initial['height']} px"
    # the frame is the editor and its own two hairlines — nothing else is laid out inside it (a
    # control left displayed under a hidden state doubles the frame and pushes the page down)
    assert initial["sheetHeight"] == 462, (
        f"the sheet's frame measures {initial['sheetHeight']} px around a 460 px editor")

    checked = expert["checked"]
    assert checked["verdictState"] == "ok", f"the copy's Check answered {checked['verdict']!r}"
    assert checked["verdict"] == ACCEPTED, f"the verdict reads {checked['verdict']!r}"
    assert not report["errors"], report["errors"]


def test_the_css_mode_emits_the_classes_the_theme_maps(served):
    """PIN 1 — the theme work's acceptance gate: the mode's own tokenization is the authority.

    A class the mode never emits is a rule that silently paints nothing, so every class the vendored
    5.65.16 CSS mode produces — over this sheet and over the four shapes a refusal names — must have a
    rule in this page's own sheet (``settings-scope.css``) under ``.cfg-editor``, and the sheet must
    carry that theme's class or the rules are out of scope. The CSS mode exists only on this page, so
    its token rules live here rather than in the console-wide ``shell.css``.
    """
    report = _probe(served, "expert")
    expert = report["expert"]
    emitted = expert["mode"]
    # the two classes the design names, and the one it does not: the mode spells a custom property's
    # NAME `variable-2` (a variable, not a declaration), which is what this sheet is made of
    for cls in ("property", "tag", "variable-2"):
        assert cls in emitted, f"the vendored mode never emits cm-{cls}: it emitted {emitted}"
    sheet = SCOPE_CSS.read_text(encoding="utf-8") + SHELL_CSS.read_text(encoding="utf-8")
    for cls in emitted:
        assert f".cfg-editor .CodeMirror .cm-{cls}" in sheet, (
            f"the vendored mode emits cm-{cls} but neither settings-scope.css nor shell.css maps a "
            f"`.cfg-editor .CodeMirror .cm-{cls}` — that rule would paint nothing")
    assert expert["initial"]["themeScope"] is True, (
        "the sheet does not carry `.cfg-editor`, so the theme's rules are out of scope")
    assert not report["errors"], report["errors"]


def test_a_keystroke_makes_it_edited_and_the_restore_asks_once(served):
    """PIN 3: the badge flips on the first keystroke, the restore control comes alive, and it asks.

    The restore is the only way back to the copy, and it is operator work being replaced — so it asks
    once, and afterwards the badge and the disabled state are the copy's again.
    """
    report = _probe(served, "expert")
    expert = report["expert"]

    typed = expert["typed"]
    assert typed["badge"] == EDITED_BADGE, f"the badge still reads {typed['badge']!r}"
    assert typed["loadDisabled"] is False, "the restore control stayed disabled on an edited sheet"

    assert expert["asked"] == [REPLACE], f"the restore asked {expert['asked']!r}"
    restored = expert["restored"]
    assert restored["text"] == SHEET.text, "the restore did not put the copy back"
    assert restored["badge"] == COPY_BADGE, f"the restored badge reads {restored['badge']!r}"
    assert restored["loadDisabled"] is True, "the restore control is still live on the restored copy"
    assert not report["errors"], report["errors"]


def test_the_refusal_marks_the_line_in_the_gutter_and_across_it(served):
    """PIN 4: the refused line carries both classes, and the verdict names the line and the token.

    Read from the DOM, not from a screenshot: the class names on the line's gutter and on its wash,
    the paint behind each, and the server's own sentence.
    """
    report = _probe(served, "expert")
    expert = report["expert"]
    after = expert["after_type"]

    assert after["verdictState"] == "bad", f"the refusal did not reach the verdict: {after['verdict']!r}"
    assert f"Line {REFUSED_LINE}" in after["verdict"], (
        f"the verdict names no line: {after['verdict']!r}")
    assert TYPED in after["verdict"], f"the verdict names no token: {after['verdict']!r}"

    marks = expert["marks"]
    line = marks["line12"]
    assert "sc-refused" in line["gutterClass"], f"the gutter carries {line['gutterClass']!r}"
    assert "sc-refused-wash" in line["washClass"], f"the line carries {line['washClass']!r}"
    assert line["washBackground"] not in ("", "rgba(0, 0, 0, 0)"), (
        f"the marked line's wash paints nothing: {line['washBackground']!r}")
    assert "2px" in line["washShadow"], f"the marker's inset is {line['washShadow']!r}"
    other = marks["line11"]
    assert "sc-refused" not in other["gutterClass"], "a second line's gutter was marked"
    assert not other["washClass"], f"a second line was washed: {other['washClass']!r}"
    assert not report["errors"], report["errors"]


def test_typing_a_whole_line_while_verdicts_arrive_never_moves_the_caret(served):
    """PIN 5 — the trap: the verdict slot is reserved, so a refusal cannot shift the sheet's rows.

    A whole line is typed with the debounce firing mid-sequence, so verdicts come and go underneath
    the caret. The document must be EXACTLY what was typed, and the caret must be where the last
    keystroke left it — a check that repositions either corrupts the sheet by being read.
    """
    report = _probe(served, "expert")
    expert = report["expert"]
    lines = SHEET.text.split("\n")
    assert lines[REFUSED_LINE - 1].startswith("--fg-3"), (
        f"line {REFUSED_LINE} is {lines[REFUSED_LINE - 1]!r}, not the token the probe falsifies")

    after = expert["after_type"]
    grown = list(lines)
    grown[REFUSED_LINE - 1] += " " + TYPED
    assert after["text"] == "\n".join(grown), (
        "the keystrokes did not land where they were aimed:\n"
        f"{after['text'].splitlines()[REFUSED_LINE - 1]!r}")
    assert after["caret"] == {"line": REFUSED_LINE - 1, "ch": len(grown[REFUSED_LINE - 1])}, (
        f"the caret sits at {after['caret']!r} after the verdicts arrived")

    more = expert["after_more"]
    grown[REFUSED_LINE - 1] += " x"
    assert more["text"] == "\n".join(grown), (
        f"the second sequence landed wrong: {more['text'].splitlines()[REFUSED_LINE - 1]!r}")
    assert more["caret"] == {"line": REFUSED_LINE - 1, "ch": len(grown[REFUSED_LINE - 1])}, (
        f"the caret moved again: {more['caret']!r}")
    # and the reservation itself: the sheet's own position did not move while the verdicts came and went
    tops = {state: expert[state]["sheetTop"] for state in ("initial", "checked", "typed",
                                                          "after_type", "after_more")}
    assert len(set(tops.values())) == 1, f"the sheet moved under the caret: {tops}"
    assert not report["errors"], report["errors"]


# ============================ §E · the picker and the box, driven in a real browser (pins 1, 2)

#: what the picker states before a file is chosen: the page's own accept sentence.
ACCEPT_LEAD = "PNG, JPEG or WebP"

#: the preset the probe presses: tile 0 is the shipped palette, then one tile per shipped preset.
PRESSED_PRESET = "slate"
PRESSED_TILE = 1 + [preset.id for preset in theme_css.PRESETS].index(PRESSED_PRESET)


def test_the_mark_picker_is_one_control_and_the_box_shows_the_upload(served, live_config):
    """PIN 1 and PIN 2, in the browser:

    * the native widget is drawn not at all — a 1 px box, clipped — while its own trigger is the
      console's button and the line under it states the page's words;
    * after a mark write the box's document shows THAT file (read as a pixel, because the address the
      document points at never changes) and the box's own address is a new one.
    """
    report = _probe(served, "mark")
    mark = report["mark"]

    picker = mark["picker"]
    assert picker["found"], "the card draws no file input"
    assert picker["width"] <= 2 and picker["height"] <= 2, (
        f"the browser's own file widget is laid out at {picker['width']}x{picker['height']} px")
    assert "inset(50%)" in picker["clip"], f"the input is not clipped: {picker['clip']!r}"
    assert picker["display"] != "none" and picker["visibility"] != "hidden", (
        "the input is hidden in a way that takes it out of the keyboard's reach")
    assert picker["label"], "no label is bound to the file input"
    assert picker["label"]["tag"] == "LABEL" and "st-btn" in picker["label"]["cls"].split(), (
        f"the trigger is {picker['label']!r}")
    assert picker["label"]["text"] == "Choose an image", picker["label"]["text"]
    # it IS a control, not a bare word: the console's button is laid out as one
    assert picker["label"]["height"] >= 20 and picker["label"]["display"] != "inline", (
        f"the trigger is laid out {picker['label']!r}")
    assert picker["statusText"].startswith(ACCEPT_LEAD), (
        f"before a choice the control states {picker['statusText']!r}")

    chosen = mark["picker_after_choose"]
    assert chosen["statusText"].startswith("Selected: ") and chosen["statusText"].endswith(".png"), (
        f"choosing a file states {chosen['statusText']!r}")
    # the submit is still there: choosing a file is not uploading
    assert "Upload an image" in chosen["actsText"], chosen["actsText"]

    first, second = mark["first_pixel"], mark["second_pixel"]
    assert first["found"] and first["src"] == "/avatar.png", first
    assert first["pixel"] == mark["expected"]["first"], (
        f"the box did not draw the file just uploaded: {first['pixel']} is not "
        f"{mark['expected']['first']}")
    assert second["pixel"] == mark["expected"]["second"], (
        f"the REPLACED mark never reached the box: it still shows {second['pixel']}")
    assert second["src"] == first["src"], "the mark's address moved, so the pin proves nothing"
    assert mark["after_iframe"] != mark["iframe"], (
        f"the box is still pointed at {mark['after_iframe']!r}, the address a cache may answer")
    assert mark["state"] == "uploaded", mark["state"]
    assert [preview["w"] for preview in mark["previews"]] == [32, 64, 128, 256], mark["previews"]
    assert not report["errors"], report["errors"]


def test_pressing_the_mark_trigger_moves_nothing_and_raises_no_scrollbar(served):
    """THE OPERATOR'S SYMPTOM: pressing the trigger resized the card and raised a scrollbar.

    The trigger is a `label[for]` on a clipped input, so the press is the one gesture where the two
    can part company: the picker's focus lands on the input, and a focused box is scrolled into view
    by every scroll container above it. The pin is the three states — before, during and after a real
    press that really opens the picker — and in all three the document's own `scrollHeight` must not
    exceed its `clientHeight`, the card's must not exceed its own, and the document's scroll height,
    the slot's height, the card's height, the window's scroll position and the pane's must be
    byte-identical. The clipped input must also sit in the row it is drawn in, and the same control
    must still open the picker from the keyboard.
    """
    report = _probe(served, "press")
    press = report["press"]
    states = [press["before"], press["during"], press["after"]]

    assert press["chooser"], "a click on the trigger opened no file picker"
    for name, state in zip(("before", "during", "after"), states):
        assert state["scrollHeight"] <= state["clientHeight"], (
            f"{name} the press: the page scrolls ({state['scrollHeight']} > "
            f"{state['clientHeight']})")
        assert state["cardScrollHeight"] <= state["cardClientHeight"], (
            f"{name} the press: the card scrolls ({state['cardScrollHeight']} > "
            f"{state['cardClientHeight']})")
        assert state["inputTop"] == state["actsTop"], (
            f"{name} the press: the clipped input is drawn {state['inputTop']} px while its row is "
            f"at {state['actsTop']} px")

    boxes = {(state["scrollHeight"], state["slot"], state["card"], state["scrollY"],
              state["paneScrollTop"]) for state in states}
    assert len(boxes) == 1, (
        "the press moved the layout: "
        + ", ".join(f"{name}={state['scrollHeight']}/{state['slot']}/{state['card']}"
                    f"/scrollY {state['scrollY']}/pane {state['paneScrollTop']}"
                    for name, state in zip(("before", "during", "after"), states)))

    assert press["tab_lands_on_input"], "Tab does not reach the picker's input"
    assert press["keyboard"], "Enter on the focused input opened no file picker"
    assert not report["errors"], report["errors"]


# ================================ §Style · a preset press is visible before Save (pin 3a)

def test_a_preset_press_is_visible_before_the_save_and_the_save_reaches_the_site(served,
                                                                                live_config):
    """PIN (a), then (b) and (c) through the browser: the press moves the tile's state and the badge
    with no scroll and no save (only the unsaved draft the demo pane renders); the save writes the
    preset and the site serves that sheet.
    """
    report = _probe(served, "preset")
    preset = report["preset"]

    before = preset["before"]
    assert before["on"] == [0] and before["badges"] == [0], (
        f"the shipped palette starts at on={before['on']} badges={before['badges']}")
    pressed = preset["pressed"]
    assert pressed["checked"] == [PRESSED_TILE], (
        f"the press checked {pressed['checked']}, not the tile it pressed")
    assert pressed["on"] == [PRESSED_TILE], (
        f"a press leaves the pressed state on {pressed['on']} — the shipped palette is still 'on'")
    assert pressed["badges"] == [PRESSED_TILE], (
        f"the 'in use' badge sits on {pressed['badges']} after the press")
    assert pressed["badgeTexts"] == ["In use"], pressed["badgeTexts"]
    assert pressed["scrollY"] == before["scrollY"], (
        f"a press scrolled the window {before['scrollY']} -> {pressed['scrollY']}")
    # A press moves the tile with no save and no apply. It MAY file the unsaved draft the demo pane
    # renders (that is the pane's whole binding); anything else from a press is a request that the
    # press had no business making.
    posted = preset["requests"]
    assert all("site/draft" in request for request in posted), f"a press posted {posted}"
    assert not any("save" in request or "apply" in request for request in posted), (
        f"a press saved or applied: {posted}")

    assert any("Saved" in notice for notice in preset["saved"]["notices"]), preset["saved"]
    after = preset["after_save"]
    assert after["on"] == [PRESSED_TILE] and after["badges"] == [PRESSED_TILE], after
    assert PRESSED_PRESET in preset["sheet"]["head"], (
        f"the site is served {preset['sheet']['head']!r}")

    block = sites.load_site_block(live_config) or {}
    assert (block.get("root") or {}).get("theme", {}).get("preset") == PRESSED_PRESET, (
        f"site.yaml carries {(block.get('root') or {}).get('theme')!r}")
    assert not report["errors"], report["errors"]


def test_with_scripts_off_the_textarea_posts_and_the_save_refuses(served, live_config):
    """PIN 6: no JavaScript — the plain textarea is the control, and the save still refuses.

    The sheet is empty on a card that stores none (a transcription is never invented into the form),
    the invalid sheet posted from the textarea is refused by name, and the file is untouched.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_bytes()
    report = _probe(served, "nojs")
    nojs = report["nojs"]

    assert nojs["found"], "the card draws no expert control at all with scripts off"
    assert nojs["editor"] == 0, "a CodeMirror instance was built without JavaScript"
    assert nojs["value"] == "", f"the untouched control carries {nojs['value']!r}"
    assert any("Line 1" in notice for notice in nojs["notices"]), (
        f"the save accepted an invalid sheet: {nojs['notices']}")
    assert written.read_bytes() == before, "the refused sheet was written"
    # the mark's picker is markup: no script builds it, and choosing is still its own control
    assert nojs["picker"] == ["Choose an image"], nojs["picker"]
    assert nojs["picker_input"] == 1, "the file input is gone without JavaScript"


def test_with_scripts_off_the_label_opens_the_picker_and_the_submit_posts_it(served, live_config):
    """The no-JavaScript mark path, end to end: the browser's own label, a real picker, a post.

    Nothing in that path is scripted, so the pin is that the four re-encoded files exist at their
    sizes AFTER a browser with scripting off chose a file through the label and pressed the submit.
    """
    report = _probe(served, "nojspick")
    pick = report["nojspick"]

    assert pick["label"] == ["Choose an image"], pick["label"]
    assert pick["state_before"] == "shipped", pick["state_before"]
    assert any("uploaded" in notice.lower() for notice in pick["notices"]), pick["notices"]
    assert pick["state_after"] == "uploaded", (
        f"the card still reports {pick['state_after']!r} after the post")
    assert not pick["errors"], pick["errors"]

    for size in sites.AVATAR_SIZES:
        path = sites.avatar_path("", size, live_config)
        assert path.is_file(), f"the post wrote no {size} px file"
        with Image.open(path) as image:
            assert image.size == (size, size), f"{path.name} is {image.size}"
            assert image.mode == "RGBA", f"{path.name} is {image.mode}"


# ================================ §F · the identity line, driven in a real browser (pins 1, 2)
#
# The address and the switch are the card's `<summary>`, and the save form is their SIBLING — they
# reach it only by `form="sc-save-<token>"`. Both halves are client-side, so both are pinned here.

#: the site whose card carries BOTH an editable address and a switch.
IDENTITY_SITE = "group1"
IDENTITY_SLUG = f"f.sites.{IDENTITY_SITE}.slug"
IDENTITY_ENABLED = f"f.sites.{IDENTITY_SITE}.enabled"


def test_the_header_carries_the_address_and_the_switch_and_posts_both(served):
    """PIN 1: one row, two controls, each drawn once — and the body the form would post.

    The payload is read off the PARSED FORM (`new FormData(form)`), not off the template: with the box
    unticked the card posts `enabled=0` and nothing else, and with it ticked the hidden companion and
    the box post together — `0` then `1`, which is the order the save path reads (it takes the LAST
    value of the field). The address posts the slug in both states, so pressing the switch cannot
    lose the address.
    """
    report = _probe(served, "identity", site=IDENTITY_SITE)
    identity = report["identity"]

    assert identity["name"] == IDENTITY_SITE, f"the card's header names {identity['name']!r}"
    assert identity["in_summary"] == {"slug": 1, "switch": 1, "hidden": 1}, (
        f"the card's summary carries {identity['in_summary']}")
    assert identity["in_card"] == {"slug": 1, "switch": 1, "hidden": 1}, (
        "a control exists twice in the card — a leftover row still posts a stale value: "
        f"{identity['in_card']}")
    assert identity["form_id"], "the save form carries no id"
    assert identity["field_form"] == identity["form_id"], (
        f"the address names form {identity['field_form']!r}, not {identity['form_id']!r}")
    assert identity["switch_form"] == identity["form_id"], (
        f"the switch names form {identity['switch_form']!r}, not {identity['form_id']!r}")

    # TICKED: the hidden companion first, the box second — the save reads the last value
    assert identity["checked"] is True, "the card opens with its switch already off"
    assert identity["ticked"][IDENTITY_ENABLED] == ["0", "1"], (
        f"with the box ticked the form carries {identity['ticked'][IDENTITY_ENABLED]}")
    assert identity["ticked"][IDENTITY_SLUG] == [IDENTITY_SITE], (
        f"the address posts {identity['ticked'][IDENTITY_SLUG]}")
    assert identity["ticked_words"] == ["on"], identity["ticked_words"]

    # A PRESS INSIDE THE DISCLOSURE DOES NOT TOGGLE THE CARD
    assert identity["open_after_untick"] is True, "pressing the switch closed the card"

    # UNTICKED: the companion alone, which is the whole point of it
    assert identity["unticked_checked"] is False, "the press did not reach the box"
    assert identity["unticked"][IDENTITY_ENABLED] == ["0"], (
        f"with the box unticked the form carries {identity['unticked'][IDENTITY_ENABLED]} — the save "
        "would keep the site on")
    assert identity["unticked"][IDENTITY_SLUG] == [IDENTITY_SITE], (
        "the address is lost while the switch is off")
    assert identity["unticked_words"] == ["off"], (
        f"the switch still says {identity['unticked_words']} after the press")
    assert identity["unticked_track"] != identity["ticked_track"], (
        "the track did not move on the press, so the switch shows a state the box is not in")

    # AND BACK: the box and its word are the state, not the render
    assert identity["reticked_checked"] is True, "the second press did not reach the box"
    assert identity["open_after_retick"] is True, "the second press closed the card"
    assert identity["reticked"][IDENTITY_ENABLED] == ["0", "1"], (
        f"ticking again posts {identity['reticked'][IDENTITY_ENABLED]}")
    assert identity["reticked_words"] == ["on"], identity["reticked_words"]
    assert identity["reticked_track"] == identity["ticked_track"], (
        "the track did not come back to the on state")
    assert not report["errors"], report["errors"]


def test_the_identity_line_is_one_row_and_never_overflows(served, tmp_path):
    """PIN 2 — the geometry the design measures, read off the live page at five widths.

    One row at the card's width (the name and the controls on one line, the controls ending on the
    header's own padding edge), the two character chips folded below 1050, the controls on their own
    row at 560 and 430, and not one box inside the header past the header's edge at any width.

    The CARD's own overflow is measured too, and deliberately not pinned: below a ~340 px card the
    branding block's 96 px mark slot and its text already overflow it, in a block this row does not
    touch. The header is measured on its own above, so a regression in the row cannot hide there.
    """
    shots = {name: str(tmp_path / f"header-{name}.png")
             for name in ("wide", "wide_page", "narrow", "narrow_page")}
    report = _probe(served, "identitylayout", site=IDENTITY_SITE,
                    extra=(shots["wide"], shots["wide_page"],
                           shots["narrow"], shots["narrow_page"]))
    print("SHOTS", shots, file=sys.stderr)
    ladder = report["identity"]["ladder"]
    print("LADDER", json.dumps(ladder, indent=1), file=sys.stderr)

    for width, state in ladder.items():
        assert state["headerOverflow"] <= 0, (
            f"at {width} the header overflows by {state['headerOverflow']} px")
        assert not state["headerOffenders"], (
            f"at {width} these boxes are drawn past the header's own edge: {state['headerOffenders']}")
        assert state["pageOverflow"] <= 0, (
            f"at {width} the document scrolls sideways by {state['pageOverflow']} px")
        # one right edge, and it is the header's own
        assert abs(state["controlsRight"] - state["headerEdge"]) <= 1, (
            f"at {width} the controls end at {state['controlsRight']}, the header's edge is "
            f"{state['headerEdge']}")
        # the address never collapses below its 5ch floor
        assert state["fieldWidth"] and state["fieldWidth"] >= 40, (
            f"at {width} the address field is {state['fieldWidth']} px")

    wide = ladder["1440"]
    assert wide["oneRow"] is True, f"at 1440 the line is not one row: {wide}"
    # the ADDRESS chip and the two character chips
    assert wide["chipsShown"] == 3, f"at 1440 the header shows {wide['chipsShown']} chips"

    # the chips fold first, so the line is still one row with the controls alone. The card is the
    # viewport minus the console's 248 px rail and its padding, so 884 here is the design's 596 px
    # card — the narrowest one-row card it measured. The ADDRESS is the one chip that does not fold:
    # it is the identity the row is read by and is stated nowhere else on it.
    for width in ("1050", "884"):
        assert ladder[width]["chipsShown"] == 1, (
            f"at {width} the header draws {ladder[width]['chipsShown']} chips — the address must "
            f"survive the fold and the two character chips must not")
        assert ladder[width]["oneRow"] is True, (
            f"at {width} the line wrapped before it had to: {ladder[width]}")

    # THE NARROWEST CARD TAKES THE NARROW MODEL. The address chip adds the card's own name to the
    # first line, so a 412 px card (the 700 px viewport) is past the width where the key and the
    # controls share one line: the controls drop to their own row, as they already do at 560 and 430.
    # Nothing is cut and nothing overflows — the field keeps the width the flag and the switch leave.
    assert ladder["700"]["chipsShown"] == 1, f"at 700 the header draws {ladder['700']['chipsShown']} chips"
    assert ladder["700"]["headerOverflow"] <= 0, ladder["700"]

    # narrower than that the controls take their own row
    for width in ("700", "620", "560", "430"):
        assert ladder[width]["oneRow"] is False, (
            f"at {width} the controls did not take their own row: {ladder[width]}")
    assert not report["errors"], report["errors"]


def test_the_retired_wizard_show_control_is_gone_from_every_source():
    """PIN 3 — the `.sc-brand-pv` lift and its `[data-sc-wiz-show]` handler are dead code.

    `site_config.html` draws neither control (one preview replaced both), so the wizard's slot and
    its lift handler can never fire. They are deleted from the template and the wizard script, and
    no CSS rule carries the retired class.
    """
    for path in (REPO_ROOT / "services" / "webservice" / "templates" / "site_config.html",
                 REPO_ROOT / "services" / "webservice" / "static" / "site-palette-wizard.js",
                 REPO_ROOT / "services" / "webservice" / "static" / "settings-scope.css"):
        text = path.read_text(encoding="utf-8")
        for token in ("data-sc-wiz-show", "sc-brand-pv", "sc-pv-lift"):
            assert token not in text, f"{path.name} still carries {token!r}"


# ====================== §4 · Expert Mode is a view control — the never-clear rule (pins 1 and 2)
#
# Pin 1's other half: a PRESET site stores no sheet, so its bar must say the transcription is
# SHOWN, not stored — and a Save with an untouched transcription must keep it absent.

def test_a_preset_site_shows_the_transcription_but_stores_nothing(served, live_config):
    """PIN 1 — the shown sheet is not the saved sheet, and the bar says so.

    The default fixture's site names no sheet. Opening Expert Mode draws the shipped transcription
    in the editor while the CARRIER stays empty: the bar's badge and line must say "shown, not
    stored". Saving without an edit posts the empty carrier, succeeds, and leaves ``theme.expert``
    absent — the same bytes the file carried before the run. One keystroke fills the carrier and the
    second Save stores the sheet; the file is read back to prove both sides.
    """
    written = live_config / sites.SITE_FILE
    before = written.read_text(encoding="utf-8")
    assert "theme.expert" not in before and "expert:" not in before

    report = _probe(served, "expertempty", written)["expert"]

    opened = report["opened"]
    assert opened["checked"] is True, "ticking Expert Mode did not open the sheet"
    assert opened["sheetDrawn"] is True, "the box is ticked and the sheet is not drawn"
    assert opened["carrier"] == "", "the carrier is not empty on a site that stores no sheet"
    assert opened["text"] == SHEET.text, "the editor does not show the shipped transcription"
    assert opened["badge"] == UNSTORED_BADGE, f"the bar's badge reads {opened['badge']!r}"
    assert opened["when"] == UNSTORED_WHEN, f"the bar's line reads {opened['when']!r}"
    assert opened["loadDisabled"] is True, "the restore control is live while the carrier is empty"
    assert opened["count"] == COPY_COUNT, f"the foot's bound reads {opened['count']!r}"

    saved = report["saved"]
    assert saved["notices"] and any("Saved" in notice for notice in saved["notices"]), saved["notices"]
    after = report["saved_bytes"]
    print("SAVED_BEFORE", repr(before), file=sys.stderr)
    print("SAVED_AFTER_FIRST", repr(after), file=sys.stderr)
    # the saved bytes: the untouched Save wrote no sheet and did not refuse the preset. The save
    # normalises the card's effective values, so the file is not byte-identical — but the sheet is
    # still absent and the preset is still the site's palette.
    assert "theme.expert" not in after and "expert:" not in after, (
        "the untouched Save stored a sheet:\n" + after
        + "\nBODIES: " + _expert_bodies(saved.get("bodies"))
        + "\nPOSTS: " + str(saved.get("posts"))
        + "\nLOGS: " + str(report.get("logs"))
        + "\nBEFORE:\n" + before
        + "\nNOTICES: " + str(saved.get("notices")))
    from core.utils.yaml_helper import new_yaml
    saved_block = new_yaml().load(after) or {}
    saved_theme = ((saved_block.get("sites") or {}).get("root") or {}).get("theme") or {}
    assert "expert" not in saved_theme, (
        "the untouched Save stored the transcription as a sheet:\n" + after)
    assert "preset" not in saved_theme or not saved_theme.get("preset"), (
        "the untouched Save named a preset the card did not post:\n" + after)

    reloaded = report["reloaded"]
    assert reloaded["noneDrawn"] is True, "the reloaded card hides the fact that there is no sheet"
    assert reloaded["storedDrawn"] is False
    assert "No expert sheet." in reloaded["noneText"], reloaded["noneText"]
    assert "preset" in reloaded["noneText"], reloaded["noneText"]
    assert "shown, not stored" in reloaded["noneText"], reloaded["noneText"]
    assert "write a sheet" not in reloaded["noneText"], (
        "the absent sentence still claims opening Expert Mode writes a sheet: " + reloaded["noneText"])
    assert report["header"] == {"words": "A shipped preset", "key": "theme.preset", "value": ""}, \
        report["header"]

    # ONE KEYSTROKE: the carrier fills, the badge stops claiming the copy is only shown. A space is
    # still the copy — the badge is the copy's own name, not the "not stored" one, and "edited" is
    # reserved for a transcription that no longer equals the shipped sheet.
    edited = report["edited"]
    assert edited["badge"] == COPY_BADGE, f"the edited badge reads {edited['badge']!r}"
    assert edited["carrier"].strip() == SHEET.text.strip()
    assert edited["text"].endswith(" "), "the keystroke did not reach the editor"

    saved_edit = report["saved_edit"]
    assert saved_edit["notices"] and any("Saved" in notice for notice in saved_edit["notices"]), \
        saved_edit["notices"]
    after_edit = written.read_text(encoding="utf-8")
    assert "expert:" in after_edit, "the edited Save stored no sheet:\n" + after_edit
    block = sites.load_site_block(live_config) or {}
    stored = ((block.get("root") or {}).get("theme") or {}).get("expert") or ""
    assert stored.replace("\r\n", "\n").strip() == SHEET.text.strip(), (
        "the saved sheet is not the transcription the operator edited:\n" + stored)

    reloaded_edit = report["reloaded_edit"]
    assert reloaded_edit["checked"] is True, "the reloaded card lost the stored sheet"
    assert reloaded_edit["storedDrawn"] is False and reloaded_edit["noneDrawn"] is False
    assert report["header_edit"] == {"words": "An expert sheet", "key": "theme.expert",
                                     "value": "30 declarations"}, report["header_edit"]

def _expert_bodies(bodies) -> str:
    """The theme.expert value each POST carried, compact enough for an assertion message."""
    if not bodies:
        return "none"
    lines = []
    for index, item in enumerate(bodies):
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            url, navigation, body = item[0], item[1], item[2]
        elif isinstance(item, (list, tuple)):
            url, body = item[0], item[1]
            navigation = False
        else:
            url, navigation, body = "", False, str(item)
        marker = "theme.expert"
        value = ""
        if marker in body:
            tail = body.split(marker, 1)[1]
            value = tail.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0] if "\r\n\r\n" in tail else ""
        lines.append(f"#{index} {url.rsplit('/', 1)[-1]} nav={navigation} len={len(value)} "
                     f"start={value[:40]!r} end={value[-20:]!r}")
    return " | ".join(lines)


def _stored_sheet(config_dir) -> str:
    """The sheet the site file stores for the root site."""
    block = sites.load_site_block(config_dir) or {}
    return ((block.get("root") or {}).get("theme") or {}).get("expert") or ""


def test_unticking_expert_mode_and_saving_does_not_clear_the_sheet(sheet_served, sheet_config):
    """PIN 1 — the reason the block exists: unticking the box must not delete a site's palette.

    The box has no `name` and posts nothing, and the CARRIER (the textarea) is never written by it:
    unticking hides the sheet on screen and states it instead. The proof is the file — the sheet the
    site stored before the run is the sheet it stores after a save performed with the box unticked —
    and the composed palette the public route serves.

    On byte identity: the value compared is the stored sheet, and it is compared exactly — with one
    guard. A browser bakes CRLF into a textarea even when the source used LF, so the comparison
    removes carriage returns from both sides; that escape is the only difference a form post can ever
    make to a sheet's text (and the write path stores LF, so in practice there are none). Nothing else
    moves: no declaration is added, dropped or rewritten.
    """
    written = sheet_config / sites.SITE_FILE
    before = _stored_sheet(sheet_config)
    assert before == SHEET_TEXT, "the fixture's sheet is not the transcription, so this pin is moot"

    report = _probe(sheet_served, "expertmode")["expert"]

    # PIN 2: the control — the console's own switch, on the bar of the block that draws the sheet,
    # and it carries no name
    toggle = report["toggle"]
    assert toggle is not None, "the admin's card draws no Expert Mode box"
    assert (toggle["tag"], toggle["type"], toggle["role"]) == ("INPUT", "checkbox", "switch"), toggle
    assert toggle["name"] is None, f"the box posts a field named {toggle['name']!r}"
    assert toggle["block"] == "expert", (
        f"the box sits in the {toggle['block']!r} block, not on the sheet's own bar")
    assert toggle["inPalette"] is False, "the palette block still carries the Expert Mode box"
    assert toggle["inHead"] is True, "the box is not on its block's header bar"
    assert toggle["label"] == "Expert Mode", toggle["label"]

    opened = report["opened"]
    assert opened["checked"] is True, "a site whose palette IS a sheet opens with the box ticked"
    assert opened["sheetDrawn"] is True, "the box is ticked and the sheet is not drawn"
    assert opened["stripDrawn"] is False, "the closed strip is drawn while the sheet is open"
    assert opened["carrier"] == before, "the carrier does not hold the stored sheet"
    # §4.3: the sheet is the card's OWN width, at the shipped editor's 460 px and 52 px gutter
    assert opened["editorH"] == 460, f"the sheet's editor measures {opened['editorH']} px"
    assert opened["gutter"] == 52, f"the gutter measures {opened['gutter']} px"
    assert opened["sheetW"] >= opened["cardW"] - 40, (
        f"the sheet is {opened['sheetW']} px inside a {opened['cardW']} px card")

    # UNTICK: the sheet goes off screen, the carrier keeps the sheet, the strip states it
    closed = report["closed"]
    assert closed["checked"] is False, closed
    assert closed["sheetDrawn"] is False, "unticking leaves the sheet on screen"
    assert closed["carrier"] == opened["carrier"], "unticking wrote to the carrier"
    assert closed["stripDrawn"] is True, "the closed state draws nothing where the sheet was"
    assert f"{SHEET_DECLARATIONS} declarations" in closed["stripText"], closed["stripText"]
    assert "clears nothing" in closed["stripText"], closed["stripText"]
    assert closed["noneDrawn"] is False, "a site with a sheet is told it has none"
    assert closed["say"] == ("One palette, one choice — the box at the foot of the page shows it "
                             "as you press a palette."), closed["say"]
    assert report["saved"]["notices"] and any("Saved" in notice
                                              for notice in report["saved"]["notices"])

    # TICK again: the sheet comes back, with the stored text, before the save that matters
    reopened = report["reopened"]
    assert report["editor"] == 1, "the page built no editor for the sheet"
    assert reopened["sheetDrawn"] is True
    assert reopened["carrier"] == opened["carrier"]
    assert reopened["editorText"].strip() == SHEET_TEXT.strip(), (
        "ticking the box shows a sheet other than the one stored")

    # THE PIN: the save that ran with the box UNTICKED wrote the sheet it had
    assert report["closed"]["checked"] is False, "the save ran with the box ticked"
    after = _stored_sheet(sheet_config)
    assert after.strip(), "unticking Expert Mode emptied the sheet"
    assert after.replace("\r", "") == before.replace("\r", ""), (
        "unticking Expert Mode changed the sheet:\n"
        f"before={before.strip()[:120]!r}\nafter={after[:120]!r}")
    # …and it is the same palette, token for token
    assert [line for line in after.splitlines() if line.strip()] == \
        [line for line in before.splitlines() if line.strip()]
    assert written.read_text(encoding="utf-8").count("--brass: #d9a04a;") == 1

    # the card that comes back says the same thing: the palette IS a sheet, so the box is ticked
    reloaded = report["reloaded"]
    assert reloaded["checked"] is True, "the reloaded card lost the fact that the palette is a sheet"
    assert reloaded["stripDrawn"] is False
    assert reloaded["carrier"] == after
    assert report["header"] == {"words": "An expert sheet", "key": "theme.expert",
                                "value": f"{SHEET_DECLARATIONS} declarations"}, report["header"]
