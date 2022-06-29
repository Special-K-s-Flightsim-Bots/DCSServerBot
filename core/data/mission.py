from __future__ import annotations
from core.data.dataobject import DataObject, DataObjectFactory
from dataclasses import dataclass, field
from datetime import datetime
from typing import Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .server import Server


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

    def pause(self):
        self.server.sendtoDCS({"command": "pauseMission"})

    def unpause(self):
        self.server.sendtoDCS({"command": "unpauseMission"})

    def restart(self):
        self.server.sendtoDCS({"command": "restartMission"})

    def update(self, data: dict):
        if 'start_time' in data:
            self.start_time = data['start_time']
        if 'mission_time' in data:
            self.mission_time = data['mission_time']
        if 'real_time' in data:
            self.real_time = data['real_time']
        if 'filename' in data:
            self.filename = data['filename']
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
