"""
Action functions for the ModManager — the ONE surface mods are installed, removed and changed through.

Each function takes a typed parameter set plus an :class:`~core.actions.ActionContext`, resolves its
target through the seam, asks the :class:`~services.modmanager.service.ModManagerService` to do the
work and translates the answer into a typed result. The transport wraps the result; the console renders
``result.message`` in its one-shot notice and the chat client sends it as-is.

TWO OWNERS, one parameter each — the difference is what the service does with the package, not a
choice made here:

============================  ==============  ==========================================
``folder``                    target          service call
============================  ==============  ==========================================
``RootFolder``                a NODE          ``install_root_package`` /
                                              ``uninstall_root_package`` — the DCS
                                              installation, shared by every instance on
                                              that machine
``SavedGames``                a SERVER        ``install_package`` /
                                              ``uninstall_package`` — one instance
============================  ==============  ==========================================

THE SERVICE OWNS THE WORK. Nothing here writes a file, a configuration value or a database row, and
nothing here repeats a rule the service applies — the package layout, the ``install.log`` bookkeeping,
the ``mm_packages`` row and every state guard (a server that is up cannot take a mod) are the
service's, and its refusals are surfaced as this module's own sentences rather than swallowed.

A node is resolved through :meth:`~core.actions.ActionContext.resolve_scoped_node` and a server through
:meth:`~core.actions.ActionContext.resolve_scoped_server`, so a caller that does not hold the target is
refused HERE — by the action every transport calls — and not only in a page.

The module imports ``core`` and nothing else at import time: discovery imports this file on every
install, and a module that raised would register no action at all, silently removing every mod control.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from core.action_results import ActionResult
from core.actions import NodeResolution, ServerResolution, action, audit_action

log = logging.getLogger(__name__)

__all__ = [
    "ROOT_FOLDER",
    "SAVED_GAMES",
    "MOD_FOLDERS",
    "SERVICE_MISSING",
    "is_leaf_name",
    "ModResult",
    "ModListResult",
    "ModVersionResult",
    "ModAvailabilityResult",
    "list_installed_mods",
    "list_available_mods",
    "list_repo_versions",
    "list_mod_versions",
    "install_mod",
    "uninstall_mod",
    "change_mod_version",
    "download_mod",
]

#: the two package folders of ``mm_packages`` — the wire values of the service's ``Folder``
ROOT_FOLDER = "RootFolder"
SAVED_GAMES = "SavedGames"
MOD_FOLDERS = (ROOT_FOLDER, SAVED_GAMES)

#: what a caller of an install without the ``modmanager`` plugin is told
SERVICE_MISSING = "Mod management is not installed: enable the modmanager plugin."

UNKNOWN_FOLDER = ("Unknown package folder '{folder}': a mod lives in '{root}' (the DCS installation of "
                  "a node) or '{saved}' (one server's Saved Games).").format(
    folder="{folder}", root=ROOT_FOLDER, saved=SAVED_GAMES)


# ── the results ───────────────────────────────────────────────────────────
# Two dataclasses rather than bare ActionResult values, so a transport can read the fields it words a
# notice with instead of parsing the sentence. Each carries the same facts in ``data`` (the shape every
# transport spreads), written once at construction so the two can never drift.


@dataclass
class ModResult(ActionResult):
    """The outcome of ONE mod write.

    * ``package`` / ``version`` — the package and the version the write was ABOUT (for a version
      change: the version that was chosen, never the one it replaced);
    * ``folder`` — ``RootFolder`` or ``SavedGames``;
    * ``target`` — the node (``RootFolder``) or the server (``SavedGames``) the write was aimed at.
    """

    package: str = ""
    version: str = ""
    folder: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        for key in ("package", "version", "folder", "target"):
            self.data.setdefault(key, getattr(self, key))


@dataclass
class ModListResult(ActionResult):
    """The outcome of a mod READ.

    ``packages`` is the row set the read answered — for the installed list each row is
    ``{"package", "version", "installed", "folder"}``, for an available list the service's own entry
    (``name``, ``version``, ``kind``, ``size``, ``mtime``). An empty row set with ``success`` set is
    the truthful "nothing is here"; a read that could not run is a failure carrying its own sentence.
    """

    node_name: str = ""
    server_name: str = ""
    folder: str = ""
    packages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data.setdefault("node_name", self.node_name)
        self.data.setdefault("server_name", self.server_name)
        self.data.setdefault("folder", self.folder)
        self.data.setdefault("packages", self.packages)


@dataclass
class ModVersionResult(ActionResult):
    """The outcome of a RELEASE read: the releases of one repository URL, newest first.

    ``versions`` is empty both for a URL that is not a repository and for a repository that has
    published no release; ``message`` says which of the two it is. A read that could not run is a
    failure carrying its own sentence, so a failed read never passes for an empty repository.
    """

    node_name: str = ""
    url: str = ""
    versions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data.setdefault("node_name", self.node_name)
        self.data.setdefault("url", self.url)
        self.data.setdefault("versions", self.versions)


@dataclass
class ModAvailabilityResult(ActionResult):
    """The outcome of a VERSION read over SEVERAL packages of one folder.

    ``packages`` maps a package name to what the node answered about it: ``versions`` (the folder's
    versions merged with a declared repository's releases), ``latest`` (the newest of them), ``repo``
    (the declared repository URL, empty for a plain-URL package) and ``downloaded`` (whether
    ``latest`` is a file in the folder right now). A package the node could not read is ABSENT, so a
    caller can tell "no newer version" from "not answered".
    """

    node_name: str = ""
    folder: str = ""
    packages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data.setdefault("node_name", self.node_name)
        self.data.setdefault("folder", self.folder)
        self.data.setdefault("packages", self.packages)


# ── the service and its vocabulary ────────────────────────────────────────


def _service() -> Any:
    """The process's ``ModManagerService``, or ``None`` when the plugin is not enabled.

    The import happens HERE and not at module level: the service module pulls the bot's HTTP and
    database stack, and an action module that failed to import would offer no action at all.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.modmanager import ModManagerService

        return ServiceRegistry.get(ModManagerService)
    except Exception:
        log.exception("ModManager actions: the modmanager service could not be resolved.")
        return None


def _folder(value: Any) -> Any:
    """The service's ``Folder`` for *value*, or ``None`` when *value* is not one of the two names."""
    if not isinstance(value, str) or value not in MOD_FOLDERS:
        return None
    try:
        from services.modmanager import Folder
    except Exception:
        log.exception("ModManager actions: the Folder enum could not be imported.")
        return None
    return Folder(value)


def _text(value: Any) -> str:
    """*value* as a stripped string — a caller-supplied name or version, normalised once."""
    return str(value or "").strip()


def is_leaf_name(value: Any) -> bool:
    """Whether *value* is ONE leaf name — the shape a package name and a version both have to be.

    A package name and a version are joined onto a package-folder path
    (``<folder>/<target>/<package>_v<version>``), so a value that names a LOCATION instead of a leaf
    would read, write or remove outside it. Refused: an empty value, a NUL, a path separator (``/``
    or ``\\``), the ``..`` segment and every absolute form (a leading separator, a Windows drive
    letter, a UNC prefix). Accepted: everything else, because the enumerator is the authority on what
    a package is called — ``Community_A-4E-C``, ``SRS_Radio_Standalone`` and ``2.1.2`` are names.
    """
    text = _text(value)
    if not text or "\x00" in text:
        return False
    if "/" in text or "\\" in text:
        return False
    if text == "..":
        return False
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        return False
    return True


def _unsafe_package(name: str) -> str:
    """The sentence a write refuses a package name with when it is not one leaf name."""
    return (f"'{name}' is not a package name: nothing was changed. A package name is one entry of "
            f"the package folder — it cannot be empty, absolute, or contain a path separator, a '..' "
            f"segment or a NUL character.")


def _unsafe_version(name: str, version: str) -> str:
    """The sentence a write refuses a version with when it is not one leaf name."""
    return (f"'{version}' is not a version of '{name}': nothing was changed. A version is one "
            f"package entry's own tail — it cannot be empty, absolute, or contain a path separator, "
            f"a '..' segment or a NUL character.")


def _label(target: Any, fallback: str) -> str:
    """The canonical NAME of a resolved target, or the name the caller used when it carries none."""
    return str(getattr(target, "name", "") or fallback)


def _where(folder: str, label: str) -> str:
    """WHERE a ``folder`` puts a package: the node's installation, or one server's Saved Games."""
    if folder == ROOT_FOLDER:
        return f"in the DCS installation of node '{label}'"
    return f"in Saved Games on '{label}'"


def _from(folder: str, label: str) -> str:
    """The same target read as a SOURCE, for the sentences that take a package away."""
    if folder == ROOT_FOLDER:
        return f"from the DCS installation of node '{label}'"
    return f"from Saved Games on '{label}'"


def _mod(success: bool, message: str, *, package: str = "", version: str = "", folder: str = "",
         target: str = "", **data: Any) -> ModResult:
    """A :class:`ModResult` for one outcome, its extra facts merged into ``data``."""
    result = ModResult(success=success, message=message, package=package, version=version,
                       folder=folder, target=target)
    result.data.update(data)
    return result


def _listing(success: bool, message: str, *, node_name: str = "", server_name: str = "",
             folder: str = "", packages: Any = None) -> ModListResult:
    """A :class:`ModListResult` for one outcome."""
    return ModListResult(success=success, message=message, node_name=node_name,
                         server_name=server_name, folder=folder, packages=list(packages or []))


def _versions(success: bool, message: str, *, node_name: str = "", url: str = "",
              versions: Any = None) -> ModVersionResult:
    """A :class:`ModVersionResult` for one outcome."""
    return ModVersionResult(success=success, message=message, node_name=node_name, url=url,
                            versions=[str(item) for item in (versions or [])])


async def _audited(ctx: Any, result: ModResult) -> ModResult:
    """Write the trail for *result* — ONE entry per attempt, success or refusal — and return it."""
    await audit_action(ctx, result)
    return result


# ── the seam's two resolutions ────────────────────────────────────────────


async def _node(ctx: Any, name: str) -> tuple[Any, ModResult | None]:
    """``(node, None)`` for a node in the caller's view, or ``(None, refusal)``.

    A name the caller may not see and a name nothing answers to are DIFFERENT refusals: the first is
    the scope's (the caller learns nothing about the cluster), the second is the seam's own sentence,
    which the node page already renders.
    """
    resolution = await ctx.resolve_scoped_node(name)
    if resolution.is_found:
        return resolution.node, None
    if resolution.status == NodeResolution.NOT_PERMITTED:
        return None, _mod(False, f"Not authorized: node '{name}' is outside your scope.")
    return None, _mod(False, resolution.message)


async def _server(ctx: Any, name: str) -> tuple[Any, ModResult | None]:
    """``(server, None)`` for a server in the caller's scope, or ``(None, refusal)``."""
    resolution = await ctx.resolve_scoped_server(name)
    if resolution.is_found:
        return resolution.server, None
    if resolution.status == ServerResolution.NOT_PERMITTED:
        return None, _mod(False, f"Not authorized: server '{name}' is outside your scope.")
    return None, _mod(False, resolution.message)


async def _target(ctx: Any, folder: str, *, server_name: str, node_name: str) -> tuple[Any, ModResult | None]:
    """``(target, None)`` for the object a ``folder`` write acts on, or ``(None, refusal)``.

    The folder decides WHICH reference is needed, so the absence of the other one is not a refusal —
    and the absence of the needed one is, worded as the missing thing rather than as a bad name.
    """
    if folder == ROOT_FOLDER:
        if not node_name:
            return None, _mod(False, "A mod in the DCS installation folder belongs to a node: name one.")
        return await _node(ctx, node_name)
    if not server_name:
        return None, _mod(False, "A mod in Saved Games belongs to a server: name one.")
    return await _server(ctx, server_name)


# ── the two service calls a folder maps to, shared by every write ──────────


async def _install(service: Any, folder: Any, target: Any, package: str, version: str) -> bool:
    """Run the install a ``folder`` names; ``False`` is the service's own "not available" answer."""
    if folder.value == ROOT_FOLDER:
        return await service.install_root_package(target, package, version)
    return await service.install_package(target, folder, package, version)


async def _uninstall(service: Any, folder: Any, target: Any, package: str, version: str) -> bool:
    """Run the removal a ``folder`` names; ``False`` is the service's own "cannot remove" answer."""
    if folder.value == ROOT_FOLDER:
        return await service.uninstall_root_package(target, package, version)
    return await service.uninstall_package(target, folder, package, version)


def _transport_failure(verb: str, package: str, version: str, where: str, ex: Exception) -> str:
    """The sentence a service call that RAISED earns — the verb, the package and the exception."""
    return f"Failed to {verb} '{package}' v{version} {where}: {ex}"


# ── the READS ─────────────────────────────────────────────────────────────


async def _installed_rows(service: Any, node_name: str, server_name: str) -> list[dict[str, Any]]:
    """The ``mm_packages`` rows a page renders: package, version, install stamp and folder.

    Read through the service's OWN connection pool, once per half, in the service's own order — the
    table is the service's bookkeeping, and the (package, version) pair it answers elsewhere does not
    carry the stamp or the folder a row has to state. ``RootFolder`` rows are the node's,
    ``SavedGames`` rows the named server's.
    """
    from psycopg import sql

    halves = [("node", node_name, ROOT_FOLDER)]
    if server_name:
        halves.append(("server_name", server_name, SAVED_GAMES))
    query = sql.SQL("SELECT package_name, version, time, folder FROM mm_packages "
                    "WHERE {column} = %s AND folder = %s "
                    "ORDER BY package_name, version")
    rows: list[dict[str, Any]] = []
    async with service.apool.connection() as conn:
        for column, name, folder in halves:
            cursor = await conn.execute(query.format(column=sql.Identifier(column)), (name, folder))
            async for row in cursor:
                rows.append({"package": str(row[0]), "version": str(row[1]), "installed": row[2],
                             "folder": str(row[3])})
    return rows


@action
async def list_installed_mods(ctx: Any, *, node_name: str, server_name: str = "") -> ModListResult:
    """List the mods installed for ONE node, and for ONE of its servers when one is named.

    The node half is its ``RootFolder`` rows (the DCS installation shared by every instance on that
    machine), the server half the ``SavedGames`` rows of *server_name* — the two columns of
    ``mm_packages``. An empty *server_name* is the node half alone.

    A database read, so the list answers while a node is busy or the caller's own page is rendering a
    write: it takes neither an RPC nor the in-flight guard.
    """
    service = _service()
    if service is None:
        return _listing(False, SERVICE_MISSING, node_name=node_name, server_name=server_name)
    _node_object, refusal = await _node(ctx, node_name)
    if refusal is not None:
        return _listing(False, refusal.message, node_name=node_name, server_name=server_name)
    if server_name:
        _server_object, refusal = await _server(ctx, server_name)
        if refusal is not None:
            return _listing(False, refusal.message, node_name=node_name, server_name=server_name)
    try:
        rows = await _installed_rows(service, node_name, server_name)
    except Exception as ex:
        log.exception("list_installed_mods: the installed rows could not be read")
        return _listing(False, f"The installed mods could not be read: {ex}",
                        node_name=node_name, server_name=server_name)
    where = _where(ROOT_FOLDER, node_name)
    if server_name:
        where = f"{where} and {_where(SAVED_GAMES, server_name)}"
    if not rows:
        return _listing(True, f"No mods are installed {where}.", node_name=node_name,
                        server_name=server_name)
    return _listing(True, f"{len(rows)} mod(s) installed {where}.", node_name=node_name,
                    server_name=server_name, packages=rows)


@action
async def list_available_mods(ctx: Any, *, node_name: str, folder: str) -> ModListResult:
    """List the packages sitting in ONE package folder of ONE node — what an install can pick from.

    A NODE read on purpose: the folder path carries variables that only the node that owns the disk can
    expand, so the enumeration is the node's. The entries are the service's own (``name``,
    ``version``, ``kind``, ``size``, ``mtime``) — this action maps nothing.

    A folder read on demand, never on a page render: it is an RPC to the node.
    """
    service = _service()
    if service is None:
        return _listing(False, SERVICE_MISSING, node_name=node_name, folder=folder)
    folder_enum = _folder(folder)
    if folder_enum is None:
        return _listing(False, UNKNOWN_FOLDER.format(folder=folder), node_name=node_name,
                        folder=str(folder))
    node, refusal = await _node(ctx, node_name)
    if refusal is not None:
        return _listing(False, refusal.message, node_name=node_name, folder=folder)
    label = _label(node, node_name)
    try:
        entries = await service.list_available_packages(node, folder_enum)
    except (TimeoutError, asyncio.TimeoutError):
        return _listing(False, f"Node '{label}' did not answer in time, so the packages in folder "
                               f"'{folder}' are unknown.", node_name=label, folder=folder)
    except Exception as ex:
        log.exception("list_available_mods: the package folder could not be read")
        return _listing(False, f"The packages in folder '{folder}' on node '{label}' could not be "
                               f"read: {ex}", node_name=label, folder=folder)
    if not entries:
        return _listing(True, f"No packages are available in folder '{folder}' on node '{label}'.",
                        node_name=label, folder=folder)
    return _listing(True, f"{len(entries)} package(s) available in folder '{folder}' on node "
                          f"'{label}'.", node_name=label, folder=folder, packages=entries)


@action
async def list_repo_versions(ctx: Any, *, node_name: str, url: str) -> ModVersionResult:
    """List the releases of a GitHub repository URL, newest first — what a version pick offers.

    A NODE read: the release list is fetched through the node's own proxy, so it is the node that
    downloads the packages which answers. A URL that is not a repository, and a repository that has
    published no release, are both an EMPTY list — the ``message`` says which — and a read that could
    not run is a failure carrying its own sentence, so the caller can tell an empty answer from a
    broken one.

    The node is resolved through the seam first, so a caller outside the node's scope is refused
    before any URL is inspected or any request leaves the machine.
    """
    link = _text(url)
    service = _service()
    if service is None:
        return _versions(False, SERVICE_MISSING, node_name=_text(node_name), url=link)
    node, refusal = await _node(ctx, _text(node_name))
    if refusal is not None:
        return _versions(False, refusal.message, node_name=_text(node_name), url=link)
    label = _label(node, _text(node_name))
    if not service.is_repo_url(link):
        return _versions(True, f"'{link or '(no URL)'}' is not a GitHub repository, so it has no "
                               f"release to offer.", node_name=label, url=link)
    try:
        versions = await service.list_repo_versions(node, link)
    except (TimeoutError, asyncio.TimeoutError):
        return _versions(False, f"The releases of '{link}' could not be read: node '{label}' did "
                                f"not answer in time.", node_name=label, url=link)
    except Exception as ex:
        log.exception("list_repo_versions: the release list could not be read")
        return _versions(False, f"The releases of '{link}' could not be read: {ex}", node_name=label,
                         url=link)
    ordered = [_text(version) for version in (versions or ())]
    ordered = [version for version in ordered if version]
    if not ordered:
        return _versions(True, f"'{link}' lists no release.", node_name=label, url=link)
    return _versions(True, f"{len(ordered)} release(s) of '{link}'.", node_name=label, url=link,
                     versions=ordered)


@action
async def list_mod_versions(ctx: Any, *, node_name: str, folder: str,
                            packages: list[str]) -> ModAvailabilityResult:
    """Read the versions of SEVERAL packages of one folder — the installed table's update facts.

    One NODE read for a whole table: the node answers every named package in a single call, so a
    page rendering N rows costs one RPC and not N. Each package carries its version list, its newest
    version, its declared repository and whether that newest version is a file in the folder.

    A read that could not run is a failure carrying its own sentence, and a package the node left out
    is absent from the answer: the caller words a row it can name and keeps its earlier state for a
    row it cannot. Names that are not one leaf name are dropped before anything is asked — the read
    never joins a caller's text onto a path.
    """
    service = _service()
    wanted = [_text(name) for name in (packages or ())]
    wanted = [name for name in dict.fromkeys(wanted) if name]
    folder_value = _text(folder)
    if service is None:
        return ModAvailabilityResult(success=False, message=SERVICE_MISSING,
                                     node_name=_text(node_name), folder=folder_value)
    folder_enum = _folder(folder_value)
    if folder_enum is None:
        return ModAvailabilityResult(success=False,
                                     message=UNKNOWN_FOLDER.format(folder=folder_value),
                                     node_name=_text(node_name), folder=folder_value)
    if not wanted:
        return ModAvailabilityResult(success=True, message="No package was named.",
                                     node_name=_text(node_name), folder=folder_value)
    node, refusal = await _node(ctx, _text(node_name))
    if refusal is not None:
        return ModAvailabilityResult(success=False, message=refusal.message,
                                     node_name=_text(node_name), folder=folder_value)
    label = _label(node, _text(node_name))
    read_names = [name for name in wanted if is_leaf_name(name)]
    if not read_names:
        return ModAvailabilityResult(success=False, message="No readable package name was named.",
                                     node_name=label, folder=folder_value)
    try:
        answer = await service.list_package_versions(node, folder_enum, read_names)
    except (TimeoutError, asyncio.TimeoutError):
        return ModAvailabilityResult(success=False,
                                     message=f"The versions on node '{label}' could not be read: "
                                             f"the node did not answer in time.",
                                     node_name=label, folder=folder_value)
    except Exception as ex:
        log.exception("list_mod_versions: the version read raised")
        return ModAvailabilityResult(success=False,
                                     message=f"The versions of folder '{folder_value}' on node "
                                             f"'{label}' could not be read: {ex}",
                                     node_name=label, folder=folder_value)
    rows = {str(name): dict(entry or {}) for name, entry in (answer or {}).items()}
    return ModAvailabilityResult(success=True,
                                 message=f"{len(rows)} package version(s) read on node '{label}'.",
                                 node_name=label, folder=folder_value, packages=rows)


# ── the WRITES ────────────────────────────────────────────────────────────

async def _prepared(ctx: Any, *, folder: Any, package: str, version: str, server_name: str,
                    node_name: str) -> tuple[Any, Any, str, ModResult | None]:
    """``(service, target, label, None)`` for a write about one package of one folder.

    The order is fixed and each step is one sentence: an installation without the plugin, an unknown
    folder, a missing package name or version, a missing or foreign target. Every refusal happens
    BEFORE a service is touched.
    """
    service = _service()
    if service is None:
        return None, None, "", _mod(False, SERVICE_MISSING)
    folder_enum = _folder(folder)
    if folder_enum is None:
        return None, None, "", _mod(False, UNKNOWN_FOLDER.format(folder=folder))
    if not package:
        return None, None, "", _mod(False, "A package name is required.")
    if not version:
        return None, None, "", _mod(False, f"A version is required for '{package}'.",
                                    package=package)
    if not is_leaf_name(package):
        return None, None, "", _mod(False, _unsafe_package(package), package=package)
    if not is_leaf_name(version):
        return None, None, "", _mod(False, _unsafe_version(package, version), package=package,
                                    version=version)
    target, refusal = await _target(ctx, folder, server_name=server_name, node_name=node_name)
    if refusal is not None:
        return None, None, "", refusal
    return service, (folder_enum, target), _label(target, node_name or server_name), None


@action
async def install_mod(ctx: Any, *, server_name: str = "", node_name: str = "", folder: str,
                      package: str, version: str) -> ModResult:
    """Install ONE package version into ONE folder — the operation ``/mods install`` performs.

    ``SavedGames`` installs into one instance (``install_package(server, …)``), ``RootFolder`` into the
    node's DCS installation (``install_root_package(node, …)``) and therefore needs a node where the
    other needs a server. Whether the target may take a mod at all is the service's rule; its "not
    available" answer is this action's own sentence, and a raise is reported with its text.
    """
    name, want = _text(package), _text(version)
    service, prepared, label, refusal = await _prepared(
        ctx, folder=folder, package=name, version=want, server_name=_text(server_name),
        node_name=_text(node_name))
    if refusal is not None:
        return await _audited(ctx, _mod(False, refusal.message, package=name, version=want,
                                        folder=str(folder)))
    folder_enum, target = prepared
    where = _where(folder, label)
    identity = dict(package=name, version=want, folder=folder, target=label)
    try:
        installed = await _install(service, folder_enum, target, name, want)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _mod(False, f"'{name}' v{want} was not installed {where}: the node "
                                              f"did not answer in time.", **identity))
    except Exception as ex:
        log.exception("install_mod: the install raised")
        return await _audited(ctx, _mod(False, _transport_failure("install", name, want, where, ex),
                                        **identity))
    if not installed:
        return await _audited(ctx, _mod(False, f"'{name}' v{want} was not found in folder '{folder}', "
                                              f"so nothing was installed {where}.", **identity))
    return await _audited(ctx, _mod(True, f"'{name}' v{want} installed {where}.", **identity))


