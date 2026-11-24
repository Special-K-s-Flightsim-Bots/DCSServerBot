"""The Log tab, round two (L2): the level filter, the downloads, and the optional Events tab.

the operator's four asks are the spine: the "Load older" control must wear the console's button style; the
DCS log must offer the SAME level vocabulary the bot log does; the tab must list and download the
server's log artifacts; and — while the debug plugin is active — an Events tab must do the same for
``events.log``. This module drives the app end to end: the filter vocabulary shared with the bot log,
a filtered page that WALKS BACK rather than lies empty, the artifact listing and its order, a crafted
artifact id that cannot leave the log directory, the Admin gate, the too-large refusal, the Events tab
offered only with the plugin, and the Events download limited to its two files.
"""
from __future__ import annotations

import os
import re

import pytest

from conftest import permissions
from services.webservice.pages import logs as logs_page
from services.webservice.pages import server_detail as detail_page
from services.webservice.readmodels import dcslog as dcs_log
from services.webservice.readmodels import logtail
from test_webui_server_log_tab import (  # noqa: F401 - fixtures/helpers imported on purpose
    MASTER, SERVER, PAGE, WINDOW, WindowNode, admin_app, attr_value, clean_registry, client,
    cluster, config_dir, log_server, sign_in, write_log,
)

EVENTS_PAGE = f"/fleet/{SERVER}?tab=events"
DOWNLOAD = f"/fleet/{SERVER}/log/download"

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "services", "webservice", "static", "shell.css")


def write_file(home, name, text, mtime=None):
    path = home / "Logs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" — the raw bytes are what the download route serves, so a Windows host must not
    # translate the fixture's "\n" into "\r\n" on disk.
    path.write_text(text, encoding="utf-8", newline="\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def debug_node():
    """A node whose configured plugin list names the debug plugin — the Events tab's switch."""
    return WindowNode(MASTER, plugins=["debug"])


# ------------------------------------------------------------------- 1. the filter vocabulary

def test_the_filter_vocabulary_is_the_bot_logs_own():
    """Mirror, not a second vocabulary: the DCS tab offers LITERALLY the bot log's choices and labels."""
    assert dcs_log.LEVEL_CHOICES == logtail.LEVEL_CHOICES
    assert dcs_log.LEVEL_LABELS == logtail.LEVEL_LABELS
    assert dcs_log.DEFAULT_LEVEL == logtail.DEFAULT_LEVEL
    assert dcs_log.LEVEL_FILTERS == logtail.LEVEL_FILTERS
    assert dcs_log.normalise_level is logtail.normalise_level
    assert [label for _, label in dcs_log.LEVEL_CHOICES] == ["INFO+", "WARNING+", "ALL"]


def test_the_level_parse_is_the_dcs_one_not_the_bot_logs():
    """The DCS reader parses the DCS line shape (space-separated level), the bot log its tab shape."""
    row = dcs_log.parse_line(b"2026-10-04 12:00:00.000 ERROR   APP: boom")
    assert row.level == "ERROR" and row.css == "e" and row.text == "APP: boom"
    assert dcs_log.keeps(row, dcs_log.LEVEL_FILTERS["warning"]) is True
    assert dcs_log.keeps(dcs_log.parse_line(b"2026-10-04 12:00:00.000 DEBUG   APP: noise"),
                         dcs_log.LEVEL_FILTERS["warning"]) is False


@pytest.mark.asyncio
async def test_the_tab_renders_the_bot_logs_level_choices(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: hello\n")
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    for key, label in dcs_log.LEVEL_CHOICES:
        assert f">{label}<" in html, label
        assert f"?tab=log&amp;level={key}" in html or f"?tab=log&level={key}" in html, key


# ------------------------------------------------------------------- 2. the filtered page walks back

@pytest.mark.asyncio
async def test_a_filtered_tail_walks_back_rather_than_lying_empty(config_dir, tmp_path,
                                                                  clean_registry):
    """The window ending at EOF is DEBUG-heavy; the one ERROR is FARTHER BACK. A filtered tail must walk
    back to it, not return an empty page."""
    noise = "".join(f"2026-10-04 12:00:{i % 60:02d}.000 DEBUG   NOISE-{i:05d} "
                    f"{'x' * 60}\n" for i in range(1500))
    write_log(tmp_path, "2026-10-04 11:00:00.000 ERROR   EARLY-ERROR\n" + noise)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(f"{PAGE}&level=warning")).text
    assert "EARLY-ERROR" in html, "the filtered tail must walk back to the one ERROR line"
    assert "NOISE-00000" not in html, "DEBUG lines are filtered out at WARNING+"


@pytest.mark.asyncio
async def test_a_filtered_page_back_walks_back_to_the_matching_line(config_dir, tmp_path,
                                                                    clean_registry):
    """A page-back whose window holds no matching line walks into older windows and reports it filled or
    reached the start — never an empty page that hides older matching lines."""
    noise = "".join(f"2026-10-04 12:00:{i % 60:02d}.000 DEBUG   NOISE-{i:05d} "
                    f"{'x' * 60}\n" for i in range(1500))
    path = write_log(tmp_path, "2026-10-04 11:00:00.000 ERROR   EARLY-ERROR\n" + noise)
    size = os.path.getsize(path)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        payload = (await http.get(WINDOW, params={"behind": str(size), "level": "warning"})).json()
    assert payload["available"] is True
    assert "EARLY-ERROR" in payload["lines"], "the walk must find the older matching line"
    assert payload["at_start"] is True, "the start of the file was reached"
    assert payload["before"] == 0, "no older matching line exists, so the button says 'Start of log'"


@pytest.mark.asyncio
async def test_an_unfiltered_page_back_does_not_walk(config_dir, tmp_path, clean_registry):
    """The SAME window, unfiltered, holds only DEBUG lines — proving the walk (not the window) found the
    ERROR above."""
    noise = "".join(f"2026-10-04 12:00:{i % 60:02d}.000 DEBUG   NOISE-{i:05d} {'x' * 60}\n"
                    for i in range(1500))
    path = write_log(tmp_path, "2026-10-04 11:00:00.000 ERROR   EARLY-ERROR\n" + noise)
    size = os.path.getsize(path)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        payload = (await http.get(WINDOW, params={"behind": str(size), "level": "all"})).json()
    assert "EARLY-ERROR" not in payload["lines"]
    assert "NOISE-" in payload["lines"]


@pytest.mark.asyncio
async def test_a_filtered_page_with_no_matching_line_says_so(config_dir, tmp_path, clean_registry):
    """A file with no line at the chosen level: the tab says it, never a blank panel."""
    write_log(tmp_path, "2026-10-04 12:00:00.000 DEBUG   ONLY-DEBUG\n")
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(f"{PAGE}&level=warning")).text
    assert "No log lines match the selected level" in html


