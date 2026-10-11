"""The ONE config-file write primitive: round-trip, validate, atomic, `.bak`, restart-honest.

Everything in the admin console that writes a YAML configuration file goes through the two functions
here — :func:`save_config_file` (a whole document the caller already holds) and
:func:`patch_config_file` (load, mutate in place, save). The bot's own callers use the same two, so a
later switch of the six sites that currently ``yaml.load`` -> ``yaml.dump`` (which silently destroys
the operator's comments) is a substitution, not a rewrite.

Why a primitive at all — the four properties every config write must have, and each one here:

1. **The operator's file survives the round trip.** The files are hand-maintained: comments, blank
   lines, key order, quoting, block scalars. A ``yaml.safe_load`` -> ``yaml.dump`` round trip throws
   all of that away. This primitive reads and writes with **ruamel.yaml round-trip** (``typ='rt'``,
   the project's existing tool — ``core/utils/validators.py``, ``plugins/admin/commands.py``) in the
   project's canonical style (``mapping=2, sequence=4, offset=2``, ``width`` wide enough to never
   re-wrap a line, ``preserve_quotes``). A document read and written **unchanged** is not rewritten
   at all — see § the no-op rule below — so merely opening and saving a file reformats nothing.

   The round trip is faithful for a file already in the canonical style (``presets_tournament.yaml``
   is one: it comes back byte-for-byte). A hand file written in some OTHER style is normalised by
   ruamel the moment a real change is made — indentation is the usual difference, and a hand-wrapped
   plain scalar is re-folded. That is the honest limit of the tool; the alternative (a line patch) the
   architect rejected as too fragile. The no-op rule below is what keeps the common case silent.

2. **A malformed edit never lands.** The candidate is validated with the bot's OWN validation —
   :func:`core.utils.validators.validate`, pykwalify against the file's shipped schema — before
   anything on disk changes. The caller passes the schema files the bot itself uses for that file
   (``schemas/main_schema.yaml``; for ``nodes.yaml`` the node schema PLUS every
   ``extensions/**/*_schema.yaml``; …). On a refusal the target file is **untouched** and the reason
   is in the raised :class:`ConfigValidationError`.

3. **No reader ever sees a half-written file.** The candidate is written to a temp file **in the same
   directory** (same filesystem, so the swap is atomic), flushed and fsync'd, then moved over the
   target with :func:`os.replace`. The previous generation is kept beside it as ``<name>.bak`` — that
   is the way back, and the result names it. The target's **mode and ownership survive** the swap:
   ``os.replace`` keeps the moved file's own identity, so the temp file is created already wearing the
   target's mode (and uid/gid where a ``chown`` is permitted), and the ``.bak`` keeps it too. A
   brand-new file gets the project's ordinary new-file mode (``0o666 & ~umask``), never mkstemp's 0600.

4. **The apply story is honest.** Configuration is read ONCE at startup (``Node.__init__`` /
   ``NodeData`` / ``Server.locals``), so a plain file write changes nothing in memory: the result
   carries :data:`APPLIES_ON_RESTART` and the console must say so. A caller whose write goes through
   a bot method that DOES refresh the in-memory copy overrides that field with :data:`APPLIES_NOW` —
   this primitive never guesses.

The no-op rule
--------------

Before writing, the candidate's **serialised form** — the bytes that would actually be written — is
compared with the serialised form of a fresh parse of what is on disk right now. If they are equal —
the caller loaded and changed nothing, or set a value to what it already was — **nothing is written**:
the file is left exactly as it is, ``changed`` is ``False`` and no ``.bak`` is made. Comparing the
serialised bytes, not the documents, is what makes this correct in both directions: a comment-only
edit *is* a real change (ruamel document equality ignores comments, so a purely semantic comparison
would silently drop it), while a genuinely unchanged hand-formatted file still writes nothing — such a
file re-serialises to the same canonical text on both sides, so the operator's bytes are untouched.
This is also why a second read of the same file through this primitive is not a rewrite.

Thread-safety
-------------

A process-wide lock per resolved path serialises read-modify-write, because the bot's own writers run
on other threads. The optional ``expect_revision`` guard is the second half: a caller that rendered a
form from a known revision refuses to write if the file has moved since, so two editors cannot
silently clobber each other (last-writer-wins is never the behaviour).

How it plugs into the console's write pipeline
---------------------------------------------

A console config write is declared ONCE in ``services/webservice/pages/actions.py`` (the action/token/
confirm/notice machinery already there) and its handler calls one of the two functions here — it does
NOT open, parse or write a file itself. That keeps the "one declaration" rule (the route table, the
capability map, the control and the dialog are all readings of the same record) and means no second
write path exists: the primitive is the single place a config file is changed, and the honest
post-write notice ("Applies on the next bot restart.") reads from :class:`SaveResult.applies`.

What this module does NOT do
---------------------------

* It does not reload, restart, or touch the bot's in-memory configuration — a plain write is a file
  write, and the caller says honestly what applies when.
* It does not validate the schema's own correctness, and it does not decide which schema belongs to
  which file — the caller names the schema files, exactly as the bot does.
* It does not roll back automatically; the ``.bak`` is the manual way back the caller names.
* A real bot restart applying the change is not exercised by the tests (see ``tests/test_configwrite.py``).
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import MarkedYAMLError
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError

log = logging.getLogger(__name__)

__all__ = [
    "APPLIES_ON_RESTART",
    "APPLIES_NOW",
    "ConfigWriteError",
    "ConfigParseError",
    "ConfigValidationError",
    "StaleConfigRevision",
    "SaveResult",
    "yaml_roundtrip",
    "read_config_text",
    "config_revision",
    "load_config_text",
    "load_config_file",
    "dump_config",
    "validate_against_schema",
    "save_config_file",
    "patch_config_file",
]

#: The honest apply story for a plain file write: the file is on disk, but the bot read its
#: configuration once at startup, so the change takes effect on the **next bot restart**. The console
#: renders this — it must never pretend the edit is live.
APPLIES_ON_RESTART = "restart"

#: The apply story a caller uses only when its write went through a bot method that genuinely
#: refreshed the in-memory copy (extension enable/disable, plugin install/uninstall, the presets
#: apply). This primitive never sets it on its own; the caller states the fact.
APPLIES_NOW = "now"


class ConfigWriteError(Exception):
    """A config write this primitive refused. ``str()`` is the operator-facing sentence.

    Carries the target ``path`` so a caller can name it in a notice/audit without re-deriving it.
    """

    def __init__(self, message: str, *, path: str | os.PathLike | None = None):
        super().__init__(message)
        self.path = Path(path) if path is not None else None


class ConfigParseError(ConfigWriteError):
    """The file (or the candidate) is not readable YAML. Nothing was written.

    ``line``/``column`` (1-based) locate the syntax fault when the round-trip loader reported a
    mark, so a caller (the console's Expert Mode editor) can mark the offending position; both are
    ``None`` when no mark was available. They are extra structure only — the save path reads the
    message, exactly as before.
    """

    def __init__(self, message: str, *, path: str | os.PathLike | None = None,
                 line: int | None = None, column: int | None = None):
        super().__init__(message, path=path)
        self.line = line
        self.column = column


class ConfigValidationError(ConfigWriteError):
    """The candidate failed its schema. Nothing was written; the file on disk is untouched."""

    def __init__(self, message: str, *, path: str | os.PathLike, schemas: list[str],
                 errors: str = "", error_path: str = ""):
        super().__init__(message, path=path)
        self.schemas = list(schemas)
        self.errors = errors
        #: the pykwalify PATH of the failing location (``/database/pool_min``), when the validator
        #: reported one — the console's Expert Mode editor maps it to a line. ``""`` when unknown.
        self.error_path = error_path


class StaleConfigRevision(ConfigWriteError):
    """The file changed on disk since the caller read it. Nothing was written."""

    def __init__(self, message: str, *, path: str | os.PathLike, expected: str, current: str):
        super().__init__(message, path=path)
        self.expected = expected
        self.current = current


@dataclass(frozen=True)
class SaveResult:
    """What a successful :func:`save_config_file` / :func:`patch_config_file` actually did.

    ``changed`` is ``False`` for a no-op (nothing was written, the file is byte-identical, no
    ``.bak``); ``backup`` is the ``.bak`` path when one was made, ``None`` otherwise (a brand-new
    file, or a no-op). ``revision`` is the sha256 of the file AFTER the call — feed it back as
    ``expect_revision`` on the next write from the same rendered view.
    """

    path: Path
    changed: bool
    revision: str
    backup: Path | None = None
    #: The honest apply story. Defaults to :data:`APPLIES_ON_RESTART`; a caller whose write refreshed
    #: memory passes :data:`APPLIES_NOW`.
    applies: str = field(default=APPLIES_ON_RESTART)

    @property
    def applies_on_restart(self) -> bool:
        return self.applies == APPLIES_ON_RESTART


# --------------------------------------------------------------- the round-trip YAML, the locks

def yaml_roundtrip() -> YAML:
    """A fresh ruamel round-trip :class:`YAML` in the project's canonical style.

    A NEW instance per call on purpose: ``YAML`` is not documented thread-safe, and this primitive is
    reached from the bot's own threads. ``typ='rt'`` (the default here) is the SAFE round-trip loader
    — it does not construct arbitrary Python objects, unlike ``typ='unsafe'``.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    # Wide enough that a line is never re-wrapped: the operator's line breaks are theirs, and a
    # re-wrap is a diff they did not ask for.
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


#: process-wide locks, one per resolved path — the bot's writers run on other threads, so a
#: read-modify-write from the console and one from the bot must not interleave.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str | os.PathLike) -> threading.Lock:
    key = os.path.realpath(str(path)).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


# --------------------------------------------------------------- reading, hashing, dumping

def read_config_text(path: str | os.PathLike) -> str:
    """The file's text, decoded UTF-8. Raises :class:`ConfigWriteError` if it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigWriteError(f"Configuration file does not exist: {path}", path=path)
    except OSError as ex:
        raise ConfigWriteError(f"Configuration file cannot be read ({ex}): {path}", path=path)


def _read_bytes(path: str | os.PathLike) -> bytes | None:
    """The file's raw bytes, or ``None`` when it does not exist. Other errors are raised."""
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _revision_of(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def config_revision(path: str | os.PathLike) -> str:
    """The sha256 of the file's bytes, or ``""`` when it does not exist.

    This is the token the console renders into a form and passes back as ``expect_revision``: it
    identifies the exact generation a save was prepared from.
    """
    return _revision_of(_read_bytes(path))


def load_config_text(text: str, *, path: str | os.PathLike | None = None) -> Any:
    """Parse *text* with the round-trip loader. Raises :class:`ConfigParseError` on bad YAML.

    An empty document parses to ``None`` — callers that need a mapping handle that themselves.
    """
    try:
        return yaml_roundtrip().load(text)
    except (MarkedYAMLError, ScannerError, ParserError) as ex:
        mark = getattr(ex, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        column = mark.column + 1 if mark is not None else None
        where = f"line {line}, column {column}" if mark is not None else "unknown"
        raise ConfigParseError(
            f"Configuration is not valid YAML ({where}): {path or ''}".strip(), path=path,
            line=line, column=column)


def load_config_file(path: str | os.PathLike) -> Any | None:
    """Load the file with the round-trip loader, or ``None`` when it does not exist."""
    data = _read_bytes(path)
    if data is None:
        return None
    return load_config_text(data.decode("utf-8"), path=path)


def dump_config(data: Any) -> str:
    """Serialise a document with the round-trip loader, in the canonical style."""
    yaml = yaml_roundtrip()
    stream = io.StringIO()
    try:
        yaml.dump(data, stream)
    except Exception as ex:  # ruamel raises assorted representer errors for unrepresentable objects
        raise ConfigWriteError(f"Configuration could not be serialised: {ex}")
    return stream.getvalue()


# --------------------------------------------------------------- the write identity, the atomic write

def _target_identity(path: Path) -> tuple[int, int, int] | None:
    """The existing target's ``(mode, uid, gid)``, or ``None`` when it does not exist.

    ``mode`` carries the permission bits (and any setuid/setgid/sticky bit). A config file's mode and
    ownership are part of the server's contract — another service account or operator may read it —
    so the file this primitive promotes has to carry them, not :func:`tempfile.mkstemp`'s fixed 0600.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_mode & 0o7777, st.st_uid, st.st_gid


def _new_file_mode() -> int:
    """The mode a brand-new config file gets: the project's convention, an ordinary write's.

    ``mkstemp`` hard-codes 0600, which is never what an operator's config should end up as. The bot
    creates its config files with ordinary writes (``0644``/``0664`` on disk), so a new file here gets
    ``0o666`` masked by the process umask — 0644 under a typical 0022 umask, 0664 under the 0002 umask
    this project uses.
    """
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _apply_identity(path: str | os.PathLike, mode: int | None,
                    owner: tuple[int, int] | None) -> None:
    """Give *path* the mode and ownership of the file it is about to replace.

    The mode is always applied — ``chmod`` exists on every platform the bot runs on (WSL and Windows),
    and we own the temp file, so it cannot fail on permissions.

    The ownership fix-up is OPTIONAL and capability-guarded. ``os.chown`` is a **POSIX-only** call: it
    is absent on Windows, so touching it there raises :class:`AttributeError` (not :class:`OSError`) —
    which a ``suppress(OSError)`` around the call does NOT catch. The whole save therefore crashed on
    Windows. Ownership is a best-effort nicety (only root can hand a file to another user, and a
    non-root writer keeps its own uid/gid — the only thing it can do), so the correct behaviour is to
    look the capability up first and **skip the fix-up entirely when it is unavailable**, never to
    assume it exists. The call is additionally guarded against an :class:`AttributeError` so a platform
    that exposes a broken shim degrades the same way.
    """
    if mode is not None:
        os.chmod(path, mode)
    if owner is None:
        return
    chown = getattr(os, "chown", None)
    if chown is None:
        return
    with suppress(OSError, AttributeError):
        chown(path, owner[0], owner[1])


def _write_temp(path: Path, text: str, *, mode: int | None = None,
                owner: tuple[int, int] | None = None) -> str:
    """Write *text* to a sibling temp file and return its name; the fd is closed and fsync'd.

    The temp lives in the SAME directory (same filesystem, so a later :func:`os.replace` is atomic).
    Its suffix MUST be ``.yaml``: the bot's validator (pykwalify) picks its loader from the file
    extension, and it has to read the candidate as YAML. *mode*/*owner* are applied to the temp file
    BEFORE it is returned, because ``os.replace`` keeps the MOVED file's own identity — so the temp
    must already wear the target's mode and ownership for the swap to preserve them.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".",
                                    suffix=".tmp.yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _apply_identity(tmp_name, mode, owner)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise
    return tmp_name


def _atomic_write(path: Path, text: str, *, mode: int | None = None,
                  owner: tuple[int, int] | None = None) -> None:
    """Write *text* to *path* atomically: a sibling temp file, fsync, then :func:`os.replace`.

    The target is only ever the old file or the new one — never a partial. On any failure the temp
    file is removed and the target is left as it was. *mode*/*owner*, when given, become the written
    file's identity (used to keep the ``.bak`` in the original config's mode, not 0600).
    """
    tmp_name = _write_temp(path, text, mode=mode, owner=owner)
    try:
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise


def _validate_candidate(candidate: Path, schemas: list[str], target: Path) -> None:
    """Validate a candidate against the bot's own schemas, using the temp-file write.

    The candidate is validated as a FILE through :func:`core.utils.validators.validate` — the bot's
    shipped validator (pykwalify + the file's schema files + the ``core/utils/validators.py``
    extension), not a second implementation. Nothing is written to the target on a failure; the
    candidate temp file is removed by the caller.
    """
    # Imported lazily: importing THIS MODULE must not, by itself, drag the bot's whole ``core``
    # package (Discord, psycopg, …) in. The deferral is only for module import, though — the first
    # VALIDATION CALL does load it all: ``core.utils.validators`` is reached through ``core/__init__.py``
    # and ``core/utils/__init__.py``, which star-import discord and the psycopg stack. That is fine for
    # the console (which imports core anyway); the point is not that validation is dependency-light.
    from core.utils.validators import validate
    from pykwalify.errors import PyKwalifyException

    try:
        validate(str(candidate), schemas, raise_exception=True)
    except PyKwalifyException as ex:
        detail = getattr(ex, "msg", None) or str(ex)
        # The temp file's name is a detail of the write; name the REAL file in the refusal.
        detail = detail.replace(str(candidate), target.name)
        raise ConfigValidationError(
            f"{target.name} was refused: it is not valid against {', '.join(schemas)}.",
            path=target, schemas=schemas, errors=detail,
            error_path=_error_path(ex, detail))


#: The two ways pykwalify embeds the offending location in an error's message: a wrong VALUE's path
#: (``Value path: '/plugins'``) and the path a rule failure names (``Path: '/'``). The value path is
#: tried first — it is the more precise of the two, and both can appear in one record.
_ERROR_PATH_PATTERNS = (re.compile(r"Value path: '([^']*)'"), re.compile(r"Path: '([^']*)'"))


def _error_path(ex, detail: str) -> str:
    """The pykwalify path of a validation fault, read from the exception or its message.

    pykwalify's ``SchemaError.path`` is often the ROOT (``/``) while the offending location rides in
    the message as ``Value path: '/plugins'``; this reads the message (it is where the location really
    is) and falls back to the exception's own ``path`` attribute. ``""`` when neither names one.
    """
    for pattern in _ERROR_PATH_PATTERNS:
        found = pattern.search(detail)
        if found:
            return found.group(1)
    return getattr(ex, "path", "") or ""


def validate_against_schema(text: str, schemas: list[str], *,
                            path: str | os.PathLike | None = None) -> None:
    """Schema-validate *text* EXACTLY as a save would, writing nothing to any config file.

    The console's Expert Mode editor checks every keystroke through THIS function, so the editor and
    the save can never disagree: it writes the candidate to a throw-away ``.yaml`` in the system temp
    directory (the extension is what tells pykwalify's loader to read it as YAML) and hands it to the
    bot's OWN validator — :func:`core.utils.validators.validate`, the very call the write path makes
    (``_validate_candidate``) — with the SAME schema files the caller passes to a save. Nothing on
    disk is touched: the candidate temp file is removed on the way out, success or failure.

    Raises :class:`ConfigValidationError` (carrying ``error_path``, the pykwalify path of the fault)
    when the candidate fails its schema; returns ``None`` when it is valid.
    """
    if not schemas:
        return
    fd, name = tempfile.mkstemp(suffix=".tmp.yaml")
    target = Path(path) if path is not None else Path("config.yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        _validate_candidate(Path(name), list(schemas), target)
    finally:
        with suppress(OSError):
            os.remove(name)


# --------------------------------------------------------------- the two public writes

def _ensure_target_directory(path: Path) -> None:
    if not path.parent.is_dir():
        raise ConfigWriteError(
            f"Cannot write {path.name}: directory does not exist: {path.parent}", path=path)


def _save_locked(path: Path, data: Any, *, schemas: list[str] | None, backup: bool,
                 current: bytes | None, expect_revision: str | None,
                 applies: str) -> SaveResult:
    """The body of a write, run under the per-file lock. *current* is the file's bytes just read."""
    current_revision = _revision_of(current)

    if expect_revision is not None and expect_revision != current_revision:
        raise StaleConfigRevision(
            f"{path.name} changed on disk since this form was built — reload before saving.",
            path=path, expected=expect_revision, current=current_revision)

    # The no-op rule: nothing to do when the bytes we WOULD write are what a re-serialisation of the
    # file on disk already produces. Document *equality* is not enough — ruamel's CommentedMap
    # equality ignores comments, so a comment-only edit compares equal and would be silently dropped.
    # Comparing the serialised candidate against the serialised on-disk document catches a comment
    # change, AND stays a byte-identical no-op for a hand-formatted file ruamel would reformat: such a
    # file re-serialises to the same canonical text on both sides, so nothing is written and the
    # operator's bytes are left exactly as they are. An unreadable existing file is not a no-op: the
    # candidate replaces it.
    text = dump_config(data)
    if current is not None:
        try:
            on_disk = load_config_text(current.decode("utf-8"), path=path)
            on_disk_text = dump_config(on_disk)
        except ConfigWriteError:
            on_disk_text = None
        if on_disk_text is not None and on_disk_text == text:
            return SaveResult(path=path, changed=False, revision=current_revision, applies=applies)

    # The identity of the file we are about to replace must survive the swap: capture the target's
    # mode and ownership (or, for a brand-new file, the project's normal new-file mode). This is what
    # stops every save turning a 0640 config — and its `.bak` — into mkstemp's 0600.
    identity = _target_identity(path)
    if identity is None:
        mode, owner = _new_file_mode(), None
    else:
        mode, owner = identity[0], (identity[1], identity[2])

    # Validate the candidate BEFORE anything on disk moves. The temp file carries the candidate bytes;
    # it is written, validated, and only then promoted (and it is the same file that is promoted). It
    # is created already wearing the target's identity, so the promoted file keeps it.
    tmp_name = _write_temp(path, text, mode=mode, owner=owner)
    try:
        if schemas:
            _validate_candidate(Path(tmp_name), list(schemas), path)
        if backup and current is not None:
            _atomic_write(Path(str(path) + ".bak"), current.decode("utf-8"),
                          mode=mode, owner=owner)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.remove(tmp_name)
        raise

    backup_path = Path(str(path) + ".bak") if backup and current is not None else None
    log.debug("Wrote config %s%s", path, f" (previous kept at {backup_path.name})" if backup_path else "")
    return SaveResult(path=path, changed=True, revision=config_revision(path),
                      backup=backup_path, applies=applies)


def save_config_file(path: str | os.PathLike, data: Any, *, schemas: list[str] | None = None,
                     expect_revision: str | None = None, backup: bool = True,
                     applies: str = APPLIES_ON_RESTART) -> SaveResult:
    """Write *data* to *path* — round-trip, validated, atomic, with a ``.bak``.

    :param path: the config file to write (its directory must exist).
    :param data: the whole document (any YAML-representable object).
    :param schemas: the schema files this file is validated against, exactly as the bot names them
        (e.g. ``['schemas/main_schema.yaml']``). ``None``/empty writes without a schema check — the
        caller's explicit choice, never a silent default.
    :param expect_revision: the :func:`config_revision` the caller rendered from; a mismatch refuses
        with :class:`StaleConfigRevision` and writes nothing.
    :param backup: keep the previous generation as ``<name>.bak`` (default ``True``).
    :param applies: the honest apply story, defaulting to :data:`APPLIES_ON_RESTART`.
    :raises ConfigValidationError: the candidate failed its schema; the file is untouched.
    :raises StaleConfigRevision: the file moved since ``expect_revision`` was taken.
    :raises ConfigWriteError: the file/directory is unreadable/unwritable.
    """
    path = Path(path)
    with _lock_for(path):
        _ensure_target_directory(path)
        current = _read_bytes(path)
        return _save_locked(path, data, schemas=schemas, backup=backup, current=current,
                            expect_revision=expect_revision, applies=applies)


def patch_config_file(path: str | os.PathLike, mutate: Callable[[Any], None], *,
                      schemas: list[str] | None = None, expect_revision: str | None = None,
                      backup: bool = True, applies: str = APPLIES_ON_RESTART) -> SaveResult:
    """Load *path*, call ``mutate(document)`` to change it in place, then :func:`save_config_file`.

    The read, the mutation and the write all happen under the SAME per-file lock, so no other writer
    can slip between them. ``mutate`` is given the round-trip document (comments intact) and must
    change it in place; it carries no side effects of its own. A missing file starts from an empty
    mapping.
    """
    path = Path(path)
    with _lock_for(path):
        _ensure_target_directory(path)
        current = _read_bytes(path)
        if expect_revision is not None and expect_revision != _revision_of(current):
            raise StaleConfigRevision(
                f"{path.name} changed on disk since this form was built — reload before saving.",
                path=path, expected=expect_revision, current=_revision_of(current))
        document = load_config_text(current.decode("utf-8"), path=path) if current is not None \
            else CommentedMap()
        if document is None:
            document = CommentedMap()
        mutate(document)
        return _save_locked(path, document, schemas=schemas, backup=backup, current=current,
                            expect_revision=None, applies=applies)
