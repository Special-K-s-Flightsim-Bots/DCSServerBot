"""The ModManager pages: the node's root mods, the server's Mods tab and their dialogs.

One case per acceptance criterion, driven through the REAL application (the production factory, the
shell, the local login backend, the real ``modmanager`` actions and a stub cluster) with a RECORDING
service double at the domain boundary. The five questions:

* which controls a page DRAWS, in both gate states — the open page carries the two doors and the
  Actions column, the closed one carries NEITHER and its chip says ``read-only``;
* what a dialog reads — the folder is enumerated when the DIALOG is requested, and a read that failed
  renders its own sentence with no primary button;
* whether a write can be performed without coming through its dialog — it cannot, and the service is
  never called;
* who may open what — ``mods.manage`` owns the node page and every write, ``mods.view`` the tab;
* the tab's strip carries the EXISTING shutdown control, not a second one.

The tab's own ROUTE belongs to the server page (it owns the tab strip and its order), so the tab is
exercised through a probe that registers the same capability and renders the same body — which is
exactly what the server page does with :func:`services.webservice.pages.mods.tab_context`.
"""
from __future__ import annotations

import ast
import re
import types
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from conftest import asgi_client, create_app, install_shell

from core import Status
from core.services.registry import ServiceRegistry
from services.modmanager import ModManagerService
from services.webservice import i18n, readmodels
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import mods as mods_page
from core.actions import discover_actions

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = REPO_ROOT / "services" / "webservice"

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"

NODE = "node-01"
NODE_B = "node-02"
BLUE_FLAG = "Blue Flag"
TRAINING = "Training"

ROOT = "%USERPROFILE%\\Documents\\ModManager\\RootFolder"
SAVED = "%USERPROFILE%\\Documents\\ModManager\\SavedGames"

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

USERS = (
    {"username": "specialk", "roles": ["Admin"]},
    {"username": "mod1", "roles": ["DCS Admin"], "scope": ["Blue Flag"]},
    {"username": "plain", "roles": ["DCS"]},
)

ROOT_ROWS = [("Community_A-4E-C", "2.4.1", datetime(2026, 10, 19, 22, 14), "RootFolder"),
             ("SAM_Sites_Reworked", "3.0.0", datetime(2026, 9, 30, 17, 48), "RootFolder")]
SAVED_ROWS = [("DCSOlympus", "1.0.3", datetime(2026, 9, 28, 18, 40), "SavedGames")]

#: what the node answers about each installed package: the versions it can offer and the newest of
#: them. ``Community_A-4E-C`` has a second version to roll back to but nothing newer;
#: ``SAM_Sites_Reworked`` is the single-version row; ``DCSOlympus`` has a second version too.
VERSIONS = {
    "Community_A-4E-C": {"versions": ["2.4.1", "2.4.0"], "latest": "2.4.1"},
    "SAM_Sites_Reworked": {"versions": ["3.0.0"], "latest": "3.0.0"},
    "DCSOlympus": {"versions": ["1.0.3", "1.0.1"], "latest": "1.0.3"},
}

ENTRIES = [
    {"name": "SRS_Radio_Standalone", "version": "2.1.2", "kind": "zip", "size": 26004684,
     "mtime": 1793000000.0},
    {"name": "SRS_Radio_Standalone", "version": "2.1.0", "kind": "dir", "size": 1200000,
     "mtime": 1792000000.0},
    {"name": "LotAtc_Client", "version": "2.0.6", "kind": "zip", "size": 33030000,
     "mtime": 1791000000.0},
]


# --------------------------------------------------------------------------------- test doubles

def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _users():
    return [dict(user, password_hash=_hash()) for user in USERS]


def _config() -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": _users()}},
    }


class _Cursor:
    """A recording cursor: keeps every statement and answers the pool's row set for it."""

    def __init__(self, pool):
        self._pool = pool
        self.statements: list[tuple] = []
        self._rows: list = []
        self._iter = iter(())

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        self._rows = self._pool.next_rows()
        return self

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        return False


