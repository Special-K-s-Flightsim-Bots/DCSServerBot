"""The preset files an installation names: discovery, the node read, and the name -> document rule.

A preset is a MizEdit mission-customizer document. It lives in a node's config tree in one of the two
shapes :data:`PRESET_FILE_PATTERNS` lists, and its value is either a MAP (the preset's own weather and
mission values) or a LIST of other preset names (a combination of presets).

Three plain layers, no Discord and no request objects, so a page and a plugin consume the same rule:

* DISCOVERY — :func:`list_preset_files` lists a node's preset files with each file's version;
* THE READ — :func:`read_preset_documents` parses them into
  ``{file label: {preset name: document}}``, :func:`read_node_presets` doing both in one call;
* THE RULE — :func:`resolve_presets` turns picked ``(file label, name)`` pairs into the DOCUMENTS
  they stand for, in the NODE's own order, plus the names, or a refusal reason. It performs no I/O,
  so it is testable on its own. :func:`resolve_picked_values` is the same rule over posted pick
  values (``<file label>\\n<name>``, :func:`preset_option_value`).

A refusal is a REASON (:data:`REFUSAL_NO_PICK` / :data:`REFUSAL_UNKNOWN_FILE` /
:data:`REFUSAL_UNKNOWN_PRESET`), never a sentence: the user-visible, localised wording belongs to the
caller that renders it.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from core.utils import configwrite

log = logging.getLogger(__name__)

__all__ = [
    "PRESET_FILE_GLOB", "PRESET_DIR", "PRESET_FILE_PATTERNS", "CONFIG_LABEL", "PRESET_PAIR_SEPARATOR",
    "REFUSAL_NO_PICK", "REFUSAL_UNKNOWN_FILE", "REFUSAL_UNKNOWN_PRESET",
    "preset_file_label", "preset_names_of", "preset_file_is_safe", "preset_option_value",
    "split_preset_entry", "listed_identity", "preset_read_key", "PresetRead", "list_preset_files",
    "read_preset_documents", "read_node_presets", "preset_documents", "merged_presets", "preset_rank",
    "PresetResolution", "resolve_presets", "resolve_picked_values",
]

#: the FLAT-file glob, which covers ``config/presets.yaml`` and any sibling ``config/presets_*.yaml``.
PRESET_FILE_GLOB = "presets*.yaml"
#: the directory form of the same thing (``config/presets/*.yaml``).
PRESET_DIR = "presets"
#: THE ONE PATTERN SET a node's config dir is listed with: both shapes of a preset file, derived from
#: the two literals above so neither is spelled a second time.
PRESET_FILE_PATTERNS: tuple[str, ...] = (PRESET_FILE_GLOB, f"{PRESET_DIR}/*.yaml")
#: the config tree's own name for itself, as a file label is printed (``config/presets.yaml``). A file
#: is never shown by its absolute install path.
CONFIG_LABEL = "config"
#: the separator a pick entry carries between its file label and its preset name.
PRESET_PAIR_SEPARATOR = "\n"

#: a pick that states no preset at all: no entry, or every entry naming a file and no preset.
REFUSAL_NO_PICK = "no_pick"
#: a pick naming a file the read did not list, or a value shaped like a path rather than a label.
REFUSAL_UNKNOWN_FILE = "unknown_file"
#: a pick naming a preset neither the named file nor the node's merged map carries.
REFUSAL_UNKNOWN_PRESET = "unknown_preset"


def preset_names_of(document: Any) -> list[str]:
    """The named presets ONE PARSED preset document carries, in the document's own order.

    A name is offered when the extension could apply it — a mapping (the preset's own values or the
    expert DSL) that is not marked ``hidden``, or a LIST (a combination of other names, which
    ``MizEdit.get_presets`` resolves by name too). Anything else carries no name: a scalar entry, or
    a document that is not a map at all.
    """
    if not isinstance(document, Mapping):
        return []
    return [str(name) for name, value in document.items()
            if isinstance(value, (Mapping, list))
            and not (isinstance(value, Mapping) and value.get("hidden", False))]


def preset_file_label(config_dir: Any, path: Any) -> str:
    """A node-side preset file as the config tree names it (``config/presets_winter.yaml``).

    The node answers its own absolute paths, so the config dir is stripped as a STRING (forward or
    back slashes) rather than with a path type, and a path outside it keeps its leaf name.
    """
    base = str(config_dir or "").replace("\\", "/").rstrip("/")
    shown = str(path or "").replace("\\", "/")
    if base and shown.startswith(base + "/"):
        shown = shown[len(base) + 1:]
    else:
        shown = shown.rsplit("/", 1)[-1]
    return f"{CONFIG_LABEL}/{shown}"


#: The shape a preset FILE value must have before it is even compared with a node's own file set:
#: no absolute path, no Windows drive letter, no UNC share, no ``..`` component, no colon. A value that
#: passes is still only accepted when it EQUALS one of the labels the read returned.
_PRESET_PATH_UNSAFE = re.compile(r"^(?:[A-Za-z]:|[\\/])")


def preset_file_is_safe(value: Any) -> bool:
    """Whether *value* is shaped like a preset file's ``config/…`` label and not like a path.

    ``config/presets_winter.yaml`` passes; ``/etc/passwd``, ``C:\\presets.yaml``, ``\\\\host\\share\\x``,
    ``../../x.yaml`` and an empty string do not. The test is on the REPORTED value, and it never
    normalizes: a value this answers ``True`` for is still only usable when the read lists it.
    """
    text = str(value or "").strip()
    if not text or _PRESET_PATH_UNSAFE.match(text):
        return False
    parts = text.replace("\\", "/").split("/")
    return ".." not in parts and ":" not in text


def preset_option_value(label: str, name: str) -> str:
    """The pick entry's value for *name* out of the preset file *label*.

    Two parts, the file first, so a name two files define applies the body of the group it was ticked
    in. A single-file node draws bare names instead.
    """
    return f"{label}{PRESET_PAIR_SEPARATOR}{name}"


def split_preset_entry(entry: str) -> tuple[str, str]:
    """A pick entry as ``(file label, name)`` — ``("", name)`` for a BARE name.

    The label is cleaned; the NAME is read verbatim, because a preset name is operator data looked up
    exactly. An entry whose name part is empty is a LABEL-ONLY entry — a file was picked and no preset
    — so its name answers ``""``; the label is never turned into a name.
    """
    text = str(entry or "")
    if PRESET_PAIR_SEPARATOR not in text:
        return "", text.strip()
    label, name = text.split(PRESET_PAIR_SEPARATOR, 1)
    return label.strip(), name


def listed_identity(path: str, size: Any, mtime: Any) -> str:
    """One listed file as the name for its current CONTENT: path, size and modification time.

    ``list_files`` answers each file with its size and mtime, so a preset file edited outside the
    caller is a different identity. The values are taken as the node spells them, unrounded, because
    the mtime's fraction is what separates two edits inside one second. ``\\x1e`` is not a character
    a path carries on either platform the bot runs on.
    """
    return f"{path}\x1e{size}\x1e{mtime}"


def preset_read_key(config_dir: str, versions: Iterable[str]) -> str:
    """The key an answer of ONE read is held under: the config dir plus the file set read.

    The file set is part of the key because the files that answer a read change with the node.
    ``\\x1f`` separates the parts because a path may contain any printable character, ``|`` included.
    A member carries a file's VERSION (:func:`listed_identity`), so a file rewritten on the node is a
    different key.
    """
    return "\x1f".join([str(config_dir or ""), *[str(path) for path in versions]])


def _listing_entry(entry: Any) -> tuple[str, Any, Any]:
    """One ``list_files`` entry as ``(path, size, mtime)``, with ``0`` for an absent size or mtime.

    ``list_files`` answers ``(path, size, mtime)`` triples, and a double or an older node build may
    answer the path alone — that file is still a preset file, so it is kept with no version.
    """
    if isinstance(entry, (list, tuple)):
        path = str(entry[0]).strip() if entry else ""
        size = entry[1] if len(entry) > 1 else 0
        mtime = entry[2] if len(entry) > 2 else 0
        return path, size, mtime
    return str(entry or "").strip(), 0, 0


async def list_preset_files(node: Any) -> tuple[list[tuple[str, str]], bool]:
    """The preset files a node carries, as ``[(path, version)]`` ordered by config label.

    ``node.list_files`` enumerates :data:`PRESET_FILE_PATTERNS` under the node's OWN ``config_dir``
    and answers each file's size and mtime, which :func:`listed_identity` folds into the version a
    read's answer is held under. The second answer says whether the node listed at all — ``False``
    for a node with no config dir, no ``list_files``, or a listing that failed.

    The LISTING is never remembered by this module: a preset file added, removed or rewritten on the
    node has to reach the very next read, and only the listing can tell.
    """
    config_dir = str(getattr(node, "config_dir", "") or "")
    lister = getattr(node, "list_files", None)
    if not config_dir or lister is None:
        return [], False
    node_name = str(getattr(node, "name", "") or "")
    try:
        entries = await lister(config_dir, pattern=list(PRESET_FILE_PATTERNS))
    except Exception as ex:  # noqa: BLE001 - every transport failure is the caller's fallback
        log.warning("Preset files: node '%s' could not list '%s' (%s)",
                    node_name, config_dir, type(ex).__name__)
        return [], False
    listed: list[tuple[str, str]] = []
    for entry in entries or []:
        path, size, mtime = _listing_entry(entry)
        if path:
            listed.append((path, listed_identity(path, size, mtime)))
    listed.sort(key=lambda item: preset_file_label(config_dir, item[0]))
    return listed, True


class PresetRead(NamedTuple):
    """One read of a node's preset files.

    ``files`` is ``{file label: {preset name: document}}`` in the order the files were listed and each
    document's keys in the file's own order; ``paths`` is the node path behind each label.
    ``readable`` is what a caller offers its free-text fallback on: ``False`` when nothing answered at
    all — no config dir, no transport, or every file failing. ``complete`` is ``True`` when no file
    failed, which is what makes the answer safe to remember.
    """

    files: dict[str, dict[str, Any]]
    paths: dict[str, str]
    readable: bool
    complete: bool


async def read_preset_documents(node: Any, paths: Iterable[str]) -> PresetRead:
    """THE READ: every listed preset file parsed into ``{file label: {preset name: document}}``.

    Each file is read through ``node.read_file`` once and parsed once, through the ONE config
    primitive (:func:`core.utils.configwrite.load_config_text` — never a second YAML reading). Two
    files may define the same name and nothing is merged here: the label is what tells them apart.

    A file the node cannot deliver — an exception, or an ``int`` (a failure CODE) instead of bytes —
    is skipped and makes the answer INCOMPLETE, so a caller does not remember it; it never hides the
    others. A file that does not parse, or that is not a mapping, carries no preset: it is skipped
    without making the answer incomplete, because its content is already part of its identity
    (:func:`listed_identity`) and a rewrite is therefore a different key.
    """
    config_dir = str(getattr(node, "config_dir", "") or "")
    node_name = str(getattr(node, "name", "") or "")
    reader = getattr(node, "read_file", None)
    if reader is None:
        return PresetRead({}, {}, False, False)
    files: dict[str, dict[str, Any]] = {}
    places: dict[str, str] = {}
    failed = False
    for path in paths:
        try:
            payload = await reader(path)
        except Exception as ex:  # noqa: BLE001 - one unreadable file must not hide the others
            log.warning("Preset files: node '%s' could not read '%s' (%s)",
                        node_name, path, type(ex).__name__)
            failed = True
            continue
        if not isinstance(payload, (bytes, bytearray)):
            log.warning("Preset files: node '%s' answered '%s' without content", node_name, path)
            failed = True
            continue
        try:
            document = configwrite.load_config_text(bytes(payload).decode("utf-8"), path=path)
        except configwrite.ConfigWriteError as ex:
            log.warning("Preset files: node '%s' answered unparsable content for '%s' (%s)",
                        node_name, path, type(ex).__name__)
            continue
        if not isinstance(document, Mapping):
            continue
        label = preset_file_label(config_dir, path)
        files[label] = dict(document)
        places[label] = str(path)
    return PresetRead(files, places, bool(files) if failed else True, not failed)


async def read_node_presets(node: Any) -> PresetRead:
    """Discovery and read in one call: what a caller that keeps no cache needs."""
    listed, listed_ok = await list_preset_files(node)
    if not listed_ok:
        return PresetRead({}, {}, False, False)
    return await read_preset_documents(node, [path for path, _version in listed])


def merged_presets(files: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The node's MERGED map: every file's names, a later file overriding an earlier one.

    The order is the read's own (a ``config/…`` label sort), which is the order ``MizEdit`` merges
    the files it reads, so a name two files carry answers the LAST file's value.
    """
    merged: dict[str, Any] = {}
    for document in files.values():
        merged.update(document)
    return merged


