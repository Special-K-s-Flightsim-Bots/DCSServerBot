"""The infractions read when the OPTIONAL punishment plugin is not loaded.

``get_player_infractions`` reads three lists. Two of them come from statements the punishment plugin
does not own (``missionstats`` and ``bans``) and answer with the plugin off; the third — the
``/penalty`` command's own statement — reads ``pu_events``, a table the punishment plugin's
migrations create, so it only exists while that plugin is loaded.

The read therefore ASKS whether the plugin is loaded and skips only that statement, and reports the
fact so the page can say why a points figure is not shown instead of printing ``0.00``.

* the ACTION is awaited with a recording pool and a node whose ``plugins`` list is preset — the
  ``PluginManager`` accessor the console uses reads exactly that list before ``load_plugins`` ran, so
  no seam is stubbed and the "which plugins are loaded" spelling is the shipped one;
* the VIEW and the PAGE are driven from the action's own answer shape;
* the SENTENCE the page states for the absent plugin is read back from each COMPILED ``webui``
  catalog, in every shipped language — the ``.mo`` is what renders, so a sentence present in the
  ``.po`` but never compiled still falls back to English.
"""
from __future__ import annotations

import datetime
import gettext
import re
import types
from pathlib import Path

import pytest

from core.actions import TRANSPORT_WEB, ActionContext
from plugins.mission.actions import get_player_infractions
from services.webservice.pages import player_detail as page
from services.webservice.readmodels.players import player_infractions
from test_webui_player_detail import detail_data, infractions_data  # noqa: F401 - shared shapes
from test_webui_players_read import Pool, player_row
from test_webui_write_pause import (  # noqa: F401 - the console's harness
    build, clean_registry, client, config_dir, fleet, open_page, sign_in,
)

UCID = "4b1f9c22" * 4

#: The statements' own markers — each one appears in exactly one statement of the read.
_PLAYER = "WHERE ucid = %(ucid)s"
_INFRACTIONS = "FROM missionstats m join players"
_PENALTY = "FROM pu_events"
_BANS = "FROM bans_hist"

STAMP = datetime.datetime(2026, 10, 24, 20, 38)


def infraction_row(**overrides) -> dict:
    row = {"Time": "26-10-24 20:38", "Infraction": "F/A-18C killed Kolya", "Count": 1}
    row.update(overrides)
    return row


def penalty_row(points, **overrides) -> dict:
    row = {"time": STAMP, "event": "TEAMKILL", "points": points}
    row.update(overrides)
    return row


def ban_row(**overrides) -> dict:
    row = {"Timeframe": "26-10-29 - 26-11-12", "Reason": "Teamkilling", "Status": "Active"}
    row.update(overrides)
    return row


def web_context(pool, plugins=None) -> ActionContext:
    """A WEB action context over *pool*, on a node whose loaded-plugin list is *plugins*.

    A node WITHOUT the attribute is built when *plugins* is ``None``: that is the process shape a
    caller reaches when nothing has published a plugin list at all.
    """
    node = types.SimpleNamespace(apool=pool)
    if plugins is not None:
        node.plugins = list(plugins)
    ctx = ActionContext(node=node, bus=types.SimpleNamespace(servers={}))
    ctx._transport = TRANSPORT_WEB
    ctx._scope = None
    return ctx


def pool_answering_everything() -> Pool:
    """A pool that answers all four statements, so "which ran" is the only thing left to observe."""
    return Pool([(_PLAYER, [player_row(discord_id=-1)]),
                 (_INFRACTIONS, [infraction_row()]),
                 (_PENALTY, [penalty_row(3.0), penalty_row(1.5)]),
                 (_BANS, [ban_row()])])


# ══════════════════════════════════════════════ 1. the action, punishment loaded


@pytest.mark.asyncio
async def test_with_punishment_loaded_the_penalty_statement_runs_and_the_flag_is_true():
    """The plugin IS loaded: the penalty statement runs once and its rows reach the answer."""
    pool = pool_answering_everything()
    result = await get_player_infractions(web_context(pool, ["mission", "punishment"]), UCID)

    assert result.success is True, result.message
    assert len(pool.statements_matching(_PENALTY)) == 1, "pu_events is read when the plugin is on"
    assert result.data["penalties_available"] is True
    assert [entry["points"] for entry in result.data["penalties"]] == [3.0, 1.5]
    assert result.data["penalty_total"] == pytest.approx(4.5)


@pytest.mark.asyncio
async def test_the_plugin_name_is_matched_without_case_or_a_second_spelling():
    """The configured list may carry the name in any case — the console compares lowercased."""
    pool = pool_answering_everything()
    result = await get_player_infractions(web_context(pool, ["Mission", "PUNISHMENT"]), UCID)

    assert result.data["penalties_available"] is True
    assert len(pool.statements_matching(_PENALTY)) == 1