class Pool:
    """An ``apool`` double that answers the SAME row sets for every render (cycled, never consumed)."""

    def __init__(self, row_sets=()):
        self.row_sets = [list(rows) for rows in row_sets]
        self.cursors: list[_Cursor] = []
        self._index = 0

    def next_rows(self) -> list:
        if not self.row_sets:
            return []
        rows = self.row_sets[self._index % len(self.row_sets)]
        self._index += 1
        return rows

    def connection(self):
        cursor = _Cursor(self)
        self.cursors.append(cursor)
        return _Connection(cursor)


class RecordingService:
    """The ``ModManagerService`` double: records every call and answers preset values."""

    def __init__(self, *, rows=(), saved_rows=None, entries=None, config=None, raise_list=None,
                 versions=None, raise_versions=None):
        self.calls: list[tuple] = []
        self.entries = list(ENTRIES if entries is None else entries)
        self.raise_list = raise_list
        self.versions = dict(VERSIONS if versions is None else versions)
        self.raise_versions = raise_versions
        sets = [list(rows)]
        if saved_rows is not None:
            sets.append(list(saved_rows))
        self.apool = Pool(sets)
        self.config = dict(config if config is not None else {"RootFolder": ROOT,
                                                             "SavedGames": SAVED})

    # -- reads
    def get_config(self, server=None):
        return self.config

    async def list_available_packages(self, node, folder):
        self.calls.append(("list_available_packages", getattr(node, "name", node),
                           getattr(folder, "value", folder)))
        if self.raise_list is not None:
            raise self.raise_list
        return list(self.entries)

    async def get_installed_package(self, reference, folder, package_name):
        self.calls.append(("get_installed_package", getattr(reference, "name", reference),
                           getattr(folder, "value", folder), package_name))
        return "2.1.0"

    async def list_package_versions(self, node, folder, package_names):
        """The installed table's ONE version read: records the call and answers per package."""
        self.calls.append(("list_package_versions", getattr(node, "name", node),
                           getattr(folder, "value", folder), tuple(package_names)))
        if self.raise_versions is not None:
            raise self.raise_versions
        return {name: dict(self.versions.get(name) or {"versions": [], "latest": "",
                                                      "repo": "", "downloaded": False})
                for name in package_names}

    @property
    def version_reads(self) -> list[tuple]:
        """Every call of the installed table's version read — one per rendered table."""
        return [call for call in self.calls if call[0] == "list_package_versions"]

    # -- writes
    async def install_root_package(self, node, package_name, version, repo=None):
        self.calls.append(("install_root_package", getattr(node, "name", node), package_name,
                           version))
        return True

    async def install_package(self, server, folder, package_name, version, repo=None):
        self.calls.append(("install_package", getattr(server, "name", server), package_name,
                           version))
        return True

    async def uninstall_root_package(self, node, package_name, version, force=False, repo=None):
        self.calls.append(("uninstall_root_package", getattr(node, "name", node), package_name,
                           version))
        return True

    async def uninstall_package(self, server, folder, package_name, version, force=False,
                                repo=None):
        self.calls.append(("uninstall_package", getattr(server, "name", server), package_name,
                           version))
        return True

    async def download_package(self, node, folder, url, *, version=None, package_name=None,
                              force=False):
        self.calls.append(("download_package", getattr(node, "name", node), url, version))
        return True

    # the two static readers the action asks the service for, taken from the service itself
    parse_filename = staticmethod(ModManagerService.parse_filename)
    is_repo_url = staticmethod(ModManagerService.is_repo_url)

    @property
    def writes(self) -> list[tuple]:
        return [call for call in self.calls if not call[0].startswith(("list_", "get_"))]


def node_object(name, *, master=False, instances=()):
    return types.SimpleNamespace(name=name, master=master,
                                 instances={inst: None for inst in instances})


def server(name, node_name=NODE, *, status=Status.SHUTDOWN, managed_by=None,
           instance="Instance-1"):
    return types.SimpleNamespace(
        name=name, display_name=name, status=status,
        node=types.SimpleNamespace(name=node_name), instance=types.SimpleNamespace(name=instance),
        port=10309, current_mission=None, players={}, is_remote=False,
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )


