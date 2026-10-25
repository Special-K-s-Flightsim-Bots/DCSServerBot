"""The weather wizard's field table — ONE home for every control the wizard renders.

Each entry declares, for ONE preset key, its label, its control KIND, its unit, the ``min``/``max``/
``step`` of a numeric control, and a ``source`` string saying where that number came from. The table
is DATA: it renders no prose of its own, and the page, the preview and the review panel all read the
SAME entries, so a control cannot carry a number the table does not state.

The numbers are a CURATION. ``schemas/presets_schema.yaml`` carries TYPES and no ranges — its only
``range:`` is on a combination preset's name — so every slider limit here is either the schema's own
prose (``density``/``dust_density``: "0 (clear) to 10 (overcast)"), the schema's own ``desc``
(``atmosphere_type``: "0 standard, 1 tropical"), a figure the operator supplied from the DCS Mission
Editor, or a value read out of the node's ``clouds.lua``. The ``source`` string on each row states
WHICH, so a curation is never mistaken for a DCS limit.

A ROW'S RANGE MUST ADMIT EVERY VALUE ITS OWN VENDOR DATA CARRIES. The Mission Editor's widget limits
are one half — ``MissionEditor/modules/dialogs/me_weather_panel.dlg`` declares each control's
``range`` in metres, m/s, mmHg and °C (temperature -50..50, wind speed 0..50, turbulence 0..6, qnh
720..790, dynamic cloud base 300..5000, fog thickness 0..5000, fog visibility 0..100000, dust
visibility 300..20000) and ``me_weather.lua`` narrows the cloud base to the CHOSEN preset's own
``presetAltMin``/``presetAltMax`` (and defaults it to ``layers[1].altitudeMin`` — the figure
:attr:`dcs_clouds.CloudPreset.base` reads). `clouds.lua`'s presets are the other half: they carry bases
and layer thicknesses WIDER than the ME's own dynamic-mode limits, and a preset the console pre-fills
must never be a number its own control then refuses. Where the two disagree the vendor value wins and
the row says so.

``step`` and ``box_step`` are two different things. ``step`` is a SLIDER's granularity. ``box_step`` is
what a TYPED number box carries — ``BOX_STEP_WHOLE`` or ``BOX_STEP_REAL``, never the slider's figure:
a native number input refuses any value that is not a multiple of its ``step``, so a shape's step
emitted on a box turns a slider's granularity into a validation rule. A box's ``min``/``max``/``step``
is the contract :func:`out_of_range` enforces on the write route, so the browser and the route refuse
the same values.

The units are the schema's own and are NOT converted to the Mission Editor's feet/knots/inchHg: a
preset stores ``qnh`` in mmHg, a cloud base and thickness and the fog figures in metres, and a wind
speed in m/s. The REST API is what converts to the ME's units when it reads a preset
(``plugins/restapi/commands.py``), so the wizard must not.

Three facts the table carries, not argues with. ``clouds.iprecptns`` is an int the REST API reads as
``0=none, 1=rain, 2=thunderstorm, 3=snow`` (``plugins/restapi/models.py``), so it is a SELECT and
never a slider. The fog mode is a SELECT (``auto`` builds DCS's own fog, ``manual`` uses the
``fog.rows`` table). And ``enable_dust`` is MUTUALLY EXCLUSIVE with ``enable_fog`` — DCS applies one
or the other — so ONE either-or control (:data:`DUST_OR_FOG`) picks which, and the write derives both
keys from it: neither can be posted, so neither can be set beside the other.

Two preset keys share ONE control. ``enable_dust`` carries the either-or choice itself (its value is
a MODE word, not a flag); the fog table's ``fog.rows`` is a wizard key that exists so the table's
control has an identity to be carried under, and the rows it draws are written into the ``fog`` map.

The ORDER of :data:`FIELDS` is the order a step presents its controls, so ``temperature`` sits first
on the air/wind step — the value people amend most — ahead of the wind layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import i18n

__all__ = [
    "KIND_SLIDER", "KIND_NUMBER", "KIND_SELECT", "KIND_PICKER", "KIND_TEXT", "KIND_DATE",
    "KIND_MOMENT", "KIND_CHIPS", "KIND_DIAL", "KIND_FOGTABLE", "KIND_TOGGLE",
    "BOX_STEP_WHOLE", "BOX_STEP_REAL", "NUMERIC_KEYS", "out_of_range",
    "SOURCE_SCHEMA_PROSE", "SOURCE_SCHEMA_DESC", "SOURCE_ME", "SOURCE_ME_AND_PRESETS",
    "SOURCE_CURATED", "SOURCE_CLOUDS_LUA",
    "SOURCE_RESTAPI", "Field", "FIELDS", "FIELDS_BY_KEY", "field", "moments", "MOMENTS_ADDED",
    "CLOUD_MODES", "FOG_MODES", "HALO_OFF", "HALO_AUTO", "HALO_MODES", "DUST_OR_FOG",
    "PRECIPITATION",
    "MUTUALLY_EXCLUSIVE",
    "FOG_ROWS_KEY", "FOG_COLUMNS", "FOG_HINTS", "FOG_MODE_KEY", "HAZE_KEY", "DUST_DENSITY_NAME",
    "FOG_ROW_PREFIX", "fog_row_field", "fog_row_cell",
]

# ------------------------------------------------------------------- the control kinds
# ONE declaration per kind: the wizard picks a renderer by this string, and the field rows carry it.
KIND_SLIDER = "slider"
KIND_NUMBER = "number"
KIND_SELECT = "select"
KIND_PICKER = "picker"
KIND_TEXT = "text"
KIND_DATE = "date"
KIND_MOMENT = "moment"
KIND_CHIPS = "chips"
#: a wind direction: the same value as a slider, drawn as a circular dial.
KIND_DIAL = "dial"
#: one control drawing the fog's time-keyed rows (time, thickness, visibility).
KIND_FOGTABLE = "fogtable"
KIND_TOGGLE = "toggle"

# ------------------------------------------------------------------- a typed number box's step
#: a typed-in number box's ``step``. ``1`` accepts every whole number inside the box's range and still
#: refuses a fraction — the type a whole-number preset key can store; ``any`` accepts every real
#: number. A SLIDER keeps the row's ``step`` as its granularity, and that figure is never emitted onto
#: a box: a native number input refuses a value that is not a multiple of its step.
BOX_STEP_WHOLE = "1"
BOX_STEP_REAL = "any"

# ------------------------------------------------------------------- where a number comes from
#: the schema's own PROSE describes the range (``density``, ``dust_density``).
SOURCE_SCHEMA_PROSE = i18n._("schema prose")
#: the schema's own ``desc`` states the closed set (``atmosphere_type``).
SOURCE_SCHEMA_DESC = i18n._("schema desc")
#: the DCS Mission Editor's own limits, converted to the preset's units and supplied by the operator.
SOURCE_ME = i18n._("DCS ME, supplied by the operator")
#: the ME's own widget limits WIDENED by the values the vendor's ``clouds.lua`` presets carry — the
#: two disagree, and a pre-filled preset value must be one its control accepts.
SOURCE_ME_AND_PRESETS = i18n._("DCS ME and the bases the presets carry")
#: a plausible range this tree cannot establish — a curation, plainly labelled as one.
SOURCE_CURATED = i18n._("curated — not read from the tree")
#: read out of the node's own ``Config/Effects/clouds.lua`` (the widest single layer).
SOURCE_CLOUDS_LUA = i18n._("the widest single layer in clouds.lua")
#: the REST API's own precipitation codes (``0=none, 1=rain, 2=thunderstorm, 3=snow``).
SOURCE_RESTAPI = i18n._("the REST API's precipitation codes")


@dataclass(frozen=True)
class Field:
    """One wizard control: the preset KEY it writes, its label and kind, and its numeric limits.

    ``min``/``max``/``step`` are ``None`` for a control that is not a number (a select, a picker, a
    text box). ``step`` is the SLIDER's granularity; ``box_step`` is the figure a TYPED number box
    carries — ``BOX_STEP_WHOLE`` for a control whose preset key stores a whole number, ``BOX_STEP_REAL``
    for one that stores a real number. ``choices`` carries a select's ``(value, label)`` pairs.
    ``unit`` is the schema's own unit word (metres, m/s, mmHg, °C, degrees) and is empty when the
    value is a bare number.
    """

    key: str
    label: str
    kind: str
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    source: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    #: the ``step`` a typed-in number box carries — never the slider's ``step`` (see the module doc).
    box_step: str | None = None


# ------------------------------------------------------------------- the closed sets
#: the three cloud modes the wizard offers (its own choice, not a preset key): a named DCS preset, a
#: dynamic layer the operator describes, or an empty layer.
CLOUD_MODES: tuple[tuple[str, str], ...] = (
    ("preset", i18n._("DCS preset")),
    ("dynamic", i18n._("dynamic")),
    ("clear", i18n._("clear")),
)

#: the fog modes (``fog.mode``): DCS's own automatic build, or the keyframes.
FOG_MODES: tuple[tuple[str, str], ...] = (
    ("auto", i18n._("auto")),
    ("manual", i18n._("manual")),
)

#: THE HALO'S TWO CLOSED ENTRIES. ``off`` is no halo (DCS's own default) and ``auto`` leaves the halo
#: to DCS. They are the table's own data, so EVERY builder of the halo control carries them — the
#: wizard's step and the launch dialog alike — and a row built anywhere cannot come out empty. The
#: node's ``icehalo.lua`` entries are appended BESIDE them where that list is readable.
HALO_OFF = "off"
HALO_AUTO = "auto"
HALO_MODES: tuple[tuple[str, str], ...] = (
    (HALO_OFF, i18n._("off")),
    (HALO_AUTO, i18n._("auto")),
)

#: THE EITHER-OR CHOICE for dust and fog. DCS applies dust OR fog, so ONE control picks which — never
#: two switches that can both be set. Its value is a MODE word on the ``enable_dust`` key: ``dust``
#: writes ``enable_dust: true`` and the visibility beside it, ``fog`` writes ``enable_fog: true`` and
#: the fog mode and rows, ``off`` writes neither.
DUST_OR_FOG: tuple[tuple[str, str], ...] = (
    ("off", i18n._("off")),
    ("dust", i18n._("Dust")),
    ("fog", i18n._("Fog")),
)

#: the ``clouds.iprecptns`` codes the REST API reads (``plugins/restapi/models.py``).
PRECIPITATION: tuple[tuple[str, str], ...] = (
    ("0", i18n._("none")),
    ("1", i18n._("rain")),
    ("2", i18n._("thunderstorm")),
    ("3", i18n._("snow")),
)

#: DCS applies dust OR fog: enabling one means the other is off. Enforced by :data:`DUST_OR_FOG` —
#: ONE control posts a single mode, and the write derives both keys from it, so the pair is exclusive
#: by construction rather than by a refusal.
MUTUALLY_EXCLUSIVE: tuple[tuple[str, str], ...] = (("enable_dust", "enable_fog"),)

#: the key the either-or control rides on, and the fog mode's key.
HAZE_KEY = "enable_dust"
FOG_MODE_KEY = "fog.mode"

#: the wizard key the fog TABLE's control has, so its rows have an identity to be carried under.
FOG_ROWS_KEY = "fog.rows"

# ------------------------------------------------------------------- the fog table's own cells
#: The mission's fog is ONE table keyed by SECONDS FROM MISSION START, each row ``{thickness,
#: visibility}`` (``extensions/mizedit/README.md``, ``schemas/presets_schema.yaml``). The table's
#: control draws a row per entry; these are the cells, with the same ``min``/``max``/``step`` and
#: ``source`` discipline as every other control.
FOG_COLUMNS: tuple[Field, ...] = (
    Field("time", i18n._("Time"), KIND_NUMBER, unit=i18n._("seconds"), min=0, max=86400, step=1,
          source=SOURCE_CURATED, box_step=BOX_STEP_WHOLE),
    #: the ME's own keyframe spin boxes: thickness 0..5000 m and visibility 0..100000 m
    #: (``me_weather.lua:2814``/``:2789``), the same limits this table states.
    Field("thickness", i18n._("Fog thickness"), KIND_NUMBER, unit=i18n._("metres"), min=0, max=5000,
          step=10, source=SOURCE_ME, box_step=BOX_STEP_WHOLE),
    Field("visibility", i18n._("Fog visibility"), KIND_NUMBER, unit=i18n._("metres"), min=0,
          max=100000, step=100, source=SOURCE_ME, box_step=BOX_STEP_WHOLE),
)
FOG_COLUMNS_BY_NAME: dict[str, Field] = {column.key: column for column in FOG_COLUMNS}

#: a per-cell hint — the wizard's OWN prose (the table stays prose-free).
FOG_HINTS: dict[str, str] = {
    "time": i18n._("Seconds from the mission's start."),
    "thickness": i18n._("The fog's thickness at each keyframe, in metres."),
    "visibility": i18n._("The fog's visibility at each keyframe, in metres."),
}

#: the row cells' form-field names: ``fog_row_<index>_<cell>``. The table's rows are indexed, so a
#: row's three cells are three separate form controls and EVERY row posts on submit, without script.
FOG_ROW_PREFIX = "fog_row_"


def fog_row_field(index: int, cell: str) -> str:
    """The form-field name of *cell* (``time``/``thickness``/``visibility``) in row *index*."""
    return f"{FOG_ROW_PREFIX}{index}_{cell}"


def fog_row_cell(name: str) -> tuple[int, str] | None:
    """The ``(row index, cell)`` a ``fog_row_<index>_<cell>`` field name carries, or ``None``.

    ``None`` for any other name — a field the fog table did not draw never reaches the rows.
    """
    if not name.startswith(FOG_ROW_PREFIX):
        return None
    rest = name[len(FOG_ROW_PREFIX):]
    index, _, cell = rest.rpartition("_")
    if not index.isdigit() or cell not in FOG_COLUMNS_BY_NAME:
        return None
    return int(index), cell


# ------------------------------------------------------------------- the table
#: every control the wizard renders, in the order a step presents it. ONE entry per preset key.
FIELDS: tuple[Field, ...] = (
    Field("name", i18n._("Preset name"), KIND_TEXT),
    Field("date", i18n._("Mission date"), KIND_DATE),
    Field("start_time", i18n._("Start time"), KIND_MOMENT),
    Field("terrains", i18n._("Theatre"), KIND_CHIPS),

    Field("clouds", i18n._("Cloud mode"), KIND_SELECT, choices=CLOUD_MODES),
    #: the cloud plates' own key: the picture writes it, and the row is drawn as the plate grid (or,
    #: when the node's list cannot be read, as the free-text fallback) — never as a box beside them.
    Field("clouds.preset", i18n._("Cloud preset"), KIND_PICKER),
    Field("clouds.density", i18n._("Density"), KIND_SLIDER, min=0, max=10, step=1,
          source=SOURCE_SCHEMA_PROSE, box_step=BOX_STEP_WHOLE),
    #: THE BASE: the ME's own dynamic range is 300..5000 m, but the ME also narrows a CHOSEN preset's
    #: base to that preset's ``presetAltMin``/``presetAltMax`` and defaults it to its lowest layer —
    #: which reaches 5460 m in this install's ``clouds.lua``. So the range is the tree's own written
    #: cloud-base ceiling (the Real Weather extension's ``clouds.base.maximum``, 15000 m) from 0 down,
    #: which admits every base a preset carries (938..5460 m over all 45 entries, 2520..5460 over the
    #: 34 visible ones). ``step`` stays a slider granularity; the box accepts any whole metre.
    Field("clouds.base", i18n._("Base"), KIND_SLIDER, unit=i18n._("metres"), min=0, max=15000,
          step=10, source=SOURCE_ME_AND_PRESETS, box_step=BOX_STEP_WHOLE),
    #: ``clouds.lua``'s widest single layer is 3360 m — wider than the ME's own 200..2000 m dynamic
    #: spin box, so the vendor figure is the ceiling.
    Field("clouds.thickness", i18n._("Thickness"), KIND_SLIDER, unit=i18n._("metres"), min=0,
          max=3360, step=10, source=SOURCE_CLOUDS_LUA, box_step=BOX_STEP_WHOLE),
    Field("clouds.iprecptns", i18n._("Precipitation"), KIND_SELECT, choices=PRECIPITATION,
          source=SOURCE_RESTAPI),
    Field("halo.preset", i18n._("Halo"), KIND_SELECT, choices=HALO_MODES),
    Field("halo.crystalsPreset", i18n._("Ice crystals"), KIND_PICKER),

    #: the ME's own temperature control is -50..50 °C (``me_weather_panel.dlg``), not the -25 this row
    #: used to state: a winter preset the ME accepts must not be refused here.
    Field("temperature", i18n._("Temperature"), KIND_SLIDER, unit=i18n._("°C"), min=-50, max=50,
          step=1, source=SOURCE_ME, box_step=BOX_STEP_REAL),
    #: the ME's speed controls are 0..50 m/s at ground/2000/8000 (its 0..106 m/s control is the
    #: 500 m layer, which a preset does not carry).
    Field("wind.atGround.speed", i18n._("Wind at ground"), KIND_SLIDER, unit=i18n._("m/s"), min=0,
          max=50, step=0.5, source=SOURCE_ME, box_step=BOX_STEP_REAL),
    Field("wind.atGround.dir", i18n._("Direction at ground"), KIND_DIAL, unit=i18n._("degrees"),
          min=0, max=359, step=1, source=SOURCE_ME, box_step=BOX_STEP_WHOLE),
    Field("wind.at2000.speed", i18n._("Wind at 2000 m"), KIND_SLIDER, unit=i18n._("m/s"), min=0,
          max=50, step=0.5, source=SOURCE_ME, box_step=BOX_STEP_REAL),
    Field("wind.at2000.dir", i18n._("Direction at 2000 m"), KIND_DIAL, unit=i18n._("degrees"),
          min=0, max=359, step=1, source=SOURCE_ME, box_step=BOX_STEP_WHOLE),
    Field("wind.at8000.speed", i18n._("Wind at 8000 m"), KIND_SLIDER, unit=i18n._("m/s"), min=0,
          max=50, step=0.5, source=SOURCE_ME, box_step=BOX_STEP_REAL),
    Field("wind.at8000.dir", i18n._("Direction at 8000 m"), KIND_DIAL, unit=i18n._("degrees"),
          min=0, max=359, step=1, source=SOURCE_ME, box_step=BOX_STEP_WHOLE),

    Field("groundTurbulence", i18n._("Ground turbulence"), KIND_SLIDER, unit=i18n._("m/s"), min=0,
          max=6, step=0.1, source=SOURCE_ME, box_step=BOX_STEP_REAL),
    Field("qnh", i18n._("Pressure (QNH)"), KIND_SLIDER, unit=i18n._("mmHg"), min=720, max=790,
          step=1, source=SOURCE_ME, box_step=BOX_STEP_REAL),

    Field(HAZE_KEY, i18n._("Dust or fog"), KIND_SELECT, choices=DUST_OR_FOG),
    #: the mission's field is NAMED ``dust_density`` and its meaning is the VISIBILITY, so the label
    #: says what the number is; the key is what the preset stores.
    Field("dust_density", i18n._("Visibility"), KIND_SLIDER, min=0, max=10, step=1,
          source=SOURCE_SCHEMA_PROSE, box_step=BOX_STEP_WHOLE),
    Field(FOG_MODE_KEY, i18n._("Fog"), KIND_SELECT, choices=FOG_MODES),
    Field(FOG_ROWS_KEY, i18n._("Fog keyframes"), KIND_FOGTABLE),
)

#: the table by key, so a lookup cannot drift from the declaration above.
FIELDS_BY_KEY: dict[str, Field] = {entry.key: entry for entry in FIELDS}

#: every numeric control's key, in table order — the keys a posted value can put out of range.
NUMERIC_KEYS: tuple[str, ...] = tuple(entry.key for entry in FIELDS
                                     if entry.kind in (KIND_SLIDER, KIND_NUMBER, KIND_DIAL))


def out_of_range(key: str, text: Any) -> tuple[Field, float] | None:
    """The control *key* and the number *text* states, when that number is outside the row's range.

    ``None`` when the value is inside the range, when the control is not a number (or the table does
    not carry the key), when *text* is empty, or when it is not a number at all — a value no number
    parse reaches is left to the schema's own type check, which names the type it wanted.

    The cell keys of the fog table are looked up the same way, so a fog keyframe's own limits are
    enforced by the same function as every other control's.
    """
    entry = FIELDS_BY_KEY.get(key) or FOG_COLUMNS_BY_NAME.get(key)
    if entry is None or entry.min is None or entry.max is None:
        return None
    stated = str(text if text is not None else "").strip()
    if stated == "":
        return None
    try:
        value = float(stated)
    except ValueError:
        return None
    if not (value < entry.min or value > entry.max):
        return None
    return entry, value

#: the dust control's storage name. The label beside the control is ``Visibility`` (the number's
#: MEANING, per the owner's ruling); this spelling of the field keeps its msgid.
DUST_DENSITY_NAME = i18n._("Dust density")


def field(key: str) -> Field:
    """The entry for *key*. Raises :class:`KeyError` for a key the table does not carry."""
    return FIELDS_BY_KEY[key]


# ------------------------------------------------------------------- the named moments
#: the names ``MizFile.parse_moment`` ADDS to the keys ``astral.sun.sun()`` computes; together they are
#: every token a preset's ``start_time`` accepts. ``now`` — the mission's own wall-clock time — is one
#: of them, and is no solar event.
MOMENTS_ADDED: tuple[str, ...] = ("now", "morning", "evening", "night")


def moments() -> tuple[str, ...]:
    """Every named moment a preset's ``start_time`` accepts, read from ``MizFile.parse_moment``.

    The set is the keys ``parse_moment`` puts into its ``solar_events``: those ``astral.sun.sun()``
    computes for the theatre, plus the ones the method adds itself (``MOMENTS_ADDED``). Both parts come
    from their defining source, never from prose, so the offer cannot go silently short when a
    paragraph is rewritten. The wizard's test pins the offer to the set ``parse_moment`` reports.

    ``astral`` is imported LAZILY — this module is loaded by the console shell, and the mission-tool
    dependencies behind ``core.mizfile`` are not wanted at import time.
    """
    from astral import Observer
    from astral.sun import sun
    from datetime import date

    solar = tuple(sun(Observer(latitude=0.0, longitude=0.0), date=date(2000, 1, 1)))
    return solar + MOMENTS_ADDED