# ══════════════════════════════════════════════ 2. the action, punishment absent


@pytest.mark.asyncio
async def test_without_punishment_the_penalty_statement_is_never_executed():
    """The plugin is OFF: ``pu_events`` is not asked for at all, and the answer says why."""
    pool = pool_answering_everything()
    result = await get_player_infractions(web_context(pool, ["mission"]), UCID)

    assert result.success is True, result.message
    assert pool.statements_matching(_PENALTY) == [], "a table that does not exist must not be read"
    assert result.data["penalties"] == []
    assert result.data["penalty_total"] == 0.0
    assert result.data["penalties_available"] is False


@pytest.mark.asyncio
async def test_without_punishment_the_other_two_lists_still_answer():
    """The primary caller keeps its two lists: the ban history must not be taken down with the log."""
    pool = pool_answering_everything()
    result = await get_player_infractions(web_context(pool, ["mission"]), UCID)

    assert len(pool.statements_matching(_INFRACTIONS)) == 1, "the report's own statement still runs"
    assert len(pool.statements_matching(_BANS)) == 1, "the ban history still runs"
    assert result.data["found"] is True
    assert len(result.data["infractions"]) == 1
    assert len(result.data["ban_history"]) == 1
    assert result.data["ban_history"][0]["status"] == "Active"


@pytest.mark.asyncio
async def test_a_node_that_publishes_no_plugin_list_counts_as_punishment_absent():
    """No list to read is not an error: the answer states the absence rather than failing the read."""
    pool = pool_answering_everything()
    result = await get_player_infractions(web_context(pool), UCID)

    assert result.success is True, result.message
    assert result.data["penalties_available"] is False
    assert pool.statements_matching(_PENALTY) == []
    assert len(result.data["ban_history"]) == 1


# ══════════════════════════════════════════════ 3. the real failure, replayed


class StrictPool(Pool):
    """A pool that FAILS the way the database does: a statement naming ``pu_events`` raises."""

    def answer(self, statement: str, params) -> list[dict]:
        if _PENALTY in statement:
            raise RuntimeError('relation "pu_events" does not exist')
        return super().answer(statement, params)


@pytest.mark.asyncio
async def test_the_missing_table_never_reaches_the_read_while_the_plugin_is_off():
    """The operator's traceback, replayed: the table is gone AND the plugin is not loaded."""
    pool = StrictPool([(_PLAYER, [player_row(discord_id=-1)]),
                       (_INFRACTIONS, [infraction_row()]),
                       (_BANS, [ban_row()])])
    result = await get_player_infractions(web_context(pool, ["mission"]), UCID)

    assert result.success is True, result.message
    assert result.data["penalties_available"] is False
    assert len(result.data["ban_history"]) == 1, "the ban history survives the absent table"


@pytest.mark.asyncio
async def test_the_missing_table_still_fails_the_read_while_the_plugin_claims_to_be_on():
    """The flag is the LOADED plugin, never an error swallowed on the way: a half-configured node
    reports the read as failed instead of answering an empty log."""
    pool = StrictPool([(_PLAYER, [player_row(discord_id=-1)]),
                       (_INFRACTIONS, [infraction_row()]),
                       (_BANS, [ban_row()])])
    result = await get_player_infractions(web_context(pool, ["mission", "punishment"]), UCID)

    assert result.success is False
    assert "pu_events" in result.message


# ══════════════════════════════════════════════ 4. the empty answer carries the flag too


@pytest.mark.asyncio
@pytest.mark.parametrize("plugins, available", [(["mission", "punishment"], True),
                                               (["mission"], False)])
async def test_the_empty_answer_carries_the_flag(plugins, available):
    """An unknown UCID answers the EMPTY dict on both paths, and it carries the same flag."""
    pool = Pool([(_INFRACTIONS, []), (_PENALTY, []), (_BANS, [])])
    result = await get_player_infractions(web_context(pool, plugins), UCID)

    assert result.success is True, result.message
    assert result.data["found"] is False
    assert result.data["penalties_available"] is available
    assert result.data["penalty_total"] == 0.0


# ══════════════════════════════════════════════ 5. the view


def test_the_view_takes_the_flag_from_the_answer():
    assert player_infractions({"penalties_available": False}).penalties_available is False
    assert player_infractions({"penalties_available": True}).penalties_available is True


def test_an_answer_without_the_flag_keeps_the_previous_behaviour():
    """An older answer (no key) reads as available: nothing disappears for an unchanged producer."""
    assert player_infractions({"penalty_total": 9.5}).penalties_available is True
    assert player_infractions(None).penalties_available is True


# ══════════════════════════════════════════════ 6. the page

SENTENCE = "Penalty points are not tracked, because the punishment plugin is not active."


