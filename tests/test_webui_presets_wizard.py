"""The Presets wizard: the field table and the weather steps (render only).

One section per acceptance criterion of the card:

* the FIELD TABLE (``services/webservice/weather_fields.py``) is the ONE home: every numeric control
  carries a ``min``/``max``/``step`` and a ``source``, ``clouds.iprecptns`` is a SELECT (never a
  slider), and the units are the schema's own (qnh in mmHg, cloud base/thickness/fog in metres, wind
  in m/s);
* the moments are exactly the set ``core/mizfile.parse_moment`` accepts — every offered token parses,
  every name it reports it accepts is offered, and ``now`` (a moment the docstring omitted) is there;
* a test reads the table and the RENDERED CONTEXT (not the HTML string) and asserts every control's
  min/max/step EQUALS the table's, with a rendered-HTML cross-check that catches a hand-typed number;
* every field row in the wizard shares ONE control right edge (the geometry is COMPUTED from the
  shipped template over the rendered rows — there is no layout engine in this suite, so it is never
  read by eye);
* ``GET /presets/new`` renders the SAME wizard, EMPTY, step 1 first, and states the destination rule
  (one file stated, several offered, none stated) — and the toolbar's "New preset" link points at it;
* the clouds step reads the node's own cloud presets for the plates, and falls back to FREE TEXT when
  they cannot be read (never refuses the step);
* the review step dumps the produced document through ``configwrite`` and carries the removal rule;
* the slider PAIR: one range (never submitted) wired BOTH ways to the number box (the submitted
  control) by the ONE shared asset ``static/weather-slider.js``, which both this page and the launch
  dialog load — the wizard's own row and the dialog's row cannot drift into two scripts;
* the GETs WRITE NOTHING: the preset file is sha256-identical afterwards.

Driven through the REAL application (the production factory, the shell, the local login backend and a
stub source), with the write suite's own harness reused.
"""
from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from markupsafe import escape

from services.webservice import weather_fields
from services.webservice.pages import presets as presets_page
from test_webui_presets_clouds import CloudsNode, install_clouds  # noqa: F401 - fixtures reused
from test_webui_presets_halo import (  # noqa: F401 - the halo list's own fixture
    CRYSTAL_ENTRIES, HALO_ENTRIES, install_halo,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, sign_in,
)

#: a preset carrying its own weather values, so the wizard has something to open.
SPRING = """\
Spring:
  date: '2016-04-21'
  start_time: dawn +00:00
  temperature: 8
  qnh: 760
  clouds:
    preset: Preset2
    base: 2000
    thickness: 200
  wind:
    atGround: {speed: 3, dir: 20}
  groundTurbulence: 0.5
"""

#: a combination preset (a list of names) and the expert DSL (never opened by the wizard).
OTHER = """\
Casual Weather:
  - Spring
  - Morning
Carrier Freqs:
  modify:
    file: mission
"""

STEPS = presets_page.WIZARD_STEP_VALUES

REPO_ROOT = Path(__file__).resolve().parents[1]
#: the SHARED editor the wizard's panel is driven by, and the harness that runs it browser-less.
SETTINGS_YAML_JS = REPO_ROOT / "services" / "webservice" / "static" / "settings-yaml.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"


def preset_file(config_dir: Path, text: str = SPRING, name: str = "presets.yaml") -> Path:
    path = config_dir / name
    path.write_text(text, encoding="utf-8")
    return path


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


def rendered(text: str) -> str:
    """*text* as the page's HTML carries it — autoescape turns ``'`` into ``&#39;``."""
    return str(escape(text))


def _attr(tag: str, name: str) -> str:
    """The value of attribute *name* in *tag*, or ``''`` when it carries none."""
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return match.group(1) if match else ""


def control_min_max_step(html: str) -> list[tuple[str, str, str, str]]:
    """Every NAMED control of every field row: ``(name, min, max, step)``.

    A row may draw MORE than one control (a wind row carries the speed pair and the direction dial
    beside it), so all of them are read. Only a control carrying a ``name`` submits; the decorative
    ranges do not, so they are not read here (the value the wizard keeps is a number box's).
    """
    found: list[tuple[str, str, str, str]] = []
    for block in re.findall(r'<div class="pw-f">.*?(?=<div class="pw-f">|</form>)', html, re.S):
        for control in re.finditer(
                r'<input[^>]*\bname="([^"]+)"[^>]*>|<select[^>]*\bname="([^"]+)"', block):
            name = control.group(1) or control.group(2)
            tag = block[control.start():block.index(">", control.start()) + 1]
            found.append((name, _attr(tag, "min"), _attr(tag, "max"), _attr(tag, "step")))
    return found


def capture_context(monkeypatch) -> dict:
    """Capture the RENDERED context of the next wizard render (the dict the template receives)."""
    seen: dict = {}
    real = presets_page.i18n.render

    def spy(request, name, **context):
        seen["name"] = name
        seen["context"] = context
        return real(request, name, **context)

    monkeypatch.setattr(presets_page.i18n, "render", spy)
    return seen


# --------------------------------------------------------------- 1. the field table, one home

def test_every_numeric_control_carries_a_range_and_a_source():
    """The table is the ONE home: a slider, a dial and a fog cell state min/max/step, and every row
    says where its number is from."""
    for entry in weather_fields.FIELDS:
        if entry.kind in (weather_fields.KIND_SLIDER, weather_fields.KIND_NUMBER,
                          weather_fields.KIND_DIAL):
            assert entry.min is not None and entry.max is not None and entry.step is not None, entry.key
            assert entry.min < entry.max, entry.key
        assert entry.source or entry.kind in (weather_fields.KIND_SELECT, weather_fields.KIND_PICKER,
                                              weather_fields.KIND_TEXT, weather_fields.KIND_DATE,
                                              weather_fields.KIND_MOMENT, weather_fields.KIND_CHIPS,
                                              weather_fields.KIND_FOGTABLE,
                                              weather_fields.KIND_TOGGLE), entry.key
    # the fog table's cells are numbers too, and they carry the same discipline.
    assert [column.key for column in weather_fields.FOG_COLUMNS] == ["time", "visibility", "thickness"], \
        "the table presents visibility before thickness"
    # ...and the DISPLAY order is the table's, never the carried value's: a ``fog.rows`` row keeps
    # the spelling ``time:thickness:visibility`` whatever order the columns are drawn in.
    assert weather_fields.FOG_CELL_ORDER == ("time", "thickness", "visibility")
    for column in weather_fields.FOG_COLUMNS:
        assert column.min is not None and column.max is not None and column.step is not None, column.key
        assert column.min < column.max, column.key
        assert column.source, column.key
        assert weather_fields.FOG_HINTS.get(column.key), column.key


def test_the_cloud_preset_and_the_crystal_key_are_the_picture_keys():
    """The two keys a plate grid writes: the cloud preset's and the halo's crystals."""
    assert weather_fields.field("clouds.preset").kind == weather_fields.KIND_PICKER
    assert weather_fields.field("halo.crystalsPreset").kind == weather_fields.KIND_PICKER
    # the halo preset is a LIST (the node's own entries all carry an empty thumbnail).
    assert weather_fields.field("halo.preset").kind == weather_fields.KIND_SELECT


def test_iprecptns_is_a_select_and_never_a_slider():
    """``clouds.iprecptns`` is an int the REST API reads as 0..3 — a SELECT, not a slider."""
    entry = weather_fields.field("clouds.iprecptns")
    assert entry.kind == weather_fields.KIND_SELECT
    assert [value for value, _label in entry.choices] == ["0", "1", "2", "3"]
    assert entry.min is None and entry.max is None and entry.step is None


def test_dust_and_fog_are_one_either_or_choice():
    """DCS applies dust OR fog: ONE control picks which, so the pair can never both be set."""
    dust = weather_fields.field(weather_fields.HAZE_KEY)
    assert dust.kind == weather_fields.KIND_SELECT
    assert [value for value, _label in dust.choices] == ["off", "dust", "fog"]
    assert [value for value, _label in weather_fields.FOG_MODES] == ["auto", "manual"]
    assert weather_fields.MUTUALLY_EXCLUSIVE == (("enable_dust", "enable_fog"),)


def test_the_units_are_the_schemas_own():
    """qnh in mmHg, cloud base/thickness and fog in metres, wind in m/s — never the ME's feet/knots."""
    assert weather_fields.field("qnh").unit == "mmHg"
    assert weather_fields.field("clouds.base").unit == "metres"
    assert weather_fields.field("clouds.thickness").unit == "metres"
    for column in weather_fields.FOG_COLUMNS:
        if column.key in ("thickness", "visibility"):
            assert column.unit == "metres"
    for altitude in ("atGround", "at2000", "at8000"):
        assert weather_fields.field(f"wind.{altitude}.speed").unit == "m/s"
        assert weather_fields.field(f"wind.{altitude}.dir").unit == "degrees"


# --------------------------------------------------------------- 2. the moments, one set

#: the moments ``parse_moment`` accepts; the docstring listed every one but ``now``.
ACCEPTED_MOMENTS = ("now", "dawn", "sunrise", "morning", "noon", "evening", "sunset", "dusk", "night")


def _moment_mizfile(monkeypatch):
    """A bare ``MizFile`` with a fixed theatre/date and the astral solar call stubbed out.

    ``parse_moment`` needs no node once ``sun``/``get_tz`` are stubbed and the theatre is seeded —
    that is what lets the offer be checked against the method's own behaviour.
    """
    import core.mizfile as mizfile

    fixed = {"dawn": datetime(2016, 4, 21, 5, 0), "sunrise": datetime(2016, 4, 21, 6, 0),
             "noon": datetime(2016, 4, 21, 12, 0), "sunset": datetime(2016, 4, 21, 18, 0),
             "dusk": datetime(2016, 4, 21, 19, 0)}
    monkeypatch.setattr(mizfile, "sun", lambda *args, **kwargs: dict(fixed))
    monkeypatch.setattr(mizfile, "get_tz", lambda longitude, latitude: "UTC")
    monkeypatch.setitem(mizfile.THEATRES, "Caucasus", (43.0, 40.0))
    miz = mizfile.MizFile.__new__(mizfile.MizFile)
    miz.mission = {"theatre": "Caucasus", "start_time": 0,
                   "date": {"Year": 2016, "Month": 4, "Day": 21}}
    return miz


