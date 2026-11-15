"""M4-fix2: the page comes back AFTER the write, every console page says `no-store`, and the upload's
file control is the console's own.

Also the file picker does not look great yet. It still has a grey button 'Datei auswählen'."*

Three fixes, one file each:

* ``static/submit.js`` — the interceptor now navigates ONCE THE WRITE HAS ANSWERED (``redirect:
  "manual"`` resolves after the route has run and committed the action), so the fresh render reads the
  post-write state instead of the stale pre-write one. The wait is shown on the pressed control with
  the console's own busy idiom, and a second press while one is pending is ignored (one request per
  press, nothing retried). The ORDER is pinned by a browser-less harness over the REAL shipped script
  (``submit_harness.js``) — a markup assertion cannot see a race.
* the console's pages carry ``Cache-Control: no-store`` so a re-navigation re-renders rather than
  coming out of the browser's cache.
* ``templates/server_detail.html`` + ``static/filepick.js`` + ``shell.css`` — the raw
  ``<input type="file">`` (whose button the browser draws in the browser's own language) is replaced by
  the console's own control: a clipped-but-operable real input, a ``<label for=...>`` wearing the
  tab's chip, and the chosen file's name beside it (pinned by ``filepick_harness.js``).

Driven through the real app with the real mission actions and a stub cluster.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.webservice.pages import actions as actions_page
from test_webui_missions_tab import (  # noqa: F401 - fixtures/helpers imported on purpose
    SERVER_A, configured_fleet, missions_page,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in, token_of,
)

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services" / "webservice"
SUBMIT_JS = SERVICES / "static" / "submit.js"
FILEPICK_JS = SERVICES / "static" / "filepick.js"
SHELL_CSS = SERVICES / "static" / "shell.css"
SUBMIT_HARNESS = TESTS / "submit_harness.js"
FILEPICK_HARNESS = TESTS / "filepick_harness.js"

MISSIONS_TAB = f"/fleet/{SERVER_A}?tab=missions"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


def _run_harness(harness: Path, script: Path) -> dict:
    assert harness.is_file(), f"the harness is missing: {harness}"
    assert script.is_file(), f"the shipped script is missing: {script}"
    done = subprocess.run(["node", str(harness), str(script)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def interceptor() -> dict:
    """The shipped submit.js's own answers, driven by the harness."""
    return _run_harness(SUBMIT_HARNESS, SUBMIT_JS)


@pytest.fixture(scope="module")
def filepick() -> dict:
    """The shipped filepick.js's own answers, driven by the harness."""
    return _run_harness(FILEPICK_HARNESS, FILEPICK_JS)


# ============================================ 1. the navigation waits for the write's answer

@requires_node
def test_the_interceptor_navigates_only_after_the_write_has_answered(interceptor):
    """THE card's headline. On press the POST fires; NOTHING navigates until the answer lands.

    BEFORE the fix the script called ``window.location.assign(to)`` alongside the fetch, so the fresh
    render raced the write and the removed mission was still listed. The harness drives the REAL
    script: after the submit the request is on the wire, the control is busy, and no navigation has
    happened — the navigation appears only once the answer is resolved.
    """
    waits = interceptor["waits"]
    before = waits["before"]
    assert before["prevented"] is True, "the interceptor must own the submit"
    assert before["requests"] == 1, "exactly one request per press"
    assert before["navigations"] == [], "the page must NOT be handed over before the write answers"
    assert before["order"] == ["write"], "the write is fired first and nothing navigates yet"
    assert waits["stillWaiting"] == [], "still no navigation after a tick: the wait is real"
    after = waits["after"]
    assert after["order"] == ["write", "navigate"], "write THEN navigate — never alongside"
    assert after["navigations"] == ["/fleet/SRS-1?tab=missions"], \
        "the destination is the page the control is on (path + query, no fragment)"


@requires_node
def test_the_wait_is_visible_on_the_pressed_control(interceptor):
    """The card's busy idiom, on the control that was pressed: ``busy`` + ``aria-busy`` while the
    write is in flight, cleared once the answer has landed (so the fresh page is not painted with a
    stale pulse), and the control is NEVER disabled."""
    before = interceptor["waits"]["before"]
    after = interceptor["waits"]["after"]
    assert before["busyClass"] is True, "the pressed control carries the console's busy class"
    assert before["busyAria"] is True, "and the accessible statement rides with it"
    assert after["busyClass"] is False and after["busyAria"] is None, \
        "the pulse ends once the answer has landed"


@requires_node
def test_a_slow_write_is_never_navigated_past(interceptor):
    """The exact shape of the operator's bug: the write takes a moment. The page must NOT come back before
    it: the navigation is still absent while the request is running, and lands only on the answer."""
    slow = interceptor["slow"]
    assert slow["beforeResolve"] == [], "an in-flight write must never be navigated past"
    assert slow["stillBefore"] == [], "and not after a delay either — the answer is what releases it"
    assert slow["after"] == ["/fleet/SRS-1?tab=missions"]


