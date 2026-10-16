"""Step 8 of the tour — 3rd-party apps: one row per extension that declares an installation path.

OFFER-ONLY. Nothing here downloads, installs, updates or starts anything: the row's switch means
"write this application's installation path into the node's ``extensions`` block", and nothing else.
The SRS extension's ``do_install`` (which fetches and runs an updater) is never called; neither is
any other extension's install/update path.

DETECTION MIRRORS THE EXTENSION'S OWN ``get_inst_path()``, because an :class:`core.extension.Extension`
cannot exist before a node does. Each :class:`AppSpec` carries the exact source the extension reads —
SRS: ``extensions/srs/extension.py`` reads ``Software\\DCS-SR-Standalone\\SRPathStandalone`` and falls
back to ``%ProgramFiles%\\DCS-SimpleRadio-Standalone``; Tacview: ``extensions/tacview/extension.py``
reads ``Software\\Valve\\Steam\\SteamPath`` + ``steamapps/common/Tacview`` and falls back to
``%ProgramFiles(x86)%\\Tacview``; LotAtC: ``extensions/lotatc/extension.py`` probes nothing and uses
``%ProgramFiles%\\LotAtC``. A registry key that is absent is not an error — the fallback path decides.

The rows are the extensions that declare a ``installation`` config key in their NODE schema
(``extensions/{srs,tacview,lotatc}/schemas/*_schema.yaml``), which is also exactly the set written to
``nodes.yaml -> extensions.<NAME>.installation``. Every probe is a read: a ``os.path.exists`` or a
registry read, never a network call and never an elevated subprocess.
"""

from __future__ import annotations

import logging
import os
import sys

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "AppSpec",
    "AppRow",
    "AppAnswers",
    "APPS",
    "default_path",
    "detect_app",
    "answers_from_mapping",
    "non_secret_document",
    "resolve_apps",
    "extensions_block",
]

log = logging.getLogger(__name__)

#: A registry value a Windows install writes its own path into: ``(hive, key, value)``.
REGISTRY_VALUE = tuple[int, str, str]


@dataclass(frozen=True)
class AppSpec:
    """One offered application: the node-scope key, the display name and how its path is found.

    ``key`` is the extension's class name — the key under ``extensions`` in ``nodes.yaml`` (and the
    extension's own ``name``), so it is never translated. ``registry`` is the Windows read the
    extension performs, if any; ``default_dirs`` are the directories it falls back to, in order.
    """

    key: str
    registry: REGISTRY_VALUE | None
    default_dirs: tuple[str, ...]
    #: a registry value whose directory the default paths are relative to (Steam)
    registry_base: str | None = None


#: ``HKEY_CURRENT_USER`` — the registry root the extensions read (``winreg.HKEY_CURRENT_USER``).
HKEY_CURRENT_USER = 0x80000001