def test_every_offered_moment_is_one_parse_moment_accepts(monkeypatch):
    """Every offered moment PARSES, and the historic nine names — ``now`` included — are offered.

    This fails on a list scraped from the docstring, which never mentions ``now``.
    """
    miz = _moment_mizfile(monkeypatch)
    offered = weather_fields.moments()
    for moment in offered:
        miz.parse_moment(moment)                      # must not raise
    for name in ACCEPTED_MOMENTS:
        assert name in offered, f"{name} is accepted by parse_moment but not offered"


def test_the_offer_equals_the_set_parse_moment_reports(monkeypatch):
    """The offer EQUALS the set ``parse_moment`` reports it accepts, so an added moment cannot be a
    silent omission and a removed one cannot be a silently dead pill."""
    miz = _moment_mizfile(monkeypatch)
    with pytest.raises(ValueError) as caught:
        miz.parse_moment("not-a-moment")
    _, _, listed = str(caught.value).partition("Valid events are")
    accepted = set(re.findall(r"'(\w+)'", listed))
    assert accepted == set(ACCEPTED_MOMENTS)          # the method's own contract, quoted
    assert set(weather_fields.moments()) == accepted


# ------------------------------------------------ 3. the table equals the RENDERED context

def test_the_rendered_context_carries_the_tables_numbers():
    """Every row's min/max/step EQUALS the table's — read from the context, not the HTML string."""
    rows = presets_page.wizard_rows(presets_page.WIZARD_KEYS, {})
    assert [row["key"] for row in rows] == [entry.key for entry in weather_fields.FIELDS]
    for row in rows:
        entry = weather_fields.field(row["key"])
        assert (row["min"], row["max"], row["step"]) == (entry.min, entry.max, entry.step), row["key"]
        assert row["unit"] == entry.unit, row["key"]
        assert row["source"] == entry.source, row["key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", STEPS)
async def test_every_control_renders_the_tables_min_max_step(config_dir, clean_registry, monkeypatch,
                                                             step):
    """The RENDERED context and the HTML agree with the table — a hand-typed number would fail here."""
    preset_file(config_dir)
    seen = capture_context(monkeypatch)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step={step}")

    assert seen["name"] == presets_page.PRESETS_WIZARD_TEMPLATE
    context = seen["context"]
    assert context["step"] == step
    for row in context["rows"]:
        entry = weather_fields.field(row["key"])
        # the English render states a DISTANCE in feet; every other control keeps the table's unit.
        low, high, step = weather_fields.display_bounds(entry)
        assert (row["min"], row["max"], row["step"]) == (low, high, step), row["key"]
        assert row["box_step"] == entry.box_step, row["key"]
    # the HTML the template produced carries the same NUMBERS on EVERY named control of the row — the
    # speed's number box and, on a wind row, the direction dial's own box. A named box carries the
    # table's ``box_step``, never the slider's ``step``: the slider granularity rejects typed values.
    by_key = {name: (mn, mx, st) for name, mn, mx, st in control_min_max_step(html)}
    for row in context["rows"]:
        entry = weather_fields.field(row["key"])
        if entry.kind in (weather_fields.KIND_SLIDER, weather_fields.KIND_NUMBER,
                          weather_fields.KIND_DIAL):
            low, high, _step = weather_fields.display_bounds(entry)
            assert by_key[row["key"]] == (str(low), str(high), entry.box_step), row["key"]
        if row.get("dial"):
            dial = weather_fields.field(row["dial"]["key"])
            assert by_key[row["dial"]["key"]] == (str(dial.min), str(dial.max), dial.box_step), \
                row["dial"]["key"]


# --------------------------------------------------------------- 4. one control right edge

def _split_tracks(template: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in template:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == " " and depth == 0:
            if current:
                parts.append(current)
                current = ""
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def _control_right_edge(template: str, container: float, gap: float) -> float:
    """The right edge (from the row's left) of the LAST track of *template* at *container* width.

    A tiny grid solver: a fixed track takes its size, ``minmax(a, b)`` takes ``b`` when it fits, and
    the ``fr`` tracks share what is left. Every field row uses the same template over the same
    container, so every control ends at the same x — which is what the assertion computes.
    """
    tracks = _split_tracks(template)
    content = container - gap * (len(tracks) - 1)
    fixed, fr_count = 0.0, 0
    for track in tracks:
        if "fr" in track:
            fr_count += 1
        elif track.startswith("minmax("):
            inner = track[len("minmax("):-1]
            _lo, hi = inner.split(",")
            fixed += float(hi.strip().rstrip("px"))
        else:
            fixed += float(track.rstrip("px"))
    remainder = content - fixed
    fr = remainder / fr_count if fr_count else 0.0
    return gap * (len(tracks) - 1) + fixed + fr * fr_count


def shipped_template(selector: str) -> str:
    """The ONE ``grid-template-columns`` a selector declares in the shipped presets.css."""
    css = (Path(presets_page.__file__).resolve().parent.parent / "static" / "presets.css").read_text(
        encoding="utf-8")
    blocks = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    templates = [m for block in blocks
                 for m in re.findall(r"grid-template-columns:\s*([^;]+);", block)]
    assert len(templates) == 1, f"{selector} must declare its template exactly once, got {templates}"
    return templates[0]


@pytest.mark.asyncio
async def test_every_field_row_shares_one_control_right_edge(config_dir, clean_registry, monkeypatch):
    """The COMPUTED geometry of every field row's control is one value — never read by eye."""
    preset_file(config_dir)
    seen = capture_context(monkeypatch)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind")

    template = shipped_template(".pw-f")
    rows = re.findall(r'<div class="pw-f">', html)
    # the rows are the GATED ones the step presents — a wind layer's speed and its dial share ONE row,
    # so the count is the context's own row list, never the step's full membership.
    assert len(rows) == len(seen["context"]["rows"])
    assert len(rows) >= 5
    # every row is the SAME row shape — no row carries a template of its own.
    assert not re.search(r'class="pw-f"[^>]*style="[^"]*grid-template', html)

    edges = {_control_right_edge(template, 719.0, 12.0) for _ in rows}
    assert len(edges) == 1, f"field rows do not share one right edge: {sorted(edges)}"
    # and the shared edge is FLUSH with the row's own right edge (the last track fills).
    assert edges.pop() == 719.0


# --------------------------------------------------------------- 5. the new-preset entry point

@pytest.mark.asyncio
async def test_new_preset_renders_the_wizard_empty_at_step_one(config_dir, clean_registry):
    """``GET /presets/new`` is the SAME wizard, EMPTY, step 1 first (which carries the name field)."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)

    assert re.search(r'<input[^>]*name="name"[^>]*value=""', html)
    assert 'href="' + presets_page.PRESETS_NEW_PATH + '?step=time"' in html
    # no value was invented for the weather controls
    assert re.search(r'<input[^>]*name="date"[^>]*value=""', html)


@pytest.mark.asyncio
async def test_one_preset_file_states_the_destination(config_dir, clean_registry):
    """Exactly one file: the destination is STATED, not offered."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert "config/presets.yaml" in html
    assert presets_page.WIZARD_DEST_ONE.replace("%(file)s", "config/presets.yaml") in html
    assert '<select name="file">' not in html


@pytest.mark.asyncio
async def test_several_preset_files_offer_a_real_choice(config_dir, clean_registry):
    """More than one file: the operator CHOOSES, and every file is shown."""
    preset_file(config_dir)
    preset_file(config_dir, "Summer:\n  temperature: 23\n", name="presets2.yaml")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert presets_page.WIZARD_DEST_MANY in html
    assert '<select name="file">' in html
    assert "config/presets.yaml" in html and "config/presets2.yaml" in html


@pytest.mark.asyncio
async def test_no_preset_file_says_there_is_nowhere_to_write(config_dir, clean_registry):
    """No file is a state: the wizard says so rather than inventing one."""
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert presets_page.WIZARD_DEST_NONE in html


@pytest.mark.asyncio
async def test_the_toolbar_links_to_the_new_preset_entry_point(config_dir, clean_registry):
    """The control S1 left out is drawn here, pointing at the wizard's own route."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_PATH)
    # the default tab is the install's first preset file, and the control carries it as the destination
    assert re.search(rf'<a class="ps-new" href="'
                     rf'{re.escape(presets_page.new_preset_url("config/presets.yaml"))}"', html)


# --------------------------------------------------------------- 6. an existing preset

@pytest.mark.asyncio
async def test_the_wizard_opens_a_presets_own_values(config_dir, clean_registry):
    """The wizard reads the preset's own map and renders it as the controls' values."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=time")
    assert re.search(r'<input[^>]*name="date"[^>]*value="2016-04-21"', html)
    assert re.search(r'<input[^>]*name="name"[^>]*value="Spring"', html)


@pytest.mark.asyncio
async def test_the_expert_dsl_preset_is_not_openable(config_dir, clean_registry):
    """A ``modify`` preset is listed but the wizard refuses to open it."""
    preset_file(config_dir, OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(presets_page.wizard_url("Carrier Freqs"))
    assert response.status_code == 404


# --------------------------------------------------------------- 7. the clouds step

@pytest.mark.asyncio
async def test_the_clouds_step_reads_the_nodes_own_plates(config_dir, clean_registry, tmp_path):
    """The plates come from the node's DCS installation (S2), each served from its own read route."""
    preset_file(config_dir)
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}")
    assert presets_page.cloud_image_path(node.name, "Preset1") in html
    assert re.search(r'<img[^>]*src="[^"]*Preset1[^"]*\.png"', html)


