"""Step 4 of the Scheduler wizard: the warning message with its placeholders, and the ladder as a row
table that works with the script off.

Four sections, one per behaviour the step owes:

1. THE ROWS — a map-shaped ``times`` renders one row per second with its own text, a list-shaped
   ``times`` renders one row per second with an empty text, and add/remove are submit buttons of the
   same GET form (``addrow`` / ``removerow``).
2. THE REFUSALS — a duplicate second, a non-positive second, a non-numeric second and a text with no
   seconds each keep that row out of the plan and say why under the row.
3. THE SHAPES — a new ladder is written as the list of seconds, and as the map of seconds to text as
   soon as one row carries text; the step says which one the review step writes.
4. THE MESSAGE — the three names the bot substitutes are on the page with their meanings (the
   script's click-to-insert is an enhancement over that legend), the preview lines are rendered from
   the bot's own substitution, and no control asks for a delimiter-separated ladder.

The page pins run the shipped route, template and plan reader; the plugin is a double on
``server_detail._scheduler`` because the plan read is what the page consumes.
"""
from __future__ import annotations

import re

import pytest

from services.webservice import scheduler_plan
from test_webui_scheduler_save import (  # noqa: F401 - fixtures imported on purpose
    node_data, scope_dir,
)
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, cluster, config_dir, install_plugin, scheduler_file, sign_in,
    step_page, tab_body,
)

#: a map-shaped ladder: each second carries the text shown at that moment.
MAP_PLAN = {
    "timezone": "UTC",
    "warn": {"message": "!!! {item} will {what} in {when} !!!",
             "sound": "alarm.ogg",
             "times": {"600": "restart in 10 minutes", "60": "restart in 1 minute"},
             "countdown": {"time": 10, "message": "{item} will {what} in {when}"}},
}

#: the same ladder as a plain list of seconds, with no per-row text.
LIST_PLAN = {"timezone": "UTC", "warn": {"message": "restarting soon", "times": [600, 60]}}


def ladder_rows(body: str) -> list[tuple[str, str, str]]:
    """Every ladder row of step 4 as ``(seconds, text, remove index)``, in the page's order."""
    rows: list[tuple[str, str, str]] = []
    for chunk in body.split('class="sw-lrow"')[1:]:
        seconds = re.search(r'name="warn_time\.(\d+)" value="([^"]*)"', chunk)
        text = re.search(r'name="warn_text\.(\d+)" value="([^"]*)"', chunk)
        remove = re.search(r'name="removerow" value="(\d+)"', chunk)
        rows.append((seconds.group(2), text.group(2) if text else "",
                     remove.group(1) if remove else ""))
    return rows


async def open_step(http, query: str = ""):
    return tab_body(await step_page(http, "4", query))


# ------------------------------------------------------------------- 1. the row table

@pytest.mark.asyncio
async def test_a_map_ladder_renders_one_row_per_second_with_its_text(config_dir, clean_registry,
                                                                    monkeypatch):
    """A ``times`` map keeps its per-row texts, one row each, in the file's order."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http)

    assert ladder_rows(body) == [("600", "restart in 10 minutes", "0"),
                                 ("60", "restart in 1 minute", "1")]


@pytest.mark.asyncio
async def test_a_list_ladder_renders_one_row_per_second_with_an_empty_text(config_dir,
                                                                           clean_registry,
                                                                           monkeypatch):
    """A ``times`` list keeps the list shape: two rows, and neither carries a text."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, LIST_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http)

    assert ladder_rows(body) == [("600", "", "0"), ("60", "", "1")]


@pytest.mark.asyncio
async def test_addrow_appends_one_empty_row_without_a_script(config_dir, clean_registry,
                                                             monkeypatch):
    """``addrow=1`` is a submit of the same GET form: one more row, the stated rows untouched."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    fields = "warn_time.0=600&warn_text.0=ten&warn_time.1=60&warn_text.1=one"
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http, fields + "&addrow=1")

    assert ladder_rows(body) == [("600", "ten", "0"), ("60", "one", "1"), ("", "", "2")]


@pytest.mark.asyncio
async def test_removerow_drops_that_row_and_renumbers_the_rest(config_dir, clean_registry,
                                                               monkeypatch):
    """``removerow=<index>`` drops exactly that row; the rows after it move up."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    fields = "warn_time.0=600&warn_text.0=ten&warn_time.1=60&warn_text.1=one"
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http, fields + "&removerow=0")

    assert ladder_rows(body) == [("60", "one", "0")]