@action
async def uninstall_mod(ctx: Any, *, server_name: str = "", node_name: str = "", folder: str,
                        package: str, version: str) -> ModResult:
    """Remove ONE installed package version from ONE folder — ``/mods uninstall``.

    The same split as the install: ``SavedGames`` removes from one instance
    (``uninstall_package(server, …)``), ``RootFolder`` from the node's DCS installation
    (``uninstall_root_package(node, …)``). A removal the service cannot perform — its ``install.log``
    is gone and could not be recreated — is reported as the refusal it is, never as a success.
    """
    name, want = _text(package), _text(version)
    service, prepared, label, refusal = await _prepared(
        ctx, folder=folder, package=name, version=want, server_name=_text(server_name),
        node_name=_text(node_name))
    if refusal is not None:
        return await _audited(ctx, _mod(False, refusal.message, package=name, version=want,
                                        folder=str(folder)))
    folder_enum, target = prepared
    where = _from(folder, label)
    identity = dict(package=name, version=want, folder=folder, target=label)
    try:
        removed = await _uninstall(service, folder_enum, target, name, want)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _mod(False, f"'{name}' v{want} was not removed {where}: the node "
                                              f"did not answer in time.", **identity))
    except Exception as ex:
        log.exception("uninstall_mod: the removal raised")
        return await _audited(ctx, _mod(False, _transport_failure("remove", name, want, where, ex),
                                        **identity))
    if not removed:
        return await _audited(ctx, _mod(False, f"'{name}' v{want} could not be removed {where}: its "
                                              f"installation log is missing and could not be "
                                              f"recreated, so nothing was removed.", **identity))
    return await _audited(ctx, _mod(True, f"'{name}' v{want} removed {where}.", **identity))


