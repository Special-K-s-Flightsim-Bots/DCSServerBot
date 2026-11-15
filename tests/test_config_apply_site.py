"""The apply door's FOURTH target: the public site's settings file, applied by a RE-READ.

``config/site.yaml`` is the one config file whose change does not reload a plugin and does not
restart a bot: the console reads it per request, so applying it means reading it again — and that
works with the bot stopped. These pins are the door's own contract for it: the path resolves to the
console target, the three sentences say what it is, the pre-flight accepts it without a bot (while
the plugin path still refuses one), the response NEVER awaits the re-read, and a second press while
one is running is refused rather than queued.

The session offer is stood in for where the flow needs one: no page writes ``site.yaml`` yet, so the
``Apply now`` control is not reachable end to end. Everything the pins are about — the resolution,
the pre-flight, the in-flight claim and the scheduling — is the real code.
"""
from __future__ import annotations

import asyncio
import re
import time
import types

import pytest

from core.utils import configwrite
from services.webservice import config_apply
from services.webservice.pages import settings as settings_page
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, instance, server, sign_in,
)

ADMIN = "specialk"
MASTER = "dcs-master-01"
BACK = "/settings/main/main"

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

MAIN_YAML = """\
guild_id: 112233445566
guild_name: My Server
language: en
plugins:
  - mission
"""