# ------------------------------------------------------------------- 3. the artifact listing

@pytest.mark.asyncio
async def test_the_tab_offers_the_log_artifacts_as_a_picker_newest_first(config_dir, tmp_path,
                                                                         clean_registry):
    write_file(tmp_path, "dcs.log", "live\n", mtime=3_000)
    write_file(tmp_path, "dcs.log.old", "previous\n", mtime=1_000)
    write_file(tmp_path, "dcs-crash.zip", "PK\x03\x04binary", mtime=2_000)
    write_file(tmp_path, "unrelated.txt", "noise\n", mtime=4_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert "unrelated.txt" not in html, "only the dcs* artifacts are listed"
    # A PICKER, NOT A WALL OF LINKS (L3): the artifacts are `<option>` values inside ONE select.
    assert '<select' in html and 'name="artifact"' in html
    assert "artifact=" not in html, "no per-file download LINK any more — the form chooses"
    order = [m for m in re.findall(r'<option value="([^"]+)"', html)]
    assert order == ["dcs.log", "dcs-crash.zip", "dcs.log.old"], "newest first"
    assert "dcs.log.old" in html and "previous" not in html
    # each option carries enough to choose with: the name, its size and its modification time
    assert re.search(r'<option value="dcs\.log\.old">[^<]*\d+ B[^<]*\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC',
                     html), "the option shows the name, the size and the timestamp"
    assert ">Download<" in html, "one Download control"


@pytest.mark.asyncio
async def test_the_artifact_listing_shows_bytes(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "dcs.log", "12345678", mtime=1_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert "8 B" in html, "the row shows the size"


# ------------------------------------------------------------------- 4. the download

@pytest.mark.asyncio
async def test_the_download_serves_the_artifact_as_an_attachment(config_dir, tmp_path,
                                                                 clean_registry):
    write_file(tmp_path, "dcs.log", "the live log\n", mtime=2_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(DOWNLOAD, params={"artifact": "dcs.log"})
    assert reply.status_code == 200
    assert reply.content == b"the live log\n"
    assert "attachment" in reply.headers["content-disposition"]
    assert "dcs.log" in reply.headers["content-disposition"]
    assert reply.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_a_crash_zip_is_served_as_a_binary_attachment(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "dcs-crash.zip", "PK\x03\x04payload", mtime=2_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(DOWNLOAD, params={"artifact": "dcs-crash.zip"})
    assert reply.status_code == 200
    assert reply.content == b"PK\x03\x04payload"
    assert reply.headers["content-type"].startswith("application/zip")


@pytest.mark.asyncio
async def test_a_crafted_artifact_id_cannot_leave_the_log_directory(config_dir, tmp_path,
                                                                    clean_registry):
    """The path never comes from the request: a traversal id names nothing in the fresh enumeration."""
    write_file(tmp_path, "dcs.log", "live\n", mtime=1_000)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    node = WindowNode(MASTER)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "specialk")
        for crafted in ("../secret.txt", "..%2Fsecret.txt", "/etc/passwd", "../../etc/passwd",
                        "dcs.log/../../secret.txt", "secret.txt"):
            reply = await http.get(DOWNLOAD, params={"artifact": crafted})
            assert reply.status_code == 404, crafted
            assert "TOP SECRET" not in reply.text
    assert not any("read" in entry for entry in node.listed), \
        "a crafted id must never reach a file read"


@pytest.mark.asyncio
async def test_the_download_is_refused_for_a_non_admin(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "dcs.log", "live\n", mtime=1_000)
    node = WindowNode(MASTER)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "mod1")                     # DCS Admin — no logs.view
        reply = await http.get(DOWNLOAD, params={"artifact": "dcs.log"})
    assert reply.status_code == 403
    assert not node.listed, "a refused caller must reach no node"


@pytest.mark.asyncio
async def test_the_download_refuses_an_oversized_log_honestly(config_dir, tmp_path, clean_registry,
                                                              monkeypatch):
    write_file(tmp_path, "dcs.log", "x" * 100, mtime=1_000)
    monkeypatch.setattr(detail_page, "LOG_DOWNLOAD_MAX_BYTES", 10)
    node = WindowNode(MASTER)
    app = admin_app(config_dir, tmp_path, node=node)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(DOWNLOAD, params={"artifact": "dcs.log"})
    assert reply.status_code == 413
    assert "100 bytes" in reply.text and "10-byte limit" in reply.text
    assert "dcs.log" in reply.text, "the path on the server is named so it can be fetched from the host"
    assert not any("read" in entry for entry in node.listed), "an oversized log is never moved"


@pytest.mark.asyncio
async def test_the_download_is_a_get_and_never_a_post(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "dcs.log", "live\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(DOWNLOAD, params={"artifact": "dcs.log"})
    assert reply.status_code == 405


# ------------------------------------------------------------------- 5. the Events tab

@pytest.mark.asyncio
async def test_the_events_tab_is_offered_only_with_the_debug_plugin(config_dir, tmp_path,
                                                                    clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: hello\n")
    write_file(tmp_path, "events.log", "2026-10-04 12:00:00.000 DEBUG   EVENT: x\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        events = await http.get(EVENTS_PAGE)
    assert "?tab=events" in html, "the debug plugin is active: the tab is offered"
    assert events.status_code == 200
    assert 'id="dcs-log-card"' in events.text and "events.log" in events.text


@pytest.mark.asyncio
async def test_the_events_tab_is_NOT_offered_without_the_debug_plugin(config_dir, tmp_path,
                                                                      clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: hello\n")
    write_file(tmp_path, "events.log", "2026-10-04 12:00:00.000 DEBUG   EVENT: x\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path)                       # WindowNode with NO plugins
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
        refused = await http.get(EVENTS_PAGE)
    assert "?tab=events" not in html, "no debug plugin: the tab is never drawn"
    assert refused.status_code == 403
    assert "debug plugin is active" in refused.text


@pytest.mark.asyncio
async def test_the_events_tab_refuses_the_window_route_without_the_plugin(config_dir, tmp_path,
                                                                          clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: hello\n")
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(WINDOW, params={"offset": "0", "which": "events"})
    assert reply.status_code == 403
    assert reply.json()["available"] is False


@pytest.mark.asyncio
async def test_the_events_tab_reads_events_log(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: dcs-line\n")
    write_file(tmp_path, "events.log", "2026-10-04 12:00:00.000 DEBUG   EVENT: mission-event\n",
               mtime=1_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(f"{EVENTS_PAGE}&level=all")).text
    assert "mission-event" in html, "the Events tab reads events.log"
    assert "dcs-line" not in html, "not dcs.log"


@pytest.mark.asyncio
async def test_the_events_download_is_limited_to_its_two_files(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "events.log", "events live\n", mtime=2_000)
    write_file(tmp_path, "events.log.old", "events old\n", mtime=1_000)
    write_file(tmp_path, "dcs.log", "dcs live\n", mtime=3_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(EVENTS_PAGE)).text
        served = await http.get(DOWNLOAD, params={"artifact": "events.log", "which": "events"})
        dcs_refused = await http.get(DOWNLOAD, params={"artifact": "dcs.log", "which": "events"})
    assert "events.log.old" in html and "dcs.log" not in html, "only the events pair is listed"
    assert served.status_code == 200 and served.content == b"events live\n"
    assert dcs_refused.status_code == 404, "the Events tab's download does not cover dcs.log"


@pytest.mark.asyncio
async def test_the_events_download_refuses_without_the_plugin(config_dir, tmp_path, clean_registry):
    write_file(tmp_path, "events.log", "events live\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(DOWNLOAD, params={"artifact": "events.log", "which": "events"})
    assert reply.status_code == 403
    assert "debug plugin is active" in reply.text


# ------------------------------------------------------------------- 5b. the Events tab has no filter (L3)

@pytest.mark.asyncio
async def test_the_events_tab_has_no_level_filter(config_dir, tmp_path, clean_registry):
    """The rule (L3): every debug entry is DEBUG, so the filter there is noise. The Events tab
    offers NO level row and NO level links, and shows every entry WITHOUT being asked for a level —
    the Log tab, meanwhile, keeps its filter untouched."""
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: dcs-line\n")
    write_file(tmp_path, "events.log",
               "2026-10-04 12:00:00.000 DEBUG   EVENT: first\n"
               "2026-10-04 12:00:01.000 DEBUG   EVENT: second\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        events = (await http.get(EVENTS_PAGE)).text
        log_tab = (await http.get(PAGE)).text
    assert 'id="dcs-log-level"' not in events, "no filter row on the Events tab"
    assert "?tab=events&level=" not in events and "?tab=events&amp;level=" not in events
    assert "first" in events and "second" in events, "every DEBUG entry shows without asking a level"
    # the Log tab KEEPS its filter row and its links
    assert 'id="dcs-log-level"' in log_tab
    assert "?tab=log&amp;level=all" in log_tab or "?tab=log&level=all" in log_tab


@pytest.mark.asyncio
async def test_the_events_window_route_ignores_a_level_parameter(config_dir, tmp_path,
                                                                 clean_registry):
    """The route keeps everything on the Events tab whatever ``?level=`` the request carries — a
    filtered-away DEBUG event would be exactly the noise the card names."""
    write_file(tmp_path, "events.log", "2026-10-04 12:00:00.000 DEBUG   EVENT: kept\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.get(WINDOW, params={"offset": "0", "which": "events",
                                               "level": "warning"})
    payload = reply.json()
    assert payload["available"] is True
    assert "kept" in payload["lines"], "the Events tab is not filtered, even with ?level=warning"


@pytest.mark.asyncio
async def test_the_events_picker_carries_the_file_selector(config_dir, tmp_path, clean_registry):
    """The Events tab's download is the SAME picker (L3), carrying ``which=events`` so the route
    enumerates only the events pair."""
    write_file(tmp_path, "events.log", "events live\n", mtime=2_000)
    write_file(tmp_path, "events.log.old", "events old\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path, node=debug_node())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(EVENTS_PAGE)).text
    assert '<select' in html and 'name="artifact"' in html
    assert '<input type="hidden" name="which" value="events">' in html
    order = re.findall(r'<option value="([^"]+)"', html)
    assert order == ["events.log", "events.log.old"], "the events pair, newest first"
    assert "cluster-wide Logs page" not in html, "the bot-log pointer is gone on this tab too"


@pytest.mark.asyncio
async def test_the_picker_is_a_get_form_to_the_download_base(config_dir, tmp_path, clean_registry):
    """The picker only CHOOSES: it is a GET form to the unchanged download route, so the download
    stays a READ and the route's contract (identity match, Admin gate, size cap) is untouched."""
    write_file(tmp_path, "dcs.log", "live\n", mtime=1_000)
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert f'action="{DOWNLOAD}"' in html
    assert 'method="get"' in html


# ------------------------------------------------------------------- 6. the styled control

def test_the_load_older_control_wears_the_console_chip():
    """Observed behaviour: the control rendered as the engine's grey default. It wears the console's
    ONE button now — the shell-level `.btn`, with its hover, focus and disabled states — and the
    rendered sibling below confirms the control itself carries the class. A `.log-card > .ft button`
    rule is deliberately ABSENT: it was a second definition of that same chip, written when `.btn`
    existed only inside a dialog."""
    with open(CSS, encoding="utf-8") as handle:
        sheet = handle.read()
    assert ".btn," in sheet or ".btn {" in sheet, "the shell declares no shared `.btn`"
    for state in (".btn:hover", ".btn:focus-visible", ".btn:disabled"):
        assert state in sheet, f"the shared button has no `{state}` state"
    assert ".log-card > .ft button" not in sheet, \
        "the footer control is a `.btn`; a container rule is a second definition of the same chip"


@pytest.mark.asyncio
async def test_the_rendered_load_older_is_a_chip_button(config_dir, tmp_path, clean_registry):
    write_log(tmp_path, "2026-10-04 12:00:00.000 INFO    APP: hello\n")
    app = admin_app(config_dir, tmp_path)
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = (await http.get(PAGE)).text
    assert re.search(r'id="dcs-log-older"[^>]*class="btn"', html), \
        "the control must carry the console's chip class"