"""The attention banner's ADVISORY line — a waiting DCS update, and the banner's third tone.

A waiting DCS update is not a fault: either the node installs it inside its own window or somebody
presses the control when convenient. So the banner states it in words, with its own dot, on an AMBER
tone — and the box keeps its red only for the classes that really are wrong.

Every pin here reads the REAL path: the read models build the state from a stub source, ``attention``
classifies it, and the page renders through the real templates. The one thing a render must never do
is ask a node, so the cached answer is seeded directly (``dcs_update.record``) and the mode comes from
the declared ``DCS`` block the node row reads as well.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import dcs_update, readmodels
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.scope import Scope
from test_webui_dashboard import BOT_YAML, Status, _build, _login, node, server

WEBSERVICE = Path(dashboard_page.__file__).parent.parent
SHELL = (WEBSERVICE / "static" / "shell.css").read_text(encoding="utf-8")

INSTALLED = "2.9.8.110"
LATEST = "2.9.9.200"
BRANCH = "release"

MASTER = "dcs-a-01"
OTHER = "dcs-b-02"
HIDDEN = "dcs-hidden-03"

BANNER = re.compile(r'<div class="banner [^"]*" role="status">.*?</div>', re.S)


# ----------------------------------------------------------------------------------- test doubles

@pytest.fixture(autouse=True)
def clean_dcs_cache():
    """Every pin starts with an EMPTY cache: an answer is only ever what the pin seeded."""
    dcs_update.reset()
    yield
    dcs_update.reset()


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def declared(*names: str, automatic=()) -> dict:
    """The ``DCS`` blocks ``nodes.yaml`` would carry — ``autoupdate`` only where it is ON."""
    return {name: {"installation": "C:/DCS World",
                   **({"autoupdate": True} if name in automatic else {})}
            for name in names}


def waiting(name: str) -> None:
    """The cache says a newer build exists on *name*'s branch."""
    dcs_update.record(name, has_dcs=True, branch=BRANCH, version=INSTALLED, latest=LATEST,
                      available=True)


def current(name: str) -> None:
    """The cache says *name* is on the newest build of its branch."""
    dcs_update.record(name, has_dcs=True, branch=BRANCH, version=INSTALLED, latest=INSTALLED,
                      available=False)


def unchecked(name: str) -> None:
    """The cache holds NO answer for *name* (never checked, or the last check raised)."""
    dcs_update.record(name, has_dcs=True, branch=BRANCH, version=INSTALLED, latest="",
                      available=None)


def hoster_server(name: str, *, node_name: str, managed_by: tuple[str, ...],
                  status=Status.RUNNING):
    """A server double carrying ``managed_by`` where the scope reads it (``locals``)."""
    return types.SimpleNamespace(
        name=name, status=status, node=types.SimpleNamespace(name=node_name),
        instance=types.SimpleNamespace(name=node_name), port=10309, current_mission=None,
        players={}, is_remote=False, locals={"managed_by": list(managed_by)})


def process_declares(monkeypatch, blocks: dict) -> dict:
    """The process configuration the mode is read from — the SAME blocks the source carries.

    ``readmodels.declared_node_dcs`` is what a real source is built from, so a stub source and the
    mode must be seeded together or the two would describe different clusters.
    """
    monkeypatch.setattr(readmodels, "declared_node_dcs", lambda: blocks)
    return blocks


def cluster(*names: str, blocks=None, servers=(), log_path=None):
    """A live source over *names* — the master first, every other node remote-but-listed."""
    members = {name: node(name=name, master=(index == 0), instances={})
               for index, name in enumerate(names)}
    return readmodels.LiveSource(servers=tuple(servers), instances=(), nodes=members,
                                 node=members[names[0]],
                                 declared_dcs=declared(*names) if blocks is None else blocks,
                                 log_path=log_path)


async def page_html(app) -> str:
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.get(dashboard_page.DASHBOARD_PATH)
    assert response.status_code == 200, response.status_code
    return response.text


def banner_of(html: str) -> str:
    """The rendered attention banner, sliced out of the page."""
    match = BANNER.search(html)
    assert match, "the page rendered no attention banner"
    return match.group(0)


def kinds(banner: dict) -> list[str]:
    return [item["kind"] for item in banner["alerts"]]


# ---------------------------------------------------------------- an advisory-only banner

@pytest.mark.asyncio
async def test_a_waiting_update_alone_renders_the_amber_advisory_banner(config_dir, monkeypatch):
    """One node with a newer build and nothing wrong: amber tone, the node named, no calm line."""
    waiting(MASTER)
    blocks = process_declares(monkeypatch, declared(MASTER, automatic=(MASTER,)))
    source = cluster(MASTER, blocks=blocks)
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source

    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["tone"] == "advisory"
    assert banner["ok"] is False, "something IS listed, so the calm line must not render"
    assert banner["count"] == 1
    assert kinds(banner) == ["update"]
    assert banner["alerts"][0]["dot"] == "update"
    assert MASTER in banner["alerts"][0]["text"]

    html = await page_html(app)
    rendered = banner_of(html)
    assert rendered.startswith('<div class="banner advisory" role="status">'), rendered[:120]
    assert dashboard_page.CALM_MESSAGE not in rendered, "the calm line must NOT be rendered"
    assert 'class="banner-list"' in rendered
    assert MASTER in rendered, "the sentence names the node"
    assert "on its own" in rendered, "the auto-update mode is stated"