class Cluster:
    """A stub cluster behind the application's own source seam."""

    def __init__(self, servers=(), *, nodes=None, node=None):
        self.servers = list(servers)
        self.nodes = dict(nodes or {})
        self.node = node

    def source(self):
        return readmodels.LiveSource(servers=tuple(self.servers), instances=(),
                                     nodes=self.nodes, node=self.node, log_path=None)


def fleet(*, blue_flag=Status.SHUTDOWN, training=Status.SHUTDOWN):
    """Two servers on ONE node — the pair the root gate is about."""
    blue = server(BLUE_FLAG, status=blue_flag, managed_by=["Blue Flag"])
    training = server(TRAINING, status=training, instance="Instance-2")
    node = node_object(NODE, master=True, instances=("Instance-1", "Instance-2"))
    return Cluster([blue, training], nodes={NODE: node}, node=node)


# --------------------------------------------------------------------------------- the harness

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def service(monkeypatch):
    """Register a recording service as the process's running ``ModManagerService``."""

    def install(**kwargs):
        double = RecordingService(**kwargs)
        monkeypatch.setitem(ServiceRegistry._singletons, ModManagerService, double)
        return double

    return install


@pytest.fixture(autouse=True)
def no_service(monkeypatch):
    """Absent unless a case registers one — the plugin-not-enabled state.

    The page-process cache of version answers is dropped too: it is keyed by the node, the folder and
    the package names, so a case that changes what the node answers would otherwise read the previous
    case's answer.
    """
    monkeypatch.delitem(ServiceRegistry._singletons, ModManagerService, raising=False)
    mods_page._forget_versions()


def build(config_dir, cluster, *, plugins=("modmanager", "mission")):
    """A real application with the shell, the plugin actions and the shell's own Mods pages.

    No page is registered here: ``install_shell`` registers the Mods pages with the rest of the core
    pages, so a second registration would be a path collision — and a fallback here would hide the
    registration going missing, which is what the shell's own pin is for.
    """
    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True, config_dir=str(config_dir),
                                                  plugins=list(plugins)),
                  config=_config())
    app.state.webui_source_provider = cluster.source
    discover_actions(list(plugins))
    return app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://testserver")


async def sign_in(http, username="specialk"):
    response = await http.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', response.text).group(1)
    reply = await http.post(auth_routes.LOGIN_PATH,
                            data={"username": username, "password": PASSWORD,
                                  "_csrf_token": token})
    assert reply.status_code in (200, 303), f"login for {username} failed: {reply.status_code}"
    return reply


