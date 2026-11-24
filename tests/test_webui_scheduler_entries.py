"""Step 3's entry list: adding and removing entries, the shared picks, and the key the step no longer offers.

Four sections, one per behaviour the step owes:

1. THE ROW OPS — ``addentry=1`` renders one more card, ``removeentry=<index>`` drops exactly that
   entry and renumbers the rest, and a removal that names no posted entry (a word, or a position the
   form did not post) is REFUSED in one sentence rather than applied to a different entry. The plan
   asserted is the one ``wizard_plan`` hands the review step and the save, not the render alone.
2. THE NEW ENTRY — it starts as ``method: restart`` with no trigger stated, and its card says that
   nothing happens until one is.
3. THE PICKS ARE SHARED — an entry's mission ids, mission files and preset pick are the SAME controls
   and the same encodings step 2 renders, produced by one helper and one template macro.
4. ``shutdown`` IS NOT OFFERED — the step draws no control for it, a file that carries it keeps it
   through a save, and the save may not prune it.

The page pins run the SHIPPED route, template and plan reader; the two reads the picks come from are
stubbed at the page's own seams (the fixture the step-2 pins use), because a mission list is not this
module's subject.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

import pytest

from services.webservice import scheduler_plan
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import settings as settings_page
from test_webui_scheduler_save import (  # noqa: F401 - fixtures and helpers imported on purpose
    ADMIN, INSTANCE_A, app_for, csrf_of, node_data, read_block, revision_of, scheduler_path,
    scope_dir,
)
from test_webui_scheduler_step2_picks import (  # noqa: F401 - fixtures imported on purpose
    PRESETS, reads, select_of,
)
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures and helpers imported on purpose
    SERVER_A, WIZARD_PLAN, build, clean_registry, client, cluster, config_dir, hidden_inputs,
    install_plugin, scheduler_file, sign_in, step_page, tab_body,
)

#: three entries with distinct values, so a removal can be shown to leave the OTHERS untouched.
THREE_ENTRIES = {
    "timezone": "Europe/Berlin",
    "action": [
        {"times": ["05:15"], "method": "restart"},
        {"times": ["09:45"], "method": "stop", "populated": False},
        {"cron": "0 4 * * *", "method": "load", "mission_id": 2},
    ],
}

#: the entry-based scheduler file for the save pin: the server's block carries one entry whose
#: ``shutdown`` key is set, and DEFAULT states no action at all.
ENTRY_YAML = """\
DEFAULT:
  startup_delay: 10
  timezone: UTC
Host-A-Instance:
  timezone: Europe/Berlin
  action:
    times:
    - 04:00
    method: restart
    shutdown: true
"""


def posted(plan: dict, *op: tuple[str, str]) -> str:
    """The pairs step 3's own form posts for *plan*, as the browser builds them.

    Every value the step draws AND every value it carries hidden is a field of the one GET form, and
    ``form_fields`` is the reader that produced those hidden inputs — so its pairs plus the shape
    markers plus the row op are exactly what a browser sends.
    """
    pairs = [(field["name"], field["value"])
             for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]
    return urlencode(pairs + list(op))


def action_of(plan: dict, extra: str = ""):
    """The ``action`` value the wizard derives from *plan* plus *extra* — what the review step sees."""
    from urllib.parse import parse_qsl

    pairs = [(field["name"], field["value"])
             for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]
    posted_pairs = pairs + (parse_qsl(extra) if extra else [])
    return scheduler_plan.wizard_plan(plan, posted_pairs).get("action")


def cards(body: str) -> list[str]:
    """Every entry card of step 3, in the page's order — the card, not the whole page."""
    parts = body.split('<div class="card">')[1:]
    return [part for part in parts if 'name="action.' in part]


def field_value(card: str, name: str) -> str | None:
    """One input's ``value`` by its field name inside ONE card, or ``None``."""
    match = re.search(r'<input[^>]*name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', card)
    if match:
        return match.group(1)
    match = re.search(r'<input[^>]*value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', card)
    return match.group(1) if match else None


# --------------------------------------------------------------- 1. add and remove