@pytest.mark.asyncio
async def test_the_clouds_step_falls_back_to_free_text_when_the_list_cannot_be_read(config_dir,
                                                                                   clean_registry,
                                                                                   tmp_path):
    """An unreadable list NEVER refuses the step: the plates are dropped and a free-text box remains."""
    preset_file(config_dir)
    node = CloudsNode(installation=tmp_path)          # no clouds.lua written
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}")
    assert rendered(presets_page.PRESETS_NO_CLOUD_LIST.format(
        node=node.name,
        path=presets_page.dcs_install.installation_file(
            node, presets_page.dcs_clouds.CLOUDS_LUA_RELATIVE_PATH))) in html
    assert re.search(r'<input[^>]*name="clouds\.preset"', html)


# --------------------------------------------------------------- 8. the review step

@pytest.mark.asyncio
async def test_the_review_dumps_the_document_and_leaves_removal_to_the_presets_page(config_dir,
                                                                                     clean_registry):
    """The produced YAML is the document dumped by the ONE config writer.

    The removal rule is NOT stated here any more: the wizard's review step carries no remove control,
    and the sentence that explains the refusal rides the Presets page's own confirm surface.
    """
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert rendered(presets_page.PRESETS_REMOVAL_SENTENCE) not in html
    # the produced YAML is a dumped document, not retyped: it carries the preset's own key block.
    assert "Spring:" in html
    assert "start_time:" in html


def test_the_produced_yaml_is_dumped_through_configwrite(tmp_path):
    """The dump goes through ``configwrite.dump_config`` — asserted on a spy, not on the string.

    The dumped document carries EXACTLY ONE top-level key: the preset the wizard is editing. The file
    the preset is written into is the SAVE's business, never the review's, so nothing here names it.
    """
    from core.utils import configwrite

    seen: list = []
    real = configwrite.dump_config

    def spy(document):
        seen.append(document)
        return real(document)

    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values.update({"date": "2016-04-21", "clouds": "preset", "clouds.preset": "Preset2"})
    import services.webservice.pages.presets as module
    original = module.configwrite.dump_config
    module.configwrite.dump_config = spy
    try:
        text = presets_page.produced_yaml("Spring", values)
    finally:
        module.configwrite.dump_config = original
    assert seen, "the produced YAML was not dumped through configwrite"
    assert list(seen[0]) == ["Spring"], "the review's document must carry the ONE edited preset"
    assert seen[0]["Spring"]["clouds"] == "Preset2"
    assert "Spring:" in text


#: the review's own YAML panel and the expert editor's carrier, as the template renders them. They
#: carry the SHARED editor's documented contract (`data-cfg-yaml-*`), the same attributes the settings
#: pages ship, so `static/settings-yaml.js` drives both.
_REVIEW_YAML = re.compile(r'<pre[^>]*data-cfg-yaml-readonly[^>]*>(.*?)</pre>', re.S)
_YAML_EDITOR = re.compile(r'<textarea[^>]*name="_yaml"[^>]*data-cfg-yaml-text[^>]*>(.*?)</textarea>', re.S)


def review_yaml(html: str) -> str:
    """The review's produced YAML, as the page rendered it."""
    match = _REVIEW_YAML.search(html)
    assert match, "the review step rendered no produced YAML"
    return html_module.unescape(match.group(1))


@pytest.mark.asyncio
async def test_the_review_yaml_carries_exactly_the_one_edited_preset(config_dir, clean_registry):
    """The review shows the ONE preset this wizard edited — not the file it will be merged into."""
    from core.utils import configwrite

    preset_file(config_dir, SPRING + OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    text = review_yaml(html)
    document = configwrite.load_config_text(text)
    assert list(document) == ["Spring"], "the review's document must carry one top-level key"
    # the SAME file's other presets are absent from the review's document...
    assert "Casual Weather" not in text
    assert "Carrier Freqs" not in text
    # ...and the file on disk is untouched by the render.
    assert "Casual Weather" in (config_dir / "presets.yaml").read_text("utf-8")


@pytest.mark.asyncio
async def test_the_review_does_not_print_the_source_panel(config_dir, clean_registry):
    """The "Where each number comes from" panel is gone; the field table keeps its own ``source``."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        review = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    assert "Where each number comes from" not in review
    assert "source_note" not in review
    # the `source` stays the table's own data, and a field row still carries it.
    for entry in weather_fields.FIELDS:
        row = presets_page.wizard_rows((entry.key,), {entry.key: ""})[0]
        assert "source" in row and row["source"] == entry.source


@pytest.mark.asyncio
async def test_the_review_offers_an_expert_editor_prefilled_with_the_produced_yaml(config_dir,
                                                                                  clean_registry):
    """Expert mode on the review: the SHARED editor's contract, its hidden companion, and the SAME YAML.

    The panel ships the documented `[data-cfg-yaml]` contract `static/settings-yaml.js` reads, so the
    wizard and the settings pages drive ONE editor and ONE server check.
    """
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    # the panel, its validate URL, its status line and its badge — the shared contract.
    assert re.search(r'<section[^>]*\bdata-cfg-yaml\b[^>]*>', html)
    assert (f'data-validate-url="{presets_page.wizard_validate_url("Spring")}"' in html)
    assert re.search(r'<[^>]*data-cfg-yaml-status[^>]*>', html)
    assert re.search(r'<[^>]*data-cfg-yaml-badge[^>]*>', html)
    # the hidden companion the toggle follows, shipped OFF and BEFORE the visible checkbox.
    assert re.search(r'<input type="hidden" name="_expert" value="0" data-cfg-expert>', html)
    assert re.search(r'<input[^>]*type="checkbox"[^>]*name="_expert"[^>]*value="1"[^>]*'
                     r'data-cfg-expert-toggle', html)
    assert html.index('name="_expert" value="0"') < html.index('type="checkbox"')
    # the read-only panel carries the produced YAML, and the editor's own carrier is the SAME text.
    match = _YAML_EDITOR.search(html)
    assert match, "the review step rendered no expert editor"
    assert html_module.unescape(match.group(1)) == review_yaml(html), (
        "the expert editor must be pre-filled with the review's own produced YAML")
    # the editor host ships EMPTY and hidden, exactly as the settings pages ship it.
    assert re.search(r'<div class="cfg-editor" data-cfg-yaml-editor hidden></div>', html)


@pytest.mark.asyncio
async def test_the_review_loads_the_shared_yaml_editor(config_dir, clean_registry):
    """The SAME vendored CodeMirror and the SAME shared script the settings pages load."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    static = Path(presets_page.__file__).resolve().parents[1] / "static"
    for asset in ("vendor/codemirror/codemirror.min.js", "vendor/codemirror/mode/yaml.min.js",
                  "vendor/codemirror/addon/hint/show-hint.js",
                  "vendor/codemirror/addon/hint/show-hint.css", "settings-yaml.js"):
        assert (static / asset).is_file(), f"the editor asset {asset} is not shipped"
    for tag in ('<link rel="stylesheet" href="/static/vendor/codemirror/codemirror.min.css?v=',
                '<script src="/static/vendor/codemirror/codemirror.min.js?v=',
                '<script src="/static/vendor/codemirror/mode/yaml.min.js?v=',
                '<link rel="stylesheet" href="/static/vendor/codemirror/addon/hint/show-hint.css?v=',
                '<script src="/static/vendor/codemirror/addon/hint/show-hint.js?v=',
                '<script src="/static/settings-yaml.js?v='):
        assert tag in html, f"the review step does not load {tag}"
    # the editor script is loaded AFTER the editor it builds (deferred scripts run in document order)
    assert html.index("codemirror.min.js") < html.index("settings-yaml.js")
    # and after the popup addon that script calls
    assert html.index("addon/hint/show-hint.js") < html.index("settings-yaml.js")


@pytest.mark.asyncio
async def test_the_expert_toggle_is_the_shared_plain_checkbox(config_dir, clean_registry):
    """The toggle is the OTHER editors' plain checkbox, not a drawn slider; the no-script swap remains.

    `presets.css` no longer paints a track (`appearance: none` + a knob): the toggle reuses the
    settings pages' own `.cfg-expert input` rule from `shell.css`. The panel swap below it stays as
    the no-script fallback and yields to the shared editor's host when that is shown.
    """
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
    # the label is the settings pages' own `.cfg-expert`, and no drawn switch survives.
    assert re.search(r'<label class="cfg-expert"><input[^>]*data-cfg-expert-toggle', html)
    css = (Path(presets_page.__file__).resolve().parents[1] / "static"
           / presets_page.PRESETS_STYLE).read_text("utf-8")
    assert ".pw-expert-switch" not in css, "the drawn slider must be gone"
    assert ".pw-expert " not in css, "the wizard must use the shared `.cfg-expert` label"
    # the plain checkbox rule is the SHARED one, in shell.css.
    shell = (Path(presets_page.__file__).resolve().parents[1] / "static" / "shell.css").read_text("utf-8")
    assert ".cfg-expert input { width: 15px; height: 15px;" in shell
    # the no-script swap: ticking reveals the plain editor and hides the read-only panel; the shared
    # editor's host being visible hides the fallback editor so the two never overlap.
    assert ".pw-yaml-edit { display: none;" in css
    assert (".pw-yaml-card:has([data-cfg-expert-toggle]:checked) .pw-yaml-edit { display: block; }"
            in css)
    assert (".pw-yaml-card:has([data-cfg-expert-toggle]:checked) .pw-yaml-readonly { display: none; }"
            in css)
    assert ".pw-yaml-card:has([data-cfg-yaml-editor]:not([hidden])) .pw-yaml-edit { display: none; }" in css


# --------------------------------------------------------------- 9. no write

@pytest.mark.asyncio
async def test_the_wizard_writes_nothing(config_dir, clean_registry):
    """Every GET leaves the preset file byte-identical — there is no write in this slice."""
    path = preset_file(config_dir)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await page(http, presets_page.PRESETS_NEW_PATH)
        for step in STEPS:
            await page(http, f"{presets_page.wizard_url('Spring')}?step={step}")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(config_dir.glob("*.bak"))


# --------------------------------------------------- 10. the fog TABLE
#
# The mission's fog is ONE table keyed by seconds from the mission's start, each row
# ``{thickness, visibility}``. Mode ``auto`` is DCS's own build with NO rows; ``manual`` draws the rows.
# Adding and removing a row are SUBMIT buttons on the step's own GET form, so the table is edited
# WITHOUT script — the opened page is what proves it, not the string in the source.

FOG_ROWS = "0:100:500, 300:200:2000"


def fog_table_block(html: str) -> str:
    """The rendered fog table block, or ``''`` when the step drew none."""
    match = re.search(r'<div class="pw-ftable">.*?</div>\s*</div>\s*\n', html, re.S)
    return match.group(0) if match else ""


@pytest.mark.asyncio
async def test_the_fog_table_draws_one_row_per_time_keyframe(config_dir, clean_registry):
    """REQUIRED OUTCOME — the fog is ONE table of (time, thickness, visibility) rows, with a row the
    mission already carries shown as its own row and the 0 s row tagged in words."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}"
                                f"?step=wind&{weather_fields.HAZE_KEY}=fog&fog.mode=manual"
                                f"&fog.rows={FOG_ROWS}")

    block = fog_table_block(html)
    assert block, "the manual fog mode must draw the table"
    # the stored rows are metres; the English table states the two distance cells in feet.
    for index, (time, thickness, visibility) in enumerate([("0", "328", "1640"),
                                                           ("300", "656", "6562")]):
        for cell, value in (("time", time), ("thickness", thickness), ("visibility", visibility)):
            assert re.search(
                rf'<input type="number" name="fog_row_{index}_{cell}" value="{value}"', block), \
                f"row {index}'s {cell} cell"
    # the columns carry the table's own labels/units, and the 0 s row says what that time IS.
    for column in weather_fields.FOG_COLUMNS:
        assert column.label in block and column.unit in block and f'name="fog_row_0_{column.key}"' in block
    assert presets_page.KEYFRAME_START_LABEL in block, "the 0 s row must name its own time"
    assert f'name="{presets_page.FOG_ADD_FIELD}"' in block, "the way to ADD a row"
    assert f'name="{presets_page.FOG_REMOVE_FIELD}"' in block, "the way to REMOVE a row"


@pytest.mark.asyncio
async def test_the_fog_table_is_absent_while_the_fog_is_automatic(config_dir, clean_registry):
    """``auto`` is DCS's own build and carries NO rows: the table is not offered at all."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        manual = await page(http, f"{presets_page.wizard_url('Spring')}"
                                  f"?step=wind&{weather_fields.HAZE_KEY}=fog&fog.mode=manual")
        auto = await page(http, f"{presets_page.wizard_url('Spring')}"
                                f"?step=wind&{weather_fields.HAZE_KEY}=fog&fog.mode=auto")
        off = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind&enable_dust=off")

    assert fog_table_block(manual), "manual draws the table (with no rows it still offers Add)"
    assert f'name="{presets_page.FOG_ADD_FIELD}"' in manual
    assert fog_table_block(auto) == "", "auto must draw no table"
    assert presets_page.FOG_ADD_FIELD not in auto
    assert fog_table_block(off) == "" and presets_page.FOG_ADD_FIELD not in off


