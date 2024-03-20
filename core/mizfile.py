from __future__ import annotations
import io
import shutil

import luadata
import os
import tempfile
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
                    try:
                        zout.write(file, f'l10n/DEFAULT/{os.path.basename(file)}')
                    except FileNotFoundError:
                        self.log.warning(f"- File {file} could not be found, skipping.")
        try:
            if new_filename and new_filename != self.filename:
                shutil.copy2(tmpname, new_filename)
            else:
                shutil.copy2(tmpname, self.filename)
            os.remove(tmpname)
        except PermissionError as ex:
            self.log.error(f"Can't write new mission file: {ex}")

    @property
    def theatre(self) -> str:
        return self.mission['theatre']

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
        def process_elements(reference: dict, **kwargs):
            if 'select' in config:
                if debug:
                    print("Processing SELECT ...")
                if config['select'].startswith('/'):
                    elements = list(utils.for_each(self.mission, config['select'][1:].split('/'),
                                                   debug=debug, **kwargs))
                else:
                    elements = list(utils.for_each(reference, config['select'].split('/'), debug=debug, **kwargs))
            else:
                elements = [reference]
            for element in elements:
                if not element:
                    if reference and 'insert' in config:
                        if debug:
                            print(f"Inserting new value: {config['insert']}")
                        reference |= config['insert']
                elif 'replace' in config:
                    for _what, _with in config['replace'].items():
                        if debug:
                            print(f"Replacing {_what} with {_with}")
                        if isinstance(_what, int) and isinstance(element, list):
                            element[_what - 1] = utils.evaluate(_with, reference=reference)
                        elif isinstance(_with, dict):
                            for key, value in _with.items():
                                if utils.evaluate(key, **element, reference=reference):
                                    element[_what] = utils.evaluate(value, **element, reference=reference)
                                    break
                        else:
                            element[_what] = utils.evaluate(_with, **element, reference=reference)
                elif 'merge' in config:
                    for _what, _with in config['merge'].items():
                        if debug:
                            print(f"Merging {_what} with {_with}")
                        if isinstance(_with, dict):
                            element[_what] |= _with
                        else:
                            for value in utils.for_each(self.mission, _with[1:].split('/'), debug=debug, **kwargs):
                                if isinstance(element[_what], dict):
                                    element[_what] |= value
                                else:
                                    element[_what] += value
                            if _with.startswith('/'):
                                utils.tree_delete(self.mission, _with[1:])
                            else:
                                utils.tree_delete(reference, _with)
                elif 'delete' in config:
                    if debug:
                        print("Processing DELETE ...")
                    if isinstance(element, list):
                        for _what in element.copy():
                            if utils.evaluate(config['delete'], **_what):
                                element.remove(_what)

        def check_where(reference: dict, config: Union[list, str], debug: bool, **kwargs: dict) -> bool:
            if isinstance(config, str):
                try:
                    next(utils.for_each(reference, config.split('/'), debug=debug, **kwargs))
                    return True
                except StopIteration:
                    return False
            else:
                for c in config:
                    if not check_where(reference, c, debug=debug, **kwargs):
                        return False
                return True

        if isinstance(config, list):
            for cfg in config:
                self.modify(cfg)
            return
        debug = config.get('debug', False)
        kwargs = {}
        if 'variables' in config:
            for name, value in config['variables'].items():
                if value.startswith('$'):
                    kwargs[name] = utils.evaluate(value, **kwargs)
                else:
                    kwargs[name] = next(utils.for_each(self.mission, value.split('/'), debug=debug, **kwargs))
        try:
            for_each = config['for-each'].lstrip('/')
        except KeyError:
            self.log.error("MizEdit: for-each missing in modify preset, skipping!")
            return
        for reference in utils.for_each(self.mission, for_each.split('/'), debug=debug, **kwargs):
            if 'where' in config:
                if debug:
                    print("Processing WHERE ...")
                if check_where(reference, config['where'], debug=debug, **kwargs):
                    process_elements(reference, **kwargs)
            else:
                process_elements(reference, **kwargs)


class UnsupportedMizFileException(Exception):
    def __init__(self, mizfile: str):
        super().__init__(f'The mission {mizfile} is not compatible with MizEdit. Please re-save it in DCS World.')