@pytest.mark.asyncio
async def test_add_renders_one_more_entry_card(config_dir, clean_registry, monkeypatch, reads):
    """``addentry=1`` appends exactly one card; every stated entry keeps its own values."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, THREE_ENTRIES)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        before = tab_body(await step_page(http, "3"))
        after = tab_body(await step_page(http, "3", posted(THREE_ENTRIES,
                                                           ("addentry", "1"))))

    assert len(cards(before)) == 3
    assert len(cards(after)) == 4, "one more entry card, and no other change"
    assert field_value(cards(after)[3], "action.3.method") is None, \
        "the new entry is a method SELECT, see the select pin below"
    assert '"restart" selected' in cards(after)[3], "a new entry starts as method restart"


@pytest.mark.asyncio
async def test_a_new_entry_states_method_restart_and_no_trigger(config_dir, clean_registry,
                                                               monkeypatch, reads):
    """The new entry is ``method: restart`` and nothing else: no trigger is invented for it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, THREE_ENTRIES)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3", posted(THREE_ENTRIES, ("addentry", "1"))))

    entries = action_of(THREE_ENTRIES, "addentry=1")
    assert len(entries) == 4
    assert entries[3] == {"method": scheduler_plan.NEW_ENTRY_METHOD}
    for key in scheduler_plan.ACTION_TRIGGER_KEYS:
        assert key not in entries[3], key
    # the card SAYS that nothing happens yet, and says it once — only the new entry states no trigger
    assert body.count(scheduler_plan.ACTION_NO_TRIGGER) == 1


@pytest.mark.asyncio
async def test_remove_drops_exactly_that_entry_and_keeps_the_others(config_dir, clean_registry,
                                                                    monkeypatch, reads):
    """``removeentry=<index>`` takes THAT entry out; the rest keep their values in their order."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(THREE_ENTRIES))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3", posted(THREE_ENTRIES,
                                                          ("removeentry", "1"))))

    entries = action_of(THREE_ENTRIES, "removeentry=1")
    assert entries == [THREE_ENTRIES["action"][0], THREE_ENTRIES["action"][2]]
    drawn = cards(body)
    assert len(drawn) == 2, "the removed entry's card is gone"
    assert field_value(drawn[0], "action_time.0.0") == "05:15", "times are rows now"
    assert field_value(drawn[1], "action.1.cron") == "0 4 * * *"
    assert "09:45" not in body, "the removed entry's own values are gone"
    assert 'value="false" checked' not in body, \
        "the removed entry's flag is gone too, and the entries are renumbered from zero"
    controls = re.findall(r'class="st-trash sw-rm" name="removeentry" value="(\d+)" '
                          r'title="Remove entry (\d+)" aria-label="Remove entry \d+"', body)
    assert controls == [("0", "1"), ("1", "2")], \
        "one glyph per remaining entry, at the end of its own card and named for its number"
    assert ">Remove entry" not in body, "the word is an attribute, never visible text"


@pytest.mark.asyncio
async def test_a_removal_that_names_a_word_is_refused_and_changes_nothing(config_dir,
                                                                         clean_registry,
                                                                         monkeypatch, reads):
    """A value that is not a position is one sentence, and the plan stays exactly as posted."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, THREE_ENTRIES)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3", posted(THREE_ENTRIES,
                                                          ("removeentry", "second"))))

    assert scheduler_plan.ACTION_REMOVE_NOT_A_POSITION in body
    assert action_of(THREE_ENTRIES, "removeentry=second") == THREE_ENTRIES["action"]
    assert len(cards(body)) == 3


@pytest.mark.asyncio
async def test_a_removal_of_a_position_the_form_did_not_post_is_refused(config_dir,
                                                                       clean_registry,
                                                                       monkeypatch, reads):
    """A number no posted entry carries is refused too: it must not drop a neighbour."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, THREE_ENTRIES)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3", posted(THREE_ENTRIES,
                                                          ("removeentry", "9"))))

    assert scheduler_plan.ACTION_REMOVE_NO_SUCH_ENTRY in body
    assert action_of(THREE_ENTRIES, "removeentry=9") == THREE_ENTRIES["action"]
    assert len(cards(body)) == 3


@pytest.mark.asyncio
async def test_a_block_with_no_entry_offers_the_way_to_add_one(config_dir, clean_registry,
                                                              monkeypatch, reads):
    """The empty state keeps the Add control: a plan with no entry is no longer a dead end."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        empty = tab_body(await step_page(http, "3"))
        added = tab_body(await step_page(http, "3", "addentry=1"))

    assert 'name="addentry" value="1"' in empty
    assert not cards(empty), "no entry card is drawn for a block that states none"
    assert len(cards(added)) == 1, "the add control is what makes the first entry"