def test_the_fog_rows_value_round_trips_through_the_tables_own_spelling():
    """The row text is composed and read in ONE place, so the page and the write cannot disagree."""
    rows = presets_page.parse_fog_rows(FOG_ROWS)
    assert rows == [("0", "100", "500"), ("300", "200", "2000")]
    assert presets_page.parse_fog_rows(presets_page.serialize_fog_rows(rows)) == rows
    # a row that names no cell at all is dropped; a cell left empty stays empty.
    assert presets_page.parse_fog_rows(",, 60:,") == [("60", "", "")]
    assert presets_page.serialize_fog_rows([("60", "", "")]) == "60::"


def test_a_fog_map_without_a_mode_is_read_as_manual_so_its_rows_survive():
    """A fog map carrying only time-keyed rows opens as ``manual``, so the save keeps those rows."""
    values = presets_page.preset_values("Spring", {"fog": {"300": {"thickness": 200}}}, {})
    assert values[weather_fields.FOG_MODE_KEY] == "manual"
    assert values[weather_fields.FOG_ROWS_KEY] == "300:200:"


# --------------------------------------------------- 11. the slider pair, both directions

SERVICES = Path(presets_page.__file__).resolve().parent.parent
WEATHER_SLIDER_JS = SERVICES / "static" / "weather-slider.js"
HARNESS = Path(__file__).resolve().parent / "weather_slider_harness.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: the shipped script cannot be run")


def slider_pair(html: str, key: str) -> tuple[str, str, str]:
    """The slider row named by *key*: its ``(range tag, number tag, readout text)``.

    The range carries no ``name`` and never submits; the number box is the row's control; the readout
    is the range's own value. A row that draws no pair fails loudly rather than returning nothing.
    """
    for block in re.findall(r'<div class="pw-sl" data-weather-pair>(.*?)</div>', html, re.S):
        num = re.search(r'<input type="number"[^>]*>', block)
        if not num or _attr(num.group(0), "name") != key:
            continue
        rng = re.search(r"<input type=\"range\"[^>]*>", block)
        out = re.search(r'<output[^>]*data-weather-readout[^>]*>(.*?)</output>', block, re.S)
        assert rng is not None, f"the '{key}' pair carries no range"
        return rng.group(0), num.group(0), (out.group(1) if out else "")
    raise AssertionError(f"no slider pair for '{key}' in the rendered page")


@pytest.mark.asyncio
async def test_a_slider_row_shows_its_value_in_both_controls_and_a_blank_row_shows_none(
        config_dir, clean_registry):
    """ACCEPTANCE 1 + 3 — a row carrying a value shows it in BOTH controls; a row carrying none is
    blank in both. The wizard builds its rows from the edited preset's own values, so the value is the
    preset's (``clouds.base: 2000`` in the fixture)."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        with_value = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")
        blank = await page(http, f"{presets_page.PRESETS_NEW_PATH}?step=clouds&clouds=dynamic")

    rng, num, readout = slider_pair(with_value, "clouds.base")
    # 2000 m is what the preset stores; the English render states it in the Mission Editor's feet.
    assert _attr(rng, "value") == "6562", "the range must carry the preset's own value"
    assert _attr(num, "value") == "6562", "and so must the number box beside it"
    assert readout == "6562", "and the slider's own readout shows it"

    rng, num, readout = slider_pair(blank, "clouds.base")
    assert _attr(rng, "value") == "", "a row with no value is blank in the range"
    assert _attr(num, "value") == "", "and blank in the number box"
    assert readout == "", "and the readout is blank too"


@pytest.mark.asyncio
async def test_with_javascript_off_the_number_box_stays_the_submitted_control(config_dir,
                                                                             clean_registry):
    """ACCEPTANCE 4 — the number box is named, carries the value, and is bounded by the SAME
    min/max/step the field table states; the range is never a submitted control (it carries no name).
    """
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")

    rng, num, _readout = slider_pair(html, "clouds.base")
    entry = weather_fields.field("clouds.base")
    low, high, _step = weather_fields.display_bounds(entry)
    assert 'name="clouds.base"' in num, "the number box is the control the form posts"
    assert _attr(num, "value") == "6562"
    assert (_attr(num, "min"), _attr(num, "max")) == (str(low), str(high)), \
        "the box keeps the table's own bounds, in the unit this render states"
    # THE STEP IS NOT THE SLIDER'S: the box carries the table's ``box_step`` and the range keeps the
    # slider granularity — a granularity on a box refuses a value a person types.
    assert _attr(num, "step") == entry.box_step, "the box carries its own step"
    assert _attr(rng, "step") == str(_step), "the range keeps the slider's granularity"
    assert "name=" not in rng, "the range must never submit a field of its own"


@pytest.mark.asyncio
async def test_the_wizard_loads_the_one_shared_slider_asset(config_dir, clean_registry):
    """CONSTRAINT 1 — the pair's behaviour is ONE shipped asset, loaded by this page; the launch
    dialog loads the SAME file (``test_webui_startup_temporary_preset``)."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")
    assert '<script src="/static/weather-slider.js" defer></script>' in html
    assert WEATHER_SLIDER_JS.is_file(), "the asset the page references is not shipped"


@requires_node
def test_the_slider_sets_the_box_and_the_box_moves_the_slider():
    """ACCEPTANCE 2 — the SHIPPED ``weather-slider.js`` driven through a browser-less DOM stub
    (``weather_slider_harness.js``). The harness loads the real file and fires the same ``input``
    events a browser would; it is the only check here that exercises the behaviour rather than the
    markup, and it is run where ``node`` exists (skipped, with a reason, where it does not)."""
    from conftest import run_node_harness

    assert HARNESS.is_file(), "the harness that drives the shipped script is missing"
    done = run_node_harness(["node", str(HARNESS), str(WEATHER_SLIDER_JS)])
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)

    filled = seen["filled"]
    assert filled["rendered"] == {"rangeValue": "2000", "numberValue": "2000", "readout": "2000"}, \
        "a pair with a value shows it in both controls on arrival"
    # DRAG the slider: the number box takes its value and the readout follows
    assert filled["afterSlide"]["numberValue"] == "1500", "dragging the slider must set the box"
    assert filled["afterSlide"]["readout"] == "1500", "and the slider's readout follows it"
    # TYPE in the box: the slider moves to it
    assert filled["afterType"]["rangeValue"] == "1800", "typing must move the slider"
    assert filled["afterType"]["readout"] == "1800"

    # a pair with NO value is blank, and the moment the slider moves it gets one
    blank = seen["blank"]
    assert blank["rendered"]["numberValue"] == "" and blank["rendered"]["readout"] == ""
    assert blank["afterSlide"]["numberValue"] == "4", "a drag gives a blank pair a value at once"
    assert blank["afterSlide"]["readout"] == "4"

    # the browser's own range rules, pinned: out-of-range clamps, a non-number leaves the range
    assert seen["clamped"]["rangeValue"] == "359", "typing above max clamps the slider to max"
    assert seen["nonNumeric"]["readout"] == "", "a non-number blanks the readout"
    assert seen["emptied"]["readout"] == "", "emptying the box blanks the readout again"

    # THE DIRECTION DIAL: the SAME pair drawn round — the container's `--angle` follows the value,
    # and the clamp/empty rules are the pair's own.
    dial = seen["dial"]
    assert abs(float(dial["rendered"]) - 220 / 359) < 1e-6, "the dial's angle is the value's place"
    assert abs(float(dial["afterSlide"]) - 90 / 359) < 1e-6, "a drag moves the needle"
    assert float(dial["afterClamp"]) == 1.0, "typing above max clamps the needle to the end"
    assert float(dial["afterEmpty"]) == 1.0, "an empty box leaves the range (and the needle) be"

    # THE TIME HELPER: it fills the field it names, and a named moment leaves it blank.
    time = seen["time"]
    assert time["renderedNamed"] == "", "a named moment is not a time of day"
    assert time["afterHelper"] == "06:30", "typing in the helper fills the text box"
    assert time["renderedClock"] == "06:30", "a plain HH:MM fills the helper on arrival"
    assert time["afterMoment"] == "", "a moment typed in the box blanks the helper"
    assert time["afterBack"] == "08:15", "and a plain HH:MM brings it back"


