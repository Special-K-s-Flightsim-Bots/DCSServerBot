"""Shared fixtures for the admin web shell test suite.

Run with the system pytest (interpreter ``/usr/bin/python3``); pytest is a developer tool, not a
runtime dependency, so it is deliberately not listed in ``requirements.in``.

Nothing here imports the product at module level. Every test in ``tests/`` loads this conftest, so a
module-level ``services.webservice`` import would put the console stack — ``shell``, its page
modules, ``core.actions``, ``core.utils.helper`` — into the dependency set of every test in the tree,
whether it touches the console or not. The names are therefore resolved on first use: through
``__getattr__`` for the ones test modules import from here, at collection time for the whole stack
when the worker collected a console test, and inside the fixture that needs one. A worker running
only tests that never mention the console imports none of it, so a test's dependency set is the set
of modules it actually exercises.
"""
from __future__ import annotations

import functools
import importlib
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest

@functools.lru_cache(maxsize=1)
def symlinks_available() -> bool:
    """Can this host create a directory symlink?

    A POSIX host can. Windows allows it only with ``SeCreateSymbolicLinkPrivilege`` — Developer Mode
    or an elevated shell — and a normal account's attempt raises ``WinError 1314``. Probed once with
    a real link, so a machine where it works is never skipped.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        target = Path(root, "target")
        target.mkdir()
        try:
            Path(root, "link").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
    return True


#: a STRING condition: it is evaluated when the test is collected, so a suite run under a
#: symlink-denying host is judged by that host, not by the moment conftest was imported.
needs_symlinks = pytest.mark.skipif(
    "not symlinks_available()",
    reason="this host cannot create a directory symlink (Windows without Developer Mode / "
           "SeCreateSymbolicLinkPrivilege)")


@pytest.fixture(autouse=True)
def _no_window_minimize(monkeypatch):
    """Neutralise ``ServerImpl._minimize`` on Windows, where the launch path actually calls it.

    It enumerates REAL top-level windows through pywin32 and needs ``self.process``; a test owns no
    window and no process, so the call dies with ``AttributeError: 'NoneType' object has no
    attribute 'pid'`` before doing anything. The call site is guarded by ``sys.platform == 'win32'``,
    so on every other host this fixture is not installed at all.
    """
    if sys.platform == "win32":
        from core.data.impl import serverimpl
        monkeypatch.setattr(serverimpl.ServerImpl, "_minimize", lambda self: None)


#: Every Python child a test starts (the setup console, browser probes, CLI harnesses) writes to a PIPE
#: this suite decodes as UTF-8. Without this, a Windows child encodes stdio with the console's own code
#: page, and a single 0x92/0xB7 byte breaks the PARENT's reader thread — the child's stdout arrives as
#: ``None`` and the test dies on an unrelated ``AttributeError``. Set once here: children inherit it.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def host_path(path: str) -> str:
    """*path* as the bot states a mission path ON THIS HOST.

    ``core/utils/dcs.py``'s one normaliser joins the components with ``os.sep``, so a mission list
    the bot writes back carries the host's own separators. An expectation built from a POSIX literal
    has to be normalised the same way or it can only match on a POSIX host.
    """
    return os.path.normpath(path)


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def link_or_copy(target, link) -> None:
    """*link* as a directory symlink to *target*, or a COPY of it where the host cannot make one.

    Windows needs SeCreateSymbolicLinkPrivilege — Developer Mode, or an elevated process — to create
    a symlink, and answers ``WinError 1314`` without it. A fixture that only needs the files PRESENT
    (the site probes' ``static`` sheet) copies instead of failing the render on such a host; a test
    whose SUBJECT is a link must not use this and should skip instead.
    """
    target, link = Path(target), Path(link)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.copytree(target, link)


# ``services/webservice/__init__.py`` used to eagerly import ``.service`` -> the bot's ``core``
# (Discord, psycopg, lupa,...), which this suite's interpreter cannot import. A fake
# ``services.webservice`` module used to be registered here to dodge that — and it is exactly why
# 171 tests missed that ``python -m services.webservice.auth.local --hash`` could not run at all:
# in-process, the package was never the one the operator imports. The package is now import-safe
# (a PEP 562 ``__getattr__`` resolves ``WebService``/``WebServiceBase`` lazily), so the real
# package is imported (lazily, see ``__getattr__`` below) and any regression on that property fails
# the suite instead of being masked. The real command is additionally run as a subprocess in
# ``test_webui_auth_cli.py``.

#: Names a test module imports from this one, resolved on first access. ``(module, attribute)``;
#: ``attribute=None`` means the module object itself. ``test_webui_auth_cli.py`` and friends rely on
#: this: the name must exist as an attribute of this module, so it cannot simply move into a fixture.
_LAZY_ATTRIBUTES: dict[str, tuple[str, str | None]] = {
    "APIRouter": ("fastapi", "APIRouter"),
    "Depends": ("fastapi", "Depends"),
    "Request": ("fastapi", "Request"),
    "contributors": ("services.webservice.contributors", None),
    "permissions": ("services.webservice.permissions", None),
    "session": ("services.webservice.session", None),
    "Registrar": ("services.webservice.registry", "Registrar"),
    "Scope": ("services.webservice.scope", "Scope"),
    "CLUSTER_ROLES": ("services.webservice.scope", "CLUSTER_ROLES"),
    "create_app": ("services.webservice.shell", "create_app"),
    "install_shell": ("services.webservice.shell", "install_shell"),
}

__all__ = ["permissions", "session", "contributors", "Registrar", "create_app", "install_shell",
           "Scope", "CLUSTER_ROLES", "APIRouter", "Depends", "Request", "asgi_client",
           "simple_router", "stub_identity", "run_node_harness", "back_target",
           "KEEPS_PROCESS_STATE", "input_fields", "password_fields"]


def __getattr__(name: str):
    """Resolve a name from :data:`_LAZY_ATTRIBUTES` on first access, then cache it here.

    A module-level ``__getattr__`` is only consulted for names that are NOT in the module globals,
    so writing the value back makes the second access a plain lookup.
    """
    try:
        module_path, attribute = _LAZY_ATTRIBUTES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_path)
    value = module if attribute is None else getattr(module, attribute)
    globals()[name] = value
    return value


#: Marker for a test that drives TWO shell installations in one process: it needs the state the
#: first install left behind, so the automatic snapshot/restore is skipped for it. Such a test
#: restores the capability tables itself (see ``test_webui_access_matrix``).
KEEPS_PROCESS_STATE = "keeps_process_state"

#: one rendered ``<input>`` tag, attributes captured as ``(name, value)`` pairs
INPUT_TAG = re.compile(r"<input\b([^>]*)>", re.I)
ATTRIBUTE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')

#: the console package: importing it pulls the whole shell, its page modules and the core modules
#: they read (``core.actions``, ``core.utils.helper``).
CONSOLE_PACKAGE = "services.webservice"

#: every console module a fixture or a helper in this file touches. Imported together, once, at
#: collection time when this worker collected a console test (see ``pytest_collection_modifyitems``).
CONSOLE_MODULES = (
    "services.webservice.contributors",
    "services.webservice.dcs_update",
    "services.webservice.i18n",
    "services.webservice.mount",
    "services.webservice.pages.actions",
    "services.webservice.pages.dashboard",
    "services.webservice.permissions",
    "services.webservice.registry",
    "services.webservice.scope",
    "services.webservice.session",
    "services.webservice.setup.servers",
    "services.webservice.shell",
    "services.webservice.upgrade",
)

#: text a test module carries when it can reach the console. A test may import inside the test body,
#: where a fixture cannot see it before it has to capture a baseline, so the decision is made from
#: the module's own source. ``ACTION_TOKENS`` does the same for the action seam: the registry is
#: reached through ``core.actions`` itself, through ``discover_actions``, or through the registry
#: accessor — a test that merely imports a plugin's ``commands`` module is not touching it.
CONSOLE_TOKENS = (CONSOLE_PACKAGE,)
ACTION_TOKENS = ("core.actions", "core import actions", "action_registry", "discover_actions",
                 "actions_module")


@functools.lru_cache(maxsize=None)
def _source_mentions(path: str, token: str) -> bool:
    """Whether the test module at ``path`` names ``token`` in its own source.

    Unreadable source answers ``True``: isolation is never skipped on a guess.
    """
    try:
        return token in Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True


def _mentions(requester, tokens: tuple[str, ...]) -> bool:
    """True when the test module names any of ``tokens``. Takes a fixture request or a collected item."""
    path = str(requester.path)
    return any(_source_mentions(path, token) for token in tokens)


def console_test(request) -> bool:
    """Whether the running test can reach the console stack at all.

    A text match on the test module's own source, never on ``sys.modules``: the stack is imported
    ONCE per worker at collection time (``pytest_collection_modifyitems``) as soon as that worker
    collected one console test, so "already imported" says nothing about the test in hand. The match
    is a SUPERSET — every test that imports the console carries the literal, and no shared helper
    module exists in this directory that could import it on a test's behalf — so a test that only
    mentions the package in a comment pays for isolation it does not need: the safe direction.
    """
    return _mentions(request, CONSOLE_TOKENS)


def action_seam_test(request) -> bool:
    """Whether the running test can reach the ``@action`` registry (see ``isolate_action_registry``).

    Same source match as :func:`console_test`, plus :data:`ACTION_TOKENS`: a test that registers or
    reads an action names the module, the accessor or the discovery call it uses.
    """
    return console_test(request) or _mentions(request, ACTION_TOKENS)


def pytest_collection_modifyitems(config, items):
    """Import the console stack once, now, if this worker collected a console test.

    Collection is where the six imports used to live — as module-level imports of this file, which
    every test in the tree loads. A worker whose items are all outside the console now imports
    nothing of it, and the modules a console test does need are already in ``sys.modules`` before
    any test runs, so no fixture performs a first import DURING a test: an import executed inside a
    test is exactly what makes the console a recorded dependency of every test that ran.
    """
    if not any(console_test(item) for item in items):
        return
    for name in CONSOLE_MODULES:
        importlib.import_module(name)


def input_fields(html: str) -> list[dict[str, str]]:
    """Every RENDERED ``<input>`` element, attributes as a lowercase-keyed dict.

    ``login.html`` ships CSS rules that select ``input[type="password"]``, ``input[name="username"]``
    and friends on every page that embeds its stylesheet, whether or not the field is rendered. A
    substring search over the document answers yes for that CSS; parsing the tags answers for the page.
    """
    fields = []
    for attributes in INPUT_TAG.findall(html):
        fields.append({name.lower(): value for name, value in ATTRIBUTE.findall(attributes)})
    return fields


def password_fields(html: str) -> list[dict[str, str]]:
    """Every rendered password input (``input_fields`` narrowed by ``type``)."""
    return [field for field in input_fields(html)
            if field.get("type", "").strip().lower() == "password"]



def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{KEEPS_PROCESS_STATE}: skip the capability-table snapshot/restore for a test that disables "
        "it deliberately and restores the tables itself")



def back_target(markup: str, page_path: str) -> str:
    """The URL a dialog's ``data-back`` RESOLVES to, read from the page that rendered it.

    A dialog returns with a reference RELATIVE to itself (``../../../fleet?tab=players``), so that the
    reverse-proxy prefix it was served under survives the navigation; the browser resolves it against
    the dialog's own URL. This does the same resolution, so a test asserts WHERE the person lands
    rather than the spelling of the attribute.
    """
    match = re.search(r'data-back="([^"]*)"', markup)
    assert match, "the form states no data-back"
    return urljoin(page_path, match.group(1))


@pytest.fixture(autouse=True)
def clean_capability_state(request):
    """Capabilities and the three resolvers are process state: restore them after every test so a
    declaration or a narrowing made by one test cannot silently authorise a route in the next one.

    ``SCOPE_GRANTS`` is restored alongside ``CAPABILITIES`` because the two are the two axes of one
    declaration: a test that narrows a capability (see ``test_webui_tab_scope.narrow``) writes both,
    and restoring only the role tuple would leave the scope axis narrowed — the next test's
    ``declare_capability`` would then find a changed rule and raise, or worse, quietly admit a
    manager through a capability that no longer carries the scope.

    A test marked :data:`KEEPS_PROCESS_STATE` opts OUT of the snapshot/restore: it drives two shell
    installations in one process and has to see what the first one left behind. It restores the
    tables itself — that is what the marker commits it to.

    The snapshot is taken from the tables as the test finds them, which is only meaningful for a
    test that can reach them at all: hence the :func:`console_test` gate. A test outside the console
    gets no snapshot and needs none — nothing it can reach writes these tables.
    """
    if not console_test(request):
        yield
        return

    from services.webservice import permissions

    if request.node.get_closest_marker(KEEPS_PROCESS_STATE) is not None:
        try:
            yield
        finally:
            permissions.set_role_resolver(None)
            permissions.set_scope_resolver(None)
            permissions.set_manager_resolver(None)
        return

    saved = dict(permissions.CAPABILITIES)
    saved_grants = dict(permissions.SCOPE_GRANTS)
    saved_base = dict(permissions.BASE_CAPABILITIES)
    saved_base_grants = dict(permissions.BASE_SCOPE_GRANTS)
    saved_applied = dict(permissions.APPLIED_OVERRIDES)
    try:
        yield
    finally:
        for table, values in ((permissions.CAPABILITIES, dict(saved)),
                              (permissions.SCOPE_GRANTS, dict(saved_grants)),
                              (permissions.BASE_CAPABILITIES, dict(saved_base)),
                              (permissions.BASE_SCOPE_GRANTS, dict(saved_base_grants)),
                              (permissions.APPLIED_OVERRIDES, dict(saved_applied))):
            table.clear()
            table.update(values)                  # type: ignore[arg-type]  # five tables, one shape
        permissions.set_role_resolver(None)
        permissions.set_scope_resolver(None)
        permissions.set_manager_resolver(None)


@pytest.fixture(autouse=True)
def isolate_action_registry(request):
    """The ``@action`` REGISTRY is PROCESS state — the single biggest order-dependence in this suite.

    A console page decides which write controls to offer from ``core.actions.action_available``,
    which answers out of the process-global ``core.actions._ACTIONS`` mapping. That mapping is
    populated ONCE PER PROCESS by ``discover_actions``, which works by IMPORTING a plugin's
    ``actions.py`` and running its ``@action`` decorators — and an import is a no-op the second time
    in one process. So whether a page renders an "Actions" column (or a node row's controls) depends
    ENTIRELY on which plugin's ``actions.py`` some EARLIER test happened to import:

 * ``test_webui_console_layout`` renders the Servers table; if any earlier test imported
      ``plugins.mission.actions`` the registry now holds ``startup_server`` … so the page grows an
      Actions column the table model does not describe, and the "one header per column" pin fails;
 * ``test_webui_server_maintenance`` CLEARS the registry (via its own ``clean_registry``) and then
      relies on the shell's install to re-discover the node actions — but if ``plugins.admin.actions``
      is still cached, the re-import is a no-op, the registry stays EMPTY, and the node row renders
      no controls at all.

    Both are the same defect: process state leaking across tests. This fixture makes every test start
    from the SAME baseline — the registry cleared and the plugin ``*.actions`` modules dropped from
    the module cache — so a test that wants controls must have its OWN install discover them, exactly
    as the running console does. The saved registry and every dropped module are restored afterwards,
    so the fixture undoes only its own changes (and nests cleanly under the per-file ``clean_registry``
    / ``clean_scheduler_registry`` fixtures, which do the same job for one plugin).

    It also resets the seam's other two process-global bits — the in-flight guard and the per-server
    config write locks — for the same reason: a sticky target or lock is a refusal no test asked for.

    Skipped for a test that cannot reach the seam — see :func:`action_seam_test`: a test that never
    imports ``core.actions`` and never imports a plugin's ``actions`` module can neither populate the
    registry nor read it, so the state this fixture resets IS the baseline for it.
    """
    if not action_seam_test(request):
        yield
        return

    from core import actions as actions_module

    def _action_modules() -> dict:
        return {name: module for name, module in list(sys.modules.items())
                if name.startswith("plugins.") and name.endswith(".actions")}

    def _drop_action_modules() -> None:
        for name in list(sys.modules):
            if name.startswith("plugins.") and name.endswith(".actions"):
                del sys.modules[name]

    saved_registry = actions_module.action_registry()
    saved_modules = _action_modules()
    actions_module._ACTIONS.clear()
    actions_module.reset_in_flight()
    actions_module.reset_write_locks()
    _drop_action_modules()
    try:
        yield
    finally:
        _drop_action_modules()
        sys.modules.update(saved_modules)
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved_registry)
        actions_module.reset_in_flight()
        actions_module.reset_write_locks()


@pytest.fixture(autouse=True)
def clean_contributor_state(request):
    """The plugin-page registry is process state too (a fresh registrar replays from it), so a
    contributor registered by one test must not be replayed into the next test's shell.

    Gated on :func:`console_test`: a test that never reaches the console neither registers a
    contributor nor reads one, and skipping the import is the point of this module.
    """
    if not console_test(request):
        yield
        return

    from services.webservice import contributors

    saved = contributors.contributors()
    try:
        yield
    finally:
        contributors.set_contributors(saved)


@pytest.fixture(autouse=True)
def clean_awaiting_state(request):
    """The expectation store is PROCESS state : clear it after every test.

    It left the session for a process-side mapping keyed by target, exactly like the seam's in-flight
    set — so it needs the same discipline ``reset_in_flight`` gets: an expectation left behind by one
    test is a pulse some later render never asked for.

    The CACHED UPGRADE ANSWERS  are cleared for the same reason and at the same place: the
    cache gates the node row's Upgrade control, so an answer left by one test would change whether a
    later test's page offers that control. A test that wants a value seeds it itself. The CACHED DCS
    ANSWERS are the same kind of process state and are cleared beside them.
    """
    if not console_test(request):
        yield
        return

    from services.webservice import dcs_update, upgrade as upgrade_signal
    from services.webservice.pages import actions as actions_page

    try:
        yield
    finally:
        actions_page.reset_awaiting_changes()
        upgrade_signal.reset()
        dcs_update.reset()


@pytest.fixture(autouse=True)
def clean_mission_list_cache():
    """``utils.get_cached_mission_list`` is PROCESS state with a 10 s life, keyed by the SERVER NAME.

    Two tests that use DIFFERENT doubles carrying the SAME name (``SRS-1``) therefore share one cache
    entry, so a mission list read for one leaks into the next within the 10 s window — which is why
    ``test_mission_seam``'s picker test fails when it runs after the webui mission tests in one
    session (webui ``MissionServer("SRS-1")`` caches its ``missionList``; the seam ``ServerDouble``
    of the same name is then served THAT list and ``Only.miz`` stops being excluded). The cache is
    process state like the registry and the scope above, so it gets the same discipline: cleared
    around every test.

    Not gated on a text match: this cache is FILLED from inside an action (``get_mission_list``), so
    the tests that need it cleared never name the module. The gate is the module itself — an
    unimported ``core.utils.discord`` holds no cache, and once any test has imported it the module
    stays loaded for the rest of the process, so every test that could see a stale entry clears it.
    """
    def _clear() -> None:
        if "core.utils.discord" in sys.modules:
            sys.modules["core.utils.discord"].get_cached_mission_list.cache_clear()

    _clear()
    try:
        yield
    finally:
        _clear()


@pytest.fixture(autouse=True)
def isolated_saved_games(tmp_path, monkeypatch, request):
    """The setup commit CREATES and seeds each new instance's ``Saved Games`` folder.

    ``core.const.SAVED_GAMES`` is built from ``%USERPROFILE%``, which a POSIX test run does not carry:
    the literal variable stays in the path, so an instance home derived from it is a RELATIVE path
    inside the repository. The setup module's own module-level import is what its builders read, so
    that is the name pinned at a directory this test owns.
    """
    if not console_test(request):
        yield
        return

    from services.webservice.setup import servers as setup_servers

    monkeypatch.setattr(setup_servers, "SAVED_GAMES", str(tmp_path / "Saved Games"))
    yield


@pytest.fixture(autouse=True)
def monitoring_plugin_loaded(monkeypatch, request):
    """The console runs here as an installation WITH the optional monitoring plugin.

    The dashboard's Load tab is offered only while that plugin's read is registered
    (``dashboard.load_tab_available``), and no test imports the plugin's real actions module, so the
    registration is stubbed at the same seam the page reads. A test that wants the plugin ABSENT
    empties the registry itself.
    """
    if not console_test(request):
        yield
        return

    from services.webservice.pages import dashboard as dashboard_page

    registered = dict(dashboard_page.action_registry())
    registered.setdefault("get_load_statistics", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard_page, "action_registry", lambda: registered)
    yield


@pytest.fixture
def music_plugin_loaded(monkeypatch):
    """The console runs here as an installation WITH the optional music plugin.

    NOT autouse: the stub writes into the PROCESS-WIDE action registry, so applying it to every test
    leaks a foreign action into any pin that asserts what a plugin registers (the modmanager pin that
    compares the registry against its own eight actions). A test that wants the Media › Music entry
    offered — or the music page reachable — asks for this fixture by name; one that wants the plugin
    absent removes the action itself.

    The Media › Music nav entry is offered only while the music read action is registered
    (``pages/music.nav_items``), and no test imports the plugin's real actions module, so the
    registration is stubbed at the same seam the page reads.
    """
    from core import actions as actions_module

    monkeypatch.setitem(actions_module._ACTIONS, "get_music_radios", lambda *args, **kwargs: None)


@pytest.fixture
def shell_app():
    """A fresh application created through the production factory, with the shell installed.

    Returns ``(app, registrar)``. Nothing is shared between tests: a test that registers a
    contributor must not leak it into the next one.
    """
    shell = importlib.import_module("services.webservice.shell")
    app = shell.create_app()
    registrar = shell.install_shell(app, node=types.SimpleNamespace(master=True), config={})
    return app, registrar


def asgi_client(app) -> httpx.AsyncClient:
    """A client that drives the real application in-process (no port, no server process).

    ``https`` base URL on purpose: the cookie policy derives ``Secure`` from the bind address, so a
    console that is not loopback-only sets a Secure session cookie by default — and a browser (and
    httpx's cookie jar) does not send a Secure cookie back over plain HTTP. A test that round-trips
    a session therefore has to look like the deployment it exercises: over TLS. The tests that care
    about the plain-HTTP case configure a loopback bind explicitly.
    """
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


def stub_identity(roles, *, scope=None):
    """Stub BOTH halves of a signed-in identity (roles and scope) for tests that need a caller without a backend.

    AuthManager resolves both per request from the same backend, so a caller with only its roles stubbed
    keeps an unresolved scope, which FAILS CLOSED and renders every page empty. ``scope`` defaults to what
    a resolved identity declaring no restriction has: ``Scope.everything()`` when the roles hold a cluster
    role (``Admin`` / ``DCS Admin``), ``Scope.restricted(())`` otherwise. Pass
    ``scope=Scope.restricted({"Host A"})`` to stub a hoster's view.
    """
    from services.webservice import permissions
    from services.webservice.scope import CLUSTER_ROLES, Scope

    roles = set(roles)
    if scope is None:
        scope = (Scope.everything() if roles & set(CLUSTER_ROLES) else Scope.restricted(()))
    permissions.set_role_resolver(lambda request: set(roles))
    permissions.set_scope_resolver(lambda request: scope)


def run_node_harness(argv: list) -> subprocess.CompletedProcess:
    """Run a browser-less JS harness with the SERVED message map installed.

    The console's shipped scripts no longer carry their strings: they read ``window.dcssbMsg`` from
    the map ``base.html`` serves (``/i18n/messages.js``). So a harness that boots a shipped file is
    handed the SAME served script (English — the msgid) through ``DCSSB_MESSAGES_JS``; the harness
    evaluates it into its ``window`` before the shipped file runs. Nothing is copied into the tests:
    the map is produced by the production code (``i18n.messages_script_for``).
    """
    import os
    import tempfile

    from services.webservice import i18n

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "messages.en.js"
        path.write_text(i18n.messages_script_for(i18n.BASE_LANGUAGE), encoding="utf-8")
        env = dict(os.environ)
        env["DCSSB_MESSAGES_JS"] = str(path)
        return subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", env=env)


def simple_router(path: str, body: str = "ok", *, dependencies=None):
    """A one-route contributor router, for registration/gate probes."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get(path, dependencies=list(dependencies or []))
    async def _handler():
        return {"body": body, "path": path}

    return router


@pytest.fixture(autouse=True)
def reset_console_mount_prefix(request):
    """The console's mount prefix is PROCESS state — ``mount._PREFIX`` — and it decides every URL.

    ``mount.install_console_mount`` publishes the prefix the installed catalogue implies: ``""``
    while the console keeps ``/``, ``/console`` once an enabled site is served at the root. A page
    module that builds a URL outside a request reads that process value, so an application installed
    by one test decides the addresses another test's page prints — and the suite runs tests of
    different files in one worker. Reset it around every test: a page that needs a prefix installs
    the application that has it.
    """
    if not console_test(request):
        yield
        return

    from services.webservice import mount

    saved = mount._PREFIX
    mount._PREFIX = ""
    try:
        yield
    finally:
        mount._PREFIX = saved