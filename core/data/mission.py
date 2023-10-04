from __future__ import annotations
import asyncio
from core.data.dataobject import DataObject, DataObjectFactory
from dataclasses import dataclass, field
from datetime import datetime
from typing import Union, TYPE_CHECKING
from .. import Status, utils

if TYPE_CHECKING:
    from .server import Server

__all__ = ["Mission"]


@dataclass
@DataObjectFactory.register("Mission")
class Mission(DataObject):
    server: Server = field(compare=False)
    name: str
    map: str
    start_time: int = field(compare=False, default=0)
    mission_time: int = field(compare=False, default=0)
    real_time: int = field(compare=False, default=0)
    filename: str = None
    date: Union[str, datetime] = None
    num_slots_blue = 0
    num_slots_red = 0
    weather: dict = field(repr=False, default_factory=dict)
    clouds: dict = field(repr=False, default_factory=dict)
    airbases: list = field(repr=False, default_factory=list)

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    async def pause(self):
        if self.server.status == Status.RUNNING:
            self.server.send_to_dcs({"command": "pauseMission"})
            await self.server.wait_for_status_change([Status.PAUSED])

    async def unpause(self):
        if self.server.status == Status.PAUSED:
            self.server.send_to_dcs({"command": "unpauseMission"})
            await self.server.wait_for_status_change([Status.RUNNING])

    async def restart(self):
        self.server.send_to_dcs({"command": "restartMission"})
        # wait for a status change (STOPPED or LOADING)
        timeout = 180 if self.node.locals.get('slow_system', False) else 120
        await self.server.wait_for_status_change([Status.STOPPED, Status.LOADING], timeout)
        # wait until we are running again
        try:
            await self.server.wait_for_status_change([Status.RUNNING, Status.PAUSED], timeout)
        except asyncio.TimeoutError:
            self.log.debug(f'Trying to force start server "{self.server.name}" due to DCS bug.')
            await self.server.start()

    def update(self, data: dict):
        if 'current_map' in data:
            self.map = data['current_map']
        if 'current_mission' in data:
            self.name = data['current_mission']
        if 'start_time' in data:
            self.start_time = data['start_time']
        if 'mission_time' in data:
            self.mission_time = data['mission_time']
        if 'real_time' in data:
            self.real_time = data['real_time']
        if 'filename' in data:
            self.filename = data['filename']
        if 'num_slots_blue' in data:
            self.num_slots_blue = data['num_slots_blue']
        if 'num_slots_red' in data:
            self.num_slots_red = data['num_slots_red']
        if 'date' in data:
            if data['date']['Year'] >= 1970:
                self.date = datetime(data['date']['Year'], data['date']['Month'], data['date']['Day'], 0, 0)
            else:
                self.date = '{}-{:02d}-{:02d}'.format(data['date']['Year'], data['date']['Month'], data['date']['Day'])
        if 'weather' in data:
            self.weather = data['weather']
        if 'clouds' in data:
            self.clouds = data['clouds']
        if 'airbases' in data:
            self.airbases = data['airbases']