def preset_documents(name: str, values: Mapping[str, Any],
                     fallback: Mapping[str, Any] | None = None, *,
                     member: bool = False) -> list[Any] | None:
    """The preset DOCUMENTS *name* stands for — ``None`` when the map it is read from lacks it.

    A name resolves to its own value; a COMBINATION (a LIST of other names) resolves to its members'
    documents, in the order the combination spells them — the same expansion
    ``MizEdit.get_presets`` performs.

    The name the caller ASKED for is read from *values* alone (``member=False``). A COMBINATION's
    member is read from *values* first and then, when that file does not carry it, from *fallback* —
    the node's merged map — so a combination spanning preset files resolves exactly as the node's own
    merge does. A member neither map carries makes the whole name unresolvable, because a launch
    would otherwise apply a combination with a hole in it.
    """
    source = values
    if member and name not in values and fallback is not None:
        source = fallback
    value = source.get(name)
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (list, tuple)):
        documents: list[Any] = []
        for item in value:
            if not isinstance(item, str):
                return None
            resolved = preset_documents(item, values, fallback, member=True)
            if resolved is None:
                return None
            documents.extend(resolved)
        return documents or None
    return None


def preset_rank(label: str, name: str, files: Mapping[str, Mapping[str, Any]]) -> tuple[int, int]:
    """Where ``(label, name)`` stands in the node's own config order — ``(group, position)``.

    The groups are the files in the order the read returned them, and a position is the name's place
    among the names its file OFFERS (:func:`preset_names_of`). A bare name is ranked by the first
    file that carries it, so a payload follows the node's order even for a caller that names no file.
    A name no file offers ranks last.
    """
    for group, (file_label, document) in enumerate(files.items()):
        if label and file_label != label:
            continue
        names = preset_names_of(document)
        if name in names:
            return group, names.index(name)
    return len(files), 0