#: the file the site settings page owns. ``sites: {}`` parses to an empty catalogue, so the console
#: keeps ``/`` and the door's own addresses are the ones this suite posts to.
SITE_YAML = """\
# the public site's settings
sites: {}
"""


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "scheduler.yaml").write_text("DEFAULT:\n  timezone: UTC\n",
                                                         encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def fresh_apply_state():
    """The in-flight set is PROCESS state: a stale claim would refuse the next test's apply."""
    config_apply.reset_in_flight()
    yield
    config_apply.reset_in_flight()


@pytest.fixture
def site_file(config_dir):
    """The site file, written AFTER the shell is installed, as a save writes it."""
    return config_dir / config_apply.SITE_FILE


def cluster():
    a = server("SRS-1", ["Host A"])
    master = types.SimpleNamespace(name=MASTER, master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", a)})
    return Cluster([a], nodes={MASTER: master}, node=master,
                   instances=[instance("Host-A-Instance", a)])


def app_for(config_dir, monkeypatch):
    monkeypatch.setattr(config_apply, "master_name", lambda: MASTER)
    return build(config_dir, cluster(), plugins=("mission",))


def console() -> config_apply.ApplyTarget:
    return config_apply.ApplyTarget(config_apply.TARGET_CONSOLE)


def csrf_of(page: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', page)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


def site_record(path, *, token: str = "console-token") -> dict:
    """The offer record a save of the site file leaves — the shape :func:`config_apply.offer` writes."""
    return {"token": token, "revision": configwrite.config_revision(path), "back": BACK,
            "file_kind": "main", "file_name": config_apply.SITE_FILE,
            "kind": config_apply.TARGET_CONSOLE, "plugin": "", "node": ""}


def apply_fields(token: str, *, csrf: str, action: str = "apply") -> dict:
    return {"_csrf_token": csrf,
            config_apply.APPLY_KIND_FIELD: "main",
            config_apply.APPLY_NAME_FIELD: config_apply.SITE_FILE,
            config_apply.APPLY_FIELD: token,
            config_apply.APPLY_ACTION_FIELD: action}


async def open_page(http, path: str = BACK) -> str:
    response = await http.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}: {response.text[:300]}"
    return response.text


# ============================================================ the path is the console target

def test_site_file_targets_the_console(tmp_path):
    """``config/site.yaml`` is the console target — in the two directories a site file may live in."""
    assert config_apply.SITE_FILE == "site.yaml"
    assert config_apply.resolve_target(tmp_path / "site.yaml", tmp_path).kind == \
        config_apply.TARGET_CONSOLE
    assert config_apply.resolve_target(tmp_path / "site.yml", tmp_path).kind == \
        config_apply.TARGET_CONSOLE
    assert config_apply.resolve_target(tmp_path / "services" / "site.yaml", tmp_path).kind == \
        config_apply.TARGET_CONSOLE


def test_the_site_file_name_is_the_one_the_console_parses():
    """One name for one file: the door and the site model cannot drift apart."""
    from services.webservice import sites

    assert config_apply.SITE_FILE == sites.SITE_FILE


def test_the_two_existing_target_resolutions_are_unchanged(tmp_path):
    """The new branch is additive: a plugin file still reloads, a boot file still restarts."""
    plugin = config_apply.resolve_target(tmp_path / "plugins" / "scheduler.yaml", tmp_path)
    assert (plugin.kind, plugin.plugin) == (config_apply.TARGET_PLUGIN, "scheduler")
    for name in ("main.yaml", "servers.yaml", "nodes.yaml"):
        boot = config_apply.resolve_target(tmp_path / name, tmp_path)
        assert boot.kind == config_apply.TARGET_RESTART, name
    service = config_apply.resolve_target(tmp_path / "services" / "bot.yaml", tmp_path, node="")
    assert service.kind == config_apply.TARGET_RESTART


# ================================================================== the three sentences

def test_the_console_target_owes_nothing():
    """Nothing is owed: the file is read per request, so the change is live as it is written."""
    assert config_apply.owed(None, console()) == ""
    plugin = config_apply.ApplyTarget(config_apply.TARGET_PLUGIN, plugin="scheduler")
    restart = config_apply.ApplyTarget(config_apply.TARGET_RESTART)
    assert config_apply.owed(None, plugin) != ""
    assert "scheduler" in config_apply.owed(None, plugin)
    assert config_apply.owed(None, restart) != ""


def test_the_console_sentences_say_reread_and_nothing_restarts():
    copy = config_apply.describe(None, console())
    assert copy["heading"] == "Re-read the site settings now?"
    assert "read again" in copy["detail"]
    assert copy["warning"] == "", "no fleet warning: no DCS server is touched"
    assert copy["go"] == "Re-read the site settings"
    assert copy["target"] == "the site settings"
    text = config_apply.pending(None, console())
    assert "Re-reading the site settings" in text
    assert "nothing is reloaded or restarted" in text.lower()
    # the re-read is not an action, so the audit disclosure has nothing to disclose
    assert text == config_apply.pending(None, console(), audited=False)


# ================================================================ the pre-flight

def test_preflight_accepts_the_console_without_a_bot(config_dir, site_file, monkeypatch):
    """``site.yaml`` is the console's own file: it applies with the bot stopped or absent.

    NOTHING ABOUT THE BOT IS ASKED — and nothing about it can refuse. What the pre-flight asks is the
    console that will re-read the file: an application this process holds, with a route table and a
    file it can serve, is the whole of its question.
    """
    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    assert config_apply.preflight(types.SimpleNamespace(app=app), console()) == ""
    assert config_apply._bot() is None, "the pin must still hold with no bot in reach"


def test_preflight_still_refuses_a_plugin_without_a_bot(config_dir):
    """The plugin arm is untouched: no bot in this process is still a refusal, with its reason."""
    plugin = config_apply.ApplyTarget(config_apply.TARGET_PLUGIN, plugin="scheduler")
    refusal = config_apply.preflight(None, plugin)
    assert refusal != ""
    assert config_apply.WHY_NO_SERVICE in refusal


# ==================================================================== the executor

@pytest.mark.asyncio
async def test_the_console_executor_rebuilds_the_site_routes(config_dir, monkeypatch, site_file):
    from services.webservice import site_pages

    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    seen: list = []
    monkeypatch.setattr(config_apply, "_console_app", lambda: app)
    monkeypatch.setattr(site_pages, "rebuild",
                        lambda registrar, target: seen.append((registrar, target)))
    config_apply.mark_in_flight(config_apply.CONSOLE_CLAIM)
    await config_apply.execute(console(), None)
    assert [target for _registrar, target in seen] == [app], \
        "the console app's site table was not rebuilt"
    assert [registrar for registrar, _target in seen] == [app.state.webui_registrar], \
        "the rebuild must run on the console's own registrar"
    assert not config_apply.in_flight(config_apply.CONSOLE_CLAIM), \
        "the in-flight claim must be released when the rebuild is done"


@pytest.mark.asyncio
async def test_the_response_does_not_await_the_reread(config_dir, monkeypatch, clean_registry,
                                                      site_file):
    """THE DEADLOCK PIN: the answer must be on the wire while the re-read is still running.

    A re-read the response awaited would hold the connection open for as long as the work takes —
    and for a console reloading itself it would never close.
    """
    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    record = site_record(site_file)
    monkeypatch.setattr(config_apply, "_DETACHED_DELAY", 0.0)
    monkeypatch.setattr(config_apply, "_record_of",
                        lambda request, form: ("main", config_apply.SITE_FILE, record))
    started: list[float] = []
    finished: list[float] = []

    async def slow_execute(target, ctx, *, app=None):
        started.append(time.monotonic())
        await asyncio.sleep(0.4)
        finished.append(time.monotonic())
        return None

    monkeypatch.setattr(config_apply, "execute", slow_execute)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        csrf = csrf_of(await open_page(http))
        began = time.monotonic()
        reply = await http.post(settings_page.SETTINGS_APPLY_PATH,
                                data=apply_fields("console-token", csrf=csrf))
        elapsed = time.monotonic() - began
        assert reply.status_code == 303, reply.text[:300]
        assert finished == [], "the response awaited the re-read"
        assert elapsed < 0.2, f"the response took {elapsed:.2f}s — it waited for the re-read"
        assert config_apply.in_flight(config_apply.CONSOLE_CLAIM), \
            "the detached re-read must hold the claim while it runs"
        await asyncio.sleep(0.5)
        assert finished, "the detached re-read never ran"
    assert started[0] >= began - 0.01


@pytest.mark.asyncio
async def test_a_second_console_apply_while_one_is_in_flight_is_refused(config_dir, monkeypatch,
                                                                       clean_registry, site_file):
    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    record = site_record(site_file)
    monkeypatch.setattr(config_apply, "_record_of",
                        lambda request, form: ("main", config_apply.SITE_FILE, record))
    ran: list = []

    async def fake_execute(target, ctx, *, app=None):
        ran.append(target)

    monkeypatch.setattr(config_apply, "execute", fake_execute)
    config_apply.mark_in_flight(config_apply.CONSOLE_CLAIM)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        csrf = csrf_of(await open_page(http))
        reply = await http.post(settings_page.SETTINGS_APPLY_PATH,
                                data=apply_fields("console-token", csrf=csrf))
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "already being re-read" in landed
    assert ran == [], "a refused apply must not schedule anything"


@pytest.mark.asyncio
async def test_a_posted_form_cannot_change_the_target(config_dir, monkeypatch, clean_registry,
                                                     site_file):
    """The target comes from the session's offer, never from the post: no offer, no apply."""
    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    ran: list = []

    async def fake_execute(target, ctx):
        ran.append(target)

    monkeypatch.setattr(config_apply, "execute", fake_execute)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        csrf = csrf_of(await open_page(http))
        reply = await http.post(settings_page.SETTINGS_APPLY_PATH,
                                data=apply_fields("forged", csrf=csrf))
        assert reply.status_code == 303
        landed = await open_page(http)
    assert "no longer valid" in landed
    assert ran == []


# ======================================================== the manual door is UNCHANGED

@pytest.mark.asyncio
async def test_a_hand_edited_site_file_still_asks_then_applies(config_dir, monkeypatch,
                                                              clean_registry, site_file):
    """A hand-edited ``site.yaml`` keeps its own Apply now -> confirmation -> go door.

    Only the SAVE stopped asking: the door's two routes are untouched, so an offer recorded for the
    site file still renders the console's confirmation first and only the confirmation's own go
    reaches the re-read.
    """
    from services.webservice import site_pages

    site_file.write_text(SITE_YAML, encoding="utf-8")
    app = app_for(config_dir, monkeypatch)
    record = site_record(site_file)
    monkeypatch.setattr(config_apply, "_record_of",
                        lambda request, form: ("main", config_apply.SITE_FILE, record))
    rebuilt: list = []
    monkeypatch.setattr(site_pages, "rebuild",
                        lambda registrar, target: rebuilt.append(target))
    monkeypatch.setattr(config_apply, "_DETACHED_DELAY", 0.0)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        csrf = csrf_of(await open_page(http))
        # the file's own control opens the CONFIRMATION, and applies nothing
        ask = await http.post(config_apply.APPLY_ASK_PATH,
                              data=apply_fields("console-token", csrf=csrf))
        assert ask.status_code == 200, ask.text[:400]
        assert "Re-read the site settings now?" in ask.text
        assert rebuilt == [], "the confirmation page applied the file before its go"
        go = {"_csrf_token": csrf_of(ask.text),
              config_apply.APPLY_FIELD: "console-token",
              config_apply.APPLY_KIND_FIELD: "main",
              config_apply.APPLY_NAME_FIELD: config_apply.SITE_FILE,
              config_apply.APPLY_ACTION_FIELD: config_apply.APPLY_ACTION_APPLY}
        applied = await http.post(config_apply.APPLY_PATH, data=go)
        assert applied.status_code == 303, applied.text[:400]
        # the door's re-read is DETACHED (the answer goes first): give it its own tick to land
        for _ in range(200):
            if rebuilt:
                break
            await asyncio.sleep(0.01)
    assert rebuilt == [app], "the confirmation's go did not re-read the site file"