def token_of(html: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the page rendered no CSRF field"
    return match.group(1)


async def open_node_page(http, node=NODE):
    response = await http.get(mods_page.node_page_url(node))
    assert response.status_code == 200, response.text
    return response.text


# ------------------------------------------------------------------ 1. the node page, open gate

@pytest.mark.asyncio
async def test_the_node_page_with_an_open_gate_carries_both_doors_and_the_actions_column(
        config_dir, service):
    """The OPEN page: both doors, the Actions column and both row glyphs — asserted PRESENCE first."""
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    # the page head, with the configured folder value printed VERBATIM
    assert "<h1>Root mods</h1>" in html
    assert ROOT in html, "the resolved package folder must be printed as configured"
    # the two page-level doors, as POST forms pointing at their dialogs
    assert f'action="/mods/node/{NODE}/install"' in html
    assert f'action="/mods/node/{NODE}/download"' in html
    assert "Install a mod" in html and "Download" in html
    # the strip says the gate is open
    assert "Every server on this node is shut down" in html
    # the rows, the Actions column and the two glyph buttons per row
    assert "Community_A-4E-C" in html and "2.4.1" in html
    assert "2026-10-19 22:14 UTC" in html, "the install stamp is the console's ONE stamp format"
    assert '<th scope="col" class="acts">Actions</th>' in html
    assert f'action="/mods/node/{NODE}/change"' in html
    assert f'action="/mods/node/{NODE}/remove"' in html
    assert 'aria-label="Change the installed version of Community_A-4E-C"' in html
    assert f'aria-label="Remove Community_A-4E-C v2.4.1 from {NODE}"' in html
    # a glyph, never a button that spells the verb
    assert ">Change version<" not in html and ">Remove<" not in html


# ----------------------------------------------------------------- 2. the node page, closed gate

@pytest.mark.asyncio
async def test_the_node_page_with_a_closed_gate_carries_neither_door_nor_the_actions_column(
        config_dir, service):
    """The BLOCKED page: no doors, no Actions column, a ``read-only`` chip, and links to the gate.

    The count is asserted BEFORE the absence: two servers are up, so the strip names both and links
    to each server's page — an operator is never told "something is running" and left to find which.
    """
    service(rows=ROOT_ROWS)
    app = build(config_dir, fleet(blue_flag=Status.RUNNING, training=Status.PAUSED))
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    # the strip names the servers holding the gate and links to each of them
    assert "<b>Blue Flag, Training</b> are running on this node" in html
    assert html.count("Shut down ") == 2
    assert f'href="/fleet/{BLUE_FLAG.replace(" ", "%20")}"' in html
    # the rows stay fully readable
    assert "Community_A-4E-C" in html and "SAM_Sites_Reworked" in html
    # the card's chip says read-only
    assert '<span class="tag">read-only</span>' in html
    # ... and NOTHING can be pressed: no door, no column, no glyph
    assert f'action="/mods/node/{NODE}/install"' not in html
    assert f'action="/mods/node/{NODE}/download"' not in html
    assert '<th scope="col" class="acts">' not in html
    assert f'action="/mods/node/{NODE}/change"' not in html
    assert f'action="/mods/node/{NODE}/remove"' not in html
    assert "act-btn" not in html


@pytest.mark.asyncio
async def test_the_node_page_says_so_when_the_plugin_is_not_enabled(config_dir):
    """With no service the rows cannot be read: the page renders the ACTION's own sentence."""
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)
    assert "Mod management is not installed" in html or "is not available in this installation" in html
    assert "Traceback" not in html


# ------------------------------------------------------------------------- 3. the install dialog

@pytest.mark.asyncio
async def test_the_install_dialog_lists_the_packages_read_from_the_node(config_dir, service):
    """The folder is enumerated when the DIALOG is requested — one option per package version."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        response = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "install"),
                                   data={"_csrf_token": token_of(page)})
    assert response.status_code == 200, response.text
    html = response.text
    # the node was asked for the folder, exactly once, and only because the dialog was requested
    assert ("list_available_packages", NODE, "RootFolder") in double.calls
    # every option names package, version, size and when it arrived
    assert "SRS_Radio_Standalone — v2.1.2 · 24.8 MB · " in html
    assert "LotAtc_Client — v2.0.6 · 31.5 MB · " in html
    assert 'value="SRS_Radio_Standalone/2.1.2" selected' in html, "the newest entry is preselected"
    assert 'id="pkg"' in html
    assert 'type="submit"' in html and ">Install<" in html
    # nothing is installed by opening the dialog
    assert double.writes == []


@pytest.mark.asyncio
async def test_a_failing_folder_read_renders_its_own_sentence_and_no_install_button(
        config_dir, service):
    """A read that failed says so and offers NOTHING — never an empty pick that reads as empty."""
    service(rows=ROOT_ROWS, raise_list=RuntimeError("the node did not answer"))
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        response = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "install"),
                                   data={"_csrf_token": token_of(page)})
    assert response.status_code == 200
    html = response.text
    assert "the node did not answer" in html
    assert "<select" not in html
    assert '<button class="btn" type="submit"' not in html, \
        "a read that failed must not offer a primary button"
    assert ">Install<" not in html


# ----------------------------------------------------------------------- 4. the confirm token

@pytest.mark.asyncio
async def test_the_install_route_without_its_confirm_token_is_refused_and_calls_nothing(
        config_dir, service):
    """A write route that did not come through its dialog REFUSES, and the service is untouched."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        response = await http.post(mods_page.run_url(mods_page.NODE_OWNER, NODE, "install"),
                                   data={"_csrf_token": token_of(page),
                                         "package": "SRS_Radio_Standalone/2.1.2"})
    assert response.status_code == 403
    assert double.writes == [], "a refused write never reached the service"
    # the service was not even asked to enumerate the folder
    assert ("list_available_packages", NODE, "RootFolder") not in double.calls


