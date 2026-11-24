"""Pins for the ModManager ACTION layer.

Every mod write the console and the chat client perform goes through one ``@action`` function in
``plugins.modmanager.actions``, invoked as ``call_action`` (write) or ``read_action`` (read) with an
:class:`~core.actions.ActionContext`. These cases drive those functions directly with a RECORDING
service double: the questions are the ones the action layer owns — which service method a folder
maps to, in which order a version change runs its two halves, what a half-done change says, whether
a bad folder or a foreign name is refused before anything is called, and whether an installation
without the service gets a typed sentence instead of a ``TypeError`` or an empty list.

The service double is ``ModManagerService.__new__``-free on purpose: an action resolves the service
from ``ServiceRegistry``, so the double only has to answer methods — and be *absent* from the
registry when the plugin is not enabled.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import types
from datetime import datetime

import pytest

try:
    from core.actions import (ActionContext, action_registry, call_action, discover_actions,
                              in_flight_targets, read_action)
    from core.action_results import ActionResult
    from core.services.registry import ServiceRegistry
    from services.modmanager import ModManagerService
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)

from plugins.modmanager.actions import (ROOT_FOLDER, SAVED_GAMES, SERVICE_MISSING, is_leaf_name)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ACTIONS_FILE = REPO_ROOT / "plugins" / "modmanager" / "actions.py"

STAMP = datetime(2024, 5, 6, 7, 8)


# --------------------------------------------------------------------------------------------- doubles


def folder_name(folder) -> str:
    """``Folder`` value or the string that was passed — the folder as the double records it."""
    return getattr(folder, "value", folder)


class _Cursor:
    """A recording cursor: keeps every statement with its parameters, answers a preset row set.

    The row set is taken from the pool PER STATEMENT, so a read that issues two queries over ONE
    connection gets the second answer for the second query.
    """

    def __init__(self, pool):
        self._pool = pool
        self.statements: list[tuple[object, object]] = []
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
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        return False


class Pool:
    """An ``apool`` double:each statement consumes the next preset row set and is recorded."""

    def __init__(self, row_sets=()):
        self.row_sets = [list(rows) for rows in row_sets]
        self.cursors: list[_Cursor] = []

    def next_rows(self) -> list:
        return self.row_sets.pop(0) if self.row_sets else []

    def connection(self):
        cursor = _Cursor(self)
        self.cursors.append(cursor)
        return _Connection(cursor)

    @property
    def statements(self) -> list[tuple[object, object]]:
        return [statement for cursor in self.cursors for statement in cursor.statements]


class RecordingService:
    """A ``ModManagerService`` double: records every call and answers a preset value per method."""

    def __init__(self, *, answers=None, rows=(), installed=None, entries=(), row_sets=None):
        self.calls: list[tuple] = []
        self.answers = dict(answers or {})
        self.installed = installed
        self.entries = list(entries)
        self.apool = Pool(row_sets if row_sets is not None else [rows])

    async def _answer(self, method):
        value = self.answers.get(method, True)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return await value()
        return value

    def _record(self, method, target, *rest):
        self.calls.append((method, str(getattr(target, "name", target))) + rest)

    async def install_package(self, server, folder, package_name, version, repo=None):
        self._record("install_package", server, folder_name(folder), package_name, version)
        return await self._answer("install_package")

    async def install_root_package(self, node, package_name, version, repo=None):
        self._record("install_root_package", node, package_name, version)
        return await self._answer("install_root_package")

    async def uninstall_package(self, server, folder, package_name, version, force=False, repo=None):
        self._record("uninstall_package", server, folder_name(folder), package_name, version)
        return await self._answer("uninstall_package")

    async def uninstall_root_package(self, node, package_name, version, force=False, repo=None):
        self._record("uninstall_root_package", node, package_name, version)
        return await self._answer("uninstall_root_package")

    async def get_installed_package(self, reference, folder, package_name):
        self._record("get_installed_package", reference, folder_name(folder), package_name)
        return self.installed

    async def list_available_packages(self, node, folder):
        self._record("list_available_packages", node, folder_name(folder))
        answer = await self._answer("list_available_packages")
        return self.entries if answer is True else answer

    async def download_package(self, node, folder, url, *, version=None, package_name=None, force=False):
        self._record("download_package", node, folder_name(folder), url, version, package_name)
        return await self._answer("download_package")

    # the two static readers the action asks the service for, taken from the service itself
    parse_filename = staticmethod(ModManagerService.parse_filename)
    is_repo_url = staticmethod(ModManagerService.is_repo_url)

    @property
    def methods_called(self) -> list[str]:
        return [call[0] for call in self.calls]


# --------------------------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def no_service(monkeypatch):
    """The service is ABSENT unless a case registers a double — the plugin-not-enabled case."""
    monkeypatch.delitem(ServiceRegistry._singletons, ModManagerService, raising=False)


@pytest.fixture
def registered(monkeypatch):
    """Register a service double as the process's running ``ModManagerService``."""

    def install(double):
        monkeypatch.setitem(ServiceRegistry._singletons, ModManagerService, double)
        return double

    return install