def stub_reads(monkeypatch, *, available, penalties=None, total=9.5, ucid=UCID):
    """The page's read seam: the infractions answer carries *available* and *penalties*."""

    async def read(qualname, ctx, /, **params):
        if qualname == "get_player_detail":
            return types.SimpleNamespace(success=True, data=detail_data(ucid))
        if qualname == "get_player_infractions":
            data = infractions_data(ucid, penalty_total=total)
            data["penalties_available"] = available
            if penalties is not None:
                data["penalties"] = penalties
            return types.SimpleNamespace(success=True, data=data)
        if qualname == "get_player_statistics":
            raise AssertionError(qualname)
        raise AssertionError(qualname)

    monkeypatch.setattr(page, "read_action", read)


def render(config_dir, monkeypatch, **kwargs) -> str:
    import asyncio

    stub_reads(monkeypatch, **kwargs)
    app = build(config_dir, fleet(), plugins=("mission",))

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            return await open_page(http, f"/players/{UCID}/infractions")

    return asyncio.run(run())


def test_the_page_states_the_numbers_while_the_plugin_is_active(config_dir, clean_registry,
                                                               monkeypatch):
    """POSITIVE first: with the plugin on, nothing about the render changes."""
    html = render(config_dir, monkeypatch, available=True)

    assert SENTENCE not in html, "the sentence belongs to the absent case only"
    assert "<dt>Total</dt><dd>9.50</dd>" in html
    assert "Teamkill" in html, "the penalty log renders the plugin's own event word"
    assert "This player has no penalty points." not in html


def test_the_page_states_the_absence_instead_of_a_zero_total(config_dir, clean_registry,
                                                             monkeypatch):
    """The plugin is off: the total is a dash and the card says why."""
    html = render(config_dir, monkeypatch, available=False, penalties=[], total=0.0)

    assert SENTENCE in html
    assert "<dt>Total</dt><dd>—</dd>" in html
    assert "0.00" not in html, "a points figure that was never read must not be printed as zero"
    assert "This player has no penalty points." not in html, (
        "the empty line is for a MEASURED zero, never for an unread one")


def test_the_empty_line_stays_for_a_measured_zero_while_the_plugin_is_active(config_dir,
                                                                             clean_registry,
                                                                             monkeypatch):
    """Plugin on, no rows: today's sentence, and no absence sentence."""
    html = render(config_dir, monkeypatch, available=True, penalties=[], total=0.0)

    assert "This player has no penalty points." in html
    assert SENTENCE not in html


def test_the_moderation_figure_is_a_dash_when_the_plugin_is_off(config_dir, clean_registry,
                                                                monkeypatch):
    """The overview tab prints the same flag: no ``0.00`` points figure for an unread log."""
    import asyncio

    stub_reads(monkeypatch, available=False, penalties=[], total=0.0)
    app = build(config_dir, fleet(), plugins=("mission",))

    async def run():
        async with client(app) as http:
            await sign_in(http, "mod1")
            return await open_page(http, f"/players/{UCID}")

    html = asyncio.run(run())
    assert "<dt>Penalty points</dt><dd>—</dd>" in html
    assert "see the log" not in html, "there is no log to see"
    assert "0.00" not in html


# ══════════════════════════════════════════════ 7. the sentence, in every compiled catalog

#: The catalogs the console ships. ``de`` and ``es`` are the CONTROL of this test: they already carry
#: the translation, so a run in which they fail too measures the fallback rather than the catalogs.
CATALOG_LANGUAGES = ("de", "es", "ru", "cn")

#: The placeholder spellings a translation must keep. This sentence interpolates nothing.
_PLACEHOLDER = re.compile(r"%\(\w+\)[sdif]|%[sdif]|\{\{\s*\w+\s*\}\}|\{\w*\}")


def placeholders(text: str) -> set[str]:
    """The placeholders *text* interpolates, spelled as the catalogs spell them."""
    return {match.group(0).replace(" ", "") for match in _PLACEHOLDER.finditer(text)}


@pytest.mark.parametrize("language", CATALOG_LANGUAGES)
def test_the_compiled_catalog_states_the_absent_plugin_sentence_in_every_language(language):
    """The ``.mo`` the page renders from resolves the sentence, and keeps its placeholders.

    Each language is checked on its own so ``ru``/``cn`` falling back to English cannot hide behind
    a pass in ``de``/``es``: the same run reports per language.
    """
    catalog = gettext.translation("webui", localedir=str(Path(__file__).resolve().parents[1]
                                                         / "locale"),
                                  languages=[language], fallback=True)
    resolved = catalog.gettext(SENTENCE)

    assert resolved.strip(), f"{language}: the compiled webui catalog resolves the sentence to ''"
    assert resolved != SENTENCE, f"{language}: the compiled catalog still renders English"
    assert placeholders(resolved) == placeholders(SENTENCE), \
        f"{language}: the translation changes the placeholder set: {resolved!r}"