# ---------------------------------------------------------------- a fault plus an advisory

@pytest.mark.asyncio
async def test_a_fault_keeps_the_red_tone_and_renders_before_the_advisory(config_dir, monkeypatch):
    """A paused server plus a waiting update: red, the fault FIRST, the advisory still there."""
    waiting(MASTER)
    blocks = process_declares(monkeypatch, declared(MASTER, automatic=(MASTER,)))
    source = cluster(MASTER, blocks=blocks,
                     servers=[server(name="Training", status=Status.PAUSED, node=MASTER)])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source

    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["tone"] == "alert", "a fault is listed, so the box keeps its red"
    assert kinds(banner) == ["pause", "update"], "the advisory is APPENDED LAST"

    html = await page_html(app)
    rendered = banner_of(html)
    assert rendered.startswith('<div class="banner alert" role="status">'), rendered[:120]
    fault_at = rendered.index(banner["alerts"][0]["text"])
    advisory_at = rendered.index(banner["alerts"][1]["text"])
    assert fault_at < advisory_at, "the fault line must render ahead of the advisory one"
    assert MASTER in rendered and "Training" in rendered


# ---------------------------------------------------------------- nothing listed

@pytest.mark.asyncio
async def test_nothing_listed_is_the_calm_tone_and_the_calm_line(config_dir):
    """A healthy cluster is byte-for-byte what it was: calm tone, calm sentence, no list."""
    current(MASTER)
    source = cluster(MASTER, blocks=declared(MASTER, automatic=(MASTER,)),
                     servers=[server(name="Training", status=Status.RUNNING, node=MASTER)])
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source

    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["tone"] == "calm"
    assert banner["ok"] is True
    assert banner["alerts"] == [] and banner["count"] == 0

    rendered = banner_of(await page_html(app))
    assert rendered.startswith('<div class="banner calm" role="status">'), rendered[:120]
    assert dashboard_page.CALM_MESSAGE in rendered
    assert 'class="banner-list"' not in rendered and "update" not in rendered


# ---------------------------------------------------------------- silent cases

@pytest.mark.asyncio
async def test_an_unknown_check_and_a_node_without_dcs_contribute_nothing(config_dir):
    """UNKNOWN, no DCS block and "already current" are all silent — no line, no count, no claim."""
    unchecked(MASTER)
    waiting(OTHER)
    source = cluster(MASTER, OTHER,
                     blocks=declared(MASTER),            # OTHER declares no DCS block at all
                     servers=[server(name="Training", node=MASTER)])
    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["alerts"] == [], banner["alerts"]
    assert banner["tone"] == "calm" and banner["ok"] is True

    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: source
    rendered = banner_of(await page_html(app))
    assert "update" not in rendered and OTHER not in rendered


@pytest.mark.asyncio
async def test_a_node_outside_the_caller_s_scope_is_never_named(config_dir, monkeypatch):
    """The scoped source drops the row, so the banner cannot name the node it cannot see."""
    waiting(MASTER)
    waiting(HIDDEN)
    blocks = process_declares(monkeypatch,
                              declared(MASTER, HIDDEN, automatic=(MASTER, HIDDEN)))
    source = cluster(MASTER, HIDDEN, blocks=blocks,
                     servers=[hoster_server("Host-A", node_name=MASTER, managed_by=("host-a",)),
                              hoster_server("Host-B", node_name=HIDDEN, managed_by=("host-b",))])
    scoped = readmodels.scoped_source(source, Scope(unrestricted=False,
                                                    tokens=frozenset({"host-a"})))
    app, _registrar = _build(config_dir)
    app.state.webui_source_provider = lambda: scoped

    state = readmodels.overview(scoped)
    assert {view.name for view in state.nodes} == {MASTER}, "floor: the scope really dropped it"

    banner = dashboard_page.attention(state)
    assert banner["tone"] == "advisory"
    assert MASTER in banner["alerts"][0]["text"]
    assert HIDDEN not in banner["alerts"][0]["text"]

    html = await page_html(app)
    assert HIDDEN not in html, "an out-of-scope node name must not reach the page"
    assert MASTER in banner_of(html)


# ---------------------------------------------------------------- the mode is stated