APPS: tuple[AppSpec, ...] = (
    AppSpec(key="SRS",
            registry=(HKEY_CURRENT_USER, r"Software\DCS-SR-Standalone", "SRPathStandalone"),
            default_dirs=("%ProgramFiles%\\DCS-SimpleRadio-Standalone",)),
    AppSpec(key="Tacview",
            registry=(HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            default_dirs=("%ProgramFiles(x86)%\\Tacview", "%ProgramFiles%\\Tacview"),
            registry_base="steamapps\\common\\Tacview"),
    AppSpec(key="LotAtC",
            registry=None,
            default_dirs=("%ProgramFiles%\\LotAtC",)),
)


# ---------------------------------------------------------------------------------- the detection

def _registry_path(key: str, value: str) -> str | None:
    """The value a Windows install wrote into ``HKCU\\<key>``, or ``None``.

    A registry that is absent, refuses, or holds nothing returns ``None`` — the caller falls through
    to the default directory, exactly as the extension does. On any other platform this is ``None``
    without touching the registry.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0) as handle:
            return str(winreg.QueryValueEx(handle, value)[0])
    except Exception as ex:  # noqa: BLE001 - an unreadable registry is not a failure
        log.debug("Setup tour: the registry value %s\\%s could not be read (%s).", key, value, ex)
        return None


def default_path(spec: AppSpec) -> str | None:
    """The first of *spec*'s default directories (or registry-derived paths) that exists."""
    candidates: list[str] = []
    if spec.registry is not None:
        _hive, key, value = spec.registry
        base = _registry_path(key, value)
        if base and spec.registry_base:
            candidates.append(os.path.join(base, spec.registry_base))
        elif base:
            candidates.append(base)
    candidates.extend(spec.default_dirs)
    for candidate in candidates:
        path = os.path.expandvars(candidate)
        if os.path.exists(path):
            return path
    return None


def detect_app(spec: AppSpec) -> str | None:
    """The application's installation directory, or ``None`` when nothing was found."""
    try:
        return default_path(spec)
    except Exception as ex:  # noqa: BLE001 - a probe never breaks a render
        log.warning("Setup tour: the %s detection failed (%s); reporting not found.", spec.key, ex)
        return None


# ------------------------------------------------------------------------------------ the answers

@dataclass
class AppRow:
    """One offered application's row.

    ``configured`` is the operator's switch ("write this extension"); ``installation`` is the path
    the row confirms — typed, or the detected one when the operator leaves the field alone.
    """

    key: str
    configured: bool = False
    installation: str = ""


@dataclass
class AppAnswers:
    """Step 8's answers: one row per offered application, in :data:`APPS` order."""

    rows: list[AppRow] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rows is None:
            self.rows = [AppRow(key=spec.key) for spec in APPS]

    def row_for(self, key: str) -> AppRow:
        """The row for *key*, or a fresh one (the row set is fixed by :data:`APPS`)."""
        for row in self.rows:
            if row.key == key:
                return row
        return AppRow(key=key)


def _as_str(value: Any) -> str:
    return "" if value in (None, False) else str(value)


def answers_from_mapping(data: Mapping | None) -> AppAnswers:
    """Build the answers from the draft's ``apps`` block (missing keys get an off, empty row)."""
    data = data if isinstance(data, Mapping) else {}
    stored = data.get("rows")
    stored = stored if isinstance(stored, Mapping) else {}
    rows: list[AppRow] = []
    for spec in APPS:
        previous = stored.get(spec.key)
        previous = previous if isinstance(previous, Mapping) else {}
        rows.append(AppRow(key=spec.key,
                           configured=bool(previous.get("configured", False)),
                           installation=_as_str(previous.get("installation"))))
    return AppAnswers(rows=rows)


def non_secret_document(answers: AppAnswers) -> dict:
    """The draft-writable part of the answers (this step holds no secret)."""
    return {"rows": {row.key: {"configured": row.configured, "installation": row.installation}
                     for row in answers.rows}}


def resolve_apps(values: Mapping, answers: AppAnswers, *, validate: bool
                 ) -> tuple[AppAnswers | None, tuple[str, dict] | None]:
    """Turn the submitted row values into step 8's answers.

    A switched-on row must carry an installation path that exists: the path the operator typed, or
    the one the row detected. Off rows are recorded as-is so a re-render (``validate`` false) never
    reports a failure for a switch.
    """
    rows: list[AppRow] = []
    for index, spec in enumerate(APPS):
        rows.append(AppRow(key=spec.key,
                           configured=values.get(f"app_on_{index}") is not None,
                           installation=str(values.get(f"app_path_{index}") or "").strip()))
    resolved = AppAnswers(rows=rows)
    if not validate:
        return resolved, None
    for index, row in enumerate(rows):
        if not row.configured:
            continue
        path = row.installation or (detect_app(APPS[index]) or "")
        if not path:
            return None, ("app_path_required", {"app": row.key})
        if not os.path.exists(os.path.expandvars(path)):
            return None, ("app_path_missing", {"app": row.key, "path": row.installation or path})
        row.installation = path
    return resolved, None


def extensions_block(answers: AppAnswers) -> dict[str, dict]:
    """The node-scope ``extensions`` block: one ``<NAME>: {installation: <path>}`` per ON row.

    This is the shape ``install.py`` writes for SRS and the shape the node schemas validate for
    Tacview and LotAtC; an off row is absent, so the extension stays unconfigured.
    """
    return {row.key: {"installation": row.installation}
            for row in answers.rows if row.configured and row.installation}