@pytest.mark.asyncio
async def test_an_added_entry_survives_the_next_step(config_dir, clean_registry, monkeypatch,
                                                     reads):
    """The row op is applied to the PLAN, so step 4 carries the entry the operator just added."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, THREE_ENTRIES)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        pairs = posted(THREE_ENTRIES, ("addentry", "1"))
        body = tab_body(await step_page(http, "3", pairs + "&step=4"))

    carried = hidden_inputs(body)
    assert carried["action.3.method"] == "restart", "the added entry rides into the next step"
    assert "action.0.times" in carried and "action.2.cron" in carried


# ------------------------------------------------------- 2. the picks are step 2's own

@pytest.mark.asyncio
async def test_the_entry_draws_the_same_picks_with_the_same_field_names(config_dir,
                                                                       clean_registry,
                                                                       monkeypatch, reads):
    """One helper, one macro: the controls differ only by the prefix of the values they write."""
    from test_webui_scheduler_step2_picks import preset_entry

    picked = [preset_entry("config/presets.yaml", "NoMods")]
    plan = dict(WIZARD_PLAN, startup={"mission_id": 2, "settings": list(picked),
                                      "presets": "config/presets.yaml"},
                action=[{"times": ["04:00"], "method": "load", "mission_id": 2,
                         "settings": list(picked), "presets": "config/presets.yaml"}])
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        startup = tab_body(await step_page(http, "2"))
        entry = tab_body(await step_page(http, "3"))

    assert '"load" selected' in entry, "the mission picks belong to a load and its own card"
    for leaf in ("mission_id", "mission_file", "settings"):
        step2 = select_of(startup, f"startup.{leaf}")
        step3 = select_of(entry, f"action.0.{leaf}")
        assert " multiple" in step3, leaf
        # the SAME options, in the same order, with the same selected entries
        assert re.findall(r'<option[^>]*>', step2) == re.findall(r'<option[^>]*>', step3), leaf
    # the preset pick's encodings carry the file label and a newline, from preset_option_value
    assert f'value="{picked[0]}" selected' in select_of(entry, "action.0.settings")
    assert 'data-preset-name="action.0.settings"' in entry
    assert 'data-preset-name="startup.settings"' in startup
    # the configured preset path is carried, never offered, exactly as on step 2
    assert hidden_inputs(entry).get("action.0.presets") == "config/presets.yaml"
    assert 'name="action.0.presets"' not in re.sub(r'<input type="hidden"[^>]*>', "", entry)


@pytest.mark.asyncio
async def test_an_entry_pick_writes_its_own_settings_as_a_list(config_dir, clean_registry,
                                                               monkeypatch, reads):
    """The per-entry pick posts its name once per ticked preset and is read back as that entry's list."""
    from test_webui_scheduler_step2_picks import preset_entry
    from urllib.parse import parse_qsl

    pairs = parse_qsl(posted(THREE_ENTRIES))
    pairs += [("action.2.settings", preset_entry("config/presets.yaml", "NoMods")),
              ("action.2.settings", preset_entry("config/presets_winter.yaml", "Snow"))]
    plan = scheduler_plan.wizard_plan(THREE_ENTRIES, pairs)

    assert plan["action"][2]["settings"] == [preset_entry("config/presets.yaml", "NoMods"),
                                             preset_entry("config/presets_winter.yaml", "Snow")]
    # and the other entries are not given a settings list by it
    assert "settings" not in plan["action"][0] and "settings" not in plan["action"][1]


@pytest.mark.asyncio
async def test_a_failed_read_leaves_the_entry_its_text_box_and_the_sentence(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch, reads):
    """Step 3 makes the same read; when it answers nothing the entry keeps a text box and says why."""
    from test_webui_scheduler_step2_picks import preset_entry

    reads["failed"].add("missions")
    reads["failed"].add("presets")
    plan = dict(WIZARD_PLAN, action=[{"times": ["05:15"], "method": "load",
                                      "mission_id": 2,
                                      "settings": [preset_entry("config/presets.yaml", "NoMods")]}])
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    assert 'name="action.0.mission_id" multiple' not in body
    assert 'type="text" name="action.0.mission_id" value="2"' in body, "the stated id survives"
    assert 'type="text" name="action.0.settings"' in body
    assert scheduler_plan.MISSION_LIST_UNAVAILABLE in body
    assert scheduler_plan.PRESET_LIST_UNAVAILABLE in body


# ------------------------------------------------------------- 3. shutdown is not offered

@pytest.mark.asyncio
@pytest.mark.parametrize("method, drawn", [("restart", True), ("rotate", True),
                                           ("load", False), ("stop", False)])
async def test_the_shutdown_control_belongs_to_the_two_methods_that_keep_the_process(
        config_dir, clean_registry, monkeypatch, reads, method, drawn):
    """A restart and a rotation stop the server PROCESS first; a load, stop or shutdown never does."""
    plan = dict(WIZARD_PLAN, action=[{"times": ["04:00"], "method": method, "shutdown": True}])
    scheduler_file(config_dir)
    install_plugin(monkeypatch, plan)
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    control = 'type="checkbox" name="action.0.shutdown"'
    assert (control in body) is drawn, method
    if drawn:
        assert control + ' value="true" checked' in body, "a stated true is a ticked box"
        assert scheduler_plan.ACTION_SHUTDOWN_LABEL in body, "the label says PROCESS, not machine"
        assert scheduler_plan.ACTION_SHUTDOWN_HINT in body