# =================================================================================================
# The control rework, item by item (the numbering is the card's own).
# =================================================================================================


def nav_urls(html: str) -> list[str]:
    """The sidebar's own links, in the order the rail renders them: group by group."""
    nav = re.search(r'<nav class="nav">.*?</nav>', html, re.S)
    assert nav is not None, "the shell rendered no nav"
    return re.findall(r'<a class="[^"]*" href="([^"]*)"', nav.group(0))


def field_rows(html: str) -> list[str]:
    """The rendered field-row blocks, in document order."""
    return re.findall(r'<div class="pw-f">.*?(?=<div class="pw-f">|</form>)', html, re.S)


def named(tag: str, name: str) -> str:
    """The tag carrying ``name="<name>"``, or ``''`` when the row draws none."""
    match = re.search(rf'[^<]*\bname="{re.escape(name)}"[^>]*>', tag)
    return match.group(0) if match else ""


# ------------------------------------------------------------------------------- 1. the menu order

@pytest.mark.asyncio
async def test_the_menu_puts_presets_before_settings(config_dir, clean_registry):
    """ITEM 1 — the main menu puts PRESETS before Settings — and the wizard's own page is where it is
    read."""
    preset_file(config_dir)
    from core import actions as actions_module
    actions_module._ACTIONS.setdefault("get_music_radios", lambda *args, **kwargs: None)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, presets_page.PRESETS_NEW_PATH)
    assert nav_urls(html) == ["/", "/fleet", "/players", "/bans", "/music", "/nodes", "/audit",
                              "/statistics", "/presets", "/access", "/settings", "/settings/site"]


# --------------------------------------------------------------------------- 2. temperature first

@pytest.mark.asyncio
async def test_temperature_is_the_first_control_of_the_wind_step(config_dir, clean_registry):
    """ITEM 2 — temperature leads the air/wind step, ahead of the wind layers, in the table's order and
    in the rendered step."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind")

    order = [entry.key for entry in weather_fields.FIELDS]
    assert order.index("temperature") < order.index("wind.atGround.speed"), "the table's own order"
    keys = re.findall(r'<span class="kk">([^<]+)</span>', html)
    assert keys[0] == "temperature", keys[:3]
    assert keys.index("temperature") < keys.index("wind.atGround.speed")
    # and the step's own list agrees with the table
    step = presets_page.STEP_FIELDS[presets_page.WIZARD_STEP_WIND]
    assert step[0] == "temperature" and step.index("temperature") < step.index("wind.atGround.speed")


# ------------------------------------------------------------------- 3. date picker / time helper

@pytest.mark.asyncio
async def test_the_mission_date_gets_a_real_picker_and_keeps_its_day_shortcuts(config_dir,
                                                                               clean_registry):
    """ITEM 3 — a real date picker for the mission date; the day shortcuts stay, and a stored NAMED day
    (which a picker cannot hold) keeps the text box rather than being blanked."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=time")
        token = await page(http, f"{presets_page.wizard_url('Spring')}?step=time&date=today")

    assert re.search(r'<input[^>]*type="date"[^>]*name="date"[^>]*value="2016-04-21"', html), \
        "the mission date is the picker's own value"
    for day in ("today", "yesterday", "tomorrow"):
        assert f"&date={day}" in html, f"the '{day}' shortcut"
    # THE MINUTE THE VALUE IS A NAMED DAY: the picker cannot show it, so the box holds it instead.
    assert re.search(r'<input[^>]*type="text"[^>]*name="date"[^>]*value="today"', token)
    assert 'type="date"' not in token


@pytest.mark.asyncio
async def test_the_start_time_keeps_the_named_moments_and_gains_a_time_helper(config_dir,
                                                                             clean_registry):
    """ITEM 3 — the named moments STAY, and a time helper is ADDED beside them. The helper carries no
    name: the text box is the control the form posts, so the step is complete with no script."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=time")

    for moment in ("dawn", "morning", "night"):
        assert f"&start_time={moment}" in html, f"the '{moment}' moment stays"
    helper = re.search(r'<input class="pw-time"[^>]*>', html)
    assert helper is not None, "the time helper"
    assert 'type="time"' in helper.group(0)
    assert f'data-weather-time="{weather_fields.field("start_time").key}"' in helper.group(0)
    assert "name=" not in helper.group(0), "the helper posts nothing of its own"
    assert re.search(r'<input[^>]*name="start_time"', html), "the text box is the submitted control"


# ----------------------------------------------------------- 4. the cloud mode gates the values

def test_the_cloud_mode_gates_which_value_controls_are_offered():
    """ITEM 4 — the mode DECIDES the value controls. DCS preset: the pictures and the base. dynamic:
    the four values. clear: none. An unset mode offers none either — the choice is what gates them."""
    expected = {
        "preset": ("clouds.preset", "clouds.base"),
        "dynamic": ("clouds.density", "clouds.base", "clouds.thickness", "clouds.iprecptns"),
        "clear": (),
        "": (),
    }
    for mode, want in expected.items():
        keys = presets_page.step_controls(presets_page.WIZARD_STEP_CLOUDS, {"clouds": mode})
        assert tuple(key for key in keys if key.startswith("clouds.")) == want, mode
    assert "clouds" in presets_page.step_controls(presets_page.WIZARD_STEP_CLOUDS, {})


@pytest.mark.asyncio
async def test_the_cloud_mode_gates_the_rendered_cloud_controls(config_dir, clean_registry):
    """ITEM 4 — the same gating on the RENDERED page: no density/thickness/precipitation control in
    DCS-preset mode, none at all in clear."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        base = presets_page.wizard_url("Spring")
        preset = await page(http, f"{base}?step=clouds&clouds=preset")
        dynamic = await page(http, f"{base}?step=clouds&clouds=dynamic")
        clear = await page(http, f"{base}?step=clouds&clouds=clear")

    rows = lambda html: [named(block, key) for block in field_rows(html) for key in
                         ("clouds.density", "clouds.thickness", "clouds.iprecptns")]
    assert not any(rows(preset)), "the DCS-preset mode offers no density/thickness/precipitation"
    for key in ("clouds.density", "clouds.base", "clouds.thickness", "clouds.iprecptns"):
        assert re.search(rf'name="{re.escape(key)}"', dynamic), key
    assert not any(rows(clear)), "the clear mode offers no cloud value control"


@pytest.mark.asyncio
async def test_the_cloud_picture_writes_the_key_and_prefills_the_base(config_dir, clean_registry,
                                                                     tmp_path):
    """ITEM 4 — clicking a picture IS the choice: the plate writes ``clouds.preset`` itself (there is NO
    overwritable box beside it) and carries the chosen preset's own lowest altitude into Base."""
    preset_file(config_dir)
    install_clouds(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}")

    fixture = (tmp_path / presets_page.dcs_clouds.CLOUDS_LUA_RELATIVE_PATH).read_text(encoding="utf-8")
    presets = presets_page.dcs_clouds.visible_presets(presets_page.dcs_clouds.parse_cloud_presets(fixture))
    chosen = next(preset for preset in presets if preset.name == "Preset1")
    assert chosen.base is not None, "the read layer's own altitude figure"
    href = html_module.unescape(
        re.search(r'<a class="pw-plate[^"]*"[^>]*\bhref="([^"]*Preset1[^"]*)"', html).group(1))
    assert "clouds.preset=Preset1" in href, "the picture writes the key itself"
    # the prefill is the preset's own base, stated in the unit the box is drawn in.
    assert f"clouds.base={int(weather_fields.display_value('clouds.base', chosen.base))}" in href
    assert "clouds=preset" in href, "clicking a picture IS the DCS-preset mode"
    # NO overwritable box beside the pictures: the key has no text control in this mode.
    assert not re.search(r'<input[^>]*type="text"[^>]*name="clouds\.preset"', html)
    # and a plate whose picture cannot be read still shows the entry's LABEL (the alt and the name).
    assert re.search(r'<img src="[^"]*Preset1[^"]*\.png" alt="[^"]*"[^>]*>', html)
    assert re.search(r'<span class="nm">[^<]+</span>', html)


# ---------------------------------------------------- 5. dust is a Visibility control

def test_the_dust_control_is_labelled_visibility_and_stores_metres():
    """ITEM 5 — the field is ``dust_density`` and what the number IS is the visibility, in metres."""
    entry = weather_fields.field("dust_density")
    assert entry.label == "Dust visibility"
    assert entry.kind == weather_fields.KIND_SLIDER
    assert (entry.min, entry.max, entry.unit) == (300, 20000, "metres")
    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values[weather_fields.HAZE_KEY] = "dust"
    values["dust_density"] = "3048"
    assert presets_page.preset_map_from_values(values)["dust_density"] == 3048