@pytest.fixture(autouse=True)
def modmanager_actions():
    """Discover the plugin's actions — the shared fixtures empty the process registry per test."""
    discover_actions(["modmanager"])
    yield


def node(name="node-01"):
    return types.SimpleNamespace(name=name, display_name=name, is_remote=False)


def server(name="SRS-1", node_name="node-01", managed_by=("Blue Flag",)):
    return types.SimpleNamespace(
        name=name, display_name=name, node=node(node_name),
        locals={"managed_by": list(managed_by)} if managed_by else {},
    )


def everywhere():
    """The console's scope duck-type for a caller that sees everything."""
    return types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=True, resolved=True)


def nothing_outside(managed_by=("Blue Flag",)):
    """A RESTRICTED scope that holds only the servers in *managed_by* — everything else is foreign."""
    return types.SimpleNamespace(
        allows=lambda names: any(name in managed_by for name in (names or ())),
        unrestricted=False, resolved=True)


def context(*, scope=None, nodes=("node-01",), servers=None):
    """An ``ActionContext`` over a caller-visible cluster: these nodes, these servers."""
    cluster = types.SimpleNamespace(
        servers={item.name: item for item in (servers if servers is not None else [server()])},
        nodes={name: node(name) for name in nodes},
    )
    ctx = ActionContext(node=node(), bus=cluster)
    ctx._scope = everywhere() if scope is None else scope
    return ctx


# ------------------------------------------------------------------- the registry declaration


def test_the_module_registers_the_eight_actions():
    """``@action`` IS the declaration: discovery of the plugin offers all eight, and nothing else."""
    assert set(action_registry()) == {
        "list_installed_mods", "list_available_mods", "list_repo_versions", "list_mod_versions",
        "install_mod", "uninstall_mod", "change_mod_version", "download_mod",
    }


def test_the_module_parses_on_python_3_11():
    """The tree's floor is 3.11: no ``type`` statement and no 3.12-only f-string quoting."""
    source = ACTIONS_FILE.read_text(encoding="utf-8")
    ast.parse(source, filename=str(ACTIONS_FILE), feature_version=(3, 11))


# --------------------------------------------------------------- the service is not installed