@pytest.mark.asyncio
async def test_the_sentence_states_the_mode_one_item_per_mode(config_dir, monkeypatch):
    """Auto-update ON and OFF are different consequences, so they are two lines, never one."""
    waiting(MASTER)
    waiting(OTHER)
    blocks = declared(MASTER, OTHER, automatic=(MASTER,))
    monkeypatch.setattr(readmodels, "declared_node_dcs", lambda: blocks)
    source = cluster(MASTER, OTHER, blocks=blocks)

    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["tone"] == "advisory"
    assert kinds(banner) == ["update", "update"], "at most two items, one per mode"
    automatic, manual = (item["text"] for item in banner["alerts"])
    assert MASTER in automatic and OTHER not in automatic
    assert "on its own" in automatic
    assert OTHER in manual and MASTER not in manual
    assert "auto-update is off" in manual
    for item in banner["alerts"]:
        assert item["params"]["count"] == 1


@pytest.mark.asyncio
async def test_the_switch_off_is_the_default_when_the_block_carries_no_autoupdate(config_dir,
                                                                                  monkeypatch):
    """``autoupdate`` is written only when ON, so an absent key means OFF — never the reverse."""
    waiting(MASTER)
    blocks = declared(MASTER)
    monkeypatch.setattr(readmodels, "declared_node_dcs", lambda: blocks)
    banner = dashboard_page.attention(readmodels.overview(cluster(MASTER, blocks=blocks)))
    assert kinds(banner) == ["update"]
    assert "auto-update is off" in banner["alerts"][0]["text"]
    assert dashboard_page._auto_update_on(MASTER, blocks) is False
    assert dashboard_page._auto_update_on(MASTER, declared(MASTER, automatic=(MASTER,))) is True


# ---------------------------------------------------------------- the four fault classes, pinned

@pytest.mark.asyncio
async def test_the_four_fault_classes_are_item_for_item_unchanged(config_dir, tmp_path):
    """The whole item list for a state with one of each — the classes this card must not touch.

    Byte-identical to the pre-card build: same order, same keys, same dots, same sentences and the
    same interpolated figures.
    """
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "2026-10-26 10:00:00.000 INFO\tCache warm\n"
        "2026-10-26 10:00:01.000 ERROR\tLua export failed for Training\n"
        "2026-10-26 10:00:02.000 ERROR\tCallback raised\n", encoding="utf-8")
    source = readmodels.LiveSource(
        servers=(server(name="Maverick Ops", status=Status.SHUTDOWN, node=MASTER),
                 server(name="Training", status=Status.PAUSED, node=MASTER)),
        instances=(),
        nodes={MASTER: node(name=MASTER, master=True, instances={}), "dcs-dead-02": None},
        node=node(name=MASTER, master=True, instances={}),
        declared_dcs=declared(MASTER), log_path=log_file)

    banner = dashboard_page.attention(readmodels.overview(source))
    assert banner["alerts"] == [
        {"kind": "dead", "dot": "dead",
         "msgid": "%(count)d node(s) not heartbeating: %(names)s",
         "params": {"count": 1, "names": "dcs-dead-02"},
         "text": "1 node(s) not heartbeating: dcs-dead-02"},
        {"kind": "dead", "dot": "dead",
         "msgid": "%(count)d server(s) down: %(names)s",
         "params": {"count": 1, "names": "Maverick Ops"},
         "text": "1 server(s) down: Maverick Ops"},
        {"kind": "pause", "dot": "pause",
         "msgid": "%(count)d server(s) paused: %(names)s",
         "params": {"count": 1, "names": "Training"},
         "text": "1 server(s) paused: Training"},
        {"kind": "log", "dot": "dead",
         "msgid": "%(count)d ERROR line(s) in the visible log",
         "params": {"count": 2},
         "text": "2 ERROR line(s) in the visible log"},
    ]
    assert banner["tone"] == "alert" and banner["ok"] is False and banner["count"] == 4


# ---------------------------------------------------------------- the stylesheet

def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", SHELL)
    assert match, f"{selector} is not declared in shell.css"
    return match.group(1)


def test_the_advisory_tone_and_its_dot_use_existing_tokens_only():
    """Amber from the declared ``--warn-*`` family and the amber halo — no literal, no new token."""
    advisory = rule(".banner.advisory")
    assert "var(--warn-line)" in advisory, "the border must be the amber line token"
    assert "var(--warn-bg)" in advisory, "the wash must be the amber ground token"
    assert "var(--text)" in advisory, "the sentence is read as body text, on that wash"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", advisory), advisory

    dot = rule(".dot.update")
    assert "var(--warn-text)" in dot, "the dot is the amber the warning family already declares"
    assert "var(--pause-halo)" in dot, "the halo is the existing amber halo token"
    assert "box-shadow" in dot and "border-radius" not in dot, "the circle comes from .dot"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", dot), dot

    base = rule(".dot")
    assert "8px" in base and "50%" in base, "the halo'd dot is still the shared 8px circle"


def test_the_banner_renders_the_three_tones_and_no_two_way_switch():
    """The template renders ``tone``; a two-way ``ok``-driven class would drop the amber entirely."""
    template = (WEBSERVICE / "templates" / "_banner.html").read_text(encoding="utf-8")
    assert 'class="banner {{ console.banner.tone }}"' in template
    assert "'calm' if console.banner.ok else 'alert'" not in template
    assert "{% if console.banner.ok %}" in template, "the calm line stays keyed on ok"
