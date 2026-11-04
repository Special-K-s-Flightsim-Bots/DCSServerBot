"""The cloud presets a DCS installation ships, read out of ``Config/Effects/clouds.lua``.

The vendor file below ``<install>/Config/Effects/`` carries the cloud presets the Mission Editor
offers, keyed by name, each with a readable name, a short name and a thumbnail path. It is the file
``extensions/mizedit`` points at for the ``clouds.preset`` VALUE (the name MizEdit writes into a
mission, ``core/mizfile.py``: ``clouds: Preset1``), so the KEY is the name an operator's preset
stores.

THE READ IS A LINE/REGEX WALK — never a Lua evaluator. The file is UNTRUSTED, POSSIBLY ABSENT and
POSSIBLY A DIFFERENT VERSION (a DCS update may move a key), so this module never imports ``luadata``,
never calls ``exec`` and never guesses a table shape: it walks the lines of ``clouds.presets`` one
level down, reads the four scalar fields of an entry, and gives up silently on anything it does not
recognize. An entry is its ``key =`` line, bare (``Preset1``) or quoted (``["Preset 1"]``); only the
entry's OWN scalar fields are read, so a table nested inside it (``layers``, a nested ``presets``)
contributes none of its scalars to the entry. An entry with no thumbnail is kept without one and an
entry with no readable name is offered under its key; a file that cannot be read yields NO entries and
NEVER raises.

Besides the scalar fields, an entry carries its OWN altitude figures — ``presetAltMin``/``presetAltMax``
and its ``layers`` table's ``altitudeMin``/``altitudeMax`` pairs. They are read as NUMBERS, in metres,
exactly as the file spells them: nothing is converted here and nothing is interpreted as a bound. The
``layers`` table sits two indentation levels below the entry's fields, so it is walked separately and
contributes none of its scalars to them.

``readableName`` is Lua source rather than a plain string —
``'01 ##'.._('Few Scattered Clouds \\nMETAR: FEW/SCT 7/8')`` — a quoted marker concatenated with a
gettext-wrapped, escaped string. The readable name is therefore the LAST single-quoted literal of the
value (the gettext one; the ``'NN ##'`` marker is not part of it), with the Lua escapes resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import dcs_install

__all__ = ["CLOUDS_LUA_RELATIVE_PATH", "CLOUDS_LUA_MAX_BYTES", "CloudPreset", "parse_cloud_presets",
           "visible_presets"]

#: the vendor file, relative to the node's DCS installation root.
CLOUDS_LUA_RELATIVE_PATH = "Config/Effects/clouds.lua"

#: The most of ``clouds.lua`` this console will parse — the same ceiling the DCS-install picture read
#: uses, named through it rather than re-spelled so "the console's DCS-install read cap" is ONE number.
CLOUDS_LUA_MAX_BYTES = dcs_install.PRESET_IMAGE_MAX_BYTES

#: the entry names sit directly under ``clouds.presets`` — one indentation level below it.
_PRESETS = re.compile(r"^\s*presets\s*=\s*$")
#: an ENTRY: a bare key, or a quoted one ``["Name"]``/``['Name']``, with an assignment and nothing
#: after it (``\t\tPreset1 =``). A quoted key's text is the entry key, so it is read the same way.
_ENTRY = re.compile(r"""^\s*(?:([A-Za-z_][A-Za-z0-9_]*)|\['([^']*)'\]|\["([^"]*)"\])\s*=\s*$""")
#: a FIELD inside an entry: ``key = value,``.
_FIELD = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*,?\s*$")
#: the scalar fields this module reads at the entry's own level; every other line of an entry (the
#: noise maps, the level map) is ignored rather than interpreted.
_SCALARS = ("visibleInGUI", "readableName", "readableNameShort", "thumbnailName")
#: the entry's own altitude figures, read as numbers beside the scalars.
_ALTITUDE_SCALARS = ("presetAltMin", "presetAltMax")
#: the name of the nested table holding the entry's layers.
_LAYERS_TABLE = "layers"
#: the two altitudes of one layer row.
_LAYER_SCALARS = ("altitudeMin", "altitudeMax")
#: a single-quoted Lua string literal, escapes included.
_LITERAL = re.compile(r"'((?:\\.|[^'\\])*)'")

#: the Lua escapes this module resolves. A backslash before anything else yields that character.
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