@pytest.mark.asyncio
async def test_the_install_route_with_its_token_calls_the_action_once(config_dir, service):
    """The dialog's own form performs the write: ONE service call, and the page states the outcome."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        dialog = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "install"),
                                 data={"_csrf_token": token_of(page)})
        token = re.search(r'name="_confirm_token" value="([^"]*)"', dialog.text).group(1)
        target = re.search(r'name="target" value="([^"]*)"', dialog.text).group(1)
        response = await http.post(mods_page.run_url(mods_page.NODE_OWNER, NODE, "install"),
                                   data={"_csrf_token": token_of(dialog.text),
                                         "_confirm_token": token, "owner": "node",
                                         "target": target,
                                         "package": "SRS_Radio_Standalone/2.1.2"})
        assert response.status_code == 303, response.text
        assert response.headers["location"] == mods_page.node_page_url(NODE)
        after = await open_node_page(http)
    assert double.writes == [("install_root_package", NODE, "SRS_Radio_Standalone", "2.1.2")]
    assert "SRS_Radio_Standalone&#39; v2.1.2 installed" in after or \
           "SRS_Radio_Standalone' v2.1.2 installed" in after


@pytest.mark.asyncio
async def test_a_removal_posts_its_own_row_identity_and_the_token(config_dir, service):
    """The row's remove glyph opens a confirmation, and THAT form performs the removal."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        page = await open_node_page(http)
        dialog = await http.post(mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "remove"),
                                 data={"_csrf_token": token_of(page), "owner": "node",
                                       "target": NODE, "package": "Community_A-4E-C",
                                       "version": "2.4.1"})
        assert dialog.status_code == 200, dialog.text
        assert "Confirming" in dialog.text
        token = re.search(r'name="_confirm_token" value="([^"]*)"', dialog.text).group(1)
        response = await http.post(mods_page.run_url(mods_page.NODE_OWNER, NODE, "remove"),
                                   data={"_csrf_token": token_of(page),
                                         "_confirm_token": token, "owner": "node", "target": NODE,
                                         "package": "Community_A-4E-C", "version": "2.4.1"})
    assert response.status_code == 303
    assert double.writes == [("uninstall_root_package", NODE, "Community_A-4E-C", "2.4.1")]


# ------------------------------------------------------------------------------- 5. the gates

@pytest.mark.asyncio
async def test_a_mods_view_holder_is_refused_the_node_page_and_every_write(config_dir, service):
    """``mods.view`` without ``mods.manage``: the tab, and 403 everywhere a root package is written."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        refused = await http.get(mods_page.node_page_url(NODE))
        assert refused.status_code == 403
        for owner, target in ((mods_page.NODE_OWNER, NODE), (mods_page.SERVER_OWNER, BLUE_FLAG)):
            for verb in mods_page.VERBS:
                dialog = await http.post(mods_page.dialog_url(owner, target, verb), data={})
                assert dialog.status_code == 403, (owner, verb, dialog.status_code)
                run = await http.post(mods_page.run_url(owner, target, verb), data={})
                assert run.status_code == 403, (owner, verb, run.status_code)
    assert double.writes == []


@pytest.mark.asyncio
async def test_a_caller_without_mods_view_is_refused_the_tab(config_dir, service):
    """The tab's own capability, exercised through the same body the server page renders."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    registrar = app.state.webui_registrar
    _register_tab_probe(registrar)
    async with client(app) as http:
        await sign_in(http, "plain")
        response = await http.get("/probe/tab")
    assert response.status_code == 403


def _register_tab_probe(registrar):
    """Register the tab BODY under ``mods.view`` — what the server page's tab route will do."""
    router = APIRouter()

    @router.get("/probe/tab", response_class=HTMLResponse)
    async def probe(request: Request):
        context = await mods_page.tab_context(request, BLUE_FLAG)
        return HTMLResponse(i18n.render(request, mods_page.BODY_TEMPLATE, **context))

    registrar.register_pages("mods-tab-probe", router,
                             capabilities={"/probe/tab": mods_page.MODS_VIEW_CAPABILITY})
    return router