@action
async def change_mod_version(ctx: Any, *, server_name: str = "", node_name: str = "", folder: str,
                             package: str, version: str) -> ModResult:
    """Replace the installed version of ONE package with *version* — one action, ONE outcome.

    A version change is a removal and an install of the SAME package, and it must never be two writes
    an operator can abandon halfway. So both halves run here, in that order, and the action answers
    once:

    * the INSTALLED version is read from the service (:meth:`get_installed_package`) — the caller names
      only the version it wants;
    * nothing installed, or that version already installed, is a refusal and touches nothing;
    * a removal that fails says so and leaves the package as it was;
    * a removal that succeeded followed by an install that failed says exactly that the package is now
      ABSENT — never "unchanged", which would be the one report an operator cannot act on.
    """
    name, want = _text(package), _text(version)
    service, prepared, label, refusal = await _prepared(
        ctx, folder=folder, package=name, version=want, server_name=_text(server_name),
        node_name=_text(node_name))
    if refusal is not None:
        return await _audited(ctx, _mod(False, refusal.message, package=name, version=want,
                                        folder=str(folder)))
    folder_enum, target = prepared
    where = _where(folder, label)
    source = _from(folder, label)
    identity = dict(package=name, version=want, folder=folder, target=label)
    try:
        installed = await service.get_installed_package(target, folder_enum, name)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _mod(False, f"The installed version of '{name}' {where} is unknown: "
                                              f"the node did not answer in time; nothing was changed.",
                                        **identity))
    except Exception as ex:
        log.exception("change_mod_version: the installed version could not be read")
        return await _audited(ctx, _mod(False, f"The installed version of '{name}' {where} could not be "
                                              f"read, so nothing was changed: {ex}", **identity))
    current = _text(installed)
    if not current:
        return await _audited(ctx, _mod(False, f"'{name}' is not installed {where}, so there is no "
                                              f"version to change.", **identity))
    if current == want:
        return await _audited(ctx, _mod(False, f"'{name}' v{want} is already the installed version "
                                              f"{where}.", **identity))
    try:
        removed = await _uninstall(service, folder_enum, target, name, current)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _mod(False, f"'{name}' v{current} was not removed {source}: the node "
                                              f"did not answer in time; v{current} is still installed.",
                                        **identity, previous_version=current))
    except Exception as ex:
        log.exception("change_mod_version: the removal raised")
        return await _audited(ctx, _mod(False, f"'{name}' v{current} could not be removed {source}, so "
                                              f"v{current} is still installed: {ex}",
                                        **identity, previous_version=current))
    if not removed:
        return await _audited(ctx, _mod(False, f"'{name}' v{current} could not be removed {source}, so "
                                              f"v{current} is still installed: its installation log is "
                                              f"missing and could not be recreated.",
                                        **identity, previous_version=current))
    try:
        installed_ok = await _install(service, folder_enum, target, name, want)
    except Exception as ex:
        log.exception("change_mod_version: the install raised after the removal")
        installed_ok = False
        failure = str(ex)
    else:
        failure = ""
    if not installed_ok:
        detail = f": {failure}" if failure else ""
        # THE HALF-DONE STATE, named: the removal landed, so the package is ABSENT on this target.
        return await _audited(ctx, _mod(
            False,
            f"'{name}' v{current} was removed {source}, but v{want} could not be installed{detail} — "
            f"the package is now ABSENT.",
            **identity, previous_version=current, state="absent"))
    return await _audited(ctx, _mod(True, f"'{name}' v{current} was replaced with v{want} {where}.",
                                    **identity, previous_version=current))