@pytest.mark.asyncio
async def test_the_dust_row_is_drawn_as_a_visibility(config_dir, clean_registry):
    """ITEM 5 — the rendered row says Visibility and writes ``dust_density``, in the render's unit."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind"
                                f"&{weather_fields.HAZE_KEY}=dust")
    block = next(block for block in field_rows(html) if 'name="dust_density"' in block)
    assert 'name="dust_density"' in block and "Dust visibility" in block
    # the English render states the distance in the Mission Editor's feet, at the ME's own bounds.
    assert "feet" in block and 'min="984"' in block and 'max="65617"' in block
    # an empty box on a range that starts at 984 would read as 0 and jump there on the first nudge:
    # the control states its own floor instead.
    assert re.search(r'name="dust_density"[^>]*value="984"', block), block
    # dust carries no thickness: no thickness control belongs to the dust switch.
    assert "thickness" not in block


# --------------------------------------------------- 6. the either-or control keeps the pair exclusive

def test_the_either_or_control_writes_one_side_only():
    """ITEM 6 — one value posts, and the write derives BOTH keys from it: the pair is exclusive by
    construction, so a mission can never carry dust and fog at once."""
    dust = weather_fields.field(weather_fields.HAZE_KEY)
    assert dust.kind == weather_fields.KIND_SELECT
    assert [value for value, _label in dust.choices] == ["off", "dust", "fog"]
    assert weather_fields.field(weather_fields.FOG_MODE_KEY).kind == weather_fields.KIND_SELECT
    assert [value for value, _label in weather_fields.FOG_MODES] == ["auto", "manual"]
    # ``enable_fog`` is written by the either-or control, never posted as a field of its own.
    assert "enable_fog" not in presets_page.WIZARD_KEYS
    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values[weather_fields.HAZE_KEY] = "dust"
    values["dust_density"] = "3048"
    dust_map = presets_page.preset_map_from_values(values)
    assert dust_map.get("enable_dust") is True and dust_map.get("dust_density") == 3048
    assert "enable_fog" not in dust_map and "fog" not in dust_map
    values[weather_fields.HAZE_KEY] = "fog"
    values[weather_fields.FOG_MODE_KEY] = "auto"
    fog_map = presets_page.preset_map_from_values(values)
    assert fog_map.get("enable_fog") is True and fog_map.get("fog") == {"mode": "auto"}
    assert "enable_dust" not in fog_map and "dust_density" not in fog_map


@pytest.mark.asyncio
async def test_the_wind_step_draws_the_either_or_control(config_dir, clean_registry):
    """ITEM 6 — the step offers ONE control naming both effects; the fog's own controls follow it."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind")
        fog = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind"
                               f"&{weather_fields.HAZE_KEY}=fog")

    assert html.count(f'name="{weather_fields.HAZE_KEY}"') == 1
    assert re.search(rf'<select class="pw-sel" name="{weather_fields.HAZE_KEY}"[^>]*>', html)
    for value, _label in weather_fields.DUST_OR_FOG:
        assert f'<option value="{value}"' in html, value
    # with neither chosen the fog SELECT is not offered at all (the key is only carried as a hidden
    # field); choosing fog draws it.
    assert not re.search(rf'<select[^>]*name="{weather_fields.FOG_MODE_KEY}"', html)
    assert re.search(rf'<select class="pw-sel" name="{weather_fields.FOG_MODE_KEY}"[^>]*>', fog)
    for value, _label in weather_fields.FOG_MODES:
        assert f'<option value="{value}"' in fog, value
    assert 'name="enable_fog"' not in html


# ------------------------------------------------------------------------- 8. QNH stays as it is

def test_qnh_is_untouched():
    """ITEM 8 — the table's own range already IS the ME's: 720–790 mmHg, default 760 = 29.92 inHg."""
    entry = weather_fields.field("qnh")
    assert (entry.min, entry.max, entry.step, entry.unit) == (720, 790, 1, "mmHg")


# ------------------------------------------------------------- 9. wind: speed left, dial right

@pytest.mark.asyncio
async def test_each_wind_layer_draws_the_speed_and_a_dial_in_one_row(config_dir, clean_registry):
    """ITEM 9 — one row per layer: the SPEED control on the left and a circular direction DIAL on the
    right, whose own number box posts the same field name and the table's own bounds."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=wind")

    blocks = field_rows(html)
    for layer in ("atGround", "at2000", "at8000"):
        speed, direction = f"wind.{layer}.speed", f"wind.{layer}.dir"
        block = next(block for block in blocks
                     if f'name="{speed}"' in block and f'name="{direction}"' in block)
        assert "pw-sl" in block and "pw-windct" in block, "the speed is the left track"
        dial = re.search(r'<div class="pw-dial"[^>]*>.*?</div>', block, re.S).group(0)
        assert "data-weather-dial" in dial and "data-weather-pair" in dial
        entry = weather_fields.field(direction)
        assert entry.kind == weather_fields.KIND_DIAL, "the direction is a dial, not a plain slider"
        num = named(dial, direction)
        assert num, "the dial's number box is the posted control"
        assert (_attr(num, "min"), _attr(num, "max"), _attr(num, "step")) == \
            (str(entry.min), str(entry.max), str(entry.step))
        ring = re.search(r'<input[^>]*class="pw-dial-ring"[^>]*>', dial).group(0)
        assert "name=" not in ring, "the dial's ring never submits a field of its own"
        assert "data-weather-readout" not in dial, "the dial needs no readout — it IS the readout"
    # the speed's own box is named and bounded by the table, exactly as before — its step is the
    # table's BOX step, never the slider's granularity.
    speed_box = named(next(block for block in blocks if 'name="wind.atGround.speed"' in block),
                      "wind.atGround.speed")
    entry = weather_fields.field("wind.atGround.speed")
    assert (_attr(speed_box, "min"), _attr(speed_box, "max"), _attr(speed_box, "step")) == \
        (str(entry.min), str(entry.max), entry.box_step)


# -------------------------------------------------------- 10. the halo, with conditional crystals

@pytest.mark.asyncio
async def test_the_halo_preset_lists_the_files_entries_and_the_crystals_are_conditional(
        config_dir, clean_registry, tmp_path):
    """ITEM 10 — the preset is a LIST of ``auto`` plus the node's own entries; the crystal control is
    the same plate grid the cloud presets use and is offered ONLY while the preset is not ``auto``."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        named_page = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds"
                                      f"&node={node.name}&halo.preset=AtmoHighClouds")
        auto_page = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds"
                                     f"&node={node.name}&halo.preset=auto")

    select = re.search(r'<select class="pw-sel" name="halo.preset"[^>]*>.*?</select>', named_page, re.S)
    assert select is not None, "the halo preset is a plain list"
    select = select.group(0)
    assert '<option value="auto">auto</option>' in select
    assert '<option value="off">off</option>' in select
    for entry in HALO_ENTRIES:
        assert f'<option value="{entry}"' in select, entry
    # the PRESET entries carry no picture: they are never served as plates.
    assert presets_page.halo_image_path(node.name, HALO_ENTRIES[0]) not in named_page
    # the CRYSTALS are the same plate grid, each from its own entry's real thumbnail.
    for entry in CRYSTAL_ENTRIES:
        assert presets_page.halo_image_path(node.name, entry) in named_page, entry
    assert named_page.count('class="pw-plate') >= len(CRYSTAL_ENTRIES)
    # and the crystal control is NOT offered while the halo is ``auto``.
    assert presets_page.halo_image_path(node.name, CRYSTAL_ENTRIES[0]) not in auto_page


@pytest.mark.asyncio
async def test_the_crystals_are_written_only_beside_a_named_preset():
    """ITEM 10 — the keys written are exactly ``halo.preset`` and ``halo.crystalsPreset``, and the
    crystals are not written beside ``auto``."""
    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values["halo.preset"] = "AtmoHighClouds"
    values["halo.crystalsPreset"] = "AllKinds"
    produced = presets_page.preset_map_from_values(values)
    assert produced["halo"] == {"preset": "AtmoHighClouds", "crystalsPreset": "AllKinds"}
    values["halo.preset"] = "auto"
    assert presets_page.preset_map_from_values(values)["halo"] == {"preset": "auto"}


# ------------------------------------------------------- 7. the fog table (add / remove, no script)