@pytest.mark.asyncio
async def test_a_mods_view_holder_gets_the_tab(config_dir, service):
    """The DCS Admin reads the tab: its own rows, the READ-ONLY root card and a link to that page."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    _register_tab_probe(app.state.webui_registrar)
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get("/probe/tab")
    assert response.status_code == 200, response.text
    html = response.text
    assert "Installed mods (Saved Games)" in html
    assert "DCSOlympus" in html
    assert "Root mods on node-01" in html
    assert "root mod(s) are installed on node-01" in html
    assert f'href="/nodes/{NODE}/mods"' in html
    # the tab renders no page head: the server page already rendered one
    assert "<h1>" not in html


@pytest.mark.asyncio
async def test_the_tabs_strip_carries_the_existing_shutdown_control(config_dir, service):
    """While the server runs the strip carries the Servers page's OWN shutdown control."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet(blue_flag=Status.RUNNING))
    _register_tab_probe(app.state.webui_registrar)
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get("/probe/tab")
    assert response.status_code == 200, response.text
    html = response.text
    assert f"{BLUE_FLAG} is running" in html
    # the server row's own shutdown control, which posts to ITS dialog (it confirms) — the same
    # write the Servers page offers, not a second one
    assert 'action="/actions/server/shutdown/confirm"' in html
    assert "act-btn" in html
    # the write controls of the list itself are absent
    assert f'action="/mods/server/{BLUE_FLAG}/change"' not in html


# ------------------------------------------------------------------------------- 6. the floor

def test_the_touched_modules_parse_on_python_3_11():
    """The tree's floor is 3.11: no ``type`` statement and no 3.12-only f-string quoting."""
    for path in (PAGES_DIR / "pages" / "mods.py", PAGES_DIR / "readmodels" / "mods.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_the_pages_declare_both_capabilities_and_contribute_no_nav_item():
    """One string per capability, both declared, and no menu entry (the menu is a pinned six)."""
    from conftest import permissions

    assert permissions.roles_for_capability(mods_page.MODS_VIEW_CAPABILITY) == \
        mods_page.MODS_VIEW_ROLES
    assert permissions.roles_for_capability(mods_page.MODS_MANAGE_CAPABILITY) == \
        mods_page.MODS_MANAGE_ROLES
    assert permissions.scope_grants(mods_page.MODS_VIEW_CAPABILITY) is True
    assert permissions.scope_grants(mods_page.MODS_MANAGE_CAPABILITY) is False
    assert mods_page.nav_items() == ()


def test_the_folders_match_the_plugins_own_vocabulary():
    """The console keeps its own copy of the two folder words; the plugin's are the authority."""
    import sys

    sys.modules.setdefault("services.modmanager", types.ModuleType("services.modmanager"))
    from plugins.modmanager import actions as plugin_actions

    from services.webservice.readmodels import mods as read_model

    assert read_model.ROOT_FOLDER == plugin_actions.ROOT_FOLDER
    assert read_model.SAVED_GAMES == plugin_actions.SAVED_GAMES


# ------------------------------------------------- 7. the controls follow the caller and the install

@pytest.mark.asyncio
async def test_a_mods_view_only_caller_reads_the_tab_with_no_door_and_no_row_form(
        config_dir, service):
    """``mods.view`` WITHOUT ``mods.manage``: the table and its strip, and NO write control.

    Route exercised: the tab body probe ``/probe/tab``, which registers ``tab_context`` under
    ``mods.view`` — the same capability the server page declares for its Mods tab.
    """
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    _register_tab_probe(app.state.webui_registrar)
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get("/probe/tab")
    assert response.status_code == 200, response.text
    html = response.text
    # the read IS present, and the strip says the gate is open for this server
    assert "Installed mods (Saved Games)" in html
    assert "DCSOlympus" in html
    assert f"{BLUE_FLAG} is shut down" in html
    # the installed card's chip says read-only (the root card carries its own one)
    assert html.count('<span class="tag">read-only</span>') == 2
    # ... and NOTHING can be pressed: no door, no Actions column, no row form
    for verb in mods_page.VERBS:
        assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, verb)}"' \
            not in html, verb
    assert '<th scope="col" class="acts">' not in html
    assert "act-btn" not in html


