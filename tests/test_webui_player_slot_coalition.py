"""The players table's SLOT and COALITION columns.

Two facts the DCS broadcast carries and the table now reads correctly: the SEAT a player occupies
(``unit_display_name`` — never DCS's coarse ``unit_type``, which reads ``"Planes"`` for a player
sitting in NO slot at all) and the SIDE the player is on (``Player.side``), shown as a sortable
column. ``unit_display_name`` itself is ``"?"`` while a side is picked but no seat is taken, and both
that and ``""`` mean "no seat" — the row then shows the table's own "nothing here" placeholder rather
than a value the player does not have.

Pins: the read model's two readers, the column's place in the allow-list and its sort link, and the
rendered rows of the standalone Players page.
"""
from __future__ import annotations

import re
import types

import pytest

from core import Side
from services.webservice.pages import dashboard as dashboard_page
from services.webservice.pages import players as players_page

#: The page that renders the live roster's write controls: the dashboard's Players tab. The
#: Players page is the database directory and its rows carry none — a row's name is the door to
#: the player's page, where the writes live.
ROSTER = f"{dashboard_page.DASHBOARD_PATH}?tab=players"
from services.webservice.readmodels import views
from services.webservice.readmodels.model import PlayerView
from services.webservice.readmodels.players import _coalition, _slot
from test_webui_write_message import Cluster, FakePlayer, instance, server_with_players
from test_webui_write_pause import (  # noqa: F401  (the fixtures are used by name)
    build, clean_registry, client, config_dir, sign_in,
)

SERVER = "SRS-1"
PLACEHOLDER = "—"


# ------------------------------------------------------------------------------ 1. the read model

def test_the_slot_is_the_unit_display_name_and_never_the_unit_type():
    """``unit_type`` is DCS's coarse bucket ("Planes" for anything airborne), not a seat."""
    assert _slot(types.SimpleNamespace(unit_display_name="", unit_type="Planes",
                                       group_name="")) == ""
    assert _slot(types.SimpleNamespace(unit_display_name="?", unit_type="Planes",
                                       group_name="")) == ""
    assert _slot(types.SimpleNamespace(unit_display_name="A-10C II", unit_type="Planes",
                                       group_name="Hog 1")) == "A-10C II · Hog 1"
    assert _slot(types.SimpleNamespace(unit_display_name="A-10C II", unit_type="Planes",
                                       group_name="")) == "A-10C II"
    # a double that carries no unit at all is not a seat either
    assert _slot(types.SimpleNamespace(group_name="")) == ""


def test_the_coalition_is_the_side_and_an_unknown_one_reads_empty():
    """A row never claims a side the player has not picked."""
    assert _coalition(types.SimpleNamespace(side=Side.RED)) == "Red"
    assert _coalition(types.SimpleNamespace(side=Side.BLUE)) == "Blue"
    assert _coalition(types.SimpleNamespace(side=Side.NEUTRAL)) == "Neutral"
    assert _coalition(types.SimpleNamespace(side=Side.UNKNOWN)) == ""
    assert _coalition(types.SimpleNamespace()) == ""


# ------------------------------------------------------------------------------- 2. the column

def test_the_players_table_offers_a_sortable_coalition_column():
    """The column is in the allow-list after the seat, and it sorts."""
    keys = [column.key for column in views.TABLE_COLUMNS["players"]]
    assert keys == ["name", "ucid", "server", "slot", "coalition"], keys
    assert views.normalise_sort("players", "coalition") == "coalition"
    assert "sort=coalition" in views.column_link(players_page.PLAYERS_PATH, "players", "coalition")

    def row(name, coalition):
        return PlayerView(name=name, ucid=name.lower(), server_name=SERVER, slot="", active=True,
                          coalition=coalition)

    rows = [row("Viper", "Red"), row("Ghost", ""), row("Hog", "Red"), row("Falcon", "Blue")]
    assert [r.name for r in views.sort_rows("players", rows, "coalition")] == \
        ["Ghost", "Falcon", "Hog", "Viper"]
    # descending, with the NAME as the ascending tie-breaker in both directions
    assert [r.name for r in views.sort_rows("players", rows, "coalition", "desc")] == \
        ["Hog", "Viper", "Falcon", "Ghost"]


# ------------------------------------------------------------------------------ 3. the rendered page

class SeatPlayer(FakePlayer):
    """A player double whose seat and side a test chooses."""

    def __init__(self, name, ucid, *, unit="A-10C II", group="Hog 1", side=None):
        super().__init__(name, ucid)
        self.unit_display_name = unit
        self.group_name = group
        self.side = side


def seat_fleet():
    """One server with three players: a seated one, one who picked a side but no seat, and one in
    no slot and no side at all."""
    players = [SeatPlayer("Viper", "v1", unit="A-10C II", group="Hog 1", side=Side.RED),
               SeatPlayer("Fragmagnet", "f1", unit="?", group="", side=None),
               SeatPlayer("Ghost", "g1", unit="", group="", side=Side.BLUE)]
    srv = server_with_players(SERVER, ["Host A"], players=players)
    node = types.SimpleNamespace(name="dcs-a-01", master=True,
                                instances={"Host-A-Instance": srv})
    return Cluster([srv], nodes={"dcs-a-01": node}, node=node,
                   instances=[instance("Host-A-Instance", srv)])


def row_of(html: str, name: str) -> str:
    """The rendered ``<tr>`` whose name cell carries *name*."""
    match = re.search(r"<tr>(?:(?!</tr>).)*?<b>" + re.escape(name) + r"</b>.*?</tr>", html, re.S)
    assert match, f"no row rendered for {name}:\n{html}"
    return match.group(0)


def cells(row: str) -> list[str]:
    """The row's cells as plain text, markup stripped."""
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


@pytest.mark.asyncio
async def test_the_players_page_shows_the_seat_and_the_side_with_a_placeholder(config_dir,
                                                                             clean_registry):
    """ACCEPTANCE — a seat reads as the aircraft (never "Planes"), a side reads its colour, and both
    are the table's placeholder when the player has neither."""
    app = build(config_dir, seat_fleet())
    async with client(app) as http:
        await sign_in(http, "mod1")
        response = await http.get(ROSTER)
        assert response.status_code == 200, response.text
        page = response.text

    assert "Coalition" in page and "sort=coalition" in page, "the column is not offered"
    assert ">Planes<" not in page, "the coarse unit_type still reaches the table"

    seated = cells(row_of(page, "Viper"))
    assert seated[3:5] == ["A-10C II · Hog 1", "Red"], seated
    seatless = cells(row_of(page, "Fragmagnet"))
    assert seatless[3:5] == [PLACEHOLDER, PLACEHOLDER], seatless
    side_only = cells(row_of(page, "Ghost"))
    assert side_only[3:5] == [PLACEHOLDER, "Blue"], side_only
