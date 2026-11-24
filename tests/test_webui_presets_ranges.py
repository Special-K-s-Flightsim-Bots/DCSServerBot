"""A value a preset PRE-FILLS must be a value its own control accepts — the step AND the range.

One section per acceptance criterion of this card:

* the vendor data's own numbers: the fixture's ``clouds.lua`` carries 45 entries, 34 visible, and the
  visible ones carry exactly 7 distinct cloud bases (2520 / 2940 / 3360 / 3780 / 4200 / 4620 / 5460 m);
* the field table's contract: every numeric control declares a BOX step (``1`` or ``any``) that is not
  its slider's granularity, and a whole number inside the range — a value that is no multiple of the
  slider's step — is INSIDE the range;
* the rendered controls: every base all 34 visible presets pre-fill is one the WIZARD's number box and
  the DIALOG's picker+box accept, proven by the browser's own min/max/step rule read off the markup;
* the slider and the box state the SAME range in both templates;
* the route agrees with the control: a typed non-multiple of the step and a high value are WRITTEN,
  and a value just outside the range is REFUSED on the server with its own sentence, nothing written.

The ranges themselves are the field table's own (``weather_fields``): the ME's widget limits widened
by what ``clouds.lua`` presets carry and by the tree's written cloud-base ceiling.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest

from services.webservice import dcs_clouds, weather_fields
from services.webservice.pages import presets as presets_page
from test_webui_presets_clouds import CloudsNode, install_clouds
from test_webui_presets_save import post_save, preset_file, review_form
from test_webui_startup_temporary_gate import readable_fleet
from test_webui_startup_temporary_preset import weather_fieldset
from test_webui_startup_weather_picker import open_dialog
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, sign_in,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "clouds.lua"

#: the base the fixture's visible presets carry, smallest first — SEVEN distinct figures.
VISIBLE_BASES = (2520.0, 2940.0, 3360.0, 3780.0, 4200.0, 4620.0, 5460.0)

#: the fixture's own counts, as the reader reports them.
ENTRIES = 45
VISIBLE = 34

#: a plain weather preset the wizard opens.
SPRING = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  qnh: 760
"""

NODE = "dcs-a-01"


# --------------------------------------------------------------------------------- the markup rule

def _attr(tag: str, name: str) -> str:
    """The value of attribute *name* in *tag*, or ``''`` when it carries none."""
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return match.group(1) if match else ""


def browser_accepts(tag: str, value) -> bool:
    """Whether a browser accepts *value* in the number box *tag* — its own constraint validation.

    The rule a native ``<input type="number">`` applies on submit: the value must be inside
    ``min``/``max`` (when stated) and, unless ``step`` is ``any``, ``value - min`` must be an integer
    multiple of the step. ``stepMismatch`` is what told the owner "divisible by 10"; this is the same
    arithmetic, run against the markup the page actually ships.
    """
    number = float(value)
    low, high = _attr(tag, "min"), _attr(tag, "max")
    if low and number < float(low):
        return False
    if high and number > float(high):
        return False
    step = _attr(tag, "step")
    if step in ("", "any"):
        return True
    origin = float(low) if low else 0.0
    steps = (number - origin) / float(step)
    return abs(steps - round(steps)) < 1e-9


def named_box(html: str, key: str) -> str:
    """The ``<input type="number" name="key" …>`` tag the page renders for *key*."""
    for tag in re.findall(r'<input type="number"[^>]*>', html):
        if _attr(tag, "name") == key:
            return tag
    raise AssertionError(f"the page rendered no number box named '{key}'")


def slider_pair(html: str, key: str) -> tuple[str, str]:
    """The ``(range tag, number tag)`` of the pair *key* is drawn in, on a wizard step."""
    for block in re.findall(r'<div class="pw-sl" data-weather-pair>(.*?)</div>', html, re.S):
        number = re.search(r'<input type="number"[^>]*>', block)
        if not number or _attr(number.group(0), "name") != key:
            continue
        ring = re.search(r'<input type="range"[^>]*>', block)
        assert ring is not None, f"the '{key}' pair carries no range"
        return ring.group(0), number.group(0)
    raise AssertionError(f"no slider pair for '{key}' in the page")


def link_value(link: str, field: str) -> str:
    """The value *link* carries for *field* — the LAST one, the way the route reads a query string."""
    found = re.findall(rf"[&?]{re.escape(field)}=([^&]*)", link)
    return html_module.unescape(found[-1]) if found else ""


def plate_bases(html: str) -> dict[str, str]:
    """``{preset name: prefilled base}`` for every plate link the wizard rendered."""
    found: dict[str, str] = {}
    for href in re.findall(r'<a class="pw-plate[^"]*"[\s\S]*?href="([^"]*)"', html):
        link = html_module.unescape(href)
        name, base = link_value(link, "clouds.preset"), link_value(link, "clouds.base")
        if name and base:
            found[name] = base
    return found


