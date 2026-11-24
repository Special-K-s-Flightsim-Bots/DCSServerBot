"""The dashboard's log panel: no blank line between entries, and a level filter that defaults to INFO and up.

``.log`` is ``white-space: pre``, so the newline the template happened to leave BETWEEN two entries
renders as a visible empty line -- the whitespace between entries has to go while the whitespace
inside a message has to stay. The filter defaults to INFO and up, is offered in the URL (a refresh
keeps it), and scans BACKWARDS from the end of the file (a naive "take the last N lines, then filter"
shows almost nothing on a DEBUG-heavy file). Rendered in-process from a stub source."""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell
from services.webservice import readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.readmodels import logtail

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = "roles:\n  DCS Admin:\n    - mod1\n"


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(log_level=None) -> dict:
    config = {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": [
                     {"username": "mod1", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }
    if log_level is not None:
        config["log_level"] = log_level
    return config


def _build(config_dir, log_level=None):
    app = create_app()
    registrar = install_shell(app,
                              node=types.SimpleNamespace(master=True, config_dir=str(config_dir)),
                              config=_config(log_level))
    return app, registrar


async def _login(client, username: str = "mod1", password: str = PASSWORD):
    response = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token})


def _source(*, log_path=None):
    node = types.SimpleNamespace(name="dcs-master-01", master=True, instances={})
    return readmodels.LiveSource(servers=(), instances=(), nodes={"dcs-master-01": node},
                                 node=node, log_path=log_path)


def _write_log(path: Path, rows) -> Path:
    path.write_text("".join(row if row.endswith("\n") else row + "\n" for row in rows),
                    encoding="utf-8")
    return path


async def _render(config_dir, log_path, url: str = "/", *, log_level=None) -> str:
    app, _registrar = _build(config_dir, log_level=log_level)
    app.state.webui_source_provider = lambda: _source(log_path=log_path)
    async with asgi_client(app) as client:
        assert (await _login(client)).status_code == 303
        response = await client.get(url)
        assert response.status_code == 200, (url, response.status_code)
        return response.text


# ------------------------------------------------- DEFECT 1: no blank line between entries

@pytest.mark.asyncio
async def test_no_blank_line_between_log_entries(config_dir, tmp_path):
    """(a) one entry element per line, (b) no whitespace BETWEEN entries, (c) the whitespace INSIDE
    a message still renders (the panel is ``white-space: pre`` and relies on it)."""
    rows = [
        "2026-09-24 21:33:12.123 INFO\tfirst line",
        "2026-09-24 21:33:13.123 WARNING\tsecond line",
        "2026-09-24 21:33:14.123 ERROR\tthird\tline\twith\ttabs",
    ]
    html = await _render(config_dir, _write_log(tmp_path / "dcssb-dcs-master-01.log", rows))

 # (a) exactly one entry element per line
    assert html.count('<div class="ln">') == len(rows), "one entry div per log line"
 # (b) nothing but the entries between the entries
    assert re.search(r"</div>\s+<div class=\"ln\">", html) is None, \
        "a newline between entries renders as a blank line under white-space: pre"
 # (c) the message's own whitespace is preserved verbatim
    assert "third\tline\twith\ttabs" in html, "internal whitespace must survive"
 # the container the live-update card will swap, and the level control beside it
    assert '<div class="log" id="log-body">' in html
    assert 'id="log-level"' in html


# ------------------------------------------------- DEFECT 2: the level filter at the read layer

def _level_rows():
    info = [f"2026-09-24 21:00:{i:02d}.000 INFO\tinfo line {i}" for i in range(5)]
    debug = [f"2026-09-24 22:00:{i % 60:02d}.000 DEBUG\tspam {i}" for i in range(200)]
    return info, debug


def test_the_default_level_is_info_and_up(tmp_path):
    """DEBUG is excluded by default; INFO/WARNING/ERROR survive."""
    log = _write_log(tmp_path / "log", [
        "2026-09-24 21:00:01.000 DEBUG\tdebug line",
        "2026-09-24 21:00:02.000 INFO\tinfo line",
        "2026-09-24 21:00:03.000 WARNING\twarning line",
        "2026-09-24 21:00:04.000 ERROR\terror line",
    ])
    tail = logtail.tail(log)
    assert [line.text for line in tail.lines] == ["info line", "warning line", "error line"]


def test_the_filter_scans_backwards_so_a_debug_heavy_file_still_shows_info(tmp_path):
    """The pin for "the filter must not shrink the tail": 200 DEBUG lines below 5 INFO lines, and
    the 5 matching lines must still be what the panel shows."""
    info, debug = _level_rows()
    log = _write_log(tmp_path / "log", info + debug)
    tail = logtail.tail(log, lines=5)
    assert [line.text for line in tail.lines] == [f"info line {i}" for i in range(5)]
    assert all(line.level == "INFO" for line in tail.lines)