def _url_filename(link: str) -> str:
    """The FILE NAME a URL names: its last path segment, without the query or the fragment."""
    path = link.split("#", 1)[0].split("?", 1)[0]
    return path.rsplit("/", 1)[-1]


def _url_version(service: Any, link: str) -> str:
    """The version *link*'s own file name declares, or ``''`` when its name declares none.

    Read through the service's OWN reader, so the version derived here is exactly the one the folder
    listing will report for the downloaded file.
    """
    try:
        _name, found = service.parse_filename(_url_filename(link))
    except Exception:
        log.exception("download_mod: the URL's file name could not be read")
        return ""
    return _text(found)


@action
async def download_mod(ctx: Any, *, node_name: str, folder: str, url: str, version: str = "",
                       package_name: str = "") -> ModResult:
    """Download ONE package into ONE package folder of ONE node — ``/mods download``.

    The download only puts a package in the folder; installing it stays a second, deliberate step.
    It runs on the NODE, because the folder is on that machine. A GitHub repository URL resolves one
    release asset: an answer offering SEVERAL is refused, since a version has to be named to pick one.
    ``version`` empty means the repository's latest; an existing file is the service's own refusal and
    is reported with its name.

    A PLAIN file URL is fetched as given, so the FILE NAME decides the version and the ``version``
    argument is not used for it: a file whose name declares no version is refused, because a name with
    no version is one the folder listing cannot offer afterwards. ``package_name`` (the repository
    case) and a repository's ``version`` are passed through.

    On success the result names the package entry the download left in the folder — the folder is
    listed back (a read, on the node) rather than the file name guessed, because a repository asset's
    name is the release's business and not something this action may compute.
    """
    service = _service()
    if service is None:
        return await _audited(ctx, _mod(False, SERVICE_MISSING, folder=str(folder)))
    folder_enum = _folder(folder)
    if folder_enum is None:
        return await _audited(ctx, _mod(False, UNKNOWN_FOLDER.format(folder=folder),
                                        folder=str(folder)))
    link = _text(url)
    if not link:
        return await _audited(ctx, _mod(False, "A download URL is required.", folder=folder))
    requested_version, requested_name = _text(version), _text(package_name)
    if requested_name and not is_leaf_name(requested_name):
        return await _audited(ctx, _mod(False, _unsafe_package(requested_name), folder=folder,
                                        url=link))
    if requested_version and not is_leaf_name(requested_version):
        return await _audited(ctx, _mod(False, _unsafe_version(requested_name or link,
                                                               requested_version),
                                        folder=folder, url=link))
    node, refusal = await _node(ctx, node_name)
    if refusal is not None:
        return await _audited(ctx, _mod(False, refusal.message, folder=folder))
    label = _label(node, node_name)
    want, wanted_name = requested_version, requested_name
    if not service.is_repo_url(link):
        # a plain file URL is fetched as given, so the FILE NAME is the only place its version can
        # come from: the dialog's preset is not a file name and would land beside the package.
        declared = _url_version(service, link)
        if not declared:
            return await _audited(ctx, _mod(
                False, f"'{link}' names no version: the file it points at is not named "
                       f"'<package>_v<version>.zip', so the package folder could not offer it "
                       f"afterwards; nothing was downloaded.",
                folder=folder, package=wanted_name, url=link))
        want = declared
    identity = dict(package=wanted_name, version=want, folder=folder, target=label)
    try:
        answer = await service.download_package(node, folder_enum, link, version=want or None,
                                                package_name=wanted_name or None)
    except FileExistsError as ex:
        return await _audited(ctx, _mod(False, f"The file already exists in folder '{folder}' on node "
                                              f"'{label}': {ex}", **identity))
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, _mod(False, f"The download into folder '{folder}' on node '{label}' "
                                              f"did not finish in time.", **identity))
    except Exception as ex:
        log.exception("download_mod: the download raised")
        return await _audited(ctx, _mod(False, f"The download from '{link}' into folder '{folder}' on "
                                              f"node '{label}' failed: {ex}", **identity))
    if not answer:
        return await _audited(ctx, _mod(False, f"The repository offers more than one asset for "
                                              f"'{wanted_name or link}', so nothing was downloaded: "
                                              f"name the version to pick one.",
                                        **identity, url=link))
    produced = await _newest_entry(service, node, folder_enum)
    if produced:
        identity = dict(package=str(produced.get("name") or wanted_name),
                        version=str(produced.get("version") or want), folder=folder, target=label)
        return await _audited(ctx, _mod(
            True, f"Downloaded '{identity['package']}' v{identity['version']} into folder '{folder}' "
                  f"on node '{label}'; install it to activate it.", **identity, url=link))
    return await _audited(ctx, _mod(True, f"The download into folder '{folder}' on node '{label}' "
                                         f"finished; the folder listing did not name the new entry.",
                                    **identity, url=link))


async def _newest_entry(service: Any, node: Any, folder_enum: Any) -> dict[str, Any] | None:
    """The newest package entry in *folder_enum* on *node*, or ``None`` when none can be named.

    The download answers a ``bool`` and a repository asset's file name is the release's, so the file a
    download produced is read back from the folder instead of reconstructed here. A listing that fails
    is *not* a failed download: the caller words the outcome without a name.
    """
    try:
        entries = await service.list_available_packages(node, folder_enum)
    except Exception:
        log.warning("download_mod: the folder could not be listed back after the download.",
                    exc_info=True)
        return None
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.get("mtime") or 0.0)
