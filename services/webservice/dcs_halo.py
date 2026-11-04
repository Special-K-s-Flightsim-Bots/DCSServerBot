"""The ice-halo presets a DCS installation ships, read out of ``Config/Effects/icehalo.lua``.

The vendor file below ``<install>/Config/Effects/`` carries TWO tables of halo presets: ``presets``
(the halo itself, applied through a mission's ``halo.preset``) and ``crystalsPresets`` (the ice-crystal
appearance behind it, applied through ``halo.crystalsPreset``). Both are keyed by name; each entry
carries ``visibleInGUI``, ``readableName``, ``readableNameShort`` and ``thumbnailName``, and a
``crystalsPresets`` entry additionally carries a nested ``appearance`` table.

THE READ IS A LINE/REGEX WALK — never a Lua evaluator. The file is UNTRUSTED, POSSIBLY ABSENT and
POSSIBLY A DIFFERENT VERSION (a DCS update may move a key), so this module never imports ``luadata``,
never calls ``exec`` and never guesses a table shape: it walks the lines of ONE named table one
indentation level down, reads the four scalar fields of an entry, and gives up silently on anything it
does not recognize. Only an entry's OWN scalar fields are read, so the ``appearance`` table nested
inside a ``crystalsPresets`` entry contributes none of its scalars to the entry. An entry with no
thumbnail is kept without one and an entry with no readable name is offered under its key; a file that
cannot be read yields NO entries and NEVER raises.

``readableName`` is Lua source rather than a plain string —
``'01 ##'.. _('All Kinds of Ice Halo')`` — a quoted marker concatenated with a gettext-wrapped string.
The readable name is therefore the LAST single-quoted literal of the value (the gettext one; the
``'NN ##'`` marker is not part of it), with the Lua escapes resolved.

``thumbnailName`` is read VERBATIM and is never rebuilt from the entry key: the file's own names carry
a space (``AllKinds 1.png``) and their casing does not always match the key (``BasicHaloWithSundogs``
names ``BasicHaloWithSunDogs 1.png``), so a derived path would miss that entry.

A mission's ``halo.preset`` may ALSO carry ``"auto"`` — DCS decides the halo. That value is NOT an
entry of this file: :func:`auto_preset` hands it over for the caller to offer IN ADDITION to the parsed
entries, and ``crystalsPreset`` is written only for a named preset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import dcs_install

__all__ = ["HALO_LUA_RELATIVE_PATH", "HALO_LUA_MAX_BYTES", "HALO_AUTO_PRESET", "HaloPreset",
           "parse_halo_presets", "parse_crystal_presets", "visible_presets", "auto_preset"]

#: the vendor file, relative to the node's DCS installation root.
HALO_LUA_RELATIVE_PATH = "Config/Effects/icehalo.lua"

#: The most of ``icehalo.lua`` this console will parse — the same ceiling the DCS-install picture read
#: uses, named through it rather than re-spelled so "the console's DCS-install read cap" is ONE number.
HALO_LUA_MAX_BYTES = dcs_install.PRESET_IMAGE_MAX_BYTES

#: the value a mission's ``halo.preset`` carries when DCS decides the halo itself. It is NOT an entry
#: of the vendor file, so no parser ever returns it.
HALO_AUTO_PRESET = "auto"

#: the two entry tables the file carries. Each is walked the same way; the names are the file's own.
_HALO_TABLE = "presets"
_CRYSTALS_TABLE = "crystalsPresets"

#: a table's NAME line, one level above its entries (``presets =``, bare or with an opening brace).
_TABLE = {name: re.compile(rf"^\s*{name}\s*=\s*\{{?\s*$")
          for name in (_HALO_TABLE, _CRYSTALS_TABLE)}

#: an ENTRY: a bare key, or a quoted one ``["Name"]``/``['Name']``, with an assignment and nothing
#: after it (``\tAtmoHighClouds =``). A quoted key's text is the entry key, so it is read the same way.
_ENTRY = re.compile(r"""^\s*(?:([A-Za-z_][A-Za-z0-9_]*)|\[[']([^']*)[']\]|\[["]([^"]*)["]\])\s*=\s*$""")
#: a FIELD inside an entry: ``key = value,``.
_FIELD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*,?\s*$")
#: the scalar fields this module reads; every other line of an entry (``appearance`` and everything
#: nested inside it) is ignored rather than interpreted.
_SCALARS = ("visibleInGUI", "readableName", "readableNameShort", "thumbnailName")
#: a single-quoted Lua string literal, escapes included.
_LITERAL = re.compile(r"'((?:\\.|[^'\\])*)'")

#: the Lua escapes this module resolves. A backslash before anything else yields that character.
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


@dataclass(frozen=True)
class HaloPreset:
    """One entry of a halo table: its key, its two readable names and its thumbnail path.

    ``name`` is the entry KEY — the value a mission's ``halo.preset``/``halo.crystalsPreset`` stores.
    It is not the readable name: the readable names are neither unique in the vendor file nor URL-safe,
    so the key is what identifies an entry in a URL.
    """

    name: str
    readable_name: str
    readable_name_short: str
    thumbnail: str
    visible: bool

    @property
    def label(self) -> str:
        """What to show for this entry: its readable name, else its short name, else its own key.

        The key is the fallback because it is the value a mission stores — an entry the vendor ships
        without a readable name is still offered, under the only name it has.
        """
        return self.readable_name or self.readable_name_short or self.name

    def item(self) -> dict:
        """The entry as the list route hands it over — the readable label, the path passed through.

        ``thumbnail`` is the file's own ``thumbnailName`` VERBATIM (``None`` when the entry carries
        none), never a path rebuilt from the key: the stored names carry a space and a casing that
        does not always match the key.
        """
        return {"name": self.name, "readable_name": self.label, "thumbnail": self.thumbnail or None}


def auto_preset() -> HaloPreset:
    """The ``"auto"`` choice: DCS decides the halo. It is NOT an entry of the vendor file.

    A caller offers it IN ADDITION to the parsed entries, so ``crystalsPreset`` is written only for a
    named preset. It carries no picture and no readable name of its own, so it is labelled by its key.
    """
    return HaloPreset(name=HALO_AUTO_PRESET, readable_name="", readable_name_short="", thumbnail="",
                      visible=True)


def _indent(line: str) -> int:
    """The line's indentation in LEVELS: a tab is one level, four spaces are one level."""
    width = 0
    for char in line:
        if char == "\t":
            width += 4
        elif char == " ":
            width += 1
        else:
            break
    return width // 4


def _unescape(value: str) -> str:
    """*value* with the Lua escapes resolved (``\\n`` becomes a newline)."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            following = value[index + 1]
            out.append(_ESCAPES.get(following, following))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _string_value(value: str) -> str:
    """The string a Lua value carries: its LAST single-quoted literal, escapes resolved.

    ``'01 ##'.. _('All Kinds of Ice Halo')`` yields the gettext text (the marker is dropped with the
    wrapper); ``_('Ice Halo on all mediums')`` yields the name; ``''`` yields nothing. A value with no
    literal at all is taken as itself.
    """
    literals = _LITERAL.findall(value)
    if not literals:
        return _unescape(value.strip())
    return _unescape(literals[-1])


def _walk_entries(text: str, table: str) -> list[tuple[str, dict[str, str]]]:
    """Every entry under the *table* table of *text*, in file order, as ``(key, fields)`` pairs.

    Never raises: text that carries no such table, or one whose shape is not recognized, yields an
    empty list. Only an entry's OWN scalar fields are collected — a table nested inside the entry sits
    deeper, so its scalars are never mistaken for the entry's.
    """
    lines = str(text or "").splitlines()
    pattern = _TABLE[table]
    start = next((index for index, line in enumerate(lines) if pattern.match(line)), None)
    if start is None:
        return []
    level = _indent(lines[start])
    done: list[tuple[str, dict[str, str]]] = []
    name: str | None = None
    fields: dict[str, str] = {}

    def close() -> None:
        """Record the entry being walked, if there is one."""
        if name is not None:
            done.append((name, fields))

    for line in lines[start + 1:]:
        if not line.strip():
            continue
        depth = _indent(line)
        if depth <= level:
            # the table's own opening brace sits at the SAME level as its name; only a closing brace
            # means the table has ended.
            if line.strip().startswith("}"):
                break
            continue
        if depth == level + 1:
            match = _ENTRY.match(line)
            if match is None:
                # an entry's own braces (``{`` and ``},``) sit at the entry-name level; they carry no
                # field, so the entry being walked stays open.
                continue
            close()
            fields = {}
            name = match.group(1) or match.group(2) or match.group(3)
            continue
        if name is None or depth != level + 2:
            # ONLY an entry's own scalar fields are read. A table nested INSIDE the entry
            # (``appearance``, its ``crystals``) sits deeper, so its scalars are never folded in.
            continue
        match = _FIELD.match(line)
        if match is not None and match.group(1) in _SCALARS:
            fields.setdefault(match.group(1), match.group(2))
    close()
    return done


def _build(name: str, fields: dict[str, str]) -> HaloPreset:
    """One finished :class:`HaloPreset` out of the scalar fields the walk collected for *name*."""
    return HaloPreset(
        name=name,
        readable_name=_string_value(fields.get("readableName", "")),
        readable_name_short=_string_value(fields.get("readableNameShort", "")),
        thumbnail=_string_value(fields.get("thumbnailName", "")),
        visible=fields.get("visibleInGUI", "").strip() == "true",
    )


def parse_halo_presets(text: str) -> list[HaloPreset]:
    """Every entry under ``icehalo.presets``, in file order — both visible and hidden.

    Never raises: text that carries no ``presets`` table, or one whose shape is not recognized, yields
    an empty list. The ``"auto"`` value a mission may carry is NOT here (see :func:`auto_preset`).
    """
    return [_build(entry_name, entry_fields) for entry_name, entry_fields in
            _walk_entries(text, _HALO_TABLE)]


def parse_crystal_presets(text: str) -> list[HaloPreset]:
    """Every entry under ``icehalo.crystalsPresets``, in file order — both visible and hidden.

    Read through the SAME walk as :func:`parse_halo_presets`; a crystal entry's nested ``appearance``
    table contributes nothing. Never raises: an unreadable or unrecognized shape yields an empty list.
    """
    return [_build(entry_name, entry_fields) for entry_name, entry_fields in
            _walk_entries(text, _CRYSTALS_TABLE)]


def visible_presets(presets: list[HaloPreset]) -> list[HaloPreset]:
    """The entries the picker may offer: those the vendor marks ``visibleInGUI = true``.

    A visible entry is NEVER dropped for want of a label: one whose fields carry no readable name is
    offered under its own key (see :attr:`HaloPreset.label`), because the key is what a mission stores.
    """
    return [preset for preset in presets if preset.visible]