@requires_node
def test_a_json_answer_can_move_the_page_the_write_landed_on(interceptor):
    """A rename moves the page, and the route says where: the script must go THERE.

    ``fetch(redirect: "manual")`` answers an opaque response — the 303's ``Location`` is unreadable —
    so a background write that renames its own target used to come back to the OLD address and land
    on a 404. The route answers the interceptor's JSON request with the destination instead.
    """
    assert interceptor["serverRedirect"]["navigations"] == ["/fleet/RENAMED?tab=configuration"]


@requires_node
def test_a_refusal_and_a_network_failure_both_still_navigate(interceptor):
    """The rules kept: a refusal ANSWERS (the write was attempted; the one-shot notice on the page
    they land on is how they learn the outcome), and a network failure is an honest no-op — both
    navigate, and nothing is retried."""
    assert interceptor["refusal"]["navigations"] == ["/fleet/SRS-1?tab=missions"], \
        "a refusal navigates so its notice is seen"
    assert interceptor["network"]["navigations"] == ["/fleet/SRS-1?tab=missions"], \
        "a network failure navigates too (the write was attempted once)"


@requires_node
def test_one_press_is_one_request_and_the_dialog_returns_home(interceptor):
    """A second press while a write is pending is IGNORED (one request, nothing retried), and a
    dialog form returns to the page its ``data-back`` states."""
    assert interceptor["doublePress"]["requests"] == 1, \
        "a second press must not send a second request (the confirm token is spent)"
    assert interceptor["doublePress"]["navigations"] == ["/fleet/SRS-1?tab=missions"]
    assert interceptor["dataBack"]["navigations"] == ["/logs"], \
        "the dialog navigates to the page it states, not the page it is on"


def test_the_shipped_interceptor_keeps_the_rules_it_had():
    """The invariants the earlier cards pinned, read from the shipped file: still ONE fetch, the
    SAME body, no followed 303, and — the rule that matters most — the control is never disabled."""
    source = SUBMIT_JS.read_text(encoding="utf-8")
    assert source.count("fetch(") == 1, "one request per press, nothing retried"
    assert "new FormData(form)" in source, "the form's own body is what is sent"
    assert 'redirect: "manual"' in source, "fetch must not follow the 303 itself"
    assert "keepalive: true" in source, "an abandoned write still finishes server-side"
    assert ".disabled" not in source, "the pressed control is never disabled (W4g's rule)"


# ==================================== 2. a re-navigation must re-render (no-store on the pages)

@pytest.mark.asyncio
async def test_every_console_page_carries_no_store(config_dir, clean_registry):
    """A write now returns only once its route has answered, but the BROWSER must not answer the
    navigation out of its cache — or the stale list comes back even with the race fixed. The
    console's own pages say ``Cache-Control: no-store``: the dashboard, the list pages, the server
    pages, the dialog and the Logs page."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")            # Admin: reaches the Logs page too
        dashboard = await http.get("/")
        listing = await http.get("/fleet")
        detail = await http.get(MISSIONS_TAB)
        dialog = await http.post(
            f"{actions_page.MISSIONS_DELETE_PATH}{actions_page.CONFIRM_SUFFIX}",
            data={"server": SERVER_A, "_csrf_token": token_of(detail.text), "mission": "1"})
        logs = await http.get("/logs")
    for label, reply in (("the dashboard", dashboard), ("a list page", listing),
                         ("the server page", detail), ("the dialog", dialog),
                         ("the Logs page", logs)):
        assert reply.status_code == 200, (label, reply.status_code, reply.text[:160])
        assert reply.headers.get("cache-control") == "no-store", f"{label} must carry no-store"


@pytest.mark.asyncio
async def test_the_page_the_write_returns_to_is_a_console_page_with_no_store(config_dir,
                                                                            clean_registry):
    """The server page a mission write 303s back to is exactly the page that must re-render — pinned
    directly, so a future page that drops the header reds out on the page the operator actually uses."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        reply = await http.get(MISSIONS_TAB)
    assert reply.status_code == 200
    assert reply.headers.get("cache-control") == "no-store"
    assert "Training.miz" in page, "the tab rendered its list (the page under test)"


# ============================================ 3. a slow delete still lands on the fresh list