@pytest.mark.asyncio
async def test_a_posted_typical_op_fills_nothing(config_dir, clean_registry, monkeypatch):
    """The one-click ladder is gone: an old URL carrying ``typical=1`` changes no row."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, LIST_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        kept = await open_step(http, "warn_time.0=600&warn_text.0=ten&typical=1")

    assert ladder_rows(kept) == [("600", "ten", "0")], "no control on the page posts this op"


# ---------------------------------------------------------------------- 2. the refusals

def test_a_duplicate_second_is_refused_and_the_row_is_left_out():
    rows = [{"seconds": "600", "message": ""}, {"seconds": "600", "message": "again"}]

    times, errors = scheduler_plan.warn_times_from_rows(rows)

    assert times == [600]
    assert [error["row"] for error in errors] == [2]
    assert errors[0]["msgid"] == scheduler_plan.WARN_DUPLICATE_SECONDS


def test_a_zero_or_negative_or_worded_second_is_refused():
    rows = [{"seconds": "0", "message": ""}, {"seconds": "-5", "message": ""},
            {"seconds": "ten", "message": ""}, {"seconds": "60", "message": ""}]

    times, errors = scheduler_plan.warn_times_from_rows(rows)

    assert times == [60]
    assert [error["row"] for error in errors] == [1, 2, 3]
    assert {error["msgid"] for error in errors} == {scheduler_plan.WARN_SECONDS_NOT_A_NUMBER}


def test_a_text_without_seconds_is_refused():
    """A row that carries a text but no seconds would leave an empty ladder with a row standing."""
    times, errors = scheduler_plan.warn_times_from_rows([{"seconds": "", "message": "hello"}])

    assert times is None
    assert errors == [{"row": 1, "msgid": scheduler_plan.WARN_ROW_WITHOUT_SECONDS}]


@pytest.mark.asyncio
async def test_a_duplicate_row_is_refused_on_the_page(config_dir, clean_registry, monkeypatch):
    """The step marks the offending row and the plan carries the first of the two only."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, LIST_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    fields = "warn_time.0=600&warn_text.0=&warn_time.1=600&warn_text.1="
    from urllib.parse import parse_qsl

    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http, fields)

    assert scheduler_plan.WARN_DUPLICATE_SECONDS in body
    assert "Row 2:" in body
    posted = scheduler_plan.plan_from_form(parse_qsl(fields))
    assert posted["warn"]["times"] == [600]


# ------------------------------------------------------------------------ 3. the shapes

def test_a_new_ladder_without_texts_is_written_as_a_list():
    posted = scheduler_plan.plan_from_form([("warn_time.0", "600"), ("warn_time.1", "60")])

    assert posted["warn"]["times"] == [600, 60]


def test_a_new_ladder_with_a_text_is_written_as_a_map():
    posted = scheduler_plan.plan_from_form([("warn_time.0", "600"), ("warn_text.0", "ten"),
                                            ("warn_time.1", "60"), ("warn_text.1", "")])

    assert posted["warn"]["times"] == {600: "ten", 60: ""}


def test_editing_a_map_ladder_keeps_the_map_shape():
    plan = {"warn": {"times": {"600": "ten"}}}
    fields = [(field["name"], field["value"])
              for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]

    posted = scheduler_plan.plan_from_form(fields + [("warn_time.0", "600"),
                                                     ("warn_text.0", "ten minutes")])

    assert posted["warn"]["times"] == {600: "ten minutes"}


def test_editing_a_list_ladder_keeps_the_list_shape():
    plan = {"warn": {"message": "soon", "times": [600, 60]}}
    fields = [(field["name"], field["value"])
              for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]

    posted = scheduler_plan.plan_from_form(fields + [("warn_time.0", "600"), ("warn_text.0", ""),
                                                     ("warn_time.1", "60"), ("warn_text.1", "")])

    assert posted["warn"]["times"] == [600, 60]


@pytest.mark.asyncio
async def test_the_step_says_which_shape_the_review_step_writes(config_dir, clean_registry,
                                                                monkeypatch):
    """Both sentences are on the page, each with the ladder it describes."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, LIST_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        listed = await open_step(http)
        mapped = await open_step(http, "warn_time.0=600&warn_text.0=ten&warn_time.1=60")

    assert scheduler_plan.WARN_WRITES_LIST in listed
    assert scheduler_plan.WARN_WRITES_MAP in mapped
    assert scheduler_plan.WARN_WRITES_MAP not in listed


# ----------------------------------------------------------------------- 4. the message

def test_the_preview_substitutes_the_three_names_the_bot_fills():
    """The preview is the bot's own ``str.format`` call, never a second substitution rule."""
    assert scheduler_plan.warn_preview("!!! {item} will {what} in {when} !!!") == \
        "!!! server will restart in 10 minutes !!!"
    assert scheduler_plan.warn_preview("{item} {what} {when}") == "server restart 10 minutes"
    # a name the bot cannot fill leaves the text as it stands instead of breaking the page
    assert scheduler_plan.warn_preview("hello {player}") == "hello {player}"
    assert scheduler_plan.warn_preview("") == ""