@pytest.mark.asyncio
async def test_a_file_that_states_shutdown_keeps_it_across_a_save(scope_dir, node_data,
                                                                 clean_registry, monkeypatch):
    """Changing another key of the entry leaves ``shutdown: true`` in the file, byte for byte.

    The pairs posted are the ones the RENDERED step carries: the ticked checkbox among them, plus
    the entry's own control values, with ``method`` changed.
    """
    from test_webui_scheduler_save import effective

    scheduler_path(scope_dir).write_text(ENTRY_YAML, encoding="utf-8")
    plan = dict(effective(), action=[{"times": ["04:00"], "method": "restart", "shutdown": True}])
    app = app_for(scope_dir, monkeypatch, plan)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        step = tab_body(await step_page(http, "3"))
        assert 'type="checkbox" name="action.0.shutdown" value="true" checked' in step, \
            "the step states the file's value as a ticked box"
        body = urlencode([("_csrf_token", await csrf_of(http)),
                          ("_revision", await revision_of(http)),
                          ("_scope", SERVER_A)]
                         + list(hidden_inputs(step).items())
                         + [("action.0.times", "04:00"), ("action.0.method", "rotate"),
                            ("action.0.shutdown", "true")])
        reply = await http.post(settings_page.SCHEDULER_SAVE_PATH, content=body,
                                headers={"content-type": "application/x-www-form-urlencoded"})

    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert block["action"] == [{"times": ["04:00"], "method": "rotate", "shutdown": True}], \
        block["action"]


def test_clearing_the_shutdown_control_prunes_the_key():
    """The box is a real control now: unticking it is a deletion the save must be able to write."""
    assert "shutdown" in scheduler_plan.PRUNABLE_ACTION_KEYS
    assert "shutdown" in scheduler_plan.ACTION_KEYS
    assert "shutdown" in scheduler_plan.RENDERED_ACTION_KEYS
    override = scheduler_plan.minimal_override(
        {}, {"action": [{"times": ["04:00"], "shutdown": True}]},
        {"action": [{"times": ["04:00"], "method": "restart"}]})
    assert override["prune"]["action"] == [["shutdown"]], \
        "a plan that stops stating it deletes it, because the step draws it"


def test_a_new_entry_carries_the_change_of_entry_count_as_a_replacement():
    """A changed entry count is not merged entry by entry: the LIST is written wholesale."""
    assert scheduler_plan._aligned_entries([{"a": 1}, {"a": 2}], [{"a": 1}]) == [], \
        "unequal lengths replace the list, so a key the step did not state is not merged into it"


# ------------------------------------------------------------------------- 4. the catalogs

#: the strings the step adds, and the one it no longer offers.
NEW_STRINGS = {
    "Add an entry": "Einen Eintrag hinzufügen",
    "Remove entry %(number)s": "Eintrag %(number)s entfernen",
    "This server's plan schedules no actions yet.":
        "Der Plan dieses Servers plant noch keine Aktionen.",
    scheduler_plan.ACTION_NO_TRIGGER:
        "Für diesen Eintrag passiert nichts, bis ein Auslöser gesetzt ist",
    scheduler_plan.ACTION_REMOVE_NOT_A_POSITION:
        "Ein Entfernen muss einen Eintrag über seine Positionsnummer nennen",
    scheduler_plan.ACTION_REMOVE_NO_SUCH_ENTRY:
        "Dieser Plan hat an dieser Position keinen Eintrag",
}


def test_every_catalog_carries_the_new_strings_and_no_shutdown_label():
    """Four catalogs, compiled: the new sentences are translated and the dead label is gone."""
    from services.webservice import i18n

    for language in ("cn", "de", "es", "ru"):
        translations = i18n.translations_for(language)
        for msgid in NEW_STRINGS:
            assert translations.gettext(msgid) not in (msgid, ""), f"{language}: {msgid!r}"
        assert translations.gettext("Shut the computer down afterwards") == \
            "Shut the computer down afterwards", f"{language}: a dead entry is still in the catalog"
        assert translations.gettext(
            "This server's plan schedules no actions. Nothing to change here.") == \
            "This server's plan schedules no actions. Nothing to change here.", language


@pytest.mark.asyncio
async def test_the_step_renders_its_new_strings_in_german(config_dir, clean_registry,
                                                          monkeypatch, reads):
    """The German page shows the catalog's words, and none of the dead label."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, action=[{"method": "restart"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        english = tab_body(await step_page(http, "3"))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = tab_body(await step_page(http, "3"))

    ON_PAGE = ("Add an entry", scheduler_plan.ACTION_NO_TRIGGER)
    for msgid in ON_PAGE:
        assert msgid in english, msgid
        assert NEW_STRINGS[msgid] in german, NEW_STRINGS[msgid]
    assert "Remove entry 1" in english and "Eintrag 1 entfernen" in german, \
        "the remove control names its entry"
    assert "Shut the computer down afterwards" not in english