@pytest.mark.asyncio
async def test_a_slow_delete_lands_on_a_page_without_the_deleted_mission(config_dir, clean_registry):
    """End to end at the SERVER: the delete route answers (the 303) only AFTER the action has run, so
    following that answer lands on a page rendered from the post-delete state. The interceptor's half
    (waiting for that answer) is pinned by the harness above; this is the other half — the answer is
    not sent early. The removal is made SLOW on purpose, which is the shape that exposed the bug."""
    cluster = configured_fleet()
    target = cluster.servers[0]
    original = target.deleteMission

    async def slow_delete(mission_id):
        await asyncio.sleep(0.05)
        return await original(mission_id)

    target.deleteMission = slow_delete
    app = build(config_dir, cluster, plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        page = await missions_page(http)
        assert "<b>Training.miz</b>" in page, "the mission the operator is about to remove"
        dialog = await http.post(
            f"{actions_page.MISSIONS_DELETE_PATH}{actions_page.CONFIRM_SUFFIX}",
            data={"server": SERVER_A, "_csrf_token": token_of(page), "mission": "1"})
        confirm_match = re.search(r'name="_confirm_token" value="([^"]+)"', dialog.text)
        assert confirm_match, "the dialog carries no confirm token"
        reply = await http.post(
            actions_page.MISSIONS_DELETE_PATH,
            data={"server": SERVER_A, "_csrf_token": token_of(dialog.text), "mission": "1",
                  "_confirm_token": confirm_match.group(1)})
        assert reply.status_code == 303, (reply.status_code, reply.text)
        # the destination the interceptor now WAITS for; following it lands post-write
        landed = await http.get(reply.headers["location"])
    assert target.deletes == [1], "the slow write ran, once"
    assert landed.status_code == 200
    assert "<b>Training.miz</b>" not in landed.text, \
        "the page the answer leads to must not still list the deleted mission"
    assert "<b>Rotated.miz</b>" in landed.text, "the rest of the rotation is still listed"


# ============================================ 4. the upload control is the console's own

@pytest.mark.asyncio
async def test_the_upload_control_is_hidden_but_operable_and_has_no_native_button(config_dir,
                                                                                 clean_registry):
    """The raw ``<input type="file">`` is replaced by the console's own control: the REAL input stays
    in the DOM, focusable and announced (clipped, never ``display:none``, never ``aria-hidden``), a
    ``<label for=...>`` wearing the tab's chip opens it, and the chosen file's name lives in the
    console's own span — no browser-drawn button and no browser-language text anywhere."""
    app = build(config_dir, configured_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await missions_page(http)

    match = re.search(r'<input[^>]*type="file"[^>]*>', html)
    assert match, "the real file input must stay in the DOM"
    tag = match.group(0)
    assert 'name="file"' in tag and 'accept=".miz"' in tag, "the field the action needs is unchanged"
    assert 'id="upload-file"' in tag and 'class="file-input-sr"' in tag, "clipped, not display:none"
    assert "display:none" not in tag and "display: none" not in tag, "never display:none"
    assert "aria-hidden" not in tag, "never aria-hidden: it stays announced"
    assert "<label" not in tag

    assert '<label class="file-pick" for="upload-file">Choose a .miz file…</label>' in html, \
        "a label wearing the console's button chip opens the picker"
    assert 'data-file-name' in html and 'aria-live="polite"' in html, \
        "the chosen name is shown in the console's own span"
    assert '<script src="/static/filepick.js" defer></script>' in html, "the readout script loads"

    # no browser-language chrome can be read: the native control is clipped away and the copy is ours
    for wording in ("Datei auswählen", "No file chosen", "Keine Datei"):
        assert wording not in html, f"browser chrome leaked into the console: {wording}"


def test_the_stylesheet_clips_the_input_and_draws_the_focus_on_the_label():
    """The hiding is the CLIP technique (a 1px box, never ``display:none``) and the keyboard focus ring
    is drawn on the LABEL when the clipped input has focus, so a keyboard user can see where they are."""
    css = SHELL_CSS.read_text(encoding="utf-8")
    clipped = re.search(r"\.file-input-sr\s*\{([^}]*)\}", css)
    assert clipped, "the clipped input needs its own rule"
    body = clipped.group(1)
    assert "width: 1px" in body and "height: 1px" in body and "clip" in body, "the clip technique"
    assert "display: none" not in body and "display:none" not in body
    assert re.search(r"\.file-input-sr:focus-visible\s*\+\s*\.file-pick\s*\{[^}]*outline", css), \
        "the focus ring is drawn on the label the operator sees"
    assert ".cfg-form .st-w .file-pick" in css, "the label shares the tab's own button chip"


@requires_node
def test_the_chosen_files_name_appears_and_changes(filepick):
    """The rendered probe: with nothing chosen the console shows nothing (never the browser's "no file
    chosen"); a picked file's NAME appears; picking again changes it; clearing the pick clears it."""
    assert filepick["initial"] == "", "no browser wording when nothing is chosen"
    assert filepick["firstPick"] == "Training.miz", "the chosen file's name is shown"
    assert filepick["secondPick"] == "Rotated.miz", "picking again changes it"
    assert filepick["cleared"] == "", "clearing the pick clears the name"


def test_the_file_control_scripts_are_shipped_where_they_are_asked_for():
    for path in (SUBMIT_JS, FILEPICK_JS):
        assert path.is_file(), f"/static/{path.name} is referenced but not shipped"