def fixture_presets():
    """The fixture's visible presets, through the console's own reader."""
    return dcs_clouds.visible_presets(
        dcs_clouds.parse_cloud_presets(FIXTURE.read_text(encoding="utf-8")))


async def wizard_page(http, step: str = presets_page.WIZARD_STEP_CLOUDS, *, node: str = "") -> str:
    url = f"{presets_page.wizard_url('Spring')}?step={step}"
    if step == presets_page.WIZARD_STEP_CLOUDS:
        # the cloud mode gates which rows the step draws: ``preset`` offers the plates AND the base
        url += "&clouds=preset"
    if node:
        url += f"&node={node}"
    response = await http.get(url)
    assert response.status_code == 200, response.text
    return response.text


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def page(http, path: str) -> str:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.text


# ------------------------------------------------------- 1. the vendor data's own numbers

def test_the_vendor_file_carries_34_visible_presets_and_7_distinct_bases():
    """The numbers this card is about, read through the tree's own reader."""
    presets = fixture_presets()
    assert len(presets) == VISIBLE
    bases = sorted({preset.base for preset in presets})
    assert bases == list(VISIBLE_BASES), bases
    # every visible preset carries a base (no ``None`` in the set above), and each preset's own base
    # sits inside the window the ME gives that preset.
    for preset in presets:
        assert preset.base is not None, preset.name
        assert preset.preset_alt_min is None or preset.preset_alt_min <= preset.base
        assert preset.preset_alt_max is None or preset.base <= preset.preset_alt_max


# ------------------------------------------------------- 2. the field table's own contract

def test_every_numeric_control_declares_a_box_step_of_one_or_any():
    """A box's step is declared ONCE per control, and it is one of the two admissible spellings."""
    contract = (weather_fields.BOX_STEP_WHOLE, weather_fields.BOX_STEP_REAL)
    numeric = [entry for entry in weather_fields.FIELDS
               if entry.kind in (weather_fields.KIND_SLIDER, weather_fields.KIND_NUMBER,
                                 weather_fields.KIND_DIAL)]
    assert numeric, "the table declares no numeric control at all"
    for entry in numeric:
        assert entry.box_step in contract, entry.key
    for column in weather_fields.FOG_COLUMNS:
        assert column.box_step in contract, column.key


def test_a_whole_number_that_is_no_multiple_of_the_slider_step_is_inside_the_range():
    """2520 m is what the owner typed and was refused for: the slider's 10 must not police a box."""
    base = weather_fields.field("clouds.base")
    assert base.step == 10, "the slider keeps a granularity of its own"
    assert base.box_step == weather_fields.BOX_STEP_WHOLE, "the box takes any whole metre"
    assert weather_fields.out_of_range("clouds.base", "2520") is None
    assert weather_fields.out_of_range("clouds.base", "2521") is None


def test_the_base_control_admits_every_base_a_visible_preset_carries():
    """The range the control states covers all 7 figures — including the six above the old ceiling."""
    for base in VISIBLE_BASES:
        assert weather_fields.out_of_range("clouds.base", str(int(base))) is None, base


def test_the_base_range_ends_are_the_reported_bounds():
    """0 m and 15000 m are inside; one step beyond either end is outside and names its control."""
    assert weather_fields.field("clouds.base").min == 0
    assert weather_fields.field("clouds.base").max == 15000
    assert weather_fields.out_of_range("clouds.base", "0") is None
    assert weather_fields.out_of_range("clouds.base", "15000") is None
    for outside in ("-1", "15001", "5460.5x"):
        offence = weather_fields.out_of_range("clouds.base", outside)
        if outside.endswith("x"):
            # a value no number parse reaches is left to the schema's own type check
            assert offence is None, outside
        else:
            assert offence is not None, outside
            assert offence[0].key == "clouds.base"
            assert offence[1] == float(outside)


def test_temperature_and_wind_speed_admit_the_mission_editors_own_limits():
    """The ME's controls state -50..50 °C and 0..50 m/s: both ends must be inside the range."""
    for key, inside, outside in (("temperature", "-50", "-51"),
                                 ("temperature", "50", "51"),
                                 ("wind.atGround.speed", "45", "51"),
                                 ("wind.at2000.speed", "45", "51"),
                                 ("wind.at8000.speed", "45", "51")):
        assert weather_fields.out_of_range(key, inside) is None, (key, inside)
        assert weather_fields.out_of_range(key, outside) is not None, (key, outside)


