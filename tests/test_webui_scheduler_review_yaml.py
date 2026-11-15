"""The Scheduler wizard's review step: the block as YAML, and Expert Mode over that same text.

TWO READERS, ONE PRODUCER. The review step PRINTS the block a save of the shown plan produces — the
override applied to a copy of the file, serialised by the file's own round trip — and the save WRITES
that same block; a pin here parses the printed text and compares it to the block the file holds after
the save, so the two can never drift.

THE EXPERT DOOR takes the posted text AS the block, through the same guard, the same scope, the same
revision check and the same ``patch_config_file``. Refused are: text that does not parse, text that is
not a mapping, and a top-level key a server's own block cannot carry (``DEFAULT``, ``commands``,
``chat_commands``, a node, another server) — each with one sentence and nothing written.

The cluster is bound the way production binds it (``commandline.COMMAND_LINE_ARGS`` plus a reset
``validators.NodeData``), because the scheduler schema's root ``check_structure`` reads ``nodes.yaml``
through that singleton.
"""
from __future__ import annotations

import html as html_module
import re
from urllib.parse import urlencode

import pytest

from services.webservice import scheduler_plan
from services.webservice.pages import settings as settings_page
from test_webui_scheduler_save import (  # noqa: F401 - fixtures and helpers imported on purpose
    INSTANCE_A, INSTANCE_B, SERVER_A, ADMIN, DEFAULT_BLOCK, INSTANCE_B_BLOCK, app_for,
    clean_registry, client, csrf_of, digest, node_data, plan_fields, read_block,
    revision_of, save, save_pairs, scope_dir, sign_in, step_form_pairs, text_of,
)

PLAN = {"timezone": "Europe/Berlin", "schedule": {"00-12": "NNNNNNN"}}
PLAN_BLOCK = {"timezone": "Europe/Berlin", "schedule": {"00-12": "NNNNNNN"}}
REVIEW_PATH = "/fleet/" + SERVER_A


def review_url(plan) -> str:
    """The review step's URL for *plan* — the wizard keeps its state in the address."""
    pairs = [(field["name"], field["value"])
             for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]
    return REVIEW_PATH + "?tab=scheduler&step=5&" + urlencode(pairs)


def panel_yaml(page: str) -> str:
    """The text the panel's read-only block holds, un-escaped."""
    match = re.search(r'<pre class="st-yaml" data-cfg-yaml-readonly>(.*?)</pre>', page, re.S)
    assert match, "the review step rendered no read-only YAML block"
    return html_module.unescape(match.group(1))


def carrier_yaml(page: str) -> str:
    """The text the no-script textarea carries, un-escaped."""
    match = re.search(r'<textarea[^>]*data-cfg-yaml-text[^>]*>(.*?)</textarea>', page, re.S)
    assert match, "the review step rendered no YAML text carrier"
    return html_module.unescape(match.group(1))


def parse(text: str):
    from core.utils import configwrite

    return configwrite.load_config_text(text)


async def review_page(http, plan=PLAN):
    response = await http.get(review_url(plan))
    assert response.status_code == 200, response.text[:300]
    return response.text


# ------------------------------------------------- 1. the block the step prints is the block it writes

@pytest.mark.asyncio
async def test_the_review_step_prints_the_block_the_save_writes(scope_dir, node_data, clean_registry,
                                                                monkeypatch):
    """The rendered YAML is the block the file holds after the same save — not a summary of it."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http)
        printed = parse(panel_yaml(page))
        assert dict(printed) == PLAN_BLOCK, dict(printed)
        # the carrier holds the SAME text: the no-script editor starts from what a plain save writes
        assert carrier_yaml(page) == panel_yaml(page)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A} | plan_fields(PLAN))
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == PLAN_BLOCK
    assert read_block(scope_dir, INSTANCE_B) == INSTANCE_B_BLOCK
    assert read_block(scope_dir, "DEFAULT") == DEFAULT_BLOCK


@pytest.mark.asyncio
async def test_the_printed_block_is_this_servers_own_block_only(scope_dir, node_data, clean_registry,
                                                                monkeypatch):
    """DEFAULT and another server's block are never part of the printed text."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        printed = panel_yaml(await review_page(http))
    assert "DEFAULT" not in printed
    assert "startup_delay" not in printed
    assert "America/New_York" not in printed