def test_level_all_puts_debug_back(tmp_path):
    info, debug = _level_rows()
    log = _write_log(tmp_path / "log", info + debug)
    tail = logtail.tail(log, lines=5, level="all")
    assert [line.text for line in tail.lines] == [f"spam {i}" for i in range(195, 200)]
    assert [line.level for line in tail.lines] == ["DEBUG"] * 5


def test_warning_and_up_is_available(tmp_path):
    log = _write_log(tmp_path / "log", [
        "2026-09-24 21:00:01.000 INFO\tinfo line",
        "2026-09-24 21:00:02.000 WARNING\twarning line",
        "2026-09-24 21:00:03.000 ERROR\terror line",
    ])
    tail = logtail.tail(log, level="warning")
    assert [line.text for line in tail.lines] == ["warning line", "error line"]


def test_a_line_with_no_level_stays_visible_under_the_default_filter(tmp_path):
    """An unparseable line is never silently dropped (logtail.py's documented behaviour)."""
    log = _write_log(tmp_path / "log", [
        "2026-09-24 21:00:01.000 INFO\tinfo line",
        "this line does not match the log format at all",
        "2026-09-24 21:00:03.000 DEBUG\thidden debug line",
    ])
    tail = logtail.tail(log)
    assert [line.text for line in tail.lines] == ["info line",
                                                  "this line does not match the log format at all"]


def test_an_unknown_level_is_refused_not_passed_through():
    """The known set is the only thing the filter accepts; an arbitrary string never reaches it."""
    assert logtail.normalise_level("all") == "all"
    assert logtail.normalise_level("INFO") == "info"
    assert logtail.normalise_level("warning") == "warning"
    for bogus in ("nonsense", "", None, "'; DROP TABLE", "info+"):
        assert logtail.normalise_level(bogus) is None, bogus


# ------------------------------------------------- DEFECT 2: the URL and the config default

@pytest.mark.asyncio
async def test_the_page_defaults_to_info_and_the_choice_lives_in_the_url(config_dir, tmp_path):
    rows = ["2026-09-24 21:00:01.000 DEBUG\tdebug line",
            "2026-09-24 21:00:02.000 INFO\tinfo line"]
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", rows)

    default = await _render(config_dir, log)
    assert "info line" in default and "debug line" not in default

    filtered = await _render(config_dir, log, "/?level=all")
    assert "debug line" in filtered, "?level=all is how a refresh keeps the choice"

    refused = await _render(config_dir, log, "/?level=nonsense")
    assert "debug line" not in refused, "an unknown value must not be passed through"
    assert "info line" in refused, "an unknown value falls back to the configured default"


@pytest.mark.asyncio
async def test_the_default_level_is_config_driven(config_dir, tmp_path):
    """`log_level` in webservice.yaml decides the default; the URL still overrides it."""
    rows = ["2026-09-24 21:00:01.000 DEBUG\tdebug line",
            "2026-09-24 21:00:02.000 INFO\tinfo line",
            "2026-09-24 21:00:03.000 WARNING\twarning line"]
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", rows)

    configured = await _render(config_dir, log, log_level="all")
    assert "debug line" in configured, "the configured default must be honoured"

    overridden = await _render(config_dir, log, "/?level=warning", log_level="all")
    assert "debug line" not in overridden and "info line" not in overridden
    assert "warning line" in overridden


@pytest.mark.asyncio
async def test_an_unknown_configured_default_is_refused_too(config_dir, tmp_path):
    """The config is hand-edited: a value nobody defined must not be able to widen or narrow the
    panel, so it goes through the same validator as the URL and falls back to DEFAULT_LEVEL."""
    rows = ["2026-09-24 21:00:01.000 DEBUG\tdebug line",
            "2026-09-24 21:00:02.000 INFO\tinfo line"]
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", rows)
    html = await _render(config_dir, log, log_level="bogus")
    assert "info line" in html and "debug line" not in html


@pytest.mark.asyncio
async def test_the_level_control_marks_the_active_choice(config_dir, tmp_path):
    """The control is a set of links (no JS): the active one is marked so a reader can tell which
    filter is in force."""
    rows = ["2026-09-24 21:00:02.000 INFO\tinfo line"]
    log = _write_log(tmp_path / "dcssb-dcs-master-01.log", rows)
    html = await _render(config_dir, log, "/?level=warning")
    control = re.search(r'<span class="log-level" id="log-level">(.*?)</span>', html, re.S)
    assert control, "the level control must be a stable element"
    assert re.search(r'href="/\?level=all"', control.group(1))
    assert re.search(r'href="/\?level=warning"[^>]*class="tag a"', control.group(1)), \
        "the active choice is marked"