ALL_CALLS = {
    "list_installed_mods": dict(node_name="node-01", server_name="SRS-1"),
    "list_available_mods": dict(node_name="node-01", folder=ROOT_FOLDER),
    "list_repo_versions": dict(node_name="node-01", url="https://github.com/example/mod"),
    "list_mod_versions": dict(node_name="node-01", folder=ROOT_FOLDER, packages=["SRS"]),
    "install_mod": dict(server_name="SRS-1", folder=SAVED_GAMES, package="SRS", version="2.1.2"),
    "uninstall_mod": dict(server_name="SRS-1", folder=SAVED_GAMES, package="SRS", version="2.1.2"),
    "change_mod_version": dict(server_name="SRS-1", folder=SAVED_GAMES, package="SRS", version="2.1.2"),
    "download_mod": dict(node_name="node-01", folder=ROOT_FOLDER, url="https://example.invalid/p.zip"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("qualname", sorted(ALL_CALLS))
async def test_every_action_refuses_with_a_typed_result_when_the_service_is_absent(
        qualname):
    """An installation without the plugin answers the one sentence — never a ``TypeError``."""
    discover_actions(["modmanager"])

    result = await call_action(qualname, context(), **ALL_CALLS[qualname])

    assert isinstance(result, ActionResult)
    assert result.success is False
    assert result.message == SERVICE_MISSING


# ------------------------------------------------------------------- the folder decides the call


@pytest.mark.asyncio
async def test_install_mod_on_root_folder_calls_install_root_package(registered):
    """``RootFolder`` is the NODE's DCS installation: ``install_root_package(node, …)``."""
    double = registered(RecordingService())

    result = await call_action("install_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                               package="SRS_Radio_Standalone", version="2.1.2")

    assert result.success is True
    assert double.calls == [("install_root_package", "node-01", "SRS_Radio_Standalone", "2.1.2")]
    assert result.data["package"] == "SRS_Radio_Standalone"
    assert result.data["version"] == "2.1.2"
    assert result.data["folder"] == ROOT_FOLDER
    assert result.data["target"] == "node-01"
    assert "installed" in result.message


@pytest.mark.asyncio
async def test_install_mod_on_saved_games_calls_install_package(registered):
    """``SavedGames`` is ONE instance: ``install_package(server, Folder.SavedGames, …)``."""
    double = registered(RecordingService())

    result = await call_action("install_mod", context(), server_name="SRS-1", folder=SAVED_GAMES,
                               package="SRS_Radio_Standalone", version="2.1.2")

    assert result.success is True
    assert double.calls == [("install_package", "SRS-1", SAVED_GAMES, "SRS_Radio_Standalone", "2.1.2")]
    assert result.data["target"] == "SRS-1"
    assert "SRS-1" in result.message


@pytest.mark.asyncio
async def test_uninstall_mod_calls_the_folder_specific_method(registered):
    """The removal split is the install split: root on the node, Saved Games on the server."""
    double = registered(RecordingService())

    root = await call_action("uninstall_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                             package="SAM_Sites", version="3.0.0")
    saved = await call_action("uninstall_mod", context(), server_name="SRS-1", folder=SAVED_GAMES,
                              package="SAM_Sites", version="3.0.0")

    assert root.success is True and saved.success is True
    assert double.calls == [
        ("uninstall_root_package", "node-01", "SAM_Sites", "3.0.0"),
        ("uninstall_package", "SRS-1", SAVED_GAMES, "SAM_Sites", "3.0.0"),
    ]


@pytest.mark.asyncio
async def test_install_mod_refuses_when_the_folder_needs_a_reference_it_does_not_have(registered):
    """A ``RootFolder`` write with no node, and a ``SavedGames`` write with no server, are refusals."""
    double = registered(RecordingService())

    root = await call_action("install_mod", context(), folder=ROOT_FOLDER, package="P", version="1.0")
    saved = await call_action("install_mod", context(), folder=SAVED_GAMES, package="P", version="1.0")

    assert root.success is False and "node" in root.message.lower()
    assert saved.success is False and "server" in saved.message.lower()
    assert double.calls == []


# ------------------------------------------------------------------------------- version change


@pytest.mark.asyncio
async def test_change_mod_version_uninstalls_the_installed_version_then_installs_the_chosen_one(
        registered):
    """One action, two service calls, that order — a version change is never half a pick."""
    double = registered(RecordingService(installed="2.0.0"))

    result = await call_action("change_mod_version", context(), server_name="SRS-1",
                               folder=SAVED_GAMES, package="SRS", version="2.1.2")

    assert result.success is True
    assert double.methods_called == ["get_installed_package", "uninstall_package", "install_package"]
    assert double.calls[1] == ("uninstall_package", "SRS-1", SAVED_GAMES, "SRS", "2.0.0")
    assert double.calls[2] == ("install_package", "SRS-1", SAVED_GAMES, "SRS", "2.1.2")
    assert result.data["version"] == "2.1.2"


@pytest.mark.asyncio
async def test_change_mod_version_reports_an_absent_package_when_the_install_fails(registered):
    """The uninstall landed, so the honest report is ABSENT — never "unchanged"."""
    double = registered(RecordingService(installed="2.0.0",
                                         answers={"install_package": False}))

    result = await call_action("change_mod_version", context(), server_name="SRS-1",
                               folder=SAVED_GAMES, package="SRS", version="2.1.2")

    assert result.success is False
    assert "absent" in result.message.lower()
    assert "2.1.2" in result.message
    assert double.methods_called == ["get_installed_package", "uninstall_package", "install_package"]


@pytest.mark.asyncio
async def test_change_mod_version_refuses_when_the_chosen_version_is_already_installed(registered):
    """Nothing to change is a refusal, not two calls that would rewrite the same files."""
    double = registered(RecordingService(installed="2.1.2"))

    result = await call_action("change_mod_version", context(), server_name="SRS-1",
                               folder=SAVED_GAMES, package="SRS", version="2.1.2")

    assert result.success is False
    assert double.methods_called == ["get_installed_package"]


# --------------------------------------------------------------------------------- the guards


@pytest.mark.asyncio
async def test_an_unknown_folder_is_refused_before_any_service_call(registered):
    """``folder`` is one of two names — a third is never coerced into one of them."""
    double = registered(RecordingService())

    write = await call_action("install_mod", context(), server_name="SRS-1",
                              folder="SavedGames/..", package="P", version="1.0")
    read = await read_action("list_available_mods", context(), node_name="node-01", folder="mods")

    assert write.success is False and "SavedGames/.." in write.message
    assert read.success is False and "mods" in read.message
    assert double.calls == []


@pytest.mark.asyncio
async def test_a_server_outside_the_callers_scope_is_refused(registered):
    """A manager naming somebody else's server learns nothing and reaches no service method."""
    double = registered(RecordingService())
    foreign = context(scope=nothing_outside(managed_by=("Host A",)))

    result = await call_action("install_mod", foreign, server_name="SRS-1",
                               folder=SAVED_GAMES, package="P", version="1.0")

    assert result.success is False
    assert "scope" in result.message.lower()
    assert double.calls == []


@pytest.mark.asyncio
async def test_a_node_outside_the_callers_scope_is_refused(registered):
    """The root half is refused at the scope, not in the page: no node capability, no write."""
    double = registered(RecordingService())
    foreign = context(scope=nothing_outside(), nodes=())

    result = await call_action("install_mod", foreign, node_name="node-02", folder=ROOT_FOLDER,
                               package="P", version="1.0")

    assert result.success is False
    assert "scope" in result.message.lower()
    assert double.calls == []


@pytest.mark.asyncio
async def test_read_action_lists_while_a_write_is_in_flight_on_the_same_target(registered):
    """A read takes no in-flight guard: the page renders WHILE the write runs."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_install():
        entered.set()
        await release.wait()
        return True

    registered(RecordingService(answers={"install_package": slow_install}))
    ctx = context()
    write = asyncio.create_task(call_action("install_mod", ctx, server_name="SRS-1",
                                            folder=SAVED_GAMES, package="P", version="1.0"))
    await entered.wait()
    assert in_flight_targets() == frozenset({"server:srs-1"})

    read = await read_action("list_installed_mods", ctx, node_name="node-01", server_name="SRS-1")
    release.set()
    written = await write

    assert read.success is True
    assert written.success is True


# ------------------------------------------------------------------------------------ the reads


@pytest.mark.asyncio
async def test_list_installed_mods_reports_package_version_stamp_and_folder(registered):
    """The rows a page renders: the node's root half, and the server's Saved Games half."""
    from plugins.modmanager.actions import ModListResult

    double = registered(RecordingService(row_sets=[
        [("Community_A-4E-C", "2.4.1", STAMP, ROOT_FOLDER)],
        [("SRS_Radio_Standalone", "2.1.2", STAMP, SAVED_GAMES)],
    ]))

    result = await read_action("list_installed_mods", context(), node_name="node-01",
                               server_name="SRS-1")

    assert isinstance(result, ModListResult)
    assert result.success is True
    assert result.packages == [
        {"package": "Community_A-4E-C", "version": "2.4.1", "installed": STAMP,
         "folder": ROOT_FOLDER},
        {"package": "SRS_Radio_Standalone", "version": "2.1.2", "installed": STAMP,
         "folder": SAVED_GAMES},
    ]
    assert [params for _statement, params in double.apool.statements] == [
        ("node-01", ROOT_FOLDER), ("SRS-1", SAVED_GAMES),
    ]


@pytest.mark.asyncio
async def test_list_installed_mods_reads_the_node_half_only_without_a_server(registered):
    """An empty ``server_name`` is the node half alone — one read, no server resolution."""
    double = registered(RecordingService(row_sets=[[("A4E-C", "2.4.1", STAMP, ROOT_FOLDER)]]))

    result = await read_action("list_installed_mods", context(), node_name="node-01")

    assert result.success is True
    assert [row["folder"] for row in result.packages] == [ROOT_FOLDER]
    assert [params for _statement, params in double.apool.statements] == [("node-01", ROOT_FOLDER)]


@pytest.mark.asyncio
async def test_list_installed_mods_answers_an_empty_list_only_when_nothing_is_installed(registered):
    """An empty folder is a success with no rows — never a refusal that reads like one."""
    registered(RecordingService(row_sets=[[]]))

    result = await read_action("list_installed_mods", context(), node_name="node-01")

    assert result.success is True
    assert result.packages == []
    assert "no mods" in result.message.lower()


@pytest.mark.asyncio
async def test_list_available_mods_asks_the_node_for_the_folder_of_the_named_kind(registered):
    """The available list is the node's folder read: ``list_available_packages(node, Folder)``."""
    entries = [{"name": "A4E-C", "version": "2.4.1", "kind": "zip", "size": 12, "mtime": 1.0}]
    double = registered(RecordingService(entries=entries))

    result = await read_action("list_available_mods", context(), node_name="node-01",
                               folder=ROOT_FOLDER)

    assert result.success is True
    assert result.packages == entries
    assert result.folder == ROOT_FOLDER
    assert double.calls == [("list_available_packages", "node-01", ROOT_FOLDER)]


@pytest.mark.asyncio
async def test_list_available_mods_reports_a_failing_folder_read_as_its_own_sentence(registered):
    """A folder the node cannot read is a failure sentence, never an empty list standing in."""
    registered(RecordingService(answers={"list_available_packages": OSError("folder is gone")}))

    result = await read_action("list_available_mods", context(), node_name="node-01",
                               folder=ROOT_FOLDER)

    assert result.success is False
    assert "folder is gone" in result.message


# ---------------------------------------------------------------------------------- download


@pytest.mark.asyncio
async def test_download_mod_reports_the_package_the_download_produced(registered):
    """The node-side download, named back by the package entry it left in the folder."""
    double = registered(RecordingService(entries=[
        {"name": "SRS_Radio_Standalone", "version": "2.1.2", "kind": "zip", "size": 9, "mtime": 5.0},
    ]))

    result = await call_action("download_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                               url="https://example.invalid/SRS_v2.1.2.zip", version="2.1.2")

    assert result.success is True
    assert "SRS_Radio_Standalone" in result.message
    assert double.calls[0] == ("download_package", "node-01", ROOT_FOLDER,
                               "https://example.invalid/SRS_v2.1.2.zip", "2.1.2", None)
    assert double.calls[1] == ("list_available_packages", "node-01", ROOT_FOLDER)


@pytest.mark.asyncio
async def test_download_mod_refuses_a_repository_offering_several_assets(registered):
    """``False`` means the pick was ambiguous: the sentence says a version has to be named."""
    registered(RecordingService(answers={"download_package": False}))

    result = await call_action("download_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                               url="https://github.com/heclak/community-a4e-c")

    assert result.success is False
    assert "version" in result.message.lower()
    assert "nothing was downloaded" in result.message.lower()


@pytest.mark.asyncio
async def test_download_mod_refuses_a_missing_url(registered):
    """No URL is a typed refusal, before the node is asked anything."""
    double = registered(RecordingService())

    result = await call_action("download_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                               url="   ")

    assert result.success is False
    assert double.calls == []


# ------------------------------------------------------------- a raising call is SURFACED


@pytest.mark.asyncio
async def test_install_mod_reports_a_raising_service_call_with_its_own_text(registered):
    """A raise is not a refusal to swallow: the sentence carries the service's own words."""
    registered(RecordingService(answers={"install_package": PermissionError("game files are locked")}))

    result = await call_action("install_mod", context(), server_name="SRS-1", folder=SAVED_GAMES,
                               package="SRS", version="2.1.2")

    assert result.success is False
    assert "game files are locked" in result.message
    assert "not installed" not in result.message


@pytest.mark.asyncio
async def test_change_mod_version_reports_absence_when_the_install_raises_after_the_removal(registered):
    """A raise on the second half still leaves the package ABSENT — the report says so with its text."""
    registered(RecordingService(installed="2.0.0",
                                answers={"install_package": OSError("disk full")}))

    result = await call_action("change_mod_version", context(), server_name="SRS-1",
                               folder=SAVED_GAMES, package="SRS", version="2.1.2")

    assert result.success is False
    assert "absent" in result.message.lower()
    assert "disk full" in result.message
    assert result.data["state"] == "absent"


def test_the_module_imports_only_core_and_the_standard_library():
    """Discovery imports this file on every install: nothing else may be required to import it."""
    stdlib = {"__future__", "asyncio", "dataclasses", "logging", "typing"}
    tree = ast.parse(ACTIONS_FILE.read_text(encoding="utf-8"), filename=str(ACTIONS_FILE))
    imported = []
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imported.extend(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            imported.append(statement.module or "")
    assert imported, "the walk must find the module's imports"
    offenders = [name for name in imported
                 if not (name.split(".")[0] in stdlib or name == "core" or name.startswith("core."))]
    assert not offenders, f"module-level imports the install may not have: {offenders}"


# ------------------------------------------- the identity a write may carry: ONE leaf, never a path
#
# The pair a write names is joined onto a package-folder path, so a value that is a LOCATION instead
# of a leaf would reach outside it. The guard is a denylist of the traversing shapes and nothing else:
# the enumerator decides what a package is called, and the names it produces carry underscores, dots,
# hyphens and digits.

#: every shape that must never reach the service
TRAVERSING = ("../../evil", "../x", "..", "/etc/passwd", "C:\\Windows\\System32\\cmd.exe",
              "\\\\server\\share\\x", "mods/../../evil", "a/b", "a\\b", "x\x00y")

#: the names and versions the enumerator produces from the existing fixtures
PUBLISHED = (("Community_A-4E-C", "2.4.1"), ("SAM_Sites_Reworked", "3.0.0"),
             ("SRS_Radio_Standalone", "2.1.2"), ("SRS_Radio_Standalone", "2.1.0"),
             ("LotAtc_Client", "2.0.6"), ("DCSOlympus", "1.0.3"), ("A4E-C", "0.9.0"))


async def _every_write(package, version):
    """The four write actions, each called with the same pair."""
    return [
        await call_action("install_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                          package=package, version=version),
        await call_action("uninstall_mod", context(), server_name="SRS-1", folder=SAVED_GAMES,
                          package=package, version=version),
        await call_action("change_mod_version", context(), node_name="node-01",
                          folder=ROOT_FOLDER, package=package, version=version),
        await call_action("download_mod", context(), node_name="node-01", folder=ROOT_FOLDER,
                          url="https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip",
                          version=version, package_name=package),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("package", TRAVERSING)
async def test_no_write_reaches_the_service_with_a_traversing_package(registered, package):
    """A package that is a path is refused by EVERY write, before any service call."""
    double = registered(RecordingService(installed="0.0.1"))

    results = await _every_write(package, "2.1.2")

    for result in results:
        assert isinstance(result, ActionResult)
        assert result.success is False, result.message
        assert "is not a package name" in result.message
        assert package in result.message
    assert double.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", TRAVERSING)
async def test_no_write_reaches_the_service_with_a_traversing_version(registered, version):
    """The version half is guarded on its own: a path never reaches the folder join."""
    double = registered(RecordingService(installed="0.0.1"))

    results = await _every_write("SRS_Radio_Standalone", version)

    for result in results:
        assert result.success is False, result.message
        assert "is not a version" in result.message
        assert version in result.message
    assert double.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("package,version", PUBLISHED)
async def test_every_published_name_still_installs_removes_and_changes(registered, package,
                                                                      version):
    """The ordinary path is untouched: every fixture name reaches the service unchanged."""
    double = registered(RecordingService(installed="0.0.1"))

    installed, removed, changed = (await _every_write(package, version))[:3]

    assert installed.success is True, installed.message
    assert removed.success is True, removed.message
    assert changed.success is True, changed.message
    assert double.calls[:5] == [
        ("install_root_package", "node-01", package, version),
        ("uninstall_package", "SRS-1", SAVED_GAMES, package, version),
        ("get_installed_package", "node-01", ROOT_FOLDER, package),
        ("uninstall_root_package", "node-01", package, "0.0.1"),
        ("install_root_package", "node-01", package, version),
    ]
    assert double.calls[5] == (
        "download_package", "node-01", ROOT_FOLDER,
        "https://example.invalid/SRS_Radio_Standalone_v2.1.2.zip", "2.1.2", package)


@pytest.mark.asyncio
@pytest.mark.parametrize("package,version", PUBLISHED)
async def test_the_guard_accepts_every_published_pair(registered, package, version):
    """The guard refuses the traversing shapes only — every published pair passes it."""
    assert is_leaf_name(package) is True
    assert is_leaf_name(version) is True


@pytest.mark.asyncio
async def test_the_guard_accepts_every_name_the_enumerator_produces(registered, tmp_path):
    """Driven from the ENUMERATOR, not from a list: whatever it reports as a package passes.

    The folder holds the fixture names as real entries (a zip and a directory), is enumerated by the
    SERVICE, and each produced ``(name, version)`` is handed to a real install.
    """
    import zipfile

    from services.modmanager.service import Folder
    from test_modmanager_node_aware import _Node, _service

    root = tmp_path / "RootFolder"
    root.mkdir()
    for name, version in PUBLISHED:
        with zipfile.ZipFile(root / f"{name}_v{version}.zip", "w") as zfile:
            zfile.writestr("Mods/x.lua", "return")
    enumerator = _service(packages={"RootFolder": str(root), "SavedGames": str(tmp_path)})

    entries = await enumerator.list_available_packages(_Node("node-local", remote=False),
                                                      Folder.RootFolder)

    assert {(entry["name"], entry["version"]) for entry in entries} == set(PUBLISHED)
    double = registered(RecordingService(installed="0.0.1"))
    for name, version in ((entry["name"], entry["version"]) for entry in entries):
        assert is_leaf_name(name) is True
        assert is_leaf_name(version) is True
        result = await call_action("install_mod", context(), node_name="node-01",
                                   folder=ROOT_FOLDER, package=name, version=version)
        assert result.success is True, (name, version, result.message)
    assert len(double.calls) == len(PUBLISHED)