# ------------------------------------------------------------------------- 2. the panel's contract

@pytest.mark.asyncio
async def test_the_review_step_carries_the_editor_contract_and_a_posting_textarea(
        scope_dir, node_data, clean_registry, monkeypatch):
    """Script off: the textarea IS the editor and its value posts; the toggle is a plain checkbox."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http)
    assert "data-cfg-yaml" in page and 'data-cfg-yaml-readonly' in page
    assert 'data-cfg-yaml-editor' in page and 'data-cfg-yaml-status' in page
    assert 'data-cfg-yaml-badge' in page
    # the panel posts to the wizard's OWN validate route, and it knows which server it edits
    assert re.search(r'data-validate-url="[^"]*' + re.escape(settings_page.SCHEDULER_VALIDATE_PATH),
                     page), "the panel names no validate route"
    assert re.search(r'data-validate-url="[^"]*_scope=' + re.escape(SERVER_A), page)
    # the carriers: the posting textarea, and the flag the toggle writes
    assert re.search(r'<textarea[^>]*name="' + scheduler_plan.BLOCK_YAML_FIELD
                     + r'"[^>]*data-cfg-yaml-text', page)
    assert re.search(r'<input[^>]*name="' + settings_page.SETTINGS_EXPERT_FIELD
                     + r'"[^>]*value="1"[^>]*data-cfg-expert', page)
    # the editor loads the ONE editor script, not a second implementation
    assert '/static/settings-yaml.js' in page
    assert '/static/vendor/codemirror/codemirror.min.js' in page
    assert '/static/vendor/codemirror/mode/yaml.min.js' in page
    # the script looks its carriers up in `form[data-async]`, then `.cfg-form`, then the panel's own
    # ancestor: this page must offer neither of the first two, or it would search the WRONG form
    assert "data-async" not in page and 'class="cfg-form' not in page


@pytest.mark.asyncio
async def test_the_panel_renders_in_the_visitors_language(scope_dir, node_data, clean_registry,
                                                          monkeypatch):
    """Every string on the panel is wrapped: German shows the catalog's words, not the msgids."""
    from services.webservice import i18n

    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        english = await review_page(http)
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = await review_page(http)
    assert "This server's block, as YAML" in english
    assert "Der Block dieses Servers, als YAML" in german
    assert "This server's block, as YAML" not in german


def test_the_shipped_editor_script_drives_this_panels_markup():
    """The REAL ``settings-yaml.js`` over the review step's own shape, through the node harness.

    Markup assertions cannot see what a script does with them. This boots the shipped file against
    the review panel's structure — the aside INSIDE the save form, no page form marker, the plain
    textarea shipping the block, and the ONE Expert control a NAMED ``_expert`` checkbox — so the
    toggle, the carriers, the validate call and the submit guard are all proved on this page's shape.
    """
    import json
    from pathlib import Path

    from conftest import run_node_harness

    root = Path(__file__).resolve().parents[1]
    done = run_node_harness(["node", str(Path(__file__).resolve().parent / "configgen_harness.js"),
                             str(root / "services" / "webservice" / "static" / "settings-yaml.js"),
                             "review"])
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)

    off = result["off"]
    assert off["editorBuilt"] is False and off["readonlyShown"] is True
    assert off["carrierHoldsBlock"] is True, "the no-script carrier must ship the block"
    assert off["postedFlag"] == "", "an unticked box posts no flag"
    assert off["submitPrevented"] is False and off["fetches"] == 0

    on = result["on"]
    assert on["editorBuilt"] is True and on["seededFromPanel"] is True
    assert on["postedFlag"] == "1", "the toggle must post the expert flag"
    assert on["carrierMatchesEditor"] is True
    assert on["editorShown"] is True and on["readonlyHidden"] is True
    assert on["badge"] == "editing"
    assert on["fetchMethod"] == "POST"
    assert on["fetchUrl"] == "/actions/settings/scheduler/validate?_scope=SRS-1"
    assert on["bodyCarriesCsrf"] is True and on["bodyCarriesBlock"] is True
    assert on["status"] == "Valid."

    assert result["change"]["carrierUpdated"] is True
    assert result["change"]["bodyCarriesEdited"] is True
    assert result["refused"]["submitPrevented"] is True
    assert result["refused"]["statusState"] == "bad"

    off_again = result["offAgain"]
    assert off_again["postedFlag"] == "" and off_again["carrier"] == ""
    assert off_again["readonlyShown"] is True and off_again["readonlyHoldsEdit"] is True
    assert off_again["submitPrevented"] is False


