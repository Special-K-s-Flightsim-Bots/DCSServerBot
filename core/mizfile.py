from __future__ import annotations
import io
import luadata
import math  # do not remove
import os
import tempfile
import random  # do not remove
import zipfile
from datetime import datetime
from typing import Union, Any, Optional

from core import utils

__all__ = [
    "MizFile",
    "UnsupportedMizFileException"
]


class MizFile:

    def __init__(self, root: Any, filename: str):
        self.log = root.log
        self.filename = filename
        self.mission = dict()
        self.options = dict()
        self._load()
        self._files: list[str] = list()

    def _load(self):
        try:
            with zipfile.ZipFile(self.filename, 'r') as miz:
                with miz.open('mission') as mission:
                    self.mission = luadata.unserialize(io.TextIOWrapper(mission, encoding='utf-8').read(), 'utf-8')
                try:
                    with miz.open('options') as options:
                        self.options = luadata.unserialize(io.TextIOWrapper(options, encoding='utf-8').read(), 'utf-8')
                except FileNotFoundError:
                    pass
        except Exception:
            raise UnsupportedMizFileException(self.filename)

    def save(self, new_filename: Optional[str] = None):
        tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(self.filename))
        os.close(tmpfd)
        with zipfile.ZipFile(self.filename, 'r') as zin:
            with zipfile.ZipFile(tmpname, 'w') as zout:
                zout.comment = zin.comment  # preserve the comment
                for item in zin.infolist():
                    if item.filename == 'mission':
                        zout.writestr(item, "mission = " + luadata.serialize(self.mission, 'utf-8', indent='\t',
                                                                             indent_level=0))
                    elif item.filename == 'options':
                        zout.writestr(item, "options = " + luadata.serialize(self.options, 'utf-8', indent='\t',
                                                                             indent_level=0))
                    elif os.path.basename(item.filename) not in [os.path.basename(x) for x in self._files]:
                        zout.writestr(item, zin.read(item.filename))
                for file in self._files:
                    zout.write(file, f'l10n/DEFAULT/{os.path.basename(file)}')
        try:
            if new_filename and new_filename != self.filename:
                if os.path.exists(new_filename):
                    os.remove(new_filename)
                os.rename(tmpname, new_filename)
            else:
                os.remove(self.filename)
                os.rename(tmpname, self.filename)
        except PermissionError as ex:
            self.log.error(f"Can't write new mission file: {ex}")

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
        if not self.mission.get('forcedOptions'):
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
        if not self.mission.get('forcedOptions'):
            self.mission['forcedOptions'] = values
        else:
            self.mission['forcedOptions'] |= values

    @property
    def miscellaneous(self) -> dict:
        return self.options.get('miscellaneous', {})

    @miscellaneous.setter
    def miscellaneous(self, values: dict):
        if not self.options.get('miscellaneous'):
            self.options['miscellaneous'] = values
        else:
            self.options['miscellaneous'] |= values

    @property
    def difficulty(self) -> dict:
        return self.options.get('difficulty', {})

    @difficulty.setter
    def difficulty(self, values: dict):
        if not self.options.get('difficulty'):
            self.options['difficulty'] = values
        else:
            self.options['difficulty'] |= values

    @property
    def files(self) -> list:
        return self._files

    @files.setter
    def files(self, files: list[str]):
        self._files = files

    def modify(self, config: Union[list, dict]) -> None:
        def process_element(reference: dict, where: Optional[dict] = None):
            if 'select' in config:
                if debug:
                    print("Processing SELECT ...")
                if config['select'].startswith('/'):
                    element = next(utils.for_each(self.mission, config['select'][1:].split('/'), debug=debug))
                else:
                    element = next(utils.for_each(reference, config['select'].split('/'), debug=debug))
            else:
                element = reference
            for _what, _with in config['replace'].items():
                if isinstance(_with, dict):
                    for key, value in _with.items():
                        if utils.evaluate(key, **element, reference=reference, where=where):
                            element[_what] = utils.evaluate(value, **element, reference=reference, where=where)
                            break
                else:
                    element[_what] = utils.evaluate(_with, **element, reference=reference, where=where)

        if isinstance(config, list):
            for cfg in config:
                self.modify(cfg)
            return
        debug = config.get('debug', False)
        for reference in utils.for_each(self.mission, config['for-each'].split('/'), debug=debug):
            if 'where' in config:
                if debug:
                    print("Processing WHERE ...")
                for where in utils.for_each(reference, config['where'].split('/'), debug=debug):
                    process_element(reference, where)
            else:
                process_element(reference)


class UnsupportedMizFileException(Exception):
    def __init__(self, mizfile: str):
        super().__init__(f'The mission {mizfile} is not compatible with MizEdit. Please re-save it in DCS World.')