class PresetResolution(NamedTuple):
    """The outcome of the name -> document rule.

    ``documents`` are the preset bodies in order (a combination contributes its members' documents);
    ``names`` are the names the caller stated, in the same order, one per resolved entry. When
    ``reason`` is not ``None`` the pick was REFUSED and both lists are empty — a refusal never
    carries a partial answer, so a caller cannot launch applying half of what was asked.
    """

    documents: list[Any]
    names: list[str]
    reason: str | None = None

    @property
    def refused(self) -> bool:
        """Whether the pick was refused (``reason`` carries the reason)."""
        return self.reason is not None


def resolve_presets(picks: Iterable[tuple[str, str]],
                    files: Mapping[str, Mapping[str, Any]],
                    merged: Mapping[str, Any] | None = None, *,
                    named_file: str = "") -> PresetResolution:
    """THE NAME -> DOCUMENT RULE: picked ``(file label, name)`` pairs into documents and names.

    *files* is ``{file label: {preset name: document}}`` as the read returned it, so its KEYS are the
    labels this caller accepts. *merged* is the node's merged map (:func:`merged_presets`, computed
    here when the caller passes ``None``).

    * an entry that CARRIES ITS FILE resolves its name against THAT file — a name two files define
      applies the body of the file it was picked in — and its combination members may reach *merged*;
    * a BARE name resolves against *named_file* when the caller names one, else against *merged*;
    * *named_file* itself is accepted only when it is shaped like a ``config/…`` label
      (:func:`preset_file_is_safe`) and is one of *files*' keys; an empty one means the only file a
      node carries, when it carries exactly one;
    * a name neither the file nor the merged map carries is REFUSED (:data:`REFUSAL_UNKNOWN_PRESET`),
      never dropped, as is a label this caller does not accept (:data:`REFUSAL_UNKNOWN_FILE`);
    * a pick that resolves no name at all — no entry, or entries naming a file and no preset — is
      refused as :data:`REFUSAL_NO_PICK`.

    The documents follow the NODE's own config order (:func:`preset_rank`), never the click order, so
    a field two presets both set keeps the later one, exactly as the node's merge does. No I/O runs
    here.
    """
    if named_file and (not preset_file_is_safe(named_file) or named_file not in files):
        return PresetResolution([], [], REFUSAL_UNKNOWN_FILE)
    named = named_file or (next(iter(files)) if len(files) == 1 else "")
    site = merged_presets(files) if merged is None else merged
    ordered = sorted(picks, key=lambda pair: preset_rank(pair[0], pair[1], files))
    documents: list[Any] = []
    names: list[str] = []
    for label, name in ordered:
        if not name:
            continue
        if label:
            if label not in files:
                return PresetResolution([], [], REFUSAL_UNKNOWN_FILE)
            source = files[label]
        else:
            # a bare name is resolved in the file the caller named, else in the node's merged map —
            # never in a file the caller did not name
            source = files[named] if named else site
        if name not in source:
            return PresetResolution([], [], REFUSAL_UNKNOWN_PRESET)
        resolved = preset_documents(name, source, site)
        if resolved is None:
            return PresetResolution([], [], REFUSAL_UNKNOWN_PRESET)
        documents.extend(resolved)
        names.append(name)
    if not names:
        return PresetResolution([], [], REFUSAL_NO_PICK)
    return PresetResolution(documents, names)


def resolve_picked_values(values: Iterable[str],
                          files: Mapping[str, Mapping[str, Any]],
                          merged: Mapping[str, Any] | None = None, *,
                          named_file: str = "") -> PresetResolution:
    """:func:`resolve_presets` over POSTED pick values (``<file label>\\n<name>``).

    The one entry point both surfaces build their payload with: each value is split by
    :func:`split_preset_entry` (the label cleaned, the name verbatim) and then resolved by the rule.
    A value that names no preset contributes nothing, exactly as an empty pair does.
    """
    return resolve_presets([split_preset_entry(value) for value in values], files, merged,
                           named_file=named_file)