# ------------------------------------------------------------------ 3. Expert Mode: the write path

@pytest.mark.asyncio
async def test_an_expert_block_is_written_exactly_as_typed(scope_dir, node_data, clean_registry,
                                                           monkeypatch):
    """The posted block replaces the server's block — comments included — and nothing else moves."""
    app = app_for(scope_dir, monkeypatch)
    typed = ("timezone: Europe/Berlin\n"
             "warn:\n"
             "  message: 'restarting soon'   # the operator's own note\n"
             "schedule:\n"
             "  00-12: NNNNNNN\n")
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: typed,
                                  # a plan that says something ELSE: the expert text must win
                                  "timezone": "UTC", "schedule.00-12": "YYYYYYY"})
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == {
        "timezone": "Europe/Berlin",
        "warn": {"message": "restarting soon"},
        "schedule": {"00-12": "NNNNNNN"},
    }
    assert "# the operator's own note" in text_of(scope_dir)
    assert read_block(scope_dir, INSTANCE_B) == INSTANCE_B_BLOCK
    assert read_block(scope_dir, "DEFAULT") == DEFAULT_BLOCK


@pytest.mark.asyncio
async def test_a_key_the_expert_block_drops_leaves_the_file(scope_dir, node_data, clean_registry,
                                                            monkeypatch):
    """The block is REPLACED, so the schedule the text does not state is gone afterwards."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "timezone: Europe/Berlin\n"})
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == {"timezone": "Europe/Berlin"}


@pytest.mark.asyncio
async def test_an_unparseable_expert_block_is_refused_and_nothing_is_written(
        scope_dir, node_data, clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "a: [1"})
        assert reply.status_code == 303, reply.text[:300]
        notice = await review_page(http)
    assert digest(scope_dir) == before
    assert "not valid YAML" in html_module.unescape(notice)


@pytest.mark.asyncio
async def test_an_expert_block_that_is_not_a_mapping_is_refused(scope_dir, node_data,
                                                               clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "- timezone\n- schedule\n"})
        assert reply.status_code == 303, reply.text[:300]
        notice = await review_page(http)
    assert digest(scope_dir) == before
    assert "is not one" in html_module.unescape(notice)


@pytest.mark.asyncio
async def test_an_expert_block_naming_default_is_refused_and_named(scope_dir, node_data,
                                                                  clean_registry, monkeypatch):
    """``DEFAULT`` is one sentence away: the key is named, the block's contents never are."""
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "DEFAULT:\n  timezone: UTC\n"})
        assert reply.status_code == 303, reply.text[:300]
        notice = await review_page(http)
    assert digest(scope_dir) == before
    sentence = html_module.unescape(notice)
    assert "DEFAULT" in sentence and "which is not part of a server's own block" in sentence


@pytest.mark.asyncio
async def test_an_expert_block_naming_an_unknown_key_is_refused_and_named(scope_dir, node_data,
                                                                          clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "commands:\n  - restart\n"})
        assert reply.status_code == 303, reply.text[:300]
        notice = await review_page(http)
    assert digest(scope_dir) == before
    sentence = html_module.unescape(notice)
    assert "commands" in sentence and "which is not part of a server's own block" in sentence


