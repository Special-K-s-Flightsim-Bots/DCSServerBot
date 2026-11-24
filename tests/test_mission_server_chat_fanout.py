"""The server CHAT fan-out: a coalition line reaches that side's ACTIVE players, one line each.

``Server.sendChatMessage`` implements ``Coalition.ALL`` as ONE server-wide line (``net.send_chat``)
and, for a coalition, fans the line out to the active players of that side — one TARGETED line per
player, through ``Player.sendChatMessage``, which posts ``{"command": "sendChatMessage", "to":
<player id>}`` and is the primitive the mission serves as ``net.send_chat_to``. DCS has no
coalition-scoped chat broadcast, so this enumeration is what makes the choice deliverable at all.

WHERE it runs: the ``players`` a server object carries are authoritative in the process that holds
it — the master. An agent-hosted server appears there as a ``ServerProxy`` whose player objects are
created by the master's own event listeners (agents forward the DCS events and load no listeners),
and ``services/servicebus/service.py`` pushes the ACTIVE count FROM that master-side set TO the
owning node, which is the direction that says where the truth is. The console's service is
``master_only``, so the object the console resolves is that same one; each targeted line then travels
to the owning node through the proxy's own ``send_to_dcs``.

The doubles below supply only the wire and the player attributes: the ENUMERATION is the real
``Server.get_active_players`` and the DELIVERY the real ``Player.sendChatMessage``, so the payload
shapes asserted here are the ones an install sends.
"""
from __future__ import annotations

import pytest

from core import Coalition
from core.data.const import Side
from core.data.player import Player
from core.data.server import Server

SENDER = "mod1"


class Wire:
    """The server double: its OWN players, the REAL enumeration, and the DCS wire recorded."""

    #: the real enumeration — ``active`` and ``side`` filtering is what is under test
    get_active_players = Server.get_active_players

    def __init__(self, players=()):
        self.players = {player.ucid: player for player in players}
        self.sent: list[dict] = []

    async def send_to_dcs(self, payload: dict):
        self.sent.append(payload)


class PlayerDouble:
    """A player carrying the real delivery method: the payloads below are ``Player``'s own."""

    sendChatMessage = Player.sendChatMessage

    def __init__(self, ucid: str, player_id: int, side: Side, *, active: bool = True):
        self.ucid = ucid
        self.id = player_id
        self.side = side
        self.active = active
        self.server: Wire | None = None


def wire_with(*players: PlayerDouble) -> Wire:
    wire = Wire(players)
    for player in players:
        player.server = wire
    return wire


def targeted(wire: Wire) -> list[tuple[int, str | None, str]]:
    """The ``(player id, sender, message)`` of every targeted line the wire saw, in order."""
    return [(payload["to"], payload["from"], payload["message"])
            for payload in wire.sent if "to" in payload]


# ---------------------------------------------------------------- 1. ALL is unchanged

@pytest.mark.asyncio
async def test_all_is_still_one_server_wide_line_per_message_line():
    """ACCEPTANCE — ``Coalition.ALL`` posts the SAME payloads it posted before, one per line."""
    wire = wire_with(PlayerDouble("u-red", 101, Side.RED), PlayerDouble("u-blue", 102, Side.BLUE))

    await Server.sendChatMessage(wire, Coalition.ALL, "Line one.\nLine two.", SENDER)

    assert wire.sent == [
        {"command": "sendChatMessage", "from": SENDER, "message": "Line one."},
        {"command": "sendChatMessage", "from": SENDER, "message": "Line two."},
    ]
    assert wire.sent[0].keys() == {"command", "from", "message"}, \
        "the server-wide line must carry no recipient"


@pytest.mark.asyncio
async def test_all_reaches_the_wire_even_with_nobody_flying():
    """The server-wide line is the game's own broadcast: it does not depend on a player list."""
    wire = wire_with()

    await Server.sendChatMessage(wire, Coalition.ALL, "Restart in 5.")

    assert wire.sent == [{"command": "sendChatMessage", "from": None, "message": "Restart in 5."}]


# ------------------------------------------------------- 2. the coalition fan-out targets

