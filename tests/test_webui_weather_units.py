"""The weather controls' DISPLAY unit.

A preset stores a distance in metres. The Mission Editor shows feet, so an English render states every
distance control in feet (cloud base, cloud thickness, dust visibility, fog thickness, fog visibility)
and the form posts feet; every other language keeps the schema's metres. One pair converts at the two
boundaries — the render and the form — and the document never sees a foot.
"""
import pytest

from services.webservice import i18n, weather_fields
from services.webservice.pages import presets as presets_page
from test_webui_presets_wizard import (build, clean_registry, client, config_dir, fleet, page,
                                       preset_file, sign_in)  # noqa: F401 - the fixtures by name


def test_dust_states_the_mission_editors_own_range():
    """DCS's dust control is 984..65 617 ft, and the mission stores the same span in metres."""
    entry = weather_fields.field("dust_density")
    assert (entry.min, entry.max) == (300, 20000)
    assert entry.unit == "metres"
    assert weather_fields.display_bounds(entry)[:2] == (984, 65617)


@pytest.mark.parametrize("key,metres,feet", [
    ("dust_density", 3048, 10000),      # the figure the operator read off a 10 000 ft mission
    ("clouds.base", 5460, 17913),       # the highest base a visible preset carries
    ("clouds.thickness", 1000, 3281),
    ("thickness", 200, 656),
    ("visibility", 5000, 16404),
])
def test_a_stored_distance_round_trips_through_the_display_unit(key, metres, feet):
    """A render converts it to whole feet, and the write reads that foot back as the SAME metre."""
    assert weather_fields.display_value(key, metres) == feet
    assert weather_fields.stored_value(key, feet) == str(metres)


def test_the_round_trip_holds_at_both_ends_of_every_distance_control():
    """Every control's own limits survive the conversion, so a slider end is never refused."""
    feet_ends = {"dust_density": (984, 65617), "clouds.base": (0, 49213),
                 "clouds.thickness": (0, 11024), "thickness": (0, 16404),
                 "visibility": (0, 328084)}
    for key, (feet_low, feet_high) in feet_ends.items():
        entry = weather_fields.field(key) if key in weather_fields.FIELDS_BY_KEY \
            else weather_fields.FOG_COLUMNS_BY_NAME[key]
        assert entry.min is not None and entry.max is not None, key
        low, high, _step = weather_fields.display_bounds(entry)
        assert (low, high) == (feet_low, feet_high), key
        assert weather_fields.stored_value(key, low) == str(int(entry.min)), key
        assert weather_fields.stored_value(key, high) == str(int(entry.max)), key


def test_a_non_distance_key_is_never_converted():
    """Wind, temperature and QNH keep the schema's own unit in both directions."""
    assert weather_fields.display_value("temperature", 15) == 15
    assert weather_fields.stored_value("temperature", "15") == "15"
    assert weather_fields.display_value("qnh", 760) == 760


def test_english_states_feet_and_every_other_language_metres():
    assert weather_fields.uses_feet("en")
    assert weather_fields.uses_feet("en-GB")
    for language in ("de", "es", "ru", "cn", ""):
        assert not weather_fields.uses_feet(language), language


def test_a_value_below_the_floor_states_the_floor():
    """A mission carrying ``dust_density: 0`` (dust off) must not put 0 in a box that starts at 984."""
    assert weather_fields.floored("dust_density", 0) == 300
    assert weather_fields.floored("dust_density", "") == 300
    assert weather_fields.floored("dust_density", "0") == 300
    assert weather_fields.floored("dust_density", 3048) == 3048
    assert weather_fields.floored("dust_density", "nonsense") == "nonsense"
    # a control with no floor of its own is left exactly as it came
    assert weather_fields.floored("clouds.base", 0) == 0
    assert weather_fields.floored("temperature", "") == ""


def test_a_read_states_a_below_floor_distance_as_the_floor():
    """The mission read feeds CONTROLS: what it answers must fit the box it lands in."""
    values = {"dust_density": 0, "clouds.base": 2000, "temperature": "15"}
    assert presets_page.display_values(values, feet=True)["dust_density"] == 984
    assert presets_page.display_values(values, feet=False)["dust_density"] == 300
    assert presets_page.display_values(values, feet=True)["clouds.base"] == 6562
    assert presets_page.display_values(values, feet=True)["temperature"] == "15"


def test_a_stored_zero_dust_states_the_floor_in_the_row():
    row = presets_page.wizard_rows(("dust_density",), {"dust_density": "0"}, feet=True)[0]
    assert (row["min"], row["value"]) == (984, 984)


@pytest.mark.asyncio
async def test_the_wizard_draws_feet_in_english_and_metres_in_german(config_dir, clean_registry):
    """The SAME preset renders 6562 ft for an English request and 2000 m for a German one."""
    preset_file(config_dir)
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = await page(http, f"{presets_page.wizard_url('Spring')}?step=clouds")

    assert 'name="clouds.base"' in english and "6562" in english and "feet" in english
    assert 'name="clouds.base"' in german and ">2000<" in german
    assert "Meter" in german, "the German catalog's own unit word"