@pytest.mark.asyncio
async def test_a_stale_revision_writes_nothing_on_the_expert_path(scope_dir, node_data,
                                                                 clean_registry, monkeypatch):
    app = app_for(scope_dir, monkeypatch)
    before = digest(scope_dir)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": "0" * 64,
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "1",
                                  scheduler_plan.BLOCK_YAML_FIELD: "timezone: Europe/Berlin\n"})
        assert reply.status_code == 303, reply.text[:300]
        notice = await review_page(http)
    assert digest(scope_dir) == before
    assert "stale" in html_module.unescape(notice)


@pytest.mark.asyncio
async def test_the_plan_derived_path_is_unchanged_when_no_expert_field_is_posted(
        scope_dir, node_data, clean_registry, monkeypatch):
    """The carrier ships on the page with the script off; without the flag it is NOT the candidate."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  # the block the render shipped — ignored, the flag is absent
                                  scheduler_plan.BLOCK_YAML_FIELD: "timezone: America/New_York\n"}
                                 | plan_fields(PLAN))
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == PLAN_BLOCK, read_block(scope_dir, INSTANCE_A)


@pytest.mark.asyncio
async def test_the_flag_zero_is_the_plan_path_too(scope_dir, node_data, clean_registry, monkeypatch):
    """The toggle's own off value (``_expert=0``) is not Expert Mode."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        reply = await save(http, {"_csrf_token": await csrf_of(http),
                                  "_revision": await revision_of(http),
                                  "_scope": SERVER_A,
                                  settings_page.SETTINGS_EXPERT_FIELD: "0",
                                  scheduler_plan.BLOCK_YAML_FIELD: "timezone: America/New_York\n"}
                                 | plan_fields(PLAN))
    assert reply.status_code == 303, reply.text[:300]
    assert read_block(scope_dir, INSTANCE_A) == PLAN_BLOCK


# --------------------------------------------------------- 4. the validate route, one candidate

@pytest.mark.asyncio
async def test_the_validate_route_accepts_the_expert_field(scope_dir, node_data, clean_registry,
                                                           monkeypatch):
    """The editor posts its text and the token only; the verdict is on the block it would write."""
    app = app_for(scope_dir, monkeypatch)
    url = settings_page.SCHEDULER_VALIDATE_PATH + "?_scope=" + SERVER_A
    async with client(app) as http:
        await sign_in(http, ADMIN)
        token = await csrf_of(http)
        good = await http.post(url, data={"_csrf_token": token,
                                          scheduler_plan.BLOCK_YAML_FIELD:
                                              "timezone: Europe/Berlin\n"})
        bad = await http.post(url, data={"_csrf_token": token,
                                         scheduler_plan.BLOCK_YAML_FIELD:
                                             "DEFAULT:\n  timezone: UTC\n"})
        broken = await http.post(url, data={"_csrf_token": token,
                                            scheduler_plan.BLOCK_YAML_FIELD: "a: [1"})
    assert good.json()["ok"] is True, good.json()
    assert bad.json()["ok"] is False and "DEFAULT" in bad.json()["message"]
    assert broken.json()["ok"] is False and "not valid YAML" in broken.json()["message"]


@pytest.mark.asyncio
async def test_the_validate_route_refuses_a_candidate_the_schema_rejects(scope_dir, node_data,
                                                                         clean_registry, monkeypatch):
    """A block that parses and owns its keys, but breaks the schema, is a REFUSAL not a green mark."""
    app = app_for(scope_dir, monkeypatch)
    url = settings_page.SCHEDULER_VALIDATE_PATH + "?_scope=" + SERVER_A
    async with client(app) as http:
        await sign_in(http, ADMIN)
        verdict = await http.post(url, data={
            "_csrf_token": await csrf_of(http),
            scheduler_plan.BLOCK_YAML_FIELD: "schedule: not-a-map\n"})
    assert verdict.json()["ok"] is False, verdict.json()



