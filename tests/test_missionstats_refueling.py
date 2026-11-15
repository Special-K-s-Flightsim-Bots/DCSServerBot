"""Refuelling events: a STOP row closes the open refuelling from the initiator alone.

DCS raises ``S_EVENT_REFUELING_STOP`` without a target, and raises it for the tanker's own half of
the pair as well. The handler therefore closes the newest open row for the player instead of
matching on the tanker's type, and only touches the table when the lua half sent fuel figures.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from plugins.missionstats.listener import MissionStatisticsEventListener

SERVER = "CVW-66 Main Server"

# Payloads below are the rows a real server sent for one boom contact (mission times 2794-2896).


class Store:
    """Stands in for ``apool``: records every statement the handler executes."""

    def __init__(self):
        self.calls = []

    def connection(self):
        store = self

        class Conn:
            async def execute(self, sql, params=None):
                store.calls.append((" ".join(sql.split()), params))

            def __await__(self):
                return self._enter().__await__()

            async def _enter(self):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Conn()


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class Player:
    ucid = "abc123"
    unit_type = "FA-18C_hornet"
    name = "203 | 5kid"


class Server:
    name = SERVER
    mission_id = 42
    channels = {}

    def get_player(self, **kwargs):
        return PLAYER if kwargs.get("name") == PLAYER.name else None


PLAYER = Player()


def build(channel=None):
    listener = object.__new__(MissionStatisticsEventListener)
    listener.plugin = type("P", (), {
        "get_config": staticmethod(lambda server: {"persistence": False})})()
    listener.mission_stats = {SERVER: {"coalitions": {}}}
    listener.update = {}
    listener.apool = Store()
    listener.log = logging.getLogger("test.missionstats.refueling")
    listener.bot = type("B", (), {"get_channel": staticmethod(lambda _id: channel)})()
    return listener


async def dispatch(listener, server, data):
    """Drive the shipped callback: ``onMissionEvent`` is an ``Event`` wrapper."""
    await MissionStatisticsEventListener.onMissionEvent.callback(listener, server, data)


def start_row(target=True):
    data = {
        "channel": "-1", "command": "onMissionEvent", "time": 2798.203, "id": 7,
        "eventName": "S_EVENT_REFUELING", "server_name": SERVER,
        "initiator": {"type": "UNIT", "group_name": "VFA-166_WOLF2", "in_air": True,
                      "unit": {"id_": 16777477}, "coalition": 2, "unit_name": "WOLF2-3",
                      "name": PLAYER.name, "unit_type": "FA-18C_hornet", "fuel": 0.7723,
                      "life": 1, "category": 0},
    }
    if target:
        data["target"] = {"type": "UNIT", "group_name": "TEXACO-CAS", "in_air": True,
                          "unit": {"id_": 16845057}, "coalition": 2, "unit_name": "TEXACO-CAS-1",
                          "unit_type": "KC130", "life": 1, "category": 0}
    return data


def stop_row(target=False, comment=True, name=PLAYER.name):
    data = {
        "channel": "-1", "command": "onMissionEvent", "time": 2896.153, "id": 14,
        "eventName": "S_EVENT_REFUELING_STOP", "server_name": SERVER,
        "initiator": {"type": "UNIT", "group_name": "VFA-166_WOLF2", "in_air": True,
                      "unit": {"id_": 16777477}, "coalition": 2, "unit_name": "WOLF2-3",
                      "name": name, "unit_type": "FA-18C_hornet", "fuel": 1.2072,
                      "life": 1, "category": 0},
    }
    if comment:
        data["comment"] = '{"gained": 0.435, "lbs": 2130, "secs": 98.0, "full": null}'
    if target:
        data["target"] = {"type": "UNIT", "group_name": "TEXACO-CAS", "in_air": True,
                          "unit": {"id_": 16845057}, "coalition": 2, "unit_name": "TEXACO-CAS-1",
                          "unit_type": "KC130", "life": 1, "category": 0}
    return data


@pytest.mark.asyncio
async def test_stop_without_target_still_closes_the_row():
    listener = build()
    await dispatch(listener, Server(), stop_row(target=False))
    assert len(listener.apool.calls) == 1, listener.apool.calls
    sql, params = listener.apool.calls[0]
    assert sql.startswith("UPDATE refuelingstats"), sql
    assert "tanker =" not in sql, "a NULL tanker can never match: %s" % sql
    assert "ORDER BY id DESC" in sql, sql
    assert params == (2130, None, 98.0, 42, PLAYER.ucid, PLAYER.unit_type), params


@pytest.mark.asyncio
async def test_stop_with_target_closes_and_announces():
    channel = Channel()
    listener = build(channel=channel)
    await dispatch(listener, Server(), stop_row(target=True))
    assert len(listener.apool.calls) == 1, listener.apool.calls
    assert "tanker =" not in listener.apool.calls[0][0]
    await asyncio.sleep(0)
    assert len(channel.sent) == 1, channel.sent
    assert "2130" in channel.sent[0] and "KC130" in channel.sent[0], channel.sent


@pytest.mark.asyncio
async def test_stop_without_figures_writes_nothing():
    listener = build()
    await dispatch(listener, Server(), stop_row(target=True, comment=False))
    assert listener.apool.calls == [], "no fuel figures means nothing to close with"


@pytest.mark.asyncio
async def test_tanker_side_half_is_ignored():
    listener = build()
    await dispatch(listener, Server(), stop_row(target=True, name=None))
    assert listener.apool.calls == [], "the tanker's own half has no receiver to close"


@pytest.mark.asyncio
async def test_start_inserts_the_row_with_its_tanker():
    listener = build()
    await dispatch(listener, Server(), start_row())
    assert len(listener.apool.calls) == 1, listener.apool.calls
    sql, params = listener.apool.calls[0]
    assert sql.startswith("INSERT INTO refuelingstats"), sql
    assert params == (42, PLAYER.ucid, PLAYER.unit_type, "KC130"), params