@pytest.mark.asyncio
async def test_the_page_shows_the_three_names_with_their_meanings_and_both_previews(
        config_dir, clean_registry, monkeypatch):
    """With the script off the names ARE the legend: each prints its name and what it means."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http)

    for name in ("{item}", "{what}", "{when}"):
        assert name in body, name
    for meaning in (scheduler_plan.WARN_PLACEHOLDERS[0][1],
                    scheduler_plan.WARN_PLACEHOLDERS[1][1],
                    scheduler_plan.WARN_PLACEHOLDERS[2][1]):
        assert meaning in body, meaning
    assert body.count('<div class="sw-ph" data-warn-chips') == 2, \
        "the message field and the countdown field"
    # both preview lines come from the server, filled with the example words
    assert "!!! server will restart in 10 minutes !!!" in body
    assert body.count('class="sw-prev"') >= 2
    # the chips are spans the script upgrades, not submit buttons of the form
    assert re.search(r'<span class="sw-chip" data-insert="\{item\}">', body)


@pytest.mark.asyncio
async def test_no_control_asks_for_a_delimiter_separated_ladder(config_dir, clean_registry,
                                                                monkeypatch):
    """The comma-separated ladder input is gone: no field carries a list of seconds as text."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = await open_step(http)

    assert not re.search(r'name="warn\.times', body), "the ladder is rows, never one dotted field"
    assert "comma-separated" not in body
    assert "warn_time.0" in body and 'type="number"' in body


@pytest.mark.asyncio
async def test_step_4_renders_in_german(config_dir, clean_registry, monkeypatch):
    """The new strings come from the catalogs, not from the msgids."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, MAP_PLAN)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        body = await open_step(http)

    for german in ("Die Leiter", "Eine Zeile hinzufügen", "Zeile entfernen",
                   "Ein Spieler liest"):
        assert german in body, german


# ------------------------------------------------------- 5. the shapes the SAVE really writes

@pytest.mark.asyncio
async def test_the_save_writes_a_textless_ladder_as_a_list(scope_dir, node_data, clean_registry,
                                                           monkeypatch):
    """The plan a review form posts for a textless ladder lands in the file as the seconds alone."""
    import test_webui_scheduler_save as save_mod

    app = save_mod.app_for(scope_dir, monkeypatch, {"timezone": "UTC"})
    async with client(app) as http:
        await sign_in(http, save_mod.ADMIN)
        token = await save_mod.csrf_of(http)
        revision = await save_mod.revision_of(http)
        reply = await save_mod.save(http, {"_csrf_token": token, "_revision": revision,
                                           "_scope": save_mod.SERVER_A,
                                           "warn.times": ["600", "60"]})

    assert reply.status_code == 303, reply.text[:300]
    assert save_mod.read_block(scope_dir, save_mod.INSTANCE_A)["warn"] == {"times": [600, 60]}


@pytest.mark.asyncio
async def test_the_save_writes_a_ladder_with_texts_as_a_map(scope_dir, node_data, clean_registry,
                                                            monkeypatch):
    """One row carrying text makes the file hold the map of seconds to text, keys as numbers."""
    import test_webui_scheduler_save as save_mod

    app = save_mod.app_for(scope_dir, monkeypatch, {"timezone": "UTC"})
    async with client(app) as http:
        await sign_in(http, save_mod.ADMIN)
        token = await save_mod.csrf_of(http)
        revision = await save_mod.revision_of(http)
        reply = await save_mod.save(http, {"_csrf_token": token, "_revision": revision,
                                           "_scope": save_mod.SERVER_A,
                                           "shape.warn.times": "map",
                                           "warn.times.600": "ten minutes",
                                           "warn.times.60": "one minute"})

    assert reply.status_code == 303, reply.text[:300]
    assert save_mod.read_block(scope_dir, save_mod.INSTANCE_A)["warn"] == {
        "times": {600: "ten minutes", 60: "one minute"}}
