"""The ModManager pages' read model: the installed rows and the packages a dialog can pick.

PURE — no HTTP, no await, no node call, like every other module in this package. Two inputs, two
shapes:

* the INSTALLED rows are the ``mm_packages`` bookkeeping the ``list_installed_mods`` action reads
  (``{"package", "version", "installed", "folder"}`` per row). They are shaped here for the table and
  never re-queried, so a page and the action's own message cannot disagree about what is installed;
* the AVAILABLE entries are the package folder's own listing (``{"name", "version", "kind", "size",
  "mtime"}`` per entry, the service's shape). They become ONE option per (package, version) pair —
  the shape an install dialog's pick renders;
* the VERSION FACTS are one node read per table (``{"versions", "latest", "repo", "downloaded"}`` per
  installed package). They decide the two per-row verdicts this file owns: whether the package has
  another version at all (:func:`other_version`, the change door) and whether a newer one is known
  (:func:`update_chip`, the row's chip). Neither verdict is invented here — both read the node's
  answer, and an UNKNOWN answer keeps the door and draws no chip.

WHY THE PICK IS SORTED HERE. The folder's order is the filesystem's, which is neither stable across
nodes nor meaningful to a reader. The dialog offers the NEWEST version first and preselects it, which
is the answer an operator opening the dialog almost always wants. "Newest" is decided by
:func:`version_key` — a numeric comparison of the version's own chunks, so ``2.10.1`` sorts after
``2.9.0`` and a suffix such as ``1.8.2-beta`` still lands where its numbers say.

THE STAMP IS THE CONSOLE'S ONE FORMAT. Every timestamp a page renders goes through
``bans.STAMP_FORMAT``, and these rows are no exception: the ``mm_packages`` stamp and a folder
entry's modification time are both stated the same way, so two timestamps on one page are comparable
without the reader checking which format each one used. A folder's modification time is converted
from its epoch value to UTC, which is what the format's own ``UTC`` word claims.

THE TWO FOLDER WORDS ARE COPIED, NOT IMPORTED. ``RootFolder`` and ``SavedGames`` are the service's
``Folder`` values, but the console may not import a plugin module (its copy would break an
installation that ships the web UI without the plugin's dependencies). The two literals live here
and a test pins them equal to the plugin's own, which is the same arrangement the DGSA marker uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .access import text
from .bans import STAMP_FORMAT

__all__ = [
    "ROOT_FOLDER", "SAVED_GAMES", "MOD_FOLDERS", "OPTION_SEPARATOR", "MODS_TABLE",
    "INSTALLED_STATE", "PICK_LABEL", "NO_ROOT_MODS",
    "ModRow", "version_key", "format_size", "installed_stamp", "added_stamp",
    "UPDATE_CHIP", "UPDATE_IN_FOLDER", "UPDATE_NOT_DOWNLOADED",
    "installed_rows", "other_version", "update_chip", "with_updates",
    "package_options", "option_value", "split_option",
]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED (see ``readmodels.bans``)."""
    return message


#: the two package folders of ``mm_packages`` — the service's ``Folder`` values, copied (see above).
ROOT_FOLDER = "RootFolder"
SAVED_GAMES = "SavedGames"
MOD_FOLDERS = (ROOT_FOLDER, SAVED_GAMES)

#: the PAGE-ONLY table name the installed rows render through — the console's ONE table component
#: (``templates/_table.html``) dispatches on it, and ``readmodels.views.PAGE_COLUMNS`` holds its
#: columns, so the mods card is the same component every other table is.
MODS_TABLE = "mods"

#: the separator inside ONE pick option's value: ``"<package>/<version>"``. A package NAME is parsed
#: from a file name on the node, so it can carry no separator of its own, and the split is therefore
#: unambiguous (``split_option`` splits on the LAST one regardless).
OPTION_SEPARATOR = "/"

#: the state a row's chip shows. The bookkeeping states that the package IS installed on this target;
#: whether a newer copy is sitting in the folder is a NODE read, so no row claims it.
INSTALLED_STATE = _("installed")

#: ONE pick option's label: the package, its version, the size of the package and when it arrived.
PICK_LABEL = _("{package} — v{version} · {size} · {added}")

#: the sentence that replaces the root-mods card's list on a server whose node carries none.
NO_ROOT_MODS = _("No root mods are installed on {node}.")

#: ONE row's update chip: the newer version it names, beside the installed one.
UPDATE_CHIP = _("update {version}")

