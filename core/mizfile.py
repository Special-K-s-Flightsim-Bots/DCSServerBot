from __future__ import annotations
import io
import luadata
import os
import tempfile
import zipfile
from datetime import datetime
from typing import Union, TYPE_CHECKING

if TYPE_CHECKING:
    from core import DCSServerBot


class MizFile:

    def __init__(self, bot: DCSServerBot, filename: str):
        self.bot = bot
        self.log = bot.log
        self.filename = filename
        self.mission = dict()
        self._load()

    def _load(self):
        with zipfile.ZipFile(self.filename, 'r') as miz:
            with miz.open('mission') as mission:
                self.mission = luadata.unserialize(io.TextIOWrapper(mission, encoding='utf-8').read(), 'utf-8')

    def save(self):
        tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(self.filename))
        os.close(tmpfd)
        with zipfile.ZipFile(self.filename, 'r') as zin:
            with zipfile.ZipFile(tmpname, 'w') as zout:
                zout.comment = zin.comment  # preserve the comment
                for item in zin.infolist():
                    if item.filename != 'mission':
                        zout.writestr(item, zin.read(item.filename))
                    else:
                        zout.writestr(item, "mission = " + luadata.serialize(self.mission, 'utf-8', indent='\t',
                                                                             indent_level=0))
        try:
            os.remove(self.filename)
            os.rename(tmpname, self.filename)
        except PermissionError:
            self.log.error(f"Can't change mission, please check permissions on {self.filename}!")

    @property
    def start_time(self) -> int:
        return self.mission['start_time']

    @start_time.setter
    def start_time(self, value: Union[int, str]) -> None:
        if isinstance(value, int):
            start_time = value
        else:
            start_time = int((datetime.strptime(value, "%H:%M") - datetime(1900, 1, 1)).total_seconds())
        self.mission['start_time'] = start_time

    @property
    def date(self) -> datetime:
        date = self.mission['date']
        return datetime(date['Year'], date['Month'], date['Day'])

    @date.setter
    def date(self, value: datetime) -> None:
        self.mission['date'] = {"Day": value.day, "Year": value.year, "Month": value.month}

    @property
    def temperature(self) -> float:
        return self.mission['weather']['season']['temperature']

    @temperature.setter
    def temperature(self, value: float) -> None:
        self.mission['weather']['season']['temperature'] = value

    @property
    def atmosphere_type(self) -> int:
        return self.mission['weather']['atmosphere_type']

    @atmosphere_type.setter
    def atmosphere_type(self, value: int) -> None:
        self.mission['weather']['atmosphere_type'] = value

    @property
    def wind(self) -> dict:
        return self.mission['weather']['wind']

    @wind.setter
    def wind(self, values: dict) -> None:
        if 'atGround' in values:
            self.mission['weather']['wind']['atGround'] |= values['atGround']
        if 'at2000' in values:
            self.mission['weather']['wind']['at2000'] |= values['at2000']
        if 'at8000' in values:
            self.mission['weather']['wind']['at8000'] |= values['at8000']

    @property
    def groundTurbulence(self) -> float:
        return self.mission['weather']['groundTurbulence']

    @groundTurbulence.setter
    def groundTurbulence(self, value: float) -> None:
        self.mission['weather']['groundTurbulence'] = value

    @property
    def enable_dust(self) -> bool:
        return self.mission['weather']['enable_dust']

    @enable_dust.setter
    def enable_dust(self, value: bool) -> None:
        self.mission['weather']['enable_dust'] = value

    @property
    def dust_density(self) -> int:
        return self.mission['weather']['dust_density']

    @dust_density.setter
    def dust_density(self, value: int) -> None:
        self.mission['weather']['dust_density'] = value

    @property
    def qnh(self) -> float:
        return self.mission['weather']['qnh']

    @qnh.setter
    def qnh(self, value: float) -> None:
        self.mission['weather']['qnh'] = value

    @property
    def clouds(self) -> dict:
        return self.mission['weather'].get('clouds', {})

    @clouds.setter
    def clouds(self, values: dict) -> None:
        # If we're using a preset, disable dynamic weather
        if self.atmosphere_type == 1 and 'preset' in values:
            self.atmosphere_type = 0
        if 'clouds' in self.mission['weather']:
            self.mission['weather']['clouds'] |= values
        else:
            self.mission['weather']['clouds'] = values

    @property
    def enable_fog(self) -> bool:
        return self.mission['weather']['enable_fog']

    @enable_fog.setter
    def enable_fog(self, value: bool) -> None:
        self.mission['weather']['enable_fog'] = value

    @property
    def fog(self) -> dict:
        return self.mission['weather']['fog']

    @fog.setter
    def fog(self, values: dict):
        self.mission['weather']['fog'] |= values

    @property
    def halo(self) -> dict:
        return self.mission['weather'].get('halo', {"preset": "off"})

    @halo.setter
    def halo(self, values: dict):
        if 'halo' in self.mission['weather']:
            self.mission['weather']['halo'] |= values
        else:
            self.mission['weather']['halo'] = values

    @property
    def requiredModules(self) -> list[str]:
        return self.mission['requiredModules']

    @requiredModules.setter
    def requiredModules(self, values: list[str]):
        self.mission['requiredModules'] = values

    @property
    def accidental_failures(self) -> bool:
        return self.mission['forcedOptions']['accidental_failures'] if 'forcedOptions' in self.mission else False

    @accidental_failures.setter
    def accidental_failures(self, value: bool) -> None:
        if value:
            raise NotImplemented("Setting of accidental_failures is not implemented.")
        if 'forcedOptions' not in self.mission:
            self.mission['forcedOptions'] = {
                'accidental_failures': value
            }
        else:
            self.mission['forcedOptions']['accidental_failures'] = value
        self.mission['failures'] = []

    @property
    def forcedOptions(self) -> dict:
        return self.mission.get('forcedOptions', {})

    @forcedOptions.setter
    def forcedOptions(self, values: dict):
        if 'accidental_failures' in values:
            self.accidental_failures = values['accidental_failures']
        if 'forcedOptions' in self.mission:
            self.mission['forcedOptions'] |= values
        else:
            self.mission['forcedOptions'] = values