@pytest.mark.asyncio
async def test_a_red_line_reaches_exactly_the_active_red_players():
    """ACCEPTANCE — the other side and an inactive player of this side are both left out."""
    red_a = PlayerDouble("u-red-a", 101, Side.RED)
    red_b = PlayerDouble("u-red-b", 102, Side.RED)
    blue = PlayerDouble("u-blue", 103, Side.BLUE)
    red_spectator = PlayerDouble("u-red-off", 104, Side.RED, active=False)
    wire = wire_with(red_a, red_b, blue, red_spectator)

    await Server.sendChatMessage(wire, Coalition.RED, "Check your six.", SENDER)

    assert sorted(targeted(wire)) == [
        (101, SENDER, "Check your six."),
        (102, SENDER, "Check your six."),
    ]
    assert all(payload["command"] == "sendChatMessage" for payload in wire.sent)
    assert redirected_to(wire) == {101, 102}, "the fan-out reached somebody else"


def redirected_to(wire: Wire) -> set[int]:
    return {payload["to"] for payload in wire.sent if "to" in payload}


@pytest.mark.asyncio
async def test_a_blue_line_is_the_same_fan_out_for_the_other_side():
    """The mirror case, so the assertion above cannot be satisfied by a hardcoded side."""
    blue_a = PlayerDouble("u-blue-a", 201, Side.BLUE)
    red = PlayerDouble("u-red", 202, Side.RED)
    blue_spectator = PlayerDouble("u-blue-off", 203, Side.BLUE, active=False)
    wire = wire_with(blue_a, red, blue_spectator)

    await Server.sendChatMessage(wire, Coalition.BLUE, "Push north.", SENDER)

    assert targeted(wire) == [(201, SENDER, "Push north.")]


@pytest.mark.asyncio
async def test_a_multiline_coalition_message_reaches_every_player_line_by_line():
    """The per-line split is the delivery primitive's own (``Player.sendChatMessage``)."""
    red_a = PlayerDouble("u-red-a", 101, Side.RED)
    red_b = PlayerDouble("u-red-b", 102, Side.RED)
    wire = wire_with(red_a, red_b)

    await Server.sendChatMessage(wire, Coalition.RED, "Line one.\nLine two.", SENDER)

    assert wire.sent == [
        {"command": "sendChatMessage", "to": 101, "from": SENDER, "message": "Line one."},
        {"command": "sendChatMessage", "to": 101, "from": SENDER, "message": "Line two."},
        {"command": "sendChatMessage", "to": 102, "from": SENDER, "message": "Line one."},
        {"command": "sendChatMessage", "to": 102, "from": SENDER, "message": "Line two."},
    ]


@pytest.mark.asyncio
async def test_a_coalition_line_on_a_server_with_nobody_flying_sends_nothing():
    """A fan-out with no targets posts NO line: there is no server-wide fallback for a coalition."""
    wire = wire_with(PlayerDouble("u-blue", 103, Side.BLUE))

    await Server.sendChatMessage(wire, Coalition.RED, "Check your six.")

    assert wire.sent == []


@pytest.mark.asyncio
async def test_a_value_that_is_no_coalition_is_refused():
    """Only a real ``Coalition`` fans out: anything else is a programming error, not a broadcast."""
    wire = wire_with(PlayerDouble("u-red", 101, Side.RED))

    with pytest.raises(ValueError):
        await Server.sendChatMessage(wire, "red", "hi")

    assert wire.sent == []


# ------------------------------------------------------ 3. the node boundary of the fan-out

def test_the_agent_hosted_server_keeps_the_base_fan_out_and_its_own_transport():
    """The proxy does NOT override the fan-out: it inherits it and ROUTES each line itself.

    A ``ServerProxy`` overrides ``send_to_dcs`` to hand the payload to the owning node, so the
    enumeration happens on the master (where the player objects live) and the delivery on the node
    that owns the mission. An override of ``sendChatMessage`` on the proxy would move the
    enumeration to a process whose player set is empty, which is the failure this pins away.
    """
    from core.data.proxy.serverproxy import ServerProxy

    assert "sendChatMessage" not in ServerProxy.__dict__
    assert ServerProxy.sendChatMessage is Server.sendChatMessage
    assert "send_to_dcs" in ServerProxy.__dict__, \
        "the proxy must still own the per-line routing"