def test_the_fog_cells_admit_a_value_that_is_no_multiple_of_their_steps():
    """The fog table's 10 m / 100 m slider steps must not police its typed cells either."""
    assert weather_fields.out_of_range("thickness", "2501") is None
    assert weather_fields.out_of_range("visibility", "2501") is None
    # ...and their own ends still refuse what is outside them.
    assert weather_fields.out_of_range("thickness", "5001") is not None
    assert weather_fields.out_of_range("visibility", "100001") is not None


def test_a_non_numeric_or_empty_value_is_left_to_the_schemas_own_type_check():
    """The range check reports NUMBERS; a text value is refused (or not) by the schema, not here."""
    assert weather_fields.out_of_range("clouds.base", "") is None
    assert weather_fields.out_of_range("clouds.base", "high") is None
    assert weather_fields.out_of_range("name", "Spring") is None


# ------------------------------------------------------- 3. the rendered controls

@pytest.mark.asyncio
async def test_the_wizard_plate_prefills_are_all_accepted_by_its_own_box(config_dir, clean_registry,
                                                                        tmp_path):
    """All 34 plates pre-fill a base the box beside them ACCEPTS — the wizard's own surface."""
    preset_file(config_dir, SPRING)
    install_clouds(tmp_path)
    node = CloudsNode(NODE, installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await wizard_page(http, node=NODE)

    prefills = plate_bases(html)
    assert len(prefills) == VISIBLE, sorted(prefills)
    box = named_box(html, "clouds.base")
    refused = {name: base for name, base in prefills.items() if not browser_accepts(box, base)}
    assert refused == {}, refused
    # every plate states its base in the unit the box beside it is drawn in: feet for English.
    assert sorted({float(base) for base in prefills.values()}) == sorted(
        {float(weather_fields.display_value("clouds.base", base)) for base in VISIBLE_BASES})


@pytest.mark.asyncio
async def test_a_high_base_plate_renders_its_own_value_and_the_box_accepts_it(config_dir,
                                                                              clean_registry,
                                                                              tmp_path):
    """Following Preset8's plate link (5460 m) renders 5460 in a box that accepts it."""
    preset_file(config_dir, SPRING)
    install_clouds(tmp_path)
    node = CloudsNode(NODE, installation=tmp_path)
    app = build(config_dir, Cluster(nodes={node.name: node}, node=node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await wizard_page(http, node=NODE)
        high = next(preset for preset in fixture_presets() if preset.base == max(VISIBLE_BASES))
        link = next(href for href in re.findall(r'<a class="pw-plate[^"]*"[^>]*href="([^"]*)"', html)
                    if f"clouds.preset={high.name}" in html_module.unescape(href))
        followed = await page(http, html_module.unescape(link))

    box = named_box(followed, "clouds.base")
    stated = str(int(weather_fields.display_value("clouds.base", high.base)))
    assert _attr(box, "value") == stated, \
        f"the plate pre-fills the preset's own base in the box's unit ({high.base} m)"
    assert browser_accepts(box, _attr(box, "value")), \
        f"the box refuses the value its own plate just pre-filled: {box}"


@pytest.mark.asyncio
async def test_the_dialog_picker_options_all_prefill_a_value_its_box_accepts(config_dir,
                                                                             clean_registry,
                                                                             tmp_path):
    """The launch dialog's picker carries each preset's base; its own box must accept every one."""
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = weather_fieldset(dialog.text)
    row = next(part for part in re.findall(r'<div class="dlg-wrow"[^>]*>.*?</div>\s*</div>', block, re.S)
               if 'name="clouds.preset"' in part)
    prefills = {html_module.unescape(value): base
                for value, base in re.findall(r'<option value="([^"]*)"[^>]*data-base="([^"]*)"', row)}
    assert len(prefills) == VISIBLE, sorted(prefills)
    box = named_box(block, "clouds.base")
    refused = {name: base for name, base in prefills.items() if not browser_accepts(box, base)}
    assert refused == {}, refused


@pytest.mark.asyncio
async def test_the_wizard_slider_and_its_box_state_the_same_range(config_dir, clean_registry):
    """A slider capped at one ceiling beside a box allowing another is the same defect, elsewhere."""
    preset_file(config_dir, SPRING)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        html = await wizard_page(http, presets_page.WIZARD_STEP_WIND)

    for key in ("temperature", "wind.atGround.speed", "groundTurbulence", "qnh"):
        ring, box = slider_pair(html, key)
        assert (_attr(ring, "min"), _attr(ring, "max")) == (_attr(box, "min"), _attr(box, "max")), key


@pytest.mark.asyncio
async def test_the_dialog_slider_and_its_box_state_the_same_range(config_dir, clean_registry,
                                                                 tmp_path):
    """The launch dialog's pair states ONE range too, and its box carries the box step."""
    _node, dialog_fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, dialog_fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = weather_fieldset(dialog.text)
    for key in ("clouds.base", "temperature", "wind.atGround.speed", "qnh"):
        tag = named_box(block, key)
        pair = next(part for part in re.findall(r'<div class="dlg-wsl" data-weather-pair>(.*?)</div>',
                                                block, re.S)
                    if f'name="{key}"' in part)
        ring = re.search(r'<input type="range"[^>]*>', pair).group(0)
        assert (_attr(ring, "min"), _attr(ring, "max")) == (_attr(tag, "min"), _attr(tag, "max")), key
        assert _attr(tag, "step") == weather_fields.field(key).box_step, key



def no_node_fleet():
    """A cluster with NO nodes: the wizard's own steps and the save route need none of them."""
    return Cluster(nodes={}, node=None)


# ------------------------------------------------------- 4. the route agrees with the control

@pytest.mark.asyncio
async def test_a_typed_non_multiple_of_the_step_is_written(config_dir, clean_registry):
    """8271 ft — no multiple of the slider's 33 ft — reaches the file as 2521 m, the number it is."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"clouds": "dynamic", "clouds.base": "8271"})
        assert reply.status_code == 303, reply.text
        assert reply.headers["location"] == f"{presets_page.wizard_url('Spring')}?step=review"

    document = configwrite.load_config_file(path)
    assert document["Spring"]["clouds"] == {"base": 2521}


@pytest.mark.asyncio
async def test_a_high_base_is_written(config_dir, clean_registry):
    """17913 ft — the highest base a visible preset carries (5460 m) — is written, not refused."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"clouds": "dynamic", "clouds.base": "17913"})
        assert reply.status_code == 303, reply.text

    assert configwrite.load_config_file(path)["Spring"]["clouds"] == {"base": 5460}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["50000", "-1"])
async def test_a_value_outside_the_range_is_refused_by_the_route(config_dir, clean_registry, value):
    """The end the browser refuses is refused HERE too, with its own sentence and nothing written."""
    path = preset_file(config_dir, SPRING)
    before = sha256(path)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"clouds": "dynamic", "clouds.base": value})
        assert reply.status_code == 303, reply.text
        assert sha256(path) == before, "a refused value must write nothing"
        html = await page(http, reply.headers["location"])

    # the refusal states the range the control itself shows: feet in an English render.
    assert "accepts 0 to 49213" in html, "the refusal states the control's own range"
    assert value in html, "and quotes the value it was given"
    assert "nothing was written" in html