@pytest.mark.asyncio
async def test_a_mods_manage_caller_still_gets_the_tabs_doors_and_row_forms(
        config_dir, service):
    """The ORDINARY path is unchanged: ``mods.manage`` gets both doors and both row glyphs."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS)
    app = build(config_dir, fleet())
    _register_tab_probe(app.state.webui_registrar)
    async with client(app) as http:
        await sign_in(http)
        response = await http.get("/probe/tab")
    assert response.status_code == 200, response.text
    html = response.text
    assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, "install")}"' in html
    assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, "download")}"' in html
    assert '<th scope="col" class="acts">Actions</th>' in html
    assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, "change")}"' in html
    assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, "remove")}"' in html
    assert html.count('<span class="tag">read-only</span>') == 1


@pytest.mark.asyncio
async def test_with_the_plugin_off_an_open_gate_draws_no_door_and_keeps_the_sentence(config_dir):
    """Plugin off + gate open: no door is drawn; the action's own sentence carries the state.

    Route exercised: the node page ``/nodes/node-01/mods``.
    """
    app = build(config_dir, fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)
    assert f'action="{mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "install")}"' not in html
    assert f'action="{mods_page.dialog_url(mods_page.NODE_OWNER, NODE, "download")}"' not in html
    assert "Mod management is not installed" in html or \
        "is not available in this installation" in html


# ------------------------------------------- 8. the version read: the change door and the chip
#
# The installed table's version facts are ONE node read per render (``list_mod_versions``). It decides
# two things: whether a row's change-version door is drawn at all, and whether the row carries a chip
# naming a newer version. A node that does not answer leaves both at what the page drew before the read
# existed — every row keeps its change door and no row carries a chip.

def row_html(html: str, package: str) -> str:
    """The ``<tr>`` of ONE installed row, so a per-row assertion cannot be satisfied by a sibling."""
    match = re.search(r"<tr><td><b>%s</b></td>.*?</tr>" % re.escape(package), html, re.S)
    assert match, f"no installed row for {package}"
    return match.group(0)


@pytest.mark.asyncio
async def test_a_package_with_one_version_gets_no_change_door_and_one_with_two_does(config_dir, service):
    """ONE version only: no change-version control at all. A second version: the control, as today."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    single = row_html(html, "SAM_Sites_Reworked")
    assert f'action="/mods/node/{NODE}/change"' not in single, \
        "a package with one version has nothing to change to"
    assert f'aria-label="Change the installed version of SAM_Sites_Reworked"' not in single
    # the removal is a different question and stays
    assert f'action="/mods/node/{NODE}/remove"' in single
    assert f'aria-label="Remove SAM_Sites_Reworked v3.0.0 from {NODE}"' in single

    pair = row_html(html, "Community_A-4E-C")
    assert f'action="/mods/node/{NODE}/change"' in pair
    # the identity fields the change dialog has always posted
    assert 'name="owner" value="node"' in pair
    assert 'name="target" value="node-01"' in pair
    assert 'name="package" value="Community_A-4E-C"' in pair
    assert 'name="version" value="2.4.1"' in pair
    # exactly one row offers the change door
    assert html.count(f'action="/mods/node/{NODE}/change"') == 1