@pytest.mark.asyncio
async def test_a_plan_that_changes_nothing_prints_the_block_the_file_already_holds(
        scope_dir, node_data, clean_registry, monkeypatch):
    """An unchanged save writes nothing, so the panel is the block as it stands — comments included."""
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, {"timezone": "Europe/Berlin",
                                        "schedule": {"00-12": "YYYYYYY"}})
    assert dict(parse(panel_yaml(page))) == {"timezone": "Europe/Berlin",
                                             "schedule": {"00-12": "YYYYYYY"}}
    assert "# this server's own zone" in panel_yaml(page)


@pytest.mark.asyncio
async def test_a_server_that_inherits_everything_prints_no_block_and_says_so(
        scope_dir, node_data, clean_registry, monkeypatch):
    """No own block and nothing to write: the panel is empty and the step states it in words."""
    path = scope_dir / "plugins" / "scheduler.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.split("Host-A-Instance:")[0], encoding="utf-8")
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        page = await review_page(http, {"timezone": "UTC"})
    assert panel_yaml(page) == ""
    assert carrier_yaml(page) == ""
    assert "This save writes no block of its own for this server" in page


# ------------------------------------------- 3. the week the operator edits is the week reviewed

#: the step-1 week rows as a browser submits them, with row 0 switched off
WEEK_EDIT = ("week_start.0=00%3A00&week_end.0=12%3A00&week_days.0=NNNNNNN"
             "&week_start.1=12%3A00&week_end.1=00%3A00&week_days.1=NNNNNNN")


@pytest.mark.asyncio
async def test_a_week_edit_reaches_the_review_step_and_the_save(scope_dir, node_data, clean_registry,
                                                               monkeypatch):
    """A week the operator changes on step 1 is the week the review prints AND the week the save writes.

    The rows carry the key they were READ from, so an untouched range keeps the file's own spelling
    (``00-12``, never ``00:00-12:00`` beside it) and the whole week is replaced rather than merged: a
    range the operator removed must not survive the write.
    """
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        # the form the page renders for the file as it is, with row 0's days switched off and one
        # more row appended — what the operator's browser would submit
        pairs = [(name, "NNNNNNN" if name == "week_days.0" else value)
                 for name, value in await step_form_pairs(http, "", step="1")]
        pairs += [("week_start.1", "12:00"), ("week_end.1", "00:00"),
                  ("week_days.1", "NNNNNNN"), ("week_key.1", "")]
        assert ("week_key.0", "00-12") in pairs, "the row carries the key it was read from"
        url = REVIEW_PATH + "?tab=scheduler&step=5&" + urlencode(pairs + [("step", "5")])
        page = await http.get(url)
        assert page.status_code == 200, page.text[:300]
        printed = parse(panel_yaml(page.text))
        reply = await save_pairs(http, pairs)

    assert reply.status_code == 303, reply.text[:300]
    week = {"00-12": "NNNNNNN", "12-24": "NNNNNNN"}
    assert dict(printed)["schedule"] == week, dict(printed)["schedule"]
    assert read_block(scope_dir, INSTANCE_A)["schedule"] == week, \
        "the save writes the week the review printed, and only that week"


def test_writing_a_week_replaces_the_whole_map():
    """A schedule is one statement: merging it would leave a stale range running beside the new one."""
    block = {"timezone": "Europe/Berlin", "schedule": {"00-12": "YYYYYYY", "12-24": "NNNNNNN"}}
    scheduler_plan.apply_override({"A": dict(block)}, "A", {"set": {"schedule": {"09-10": "YYYYYYY"}}})
    document = {"A": dict(block)}
    scheduler_plan.apply_override(document, "A", {"set": {"schedule": {"09-10": "YYYYYYY"}}})
    assert document["A"]["schedule"] == {"09-10": "YYYYYYY"}