@pytest.mark.asyncio
async def test_the_fog_rows_add_and_remove_without_script(config_dir, clean_registry):
    """ITEM 7 — ADD and REMOVE are SUBMIT buttons on the step's own GET form: the click re-posts the
    typed cells and the step comes back with the row appended (or dropped)."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        base = (f"{presets_page.wizard_url('Spring')}?step=wind"
                f"&{weather_fields.HAZE_KEY}=fog&{weather_fields.FOG_MODE_KEY}=manual")
        cells = ("&fog_row_0_time=0&fog_row_0_thickness=100&fog_row_0_visibility=500"
                 "&fog_row_1_time=300&fog_row_1_thickness=200&fog_row_1_visibility=2000")
        added = await page(http, f"{base}{cells}&{presets_page.FOG_ADD_FIELD}=1")
        removed = await page(http, f"{base}{cells}&{presets_page.FOG_REMOVE_FIELD}=0")

    assert re.search(r'name="fog_row_2_time"', added), "ADD appends an empty row"
    assert re.search(r'name="fog_row_0_time" value="0"', added), "and keeps the typed cells"
    assert not re.search(r'name="fog_row_2_time"', removed), "REMOVE drops the row it names"
    assert re.search(r'name="fog_row_0_time" value="300"', removed), "and renumbers what is left"


# ------------------------------------------------------------------- the review names its own keys

@pytest.mark.asyncio
async def test_the_review_names_the_fog_key_and_has_no_fog_panel(config_dir, clean_registry):
    """The review prints REAL preset keys: the fog table is ``fog``, never the control's own key, and
    the panel that listed un-rendered rows is gone."""
    preset_file(config_dir, "Spring:\n  temperature: 8\n  enable_fog: true\n  fog:\n    mode: manual\n"
                            "    300:\n      thickness: 200\n")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")

    assert "<dt>fog</dt>" in html
    assert "<dt>fog.rows</dt>" not in html
    assert presets_page.WIZARD_FOG_KEEP_HEAD not in html
    # the table itself is what the review posts on, so the save gets the rows it drew.
    assert f'name="{weather_fields.FOG_ROWS_KEY}" value="300:200:"' in html


# ------------------------------------------------- 11. the launch dialog draws from this table

def test_the_temporary_presets_rows_are_this_tables_own():
    """ITEM 11 — the launch dialog's rows are this same table's, through the wizard's own row builder,
    so the new controls (the either-or choice, the dial, the fog table) follow there too."""
    from services.webservice.pages import actions as actions_page

    assert actions_page.TEMPORARY_KEYS == tuple(
        entry.key for entry in weather_fields.FIELDS if entry.key not in ("name", "terrains"))
    rows = presets_page.wizard_rows(actions_page.TEMPORARY_KEYS, {})
    assert [row["key"] for row in rows] == list(actions_page.TEMPORARY_KEYS)
    kinds = {row["key"]: row["kind"] for row in rows}
    assert kinds[weather_fields.HAZE_KEY] == weather_fields.KIND_SELECT
    assert kinds[weather_fields.FOG_ROWS_KEY] == weather_fields.KIND_FOGTABLE
    assert kinds["wind.atGround.dir"] == weather_fields.KIND_DIAL
    assert kinds["dust_density"] == weather_fields.KIND_SLIDER


# =================================================================================================
# The mode gates react at once, every long choice carries a tooltip, and the halo is a gated list.
# =================================================================================================

PRESETS_WIZARD_JS = SERVICES / "static" / "presets-wizard.js"
PRESETS_WIZARD_HARNESS = Path(__file__).resolve().parent / "presets_wizard_harness.js"

#: the mode controls the page marks: their change re-renders the step.
MODE_CONTROLS = ("clouds", weather_fields.HAZE_KEY, weather_fields.FOG_MODE_KEY, "halo.preset")


def mode_selects(html: str) -> list[str]:
    """Every select the page marked as a MODE control (a change re-submits the step)."""
    return re.findall(r'<select class="pw-sel"[^>]*\bdata-pw-mode\b[^>]*>', html)


def halo_select(html: str) -> str:
    """The halo control's rendered ``<select>…</select>`` block."""
    match = re.search(r'<select class="pw-sel" name="halo.preset"[^>]*>.*?</select>', html, re.S)
    assert match, "the halo control is a select"
    return match.group(0)


def text_plate_block(html: str) -> str:
    """The halo entry plates' block, or ``''`` when the step drew none."""
    match = re.search(r'<div class="pw-plates pw-plates-text">(.*?)</div>', html, re.S)
    return match.group(1) if match else ""


# ------------------------------------------------------------------- 12. the mode controls react

@pytest.mark.asyncio
async def test_the_mode_controls_are_marked_and_a_plain_value_select_is_not(config_dir, clean_registry,
                                                                           tmp_path):
    """ITEM 1 — every control that GATES a step's rows carries ``data-pw-mode`` (its change must
    re-submit the step); a plain value select (``clouds.iprecptns``) does not."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        clouds = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds"
                                  f"&node={node.name}&clouds=dynamic")
        wind = await page(http, f"{presets_page.wizard_url('Spring')}"
                                f"?step=wind&{weather_fields.HAZE_KEY}=fog"
                                f"&{weather_fields.FOG_MODE_KEY}=manual")

    for key in ("clouds", "halo.preset"):
        assert re.search(rf'<select class="pw-sel" name="{re.escape(key)}"[^>]*data-pw-mode', clouds), \
            key
    for key in (weather_fields.HAZE_KEY, weather_fields.FOG_MODE_KEY):
        assert re.search(rf'<select class="pw-sel" name="{re.escape(key)}"[^>]*data-pw-mode', wind), \
            key
    assert not re.search(r'<select class="pw-sel" name="clouds\.iprecptns"[^>]*data-pw-mode', clouds), \
        "a plain value select must not re-render the step"
    assert set(MODE_CONTROLS) >= {"clouds", weather_fields.HAZE_KEY, weather_fields.FOG_MODE_KEY}


@pytest.mark.asyncio
async def test_the_wizard_loads_the_mode_asset_and_it_exists(config_dir, clean_registry):
    """ITEM 1 — the behaviour is a shipped asset the page loads; the asset exists on disk."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")
    assert '<script src="/static/presets-wizard.js" defer></script>' in html
    assert PRESETS_WIZARD_JS.is_file(), "the asset the page references is not shipped"


@pytest.mark.asyncio
async def test_with_script_off_the_mode_controls_are_plain_form_controls(config_dir, clean_registry,
                                                                         tmp_path):
    """ITEM 1 — with JavaScript off the step is UNCHANGED: each mode control is a named control of the
    step's own GET form (so Next/Back submits it), and no inline script drives it."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}")

    assert re.search(r'<select class="pw-sel" name="halo.preset"[^>]*>', html), \
        "the mode control posts a field of its own, so a plain submit carries it"
    assert not re.search(r'<script(?![^>]*\bsrc=)[^>]*>', html), \
        "the step must carry no inline script — the mode is driven by the shipped asset only"


@requires_node
def test_a_mode_change_submits_the_step_and_nothing_else_does():
    """ITEM 1 — the SHIPPED ``presets-wizard.js`` driven through a browser-less DOM stub
    (``presets_wizard_harness.js``): a mode control submits its GET form, a plain value select does
    not, and a POST form (the review) is never submitted by it."""
    from conftest import run_node_harness

    assert PRESETS_WIZARD_HARNESS.is_file(), "the harness that drives the shipped script is missing"
    done = run_node_harness(["node", str(PRESETS_WIZARD_HARNESS), str(PRESETS_WIZARD_JS)])
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["bound"] == 1, "the script did not bind one delegated change listener"
    assert seen["modeSubmits"] is True, "a mode control's change must submit the step's form"
    assert seen["valueSubmits"] is False, "a plain value select must not re-render the step"
    assert seen["postSubmits"] is False, "the review's POST form must never be submitted on change"
    assert seen["bareTargetThrew"] is False, "a change target without `matches` must not raise"


# ------------------------------------------------------------- 13. every plate carries a tooltip

@pytest.mark.asyncio
async def test_every_plate_carries_its_full_label_as_a_tooltip(config_dir, clean_registry, tmp_path):
    """ITEM 2 — every plate/anchor the wizard draws carries the entry's FULL label in ``title``: the
    cloud presets, the halo choices and the crystal presets — and the title equals the visible text."""
    preset_file(config_dir)
    install_clouds(tmp_path)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds"
                                f"&node={node.name}&clouds=preset&halo.preset=AtmoHighClouds")

    anchors = re.findall(r'<a class="pw-plate[^"]*"[^>]*title="([^"]*)"[^>]*>(.*?)</a>', html, re.S)
    assert anchors, "the step drew no plate with a title"
    for title, body in anchors:
        name = re.search(r'<span class="nm">([^<]*)</span>', body)
        assert name and html_module.unescape(title) == html_module.unescape(name.group(1)), \
            f"a plate's title must be its full visible label: {title!r} vs {name and name.group(1)!r}"
    # the CLOUD preset's plate, the CRYSTAL preset's plate and the halo entry plates are all present.
    assert re.search(r'<a class="pw-plate[^"]*"[^>]*title="[^"]*"[^>]*>\s*<span class="p">'
                     r'<img[^>]*Preset1', html), "the cloud preset's plate carries no title"
    assert re.search(r'<a class="pw-plate[^"]*"[^>]*title="[^"]*"[^>]*>\s*<span class="p">'
                     r'<img[^>]*AllKinds', html), "the crystal preset's plate carries no title"
    assert 'title="Ice Halo on all mediums"' in html, "the halo entry's full label is not a tooltip"


# ------------------------------------------------------- 14. the halo is a gated list, not options

@pytest.mark.asyncio
async def test_the_halo_control_offers_off_auto_and_the_files_entries(config_dir, clean_registry,
                                                                      tmp_path):
    """ITEM 3 — the halo control offers ``off`` (no halo), ``auto`` and the file's own entries."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}")

    options = re.findall(r'<option value="([^"]*)"', halo_select(html))
    assert "off" in options and "auto" in options
    for entry in HALO_ENTRIES:
        assert entry in options, entry


@pytest.mark.asyncio
async def test_the_halo_entries_are_plates_only_for_a_real_preset(config_dir, clean_registry,
                                                                  tmp_path):
    """ITEM 3 — the file's entries are drawn as plate-style items with a ``title`` ONLY when a real
    preset is chosen: never for ``off`` and never for ``auto``. Each plate links to its entry."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    base = f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}"
    async with client(app) as http:
        await sign_in(http, "specialk")
        real = await page(http, f"{base}&halo.preset=AtmoHighClouds")
        off = await page(http, f"{base}&halo.preset=off")
        auto = await page(http, f"{base}&halo.preset=auto")

    body = text_plate_block(real)
    assert body, "a real preset must draw the entry plates"
    for entry in HALO_ENTRIES:
        assert re.search(rf'<a class="pw-plate[^"]*"[^>]*title="[^"]*"[^>]*'
                         rf'href="[^"]*halo\.preset={re.escape(entry)}"', body), f"the '{entry}' plate"
    # the chosen entry's plate is marked `on`, and its full label is the tooltip
    assert 'class="pw-plate on"' in body
    assert text_plate_block(off) == "", "no entry plates for `off`"
    assert text_plate_block(auto) == "", "no entry plates for `auto`"


@pytest.mark.asyncio
async def test_the_crystal_grid_is_offered_only_for_a_real_preset(config_dir, clean_registry,
                                                                  tmp_path):
    """ITEM 3 — the crystal plate grid is offered only for a real preset, never for ``off``/``auto``."""
    preset_file(config_dir)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    base = f"{presets_page.wizard_url('Spring')}?step=clouds&node={node.name}"
    crystal_url = presets_page.halo_image_path(node.name, CRYSTAL_ENTRIES[0])
    async with client(app) as http:
        await sign_in(http, "specialk")
        real = await page(http, f"{base}&halo.preset=AtmoHighClouds")
        off = await page(http, f"{base}&halo.preset=off")
        auto = await page(http, f"{base}&halo.preset=auto")

    assert crystal_url in real
    assert crystal_url not in off
    assert crystal_url not in auto


def test_the_crystals_key_is_gated_on_a_real_halo():
    """ITEM 3 — the crystal control's key is offered for a real preset only (``step_controls``)."""
    step = presets_page.WIZARD_STEP_CLOUDS
    for value in ("off", "auto", ""):
        assert "halo.crystalsPreset" not in presets_page.step_controls(step, {"halo.preset": value}), \
            value
    assert "halo.crystalsPreset" in presets_page.step_controls(step, {"halo.preset": "AtmoHighClouds"})