#: the sentence the chip carries when the newer version already sits in the package folder.
UPDATE_IN_FOLDER = _("Version {version} of {package} is available in the package folder; the installed version is {installed}.")

#: the sentence the chip carries when the newer version is a repository release that is not in the
#: folder yet — the one state that cannot be installed before it is downloaded.
UPDATE_NOT_DOWNLOADED = _("Version {version} of {package} is available in its repository and has to be downloaded before it can be installed.")


@dataclass(frozen=True, slots=True)
class ModRow:
    """One installed package, as a table row renders it."""
    package: str
    version: str
    installed: str
    folder: str
    #: the row's state word — the bookkeeping states that the package IS installed on this target
    state: str = INSTALLED_STATE
    #: the row's update chip, when a newer version of the package is known; ``None`` otherwise
    update: dict | None = None

    @property
    def key(self) -> str:
        """The row's identity inside one table: the package and the version it is installed at."""
        return f"{self.package}{OPTION_SEPARATOR}{self.version}"


def _field(entry: Any, name: str) -> Any:
    """One field of *entry*, whether it is a mapping (the action's dicts) or a duck-typed object."""
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


# ---------------------------------------------------------------------------------- the stamps
#
# ONE format per page (``STAMP_FORMAT``, the bans table's own), and ONE conversion: a naive value is
# already the wall clock the bookkeeping stored; an epoch value is converted to UTC. Nothing here
# decides a timezone for a value that states one.

def installed_stamp(value: Any) -> str:
    """The install stamp of one row: the console's stamp format, or the value's own text."""
    if isinstance(value, datetime):
        return value.strftime(STAMP_FORMAT)
    return text(value)


def added_stamp(value: Any) -> str:
    """A folder entry's modification time as a rendered UTC stamp, or ``''`` when unreadable."""
    try:
        stamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return stamp.strftime(STAMP_FORMAT)


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(size: Any) -> str:
    """A byte count as the pick's own short size (``24.8 MB``, ``812 B``)."""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value < 1024:
        return f"{int(value)} B"
    for unit in _SIZE_UNITS[1:]:
        value /= 1024.0
        if value < 1024 or unit == _SIZE_UNITS[-1]:
            return f"{value:.1f} {unit}"
    return f"{int(value)} B"       # pragma: no cover - the loop always returns


# --------------------------------------------------------------------------------- the versions
#
# A version is compared CHUNK BY CHUNK: a run of digits by its value, everything else by its fold, so
# ``2.10.1`` is newer than ``2.9.0`` and two identifiers are ordered the way a reader expects. Every
# chunk is a triple with the same types, so the tuples are directly comparable.

_CHUNK = re.compile(r"^(\d+)(.*)$")
_SPLIT = re.compile(r"[.\-_+ ]+")


def version_key(version: Any) -> tuple:
    """A comparable key for *version* — numeric chunks by value, the rest by their folded text."""
    parts: list[tuple[int, int, str]] = []
    for chunk in _SPLIT.split(text(version)):
        match = _CHUNK.match(chunk)
        if match:
            parts.append((0, int(match.group(1)), match.group(2).casefold()))
        else:
            parts.append((1, 0, chunk.casefold()))
    return tuple(parts)


# ---------------------------------------------------------------------------------- the rows

def installed_rows(packages: Sequence[Any] | None, folder: str | None = None) -> list[ModRow]:
    """The installed rows of *folder*, ordered by package and version, as the table renders them.

    The server tab reads BOTH halves in ONE action call, so it splits them here by this folder.

    *folder* ``None`` keeps every row of the answer. Rows are never re-ordered by the caller.
    """
    rows: list[ModRow] = []
    for entry in packages or ():
        entry_folder = text(_field(entry, "folder"))
        if folder is not None and entry_folder != folder:
            continue
        package = text(_field(entry, "package"))
        if not package:
            continue
        rows.append(ModRow(package=package,
                           version=text(_field(entry, "version")),
                           installed=installed_stamp(_field(entry, "installed")),
                           folder=entry_folder))
    rows.sort(key=lambda row: (row.package.casefold(), version_key(row.version)))
    return rows


# --------------------------------------------------------------------------------- the update
#
# A row's update chip is INFORMATION, never a control: it names the newer version and, in its
# tooltip, whether that version is already in the package folder or still has to be downloaded. The
# two verdicts behind it — which versions exist and which one is newest — are the node's own answer
# (``list_package_versions``), so this file decides nothing about availability.