@pytest.mark.asyncio
async def test_the_ranges_outside_end_agrees_with_the_control(config_dir, clean_registry):
    """49213 ft is accepted and 50000 ft refused: the box's ceiling and the route's are one number."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        inside = await post_save(http, "Spring", change={"clouds": "dynamic", "clouds.base": "49213"})
        assert inside.status_code == 303
        assert configwrite.load_config_file(path)["Spring"]["clouds"] == {"base": 15000}
        after = sha256(path)
        outside = await post_save(http, "Spring", change={"clouds": "dynamic",
                                                          "clouds.base": "50000"})
        assert outside.status_code == 303
        assert sha256(path) == after, "the value past the end writes nothing"


@pytest.mark.asyncio
async def test_a_temperature_the_me_allows_is_written(config_dir, clean_registry):
    """-40 °C is inside the ME's own control; the wizard's row must not be narrower than the ME."""
    from core.utils import configwrite

    path = preset_file(config_dir, SPRING)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={"temperature": "-40"})
        assert reply.status_code == 303, reply.text

    assert configwrite.load_config_file(path)["Spring"]["temperature"] == -40


@pytest.mark.asyncio
async def test_an_out_of_range_fog_cell_is_refused_and_nothing_is_written(config_dir,
                                                                         clean_registry):
    """A fog keyframe's own limit is enforced by the same sentence, on the same route."""
    path = preset_file(config_dir, SPRING)
    before = sha256(path)
    app = build(config_dir, no_node_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        reply = await post_save(http, "Spring", change={
            "fog.mode": "manual",
            weather_fields.FOG_ROWS_KEY: "0:20000:2000"})
        assert reply.status_code == 303, reply.text
        html = await page(http, reply.headers["location"])

    assert sha256(path) == before
    assert "Fog thickness" in html and "16404" in html, html
    assert "nothing was written" in html


def test_the_refusal_sentence_carries_the_placeholders_the_route_fills():
    """The one new sentence names the label, both ends and the value — no unnamed slot."""
    sentence = presets_page.PRESETS_REFUSED_OUT_OF_RANGE
    for slot in ("%(label)s", "%(min)s", "%(max)s", "%(value)s"):
        assert slot in sentence, slot
