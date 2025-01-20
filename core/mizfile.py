from __future__ import annotations

import importlib
import io
import logging
import luadata
import os
import shutil
import tempfile
import zipfile

from core import utils
from datetime import datetime
from packaging.version import parse, Version
from typing import Union, Optional

__all__ = [
    "MizFile",
    "UnsupportedMizFileException"
]


class MizFile:

    def __init__(self, filename: str):
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        self.log = logging.getLogger(__name__)
        self.filename = filename
        self.mission: dict = {}
        self.options: dict = {}
        self.warehouses: dict = {}
        self._load()
        self._files: list[dict] = []
        self.node = ServiceRegistry.get(ServiceBus).node

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
                try:
                    with miz.open('warehouses') as warehouses:
                        self.warehouses = luadata.unserialize(io.TextIOWrapper(warehouses, encoding='utf-8').read(),
                                                              'utf-8')
                except FileNotFoundError:
                    pass
        except Exception:
            self.log.warning(f"Error while processing mission {self.filename}", exc_info=True)
            raise UnsupportedMizFileException(self.filename)

    def save(self, new_filename: Optional[str] = None):
        tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(self.filename))
        os.close(tmpfd)
        with zipfile.ZipFile(self.filename, 'r') as zin:
            with zipfile.ZipFile(tmpname, 'w') as zout:
                zout.comment = zin.comment  # preserve the comment
                filenames = []
                for item in self._files:
                    if utils.is_valid_url(item['source']):
                        ...
                    else:
                        filenames.extend([
                            utils.make_unix_filename(item['target'], x) for x in utils.list_all_files(item['source'])
                        ])
                for item in zin.infolist():
                    if item.filename == 'mission':
                        zout.writestr(item, "mission = " + luadata.serialize(self.mission, 'utf-8', indent='\t',
                                                                             indent_level=0))
                    elif item.filename == 'options':
                        zout.writestr(item, "options = " + luadata.serialize(self.options, 'utf-8', indent='\t',
                                                                             indent_level=0))
                    elif item.filename == 'warehouses':
                        zout.writestr(item, "warehouses = " + luadata.serialize(self.warehouses, 'utf-8', indent='\t',
                                                                                indent_level=0))
                    elif item.filename not in filenames:
                        zout.writestr(item, zin.read(item.filename))
                for item in self._files:
                    def get_dir_path(name):
                        return name if os.path.isdir(name) else os.path.dirname(name)

                    for file in utils.list_all_files(item['source']):
                        if os.path.basename(file).lower() == 'desktop.ini':
                            continue
                        try:
                            zout.write(
                                os.path.join(get_dir_path(item['source']), file),
                                utils.make_unix_filename(item['target'], file)
                            )
                        except FileNotFoundError:
                            self.log.warning(
                                f"- File {os.path.join(item['source'], file)} could not be found, skipping.")
        try:
            if new_filename and new_filename != self.filename:
                shutil.copy2(tmpname, new_filename)
            else:
                shutil.copy2(tmpname, self.filename)
            os.remove(tmpname)
        except PermissionError as ex:
            self.log.error(f"Can't write new mission file: {ex}")
            raise

    def apply_preset(self, preset: Union[dict, list]):
        if isinstance(preset, list):
            for _preset in preset:
                self.apply_preset(_preset)
            return

        for key, value in preset.items():
            # handle special cases
            if key == 'date':
                if isinstance(value, str):
                    self.date = datetime.strptime(value, '%Y-%m-%d')
                else:
                    self.date = value
            elif key == 'clouds':
                if isinstance(value, str):
                    self.clouds = {"preset": value}
                elif isinstance(value, dict):
                    self.clouds = value
                else:
                    self.log.warning("Value 'clouds', str or dict required.")
            elif key == 'modify':
                self.modify(value)
            else:
                converted_value = int(value) if isinstance(value, str) and value.isdigit() else value
                try:
                    setattr(self, key, converted_value)
                except AttributeError:
                    self.log.warning(f"Value '{key}' can not be set, ignored.")

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
        if parse(self.node.dcs_version) >= Version('2.9.10'):
            return self.mission['weather'].get('fog2') is not None
        else:
            return self.mission['weather'].get('enable_fog', False)

    @enable_fog.setter
    def enable_fog(self, value: bool) -> None:
        if parse(self.node.dcs_version) >= Version('2.9.10'):
            if value:
                self.mission['weather']['fog2'] = {
                    "mode": 2
                }
            else:
                self.mission['weather'].pop('fog2', None)
            value = False
        self.mission['weather']['enable_fog'] = value

    @property
    def fog(self) -> dict:
        if parse(self.node.dcs_version) >= Version('2.9.10'):
            fog = self.mission['weather'].get('fog2')
            if not fog:
                return {}
            if fog['mode'] == 2:
                return {
                    "mode": "auto"
                }
            else:
                return {d["time"]: {"thickness": d["thickness"], "visibility": d["visibility"]} for d in fog["manual"]}
        else:
            return self.mission['weather']['fog']

    @fog.setter
    def fog(self, values: dict):
        if parse(self.node.dcs_version) >= Version('2.9.10'):
            if values.get('mode') == "auto":
                self.mission['weather']['enable_fog'] = False
                self.mission['weather']['fog2'] = {
                    "mode": 2
                }
            elif "thickness" in values or "visibility" in values:
                self.mission['weather']['enable_fog'] = True
                self.mission['weather']['fog'] |= values
            elif values.pop('mode', 'manual') == 'manual':
                self.mission['weather']['enable_fog'] = False
                self.mission['weather']['fog2'] = {
                    "manual": [
                        {"time": key, "visibility": value["visibility"], "thickness": value["thickness"]}
                        for key, value in values.items()
                        if isinstance(key, int)
                    ],
                    "mode": 4
                }
        else:
            self.mission['weather']['enable_fog'] = True
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
    def failures(self) -> dict:
        return self.mission['failures']

    @failures.setter
    def failures(self, values: dict) -> None:
        self.mission['failures'] = values

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
        self.failures = {}

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
    def files(self, files: list[Union[str, dict]]):
        self._files = []
        for file in files:
            if isinstance(file, str):
                self._files.append({"source": file, "target": "l10n/DEFAULT"})
            else:
                self._files.append(file)

    def modify(self, config: Union[list, dict]) -> None:

        def sort_dict(d):
            sorted_items = sorted(d.items())
            d.clear()
            for k, v in sorted_items:
                d[k] = v

        def process_elements(reference: dict, **kwargs):
            if 'select' in config:
                if debug:
                    self.log.debug("Processing SELECT ...")
                if config['select'].startswith('/'):
                    elements = list(utils.for_each(source, config['select'][1:].split('/'),
                                                   debug=debug, **kwargs))
                else:
                    elements = list(utils.for_each(reference, config['select'].split('/'), debug=debug, **kwargs))
            else:
                elements = [reference]
            for element in elements:
                # Lua lists sometimes are represented as dictionaries with numeric keys. We can't use them as kwargs
                if isinstance(element, dict) and not any(isinstance(key, (int, float)) for key in element.keys()):
                    kkwargs = element
                else:
                    kkwargs = {}
                if element is None:
                    if reference and 'insert' in config:
                        if debug:
                            self.log.debug(f"Inserting new value: {config['insert']}")
                        reference |= utils.evaluate(config['insert'], reference=reference, **kkwargs)
                elif 'replace' in config:
                    sort = False
                    for _what, _with in config['replace'].items():
                        if debug:
                            self.log.debug(f"Replacing {_what} with {_with}")
                        if isinstance(_what, int) and isinstance(element, (list, dict)):
                            if isinstance(element, list):
                                try:
                                    element[_what - 1] = utils.evaluate(_with, reference=reference, **kkwargs)
                                except IndexError:
                                    element.append(utils.evaluate(_with, reference=reference, **kkwargs))
                            elif isinstance(element, dict) and any(isinstance(key, (int, float)) for key in element.keys()):
                                element[_what] = utils.evaluate(_with, reference=reference, **kkwargs)
                                sort = True
                        elif isinstance(_with, dict) and isinstance(element[_what], (int, str, float, bool)):
                            for key, value in _with.items():
                                if utils.evaluate(key, reference=reference):
                                    element[_what] = utils.evaluate(value, reference=reference, **kkwargs)
                                    break
                        else:
                            element[_what] = utils.evaluate(_with, reference=reference, **kkwargs)
                    if sort:
                        sort_dict(element)
                elif 'merge' in config:
                    for _what, _with in config['merge'].items():
                        if debug:
                            self.log.debug(f"Merging {_what} with {_with}")
                        if isinstance(_with, dict):
                            if not element[_what]:
                                element[_what] = _with
                            else:
                                element[_what] |= _with
                        else:
                            for value in utils.for_each(source, _with[1:].split('/'), debug=debug, **kwargs):
                                if isinstance(element[_what], dict):
                                    element[_what] |= value
                                else:
                                    element[_what] += value
                            if _with.startswith('/'):
                                utils.tree_delete(source, _with[1:])
                            else:
                                utils.tree_delete(reference, _with)
                elif 'delete' in config:
                    if debug:
                        self.log.debug("Processing DELETE ...")
                    if isinstance(element, list):
                        for _what in element.copy():
                            if utils.evaluate(config['delete'], **_what):
                                element.remove(_what)
                elif 'run' in config:
                    if debug:
                        self.log.debug(f"Processing {config['run']}() ...")
                    module_name, func_name = config['run'].rsplit(".", 1)
                    try:
                        module = importlib.import_module(module_name)
                        func = getattr(module, func_name)
                        func(element, reference, **kwargs)
                    except AttributeError:
                        self.log.error(f"Function {func_name} not found in module {module_name}.")
                        raise
                    except ModuleNotFoundError:
                        self.log.error(f"Module {module_name} not found.")
                        raise

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

        # enable debug logging
        debug = config.get('debug', False)

        # which file has to be changed?
        file = config.get('file', 'mission')
        if file == 'mission':
            source = self.mission
        elif file == 'options':
            source = self.options
        elif file == 'warehouses':
            source = self.warehouses
        else:
            self.log.error(f"File {file} can not be changed.")
            return

        kwargs = {}
        # check if we need to import stuff
        for imp in config.get('imports', []):
            try:
                importlib.import_module(imp)
            except ModuleNotFoundError:
                self.log.error(f"Module '{imp}' could not be imported.")
            except Exception as ex:
                self.log.error(f"An error occurred while importing module '{imp}': {ex}")

        # do we need to pre-set variables to work with?
        for name, value in config.get('variables', {}).items():
            if isinstance(value, (int, float, dict, list)):
                kwargs[name] = value
            elif isinstance(value, str):
                if value.startswith('$'):
                    kwargs[name] = utils.evaluate(value, **kwargs)
                else:
                    kwargs[name] = next(utils.for_each(source, value.split('/'), debug=debug, **kwargs))
            else:
                self.log.error(f"Variable '{name}' has an unsupported value: {value}")

        # run the processing
        try:
            for_each = config['for-each'].lstrip('/')
        except KeyError:
            self.log.error("MizEdit: for-each missing in modify preset, skipping!")
            return
        for reference in utils.for_each(source, for_each.split('/'), debug=debug, **kwargs):
            if 'where' in config:
                if debug:
                    self.log.debug("Processing WHERE ...")
                if check_where(reference, config['where'], debug=debug, **kwargs):
                    process_elements(reference, **kwargs)
            else:
                process_elements(reference, **kwargs)


class UnsupportedMizFileException(Exception):
    def __init__(self, mizfile: str, message: Optional[str] = None):
        if not message:
            message = f'The mission {mizfile} is not compatible with MizEdit. Please re-save it in DCS World.'
        super().__init__(message)