@pytest.mark.asyncio
async def test_a_newer_version_chips_the_row_and_an_equal_one_does_not(config_dir, service):
    """``latest`` beats the installed version: a chip beside it, with the sentence in its tooltip."""
    service(rows=ROOT_ROWS, versions={
        "Community_A-4E-C": {"versions": ["2.4.1", "2.4.2"], "latest": "2.4.2",
                             "repo": "", "downloaded": True},
        "SAM_Sites_Reworked": {"versions": ["3.0.0"], "latest": "3.0.0",
                               "repo": "", "downloaded": True},
    })
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    chip = row_html(html, "Community_A-4E-C")
    assert ">update 2.4.2<" in chip
    assert ("Version 2.4.2 of Community_A-4E-C is available in the package folder; the installed "
            "version is 2.4.1.") in chip
    assert 'class="tag a mods-update"' in chip
    # the sentence is the accessible name too, not only a tooltip
    assert chip.count("is available in the package folder") == 2

    equal = row_html(html, "SAM_Sites_Reworked")
    assert "mods-update" not in equal, "no chip when the newest version IS the installed one"
    assert ">update" not in equal


@pytest.mark.asyncio
async def test_a_repository_only_version_carries_the_download_first_sentence(config_dir, service):
    """A release that is not a file in the folder says it has to be downloaded before it is installed."""
    service(rows=ROOT_ROWS, versions={
        "Community_A-4E-C": {"versions": ["2.4.1", "2.5.0"], "latest": "2.5.0",
                             "repo": "https://github.com/heclak/community-a4e-c",
                             "downloaded": False},
        "SAM_Sites_Reworked": {"versions": ["3.0.0"], "latest": "3.0.0",
                               "repo": "", "downloaded": True},
    })
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    chip = row_html(html, "Community_A-4E-C")
    assert ">update 2.5.0<" in chip
    assert ("Version 2.5.0 of Community_A-4E-C is available in its repository and has to be "
            "downloaded before it can be installed.") in chip
    assert "is available in the package folder" not in chip


@pytest.mark.asyncio
async def test_a_failed_version_read_renders_todays_page(config_dir, service):
    """Node offline / read timeout: every row keeps its change door and no row carries a chip."""
    double = service(rows=ROOT_ROWS, raise_versions=RuntimeError("the node did not answer"))
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        html = await open_node_page(http)

    assert html.count(f'action="/mods/node/{NODE}/change"') == 2, \
        "an unreachable node must not lose a control the page drew before the read existed"
    single = row_html(html, "SAM_Sites_Reworked")
    assert f'action="/mods/node/{NODE}/change"' in single
    assert "mods-update" not in html
    assert "the node did not answer" not in html, "the read's failure is not a page banner"
    assert double.version_reads, "the read was attempted"


@pytest.mark.asyncio
async def test_the_page_makes_one_version_call_for_the_whole_table(config_dir, service):
    """ONE node read per render, however many rows the table has — and a refresh reuses it."""
    double = service(rows=ROOT_ROWS)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http)
        await open_node_page(http)
        await open_node_page(http)

    assert double.version_reads == [
        ("list_package_versions", NODE, "RootFolder", ("Community_A-4E-C", "SAM_Sites_Reworked"))
    ], "one call for the table, answered from the cache on the second render"


@pytest.mark.asyncio
async def test_a_mods_view_only_caller_sees_the_chip_and_no_write_control(config_dir, service):
    """The chip is INFORMATION: a read-only caller sees it, and still gets no door or row form."""
    service(rows=ROOT_ROWS, saved_rows=SAVED_ROWS, versions={
        "DCSOlympus": {"versions": ["1.0.3", "1.0.4"], "latest": "1.0.4",
                       "repo": "", "downloaded": True},
        "Community_A-4E-C": {"versions": ["2.4.1"], "latest": "2.4.1",
                             "repo": "", "downloaded": True},
        "SAM_Sites_Reworked": {"versions": ["3.0.0"], "latest": "3.0.0",
                               "repo": "", "downloaded": True},
    })
    app = build(config_dir, fleet())
    _register_tab_probe(app.state.webui_registrar)
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get("/probe/tab")
    assert response.status_code == 200, response.text
    html = response.text
    assert ">update 1.0.4<" in row_html(html, "DCSOlympus")
    for verb in mods_page.VERBS:
        assert f'action="{mods_page.dialog_url(mods_page.SERVER_OWNER, BLUE_FLAG, verb)}"' \
            not in html, verb
    assert '<th scope="col" class="acts">' not in html
    assert "act-btn" not in html