def _answer(availability: Any, package: str) -> Mapping | None:
    """One package's entry inside a version read, or ``None`` when the read did not name it."""
    if not isinstance(availability, Mapping):
        return None
    entry = availability.get(package)
    return entry if isinstance(entry, Mapping) else None


def other_version(availability: Any, package: str, installed: str) -> bool:
    """Whether *package* has any version other than *installed* — the only reason to change one.

    The list is the node's answer: the folder's versions merged with a declared repository's
    releases. Two distinct versions qualify, and so does a single one that differs from the installed
    version. An UNKNOWN answer — no read at all, or a package the read did not name — qualifies too:
    the change door is a control a page draws without this read, and a node that cannot be asked must
    not take it away.
    """
    entry = _answer(availability, package)
    if entry is None:
        return True
    versions = {text(version) for version in (entry.get("versions") or ()) if text(version)}
    if len(versions) >= 2:
        return True
    return bool(versions - {text(installed)})


def update_chip(availability: Any, package: str, installed: str) -> dict | None:
    """The chip one row shows when *package* has a version newer than *installed*, else ``None``.

    The chip is a record the template renders with ``say``: ``version`` (the newer version it names),
    ``text`` (its visible "update {version}" label) and ``title`` (the sentence that says whether the
    newer version is in the folder or still has to be downloaded, which is decided by the package's
    declared repository and by whether that version is a file in the folder).
    """
    entry = _answer(availability, package)
    if entry is None:
        return None
    latest = text(entry.get("latest"))
    if not latest or version_key(latest.strip("v")) <= version_key(text(installed).strip("v")):
        return None
    not_downloaded = bool(text(entry.get("repo"))) and not entry.get("downloaded")
    return {
        "version": latest,
        "text": {"msgid": UPDATE_CHIP, "params": {"version": latest}},
        "title": {"msgid": UPDATE_NOT_DOWNLOADED if not_downloaded else UPDATE_IN_FOLDER,
                  "params": {"package": package, "version": latest,
                             "installed": text(installed)}},
    }


def with_updates(rows: Sequence[ModRow], availability: Any) -> list[ModRow]:
    """*rows* as they render, each carrying its update chip when a newer version is known."""
    return [replace(row, update=update_chip(availability, row.package, row.version))
            for row in rows]


# --------------------------------------------------------------------------------- the pick

def option_value(package: Any, version: Any) -> str:
    """The value ONE pick option posts: the package and the version, separated by ``OPTION_SEPARATOR``."""
    return f"{text(package)}{OPTION_SEPARATOR}{text(version)}"


def split_option(value: Any) -> tuple[str, str] | None:
    """``(package, version)`` a posted option names, or ``None`` when it names neither.

    The split is on the LAST separator: a package name carries none (it is a file name), so the
    right-hand half is always the version.
    """
    raw = text(value)
    if not raw or OPTION_SEPARATOR not in raw:
        return None
    package, _, version = raw.rpartition(OPTION_SEPARATOR)
    package, version = package.strip(), version.strip()
    if not package or not version:
        return None
    return package, version


def package_options(entries: Sequence[Any] | None, *, package: str | None = None) -> list[dict]:
    """The pick's options: one per (package, version) pair, NEWEST FIRST, the first one preselected.

    *package* restricts the pick to ONE package — the shape a version change needs, where the package
    is already decided and only its versions are offered. Each option is a record the template renders
    with ``say``: ``value`` (what the form posts), ``label`` (a translated sentence record) and
    ``selected`` (exactly one option carries it, or none when the pick is empty).
    """
    wanted = text(package)
    found: list[tuple[str, str, Any]] = []
    for entry in entries or ():
        name = text(_field(entry, "name"))
        version = text(_field(entry, "version"))
        if not name or not version:
            continue
        if wanted and name != wanted:
            continue
        found.append((name, version, entry))
    found.sort(key=lambda item: (version_key(item[1]), _mtime(item[2])), reverse=True)
    options: list[dict] = []
    for index, (name, version, entry) in enumerate(found):
        options.append({
            "value": option_value(name, version),
            "label": {"msgid": PICK_LABEL,
                      "params": {"package": name, "version": version,
                                 "size": format_size(_field(entry, "size")),
                                 "added": added_stamp(_field(entry, "mtime"))}},
            "selected": index == 0,
        })
    return options


def _mtime(entry: Any) -> float:
    """One entry's modification time, or ``0.0`` — the tie-break inside one version."""
    try:
        return float(_field(entry, "mtime") or 0.0)
    except (TypeError, ValueError):
        return 0.0
