from __future__ import annotations

import datetime
import importlib
import io
import logging
import luadata
import os
import re
import shutil
import tempfile
import zipfile

from astral import LocationInfo
from astral.sun import sun
from core import utils
from datetime import datetime, timedelta
from packaging.version import parse, Version
from timezonefinder import TimezoneFinder
from typing import Union, Optional
from zoneinfo import ZoneInfo

__all__ = [
    "MizFile",
    "UnsupportedMizFileException",
    "THEATRES"
]

THEATRES = {}


class MizFile:

    def __init__(self, filename: Optional[str] = None):
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.filename = filename
        self.mission: dict = {}
        self.options: dict = {}
        self.warehouses: dict = {}
        if filename:
            self._load()
        self._files: list[dict] = []
        self.node = ServiceRegistry.get(ServiceBus).node
        if not THEATRES:
            self.read_theatres()

    def read_theatres(self):
        maps_path = os.path.join(os.path.expandvars(self.node.locals['DCS']['installation']), "Mods", "terrains")
        if not os.path.exists(maps_path):
            self.log.error(f"Maps directory not found: {maps_path}, can't use timezone specific parameters!")
            return

        for terrain in os.listdir(maps_path):
            terrain_path = os.path.join(maps_path, terrain)
            entry_lua = os.path.join(terrain_path, "entry.lua")
            # sometimes, terrain folders stay even if the terrain is being uninstalled
            if not os.path.exists(entry_lua):
                continue
            pattern = r'local self_ID\s*=\s*"(.*?)";'
            with open(entry_lua, "r", encoding="utf-8") as file:
                match = re.search(pattern, file.read())
                if match:
                    terrain_id = match.group(1)
                else:
                    raise ValueError(f"No self_ID found in {entry_lua}")
            towns_file = os.path.join(terrain_path, "Map", "towns.lua")
            if os.path.exists(towns_file):
                try:
                    pattern = r"latitude\s*=\s*([\d.-]+),\s*longitude\s*=\s*([\d.-]+)"
                    with open(towns_file, "r", encoding="utf-8") as file:
                        for line in file:
                            match = re.search(pattern, line)
                            if match:
                                THEATRES[terrain_id] = {float(match.group(1)), float(match.group(2))}
                                break
                        else:
                            self.log.warning(f"No towns found in: {towns_file}")
                except Exception as ex:
                    self.log.error(f"Error reading file {towns_file}: {ex}")
            else:
                self.log.info(f"No towns.lua found for terrain: {terrain}")

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
        except FileNotFoundError:
            raise
        except Exception:
            self.log.warning(f"Error while processing mission {self.filename}", exc_info=True)
            raise UnsupportedMizFileException(self.filename)

    def save(self, new_filename: Optional[str] = None):
        tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(self.filename))
        os.close(tmpfd)
        try:
            with zipfile.ZipFile(self.filename, 'r') as zin:
                with zipfile.ZipFile(tmpname, 'w') as zout:
                    zout.comment = zin.comment  # preserve the comment
                    filenames = []
                    for item in self._files:
                        if utils.is_valid_url(item['source']):
                            ...
                        else:
                            filenames.extend([
                                utils.make_unix_filename(item['target'], x) for x in
                                utils.list_all_files(item['source'])
                            ])
                    for item in zin.infolist():
                        if item.filename == 'mission':
                            zout.writestr(item, "mission = " + luadata.serialize(self.mission, 'utf-8', indent='\t',
                                                                                 indent_level=0))
                        elif item.filename == 'options':
                            zout.writestr(item, "options = " + luadata.serialize(self.options, 'utf-8', indent='\t',
                                                                                 indent_level=0))
                        elif item.filename == 'warehouses':
                            zout.writestr(item,
                                          "warehouses = " + luadata.serialize(self.warehouses, 'utf-8', indent='\t',
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
            except PermissionError as ex:
                self.log.error(f"Can't write new mission file: {ex}")
                raise
        finally:
            os.remove(tmpname)

    def apply_preset(self, preset: Union[dict, list], **kwargs):
        if isinstance(preset, list):
            for _preset in preset:
                self.apply_preset(_preset, **kwargs)
            return

        for key, value in preset.items():
            # handle special cases
            if key == 'date':
                if isinstance(value, str):
                    try:
                        self.date = datetime.strptime(value, '%Y-%m-%d').date()
                    except ValueError:
                        if value in ['today', 'yesterday', 'tomorrow']:
                            now = datetime.today().date()
                            if value == 'today':
                                self.date = now
                            elif value == 'yesterday':
                                self.date = now - timedelta(days=1)
                            elif value == 'tomorrow':
                                self.date = now + timedelta(days=1)
                else:
                    self.date = value
            elif key == 'start_time':
                if isinstance(value, int):
                    self.start_time = value
                else:
                    try:
                        self.start_time = int((datetime.strptime(value, "%H:%M") -
                                               datetime(1900, 1, 1)).total_seconds())
                    except ValueError:
                        self.start_time = self.parse_moment(value)
            elif key == 'clouds':
                if isinstance(value, str):
                    self.clouds = {"preset": value}
                elif isinstance(value, dict):
                    self.clouds = value
                else:
                    self.log.warning("Value 'clouds', str or dict required.")
            elif key == 'modify':
                self.modify(value, **kwargs)
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
    def start_time(self, value: int) -> None:
        self.mission['start_time'] = value

    @property
    def date(self) -> datetime.date:
        value = self.mission['date']
        return datetime(year=value['Year'], month=value['Month'], day=value['Day']).date()

    @date.setter
    def date(self, value: datetime.date) -> None:
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

    def parse_moment(self, value: str = "morning") -> int:
        """
        Calculate the "moment" for the MizFile's theatre coordinates and date,
        then return the time corresponding to the "moment" parameter in seconds since midnight
        in the local timezone.

        Example: parse_moment(mizfile, "sunrise") returns the time of sunrise in seconds since midnight
        Example: parse_moment(mizfile, "sunrise + 01:00") returns the time of sunrise + 1 hour in seconds since midnight
        Example: parse_moment(mizfile, "morning") returns the time of morning in seconds since midnight

        Parameters:
        self: MizFile object with date, theatreCoordinates, and start_time properties
        value: string representing the moment to calculate

        Constants available for the "moment" parameter:
        - sunrise: The time of sunrise
        - dawn: The time of dawn
        - morning: Two hours after dawn
        - noon: The time of solar noon
        - evening: Two hours before sunset
        - sunset: The time of sunset
        - dusk: The time of dusk
        - night: Two hours after dusk
        """

        # Get the date from the MizFile object
        target_date = self.date

        # Extract latitude and longitude from theatreCoordinates
        latitude, longitude = THEATRES[self.theatre]

        # Determine the local timezone
        timezone = TimezoneFinder().timezone_at(lat=latitude, lng=longitude)
        if not timezone:
            raise ValueError("start_time: Could not determine timezone for the given coordinates!")

        # Create a LocationInfo object for astral calculations
        location = LocationInfo("Custom", "Location", timezone, latitude, longitude)

        # Calculate sun times
        solar_events = sun(location.observer, date=target_date, tzinfo=ZoneInfo(timezone)).copy()

        # Alternate moments, calculated based on the solar events above
        solar_events |= {
            "now": datetime.now(tz=ZoneInfo(timezone)),
            "morning": solar_events["dawn"] + timedelta(hours=2),
            "evening": solar_events["sunset"] - timedelta(hours=2),
            "night": solar_events["dusk"] + timedelta(hours=2)
        }

        match = re.match(r"(\w+)\s*([+-]\d{2}:\d{2})?", value.strip())
        if not match:
            raise ValueError("start_time: Invalid input format. Expected '<event> [+HH:MM|-HH:MM]'.")

        event = match.group(1)  # the event
        offset = match.group(2) # the offset time (+/- HH24:MM)

        if not event or event.lower() not in solar_events:
            raise ValueError(f"start_time: Invalid solar event '{event}'. "
                             f"Valid events are {list(solar_events.keys())}.")

        base_time = solar_events[event.lower()]
        if offset:
            hours, minutes = map(int, offset.split(":"))
            delta = timedelta(hours=hours, minutes=minutes)
            base_time += delta

        for key, value in solar_events:
            self.log.debug(f"solar_events[{key}] = '{value}'")
        self.log.debug(f"'{value}' was parsed to '{base_time}'")

        return (base_time.hour * 3600) + (base_time.minute * 60)

    def modify(self, config: Union[list, dict], **kwargs) -> None:

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
                        reference |= utils.evaluate(config['insert'], reference=reference, **kkwargs, **kwargs)
                elif 'replace' in config:
                    sort = False
                    for _what, _with in config['replace'].items():
                        if debug:
                            if isinstance(_with, str):
                                _w = utils.evaluate(_with, reference=reference, **kkwargs, **kwargs)
                            elif isinstance(_with, list):
                                _w = [utils.evaluate(x, reference=reference, **kkwargs, **kwargs) for x in _with]
                            elif isinstance(_with, dict):
                                _w = {}
                                for k, v in _with.items():
                                    _w[k] = utils.evaluate(v, reference=reference, **kkwargs, **kwargs)
                            else:
                                _w = _with
                            self.log.debug(f"Replacing {_what} with {_w}")
                        if isinstance(_what, int) and isinstance(element, (list, dict)):
                            if isinstance(element, list):
                                try:
                                    element[_what - 1] = utils.evaluate(_with, reference=reference, **kkwargs, **kwargs)
                                except IndexError:
                                    element.append(utils.evaluate(_with, reference=reference, **kkwargs, **kwargs))
                            elif isinstance(element, dict) and any(
                                    isinstance(key, (int, float)) for key in element.keys()):
                                element[_what] = utils.evaluate(_with, reference=reference, **kkwargs, **kwargs)
                                sort = True
                        elif isinstance(_with, dict) and isinstance(element[_what], (int, str, float, bool)):
                            for k, v in _with.items():
                                if utils.evaluate(k, reference=reference):
                                    element[_what] = utils.evaluate(v, reference=reference, **kkwargs, **kwargs)
                                    break
                        elif isinstance(_with, list):
                            element[_what] = [utils.evaluate(x, reference=reference, **kkwargs, **kwargs) for x in _with]
                        else:
                            element[_what] = utils.evaluate(_with, reference=reference, **kkwargs, **kwargs)
                    if sort:
                        sort_dict(element)
                elif 'merge' in config:
                    for _what, _with in config['merge'].items():
                        if debug:
                            if isinstance(_with, str):
                                _w = utils.evaluate(_with, reference=reference, **kkwargs, **kwargs)
                            elif isinstance(_with, list):
                                _w = [utils.evaluate(x, reference=reference, **kkwargs, **kwargs) for x in _with]
                            elif isinstance(_with, dict):
                                _w = {}
                                for k, v in _with.items():
                                    _w[k] = utils.evaluate(v, reference=reference, **kkwargs, **kwargs)
                            else:
                                _w = _with
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
                                elif isinstance(element[_what], list):
                                    # attention: merge of lists is not supported, as they need to keep the order
                                    element[_what] = value
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
                self.modify(cfg, **kwargs)
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
                    element = next(utils.for_each(source, value.split('/'), debug=debug, **kwargs), None)
                    if element:
                        element = element.copy()
                    kwargs[name] = element
            else:
                self.log.error(f"Variable '{name}' has an unsupported value: {value}")

        # debug
        if kwargs:
            self.log.debug(f"Variables read: {repr(kwargs)}")

        if 'if' in config and not utils.evaluate(config['if'], **kwargs):
            return

        # run the processing
        try:
            for_each = config.get('for-each', '').lstrip('/')
        except KeyError:
            self.log.error("MizEdit: for-each missing in modify preset, skipping!")
            return
        if for_each:
            all_elements = utils.for_each(source, for_each.split('/'), debug=debug, **kwargs)
        else:
            all_elements = [source]
        for reference in all_elements:
            if not reference:
                continue
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