@dataclass(frozen=True)
class CloudPreset:
    """One entry of ``clouds.presets``: its key, its two readable names and its thumbnail path.

    ``name`` is the entry KEY — the value a preset's ``clouds.preset`` stores. It is not the readable
    name: the readable names are neither unique in the vendor file nor URL-safe (they carry METAR
    text with ``/`` in it), so the key is what identifies an entry in a URL.
    """

    name: str
    readable_name: str
    readable_name_short: str
    thumbnail: str
    visible: bool
    #: the entry's own ``presetAltMin``/``presetAltMax``, in METRES, as the file spells them.
    preset_alt_min: float | None = None
    preset_alt_max: float | None = None
    #: the entry's ``layers`` rows as ``(altitudeMin, altitudeMax)`` pairs, in file order, in METRES.
    layers: tuple[tuple[float | None, float | None], ...] = ()

    @property
    def base(self) -> float | None:
        """The preset's own default cloud base, in METRES: the LOWEST layer ``altitudeMin``.

        The Mission Editor shows this preset's base as that value converted to feet. ``None`` when the
        entry carries no layer altitude, so a caller can tell "no figure" from "zero".
        """
        floors = [floor for floor, _ceiling in self.layers if floor is not None]
        return min(floors) if floors else None

    @property
    def label(self) -> str:
        """What to show for this entry: its readable name, else its short name, else its own key.

        The key is the fallback because it is the value a preset's ``clouds.preset`` stores — an entry
        the vendor ships without a readable name is still offered, under the only name it has.
        """
        return self.readable_name or self.readable_name_short or self.name

    def item(self) -> dict:
        """The entry as the list route hands it over — the readable label, the path passed through."""
        return {"name": self.name, "readable_name": self.label, "thumbnail": self.thumbnail or None}


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

    ``'01 ##'.._('Few Scattered Clouds \\nMETAR: FEW/SCT 7/8')`` yields the gettext text (the marker
    is dropped with the wrapper); ``_('Light Scattered 1')`` yields the name; ``''`` yields nothing.
    A value with no literal at all is taken as itself.
    """
    literals = _LITERAL.findall(value)
    if not literals:
        return _unescape(value.strip())
    return _unescape(literals[-1])


def parse_cloud_presets(text: str) -> list[CloudPreset]:
    """Every entry under ``clouds.presets``, in file order — both visible and hidden.

    Never raises: text that carries no ``presets`` table, or one whose shape is not recognized,
    yields an empty list. Beside the scalar fields, an entry keeps its own ``presetAltMin``/
    ``presetAltMax`` and its ``layers`` table's ``altitudeMin``/``altitudeMax`` pairs, in file order.
    """
    lines = str(text or "").splitlines()
    start = next((index for index, line in enumerate(lines) if _PRESETS.match(line)), None)
    if start is None:
        return []
    level = _indent(lines[start])
    done: list[tuple[str, dict[str, str], list[dict[str, str]]]] = []
    name: str | None = None
    fields: dict[str, str] = {}
    layers: list[dict[str, str]] = []
    in_layers = False
    layer: dict[str, str] | None = None

    def close() -> None:
        """Record the entry being walked, if there is one."""
        if name is not None:
            done.append((name, fields, layers))

    for line in lines[start + 1:]:
        if not line.strip():
            continue
        depth = _indent(line)
        if depth <= level:
            # the presets table's own opening brace sits at the SAME level as its name; only a
            # closing brace means the table has ended.
            if line.strip().startswith("}"):
                break
            continue
        if depth == level + 1:
            match = _ENTRY.match(line)
            if match is None:
                # an entry's own braces (``{`` and ``},``) sit at the entry-name level; they carry
                # no field, so the entry being walked stays open.
                continue
            close()
            fields = {}
            layers = []
            in_layers = False
            layer = None
            name = match.group(1) or match.group(2) or match.group(3)
            continue
        if name is None:
            continue
        if depth == level + 2:
            # an entry's own fields, and the ``layers`` table's opening line and closing brace.
            if line.strip().startswith("}"):
                in_layers = False
                layer = None
                continue
            match = _FIELD.match(line)
            if match is None:
                continue
            key = match.group(1)
            if key == _LAYERS_TABLE:
                in_layers = True
                layer = None
            elif key in _SCALARS or key in _ALTITUDE_SCALARS:
                fields.setdefault(key, match.group(2))
            continue
        if not in_layers:
            # ONLY an entry's own scalars and its ``layers`` altitudes are read. Any other table
            # nested inside the entry sits deeper, so its scalars are never mistaken for the entry's.
            continue
        if depth == level + 3:
            # one layer row: its opening brace starts it, its closing brace ends it.
            if line.strip().startswith("}"):
                layer = None
                continue
            layer = {}
            layers.append(layer)
            continue
        if depth == level + 4 and layer is not None:
            match = _FIELD.match(line)
            if match is not None and match.group(1) in _LAYER_SCALARS:
                layer.setdefault(match.group(1), match.group(2))
    close()
    return [_build(entry_name, entry_fields, entry_layers)
            for entry_name, entry_fields, entry_layers in done]


def _number(value: str | None) -> float | None:
    """The number a Lua value carries, or ``None`` for an absent or non-numeric one."""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _build(name: str, fields: dict[str, str], layers: list[dict[str, str]]) -> CloudPreset:
    """One finished :class:`CloudPreset` out of the fields and layer rows the walk collected."""
    return CloudPreset(
        name=name,
        readable_name=_string_value(fields.get("readableName", "")),
        readable_name_short=_string_value(fields.get("readableNameShort", "")),
        thumbnail=_string_value(fields.get("thumbnailName", "")),
        visible=fields.get("visibleInGUI", "").strip() == "true",
        preset_alt_min=_number(fields.get("presetAltMin")),
        preset_alt_max=_number(fields.get("presetAltMax")),
        layers=tuple((_number(row.get("altitudeMin")), _number(row.get("altitudeMax")))
                     for row in layers),
    )


def visible_presets(presets: list[CloudPreset]) -> list[CloudPreset]:
    """The entries the picker may offer: those the vendor marks ``visibleInGUI = true``.

    A visible entry is NEVER dropped for want of a label: one whose fields carry no readable name is
    offered under its own key (see :attr:`CloudPreset.label`), because the key is what ``clouds.preset``
    stores and what the plate URL needs.
    """
    return [preset for preset in presets if preset.visible]