def test_is_real_halo_names_only_a_preset():
    """``off`` and ``auto`` are not presets; anything else named is."""
    assert presets_page.is_real_halo("AtmoHighClouds")
    assert not presets_page.is_real_halo("off")
    assert not presets_page.is_real_halo("auto")
    assert not presets_page.is_real_halo("")
    assert not presets_page.is_real_halo(None)


def test_the_crystals_are_written_only_for_a_real_preset():
    """ITEM 4 — the crystal key is written beside a real preset, dropped for ``off`` and ``auto``."""
    values = {key: "" for key in presets_page.WIZARD_KEYS}
    values["halo.crystalsPreset"] = "AllKinds"
    for value, expected in (
            ("AtmoHighClouds", {"preset": "AtmoHighClouds", "crystalsPreset": "AllKinds"}),
            ("auto", {"preset": "auto"}),
            ("off", {"preset": "off"})):
        values["halo.preset"] = value
        assert presets_page.preset_map_from_values(values)["halo"] == expected, value


# ------------------------------------------ 15. the wizard editor's own check (the server's verdict)
#
# The review step's YAML panel is edited through the SHARED editor, which POSTs its text to this page's
# own validate URL. The check is the SERVER's: it reads the ONE preset the page has OPEN and answers
# the SAME JSON verdict the settings editor's check answers with — nothing is validated in the browser
# and nothing is written. The rule the SAVE enforces is applied here too: the document must be exactly
# the open preset (one top-level key, spelled as the file spells it).


def _csrf_from(html: str) -> str:
    match = re.search(r'name="_csrf_token" value="([^"]*)"', html)
    assert match, "the review form carried no CSRF token"
    return match.group(1)


async def post_validate(http, name: str, text: str, *, new: bool = False):
    """POST *text* to the wizard's own validate route for *name*, with the page's own CSRF token."""
    url = presets_page.PRESETS_NEW_VALIDATE_PATH if new else presets_page.wizard_validate_url(name)
    page_url = (f"{presets_page.PRESETS_NEW_PATH}?step=review" if new
                else f"{presets_page.wizard_url(name)}?step=review")
    html = await page(http, page_url)
    return await http.post(url, data={"_csrf_token": _csrf_from(html), "_yaml": text})


def _verdict_of(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_the_validate_route_answers_ok_for_the_reviews_own_document(config_dir, clean_registry):
    """The positive control: the review's own produced YAML is a VALID document for the open preset."""
    preset_file(config_dir, SPRING + OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await page(http, f"{presets_page.wizard_url('Spring')}?step=review")
        reply = await post_validate(http, "Spring", review_yaml(html))
    verdict = _verdict_of(reply)
    assert verdict["ok"] is True, verdict
    # the verdict carries the SAME keys the settings editor's check answers with, so one script renders
    # either page's verdict.
    assert set(verdict) == {"ok", "kind", "line", "column", "block", "block_line", "block_end_line",
                            "message"}


@pytest.mark.asyncio
async def test_the_validate_route_names_broken_yaml(config_dir, clean_registry):
    """Bad YAML is a syntax verdict carrying the offending line — never a traceback."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_validate(http, "Spring", "Spring:\n  date: '2016-04-21\n  bad: [")
    verdict = _verdict_of(reply)
    assert verdict["ok"] is False
    assert verdict["kind"] == "syntax"
    assert verdict["line"], verdict
    assert "Traceback" not in reply.text


@pytest.mark.asyncio
async def test_the_validate_route_names_a_schema_fault(config_dir, clean_registry):
    """A document that parses but fails the preset schema is refused with its own block/line."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_validate(http, "Spring", "Spring:\n  temperature: not-a-number\n")
    verdict = _verdict_of(reply)
    assert verdict["ok"] is False
    assert verdict["kind"] == "schema"
    assert verdict["message"], verdict


@pytest.mark.asyncio
async def test_the_validate_route_refuses_a_renamed_or_several_or_empty_document(config_dir,
                                                                                 clean_registry):
    """The SAVE's own rule holds in the check: exactly the OPEN preset — no rename, no second key."""
    preset_file(config_dir, SPRING + OTHER)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        renamed = _verdict_of(await post_validate(http, "Spring",
                                                  "Renamed:\n  temperature: 3\n"))
        several = _verdict_of(await post_validate(
            http, "Spring", "Spring:\n  temperature: 3\nOther:\n  temperature: 1\n"))
        empty = _verdict_of(await post_validate(http, "Spring", ""))
    assert renamed["ok"] is False and "Renamed" in renamed["message"] and "Spring" in renamed["message"]
    assert several["ok"] is False and "2" in several["message"]
    assert empty["ok"] is False


@pytest.mark.asyncio
async def test_the_validate_route_for_a_new_preset_accepts_its_own_key(config_dir, clean_registry):
    """A NEW preset has no open name to hold to: the document's own single key IS the name."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        ok = _verdict_of(await post_validate(http, "", "Brand New:\n  temperature: 3\n", new=True))
        several = _verdict_of(await post_validate(http, "", SPRING + OTHER, new=True))
    assert ok["ok"] is True, ok
    assert several["ok"] is False


@pytest.mark.asyncio
async def test_the_validate_route_writes_nothing(config_dir, clean_registry):
    """The check is a READ: every verdict leaves the preset file byte-identical, no backup kept."""
    path = preset_file(config_dir, SPRING + OTHER)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        await post_validate(http, "Spring", "Spring:\n  temperature: 3\n")
        await post_validate(http, "Spring", "Renamed:\n  temperature: 3\n")
        await post_validate(http, "Spring", "")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(config_dir.glob("*.bak"))


@pytest.mark.asyncio
async def test_the_validate_route_needs_a_csrf_token(config_dir, clean_registry):
    """CSRF: a POST that carries no session token never reaches the check."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await http.post(presets_page.wizard_validate_url("Spring"),
                                data={"_yaml": "Spring:\n  temperature: 3\n"})
    assert reply.status_code == 403


@pytest.mark.asyncio
async def test_the_validate_route_is_gated_on_the_page_capability(config_dir, clean_registry):
    """``presets.view`` is Admin-only: a caller without it cannot probe the check either."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "plain")
        reply = await http.post(presets_page.wizard_validate_url("Spring"),
                                data={"_csrf_token": "x", "_yaml": "Spring:\n  temperature: 3\n"})
    assert reply.status_code == 403


# ------------------------------------------------------------- 16. the shared editor, driven for real

@pytest.mark.asyncio
async def test_the_validate_route_checks_through_the_bots_own_validator(config_dir, clean_registry,
                                                                        monkeypatch):
    """The editor's check drives ``core.utils.validators.validate`` — the SAME callable a save does.

    Patched, so the test proves the ROUTE reaches the bot's own validator rather than a second
    implementation that happens to agree with it.
    """
    from core.utils import configwrite as cw

    preset_file(config_dir)
    seen: list = []
    real = cw.validate_against_schema

    def spy(text, schemas, *, path=None):
        seen.append((text, tuple(schemas)))
        return real(text, schemas, path=path)

    monkeypatch.setattr(cw, "validate_against_schema", spy)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_validate(http, "Spring", "Spring:\n  temperature: 3\n")
    assert _verdict_of(reply)["ok"] is True
    assert seen, "the validate route never reached the bot's own validator"
    # the SAME schema pair the save validates against.
    assert tuple(presets_page.preset_schema_files()) in {schemas for _text, schemas in seen}


@requires_node
def test_the_shared_editor_drives_the_wizards_panel():
    """The SHIPPED ``settings-yaml.js`` driven through the browser-less harness against the wizard DOM.

    The panel is the wizard's own shape (the `[data-cfg-yaml]` card INSIDE the POST form, the named
    `_expert` companion + checkbox, the prefilled carrier): the editor is built from the read-only
    panel, the carriers are set, the check reaches the wizard's validate URL, and the submit guard
    leaves a control that does NOT post the document alone.
    """
    from conftest import run_node_harness

    assert CONFIGGEN_HARNESS.is_file(), "the harness that drives the shipped editor is missing"
    done = run_node_harness(["node", str(CONFIGGEN_HARNESS), str(SETTINGS_YAML_JS), "wizard"])
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["on"]["editorBuilt"] is True, "the editor was not built from the panel"
    assert seen["on"]["seededFromPanel"] is True, "the editor must start from the review's own YAML"
    assert seen["on"]["carrierMatchesEditor"] is True
    assert seen["on"]["editorShown"] is True and seen["on"]["readonlyHidden"] is True
    assert seen["on"]["expert"] == "1"
    assert seen["on"]["fetchUrl"] == seen["validateUrl"], "the check must reach the wizard's own route"
    assert seen["on"]["bodyCarriesCsrf"] is True and seen["on"]["bodyCarriesDocument"] is True
    assert seen["change"]["carrierUpdated"] is True
    # the guard holds a dirty SAVE but leaves the Remove control (which posts no document) alone.
    assert seen["guard"]["dirtySavePrevented"] is True
    assert seen["guard"]["skipSubmitterPrevented"] is False
    assert seen["offAgain"]["readonlyShown"] is True and seen["offAgain"]["expert"] == "0"