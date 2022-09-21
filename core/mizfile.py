import io
import os
import re
import tempfile
import zipfile
from datetime import datetime
from typing import Union, Any


class MizFile:
    re_exp = {
        'start_time': r'^    \["start_time"\] = (?P<start_time>.*),',
        'key_value': r'\["{key}"\] = (?P<value>.*),',
        'genkey_value': r'\["(?P<key>.*)"\] = (?P<value>.*),'
    }

    def __init__(self, filename: str):
        self.filename = filename
        self.mission = []
        self._load()

    def _load(self):
        with zipfile.ZipFile(self.filename, 'r') as miz:
            with miz.open('mission') as mission:
                self.mission = io.TextIOWrapper(mission, encoding='utf-8').readlines()

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
                        zout.writestr(item, ''.join(self.mission))
        os.remove(self.filename)
        os.rename(tmpname, self.filename)

    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('"'):
            return value.strip('"')
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        elif '.' in value:
            return float(value)
        else:
            return int(value)

    @staticmethod
    def unparse(value: Any) -> str:
        if isinstance(value, bool):
            return value.__repr__().lower()
        elif isinstance(value, str):
            return '"' + value + '"'
        else:
            return value

    @property
    def start_time(self) -> int:
        exp = re.compile(self.re_exp['start_time'])
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return int(match.group('start_time'))

    @start_time.setter
    def start_time(self, value: Union[int, str]) -> None:
        if isinstance(value, int):
            start_time = value
        else:
            start_time = int((datetime.strptime(value, "%H:%M") - datetime(1900, 1, 1)).total_seconds())
        exp = re.compile(self.re_exp['start_time'])
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(start_time), self.mission[i])
                break

    @property
    def date(self) -> datetime:
        exp = {}
        date = {}
        for x in ['Day', 'Month', 'Year']:
            exp[x] = re.compile(self.re_exp['key_value'].format(key=x))
        for i in range(0, len(self.mission)):
            for x in ['Day', 'Month', 'Year']:
                match = exp[x].search(self.mission[i])
                if match:
                    date[x] = int(match.group('value'))
        return datetime(date['Year'], date['Month'], date['Day'])

    @date.setter
    def date(self, value: datetime) -> None:
        exp = {}
        date = {"Year": value.year, "Month": value.month, "Day": value.day}
        for x in ['Day', 'Month', 'Year']:
            exp[x] = re.compile(self.re_exp['key_value'].format(key=x))
        for i in range(0, len(self.mission)):
            for x in ['Day', 'Month', 'Year']:
                match = exp[x].search(self.mission[i])
                if match:
                    self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(date[x]), self.mission[i])

    @property
    def temperature(self) -> float:
        exp = re.compile(self.re_exp['key_value'].format(key='temperature'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return self.parse(match.group('value'))

    @temperature.setter
    def temperature(self, value: float) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='temperature'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(value), self.mission[i])
                break

    @property
    def atmosphere_type(self) -> int:
        exp = re.compile(self.re_exp['key_value'].format(key='atmosphere_type'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return int(match.group('value'))

    @atmosphere_type.setter
    def atmosphere_type(self, value: int) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='atmosphere_type'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(value), self.mission[i])
                break

    @property
    def wind(self) -> dict:
        exp_speed = re.compile(self.re_exp['key_value'].format(key='speed'))
        exp_dir = re.compile(self.re_exp['key_value'].format(key='dir'))
        wind = {}
        for key in ['atGround', 'at2000', 'at8000']:
            for i in range(0, len(self.mission)):
                if f'["{key}"] =' in self.mission[i]:
                    wind[key] = {
                        "speed": self.parse(exp_speed.search(self.mission[i + 2]).group('value')),
                        "dir": self.parse(exp_dir.search(self.mission[i + 3]).group('value'))
                    }
        return wind

    @wind.setter
    def wind(self, values: dict) -> None:
        for key, value in values.items():
            for i in range(0, len(self.mission)):
                if f'["{key}"] = ' in self.mission[i]:
                    if 'speed' in value:
                        self.mission[i + 2] = re.sub(' = ([^,]*)', ' = {}'.format(value['speed']), self.mission[i + 2])
                    if 'dir' in value:
                        self.mission[i + 3] = re.sub(' = ([^,]*)', ' = {}'.format(value['dir']), self.mission[i + 3])
                    break

    @property
    def groundTurbulence(self) -> float:
        exp = re.compile(self.re_exp['key_value'].format(key='groundTurbulence'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return self.parse(match.group('value'))

    @groundTurbulence.setter
    def groundTurbulence(self, value: float) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='groundTurbulence'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(value), self.mission[i])
                break

    @property
    def enable_dust(self) -> bool:
        exp = re.compile(self.re_exp['key_value'].format(key='enable_dust'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return self.parse(match.group('value'))

    @enable_dust.setter
    def enable_dust(self, value: bool) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='enable_dust'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(self.unparse(value)), self.mission[i])
                break

    @property
    def dust_density(self) -> int:
        if not self.enable_dust:
            return 0
        exp = re.compile(self.re_exp['key_value'].format(key='dust_density'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return int(match.group('value'))

    @dust_density.setter
    def dust_density(self, value: int) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='dust_density'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(value), self.mission[i])
                break

    @property
    def qnh(self) -> float:
        exp = re.compile(self.re_exp['key_value'].format(key='qnh'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return float(match.group('value'))

    @qnh.setter
    def qnh(self, value: float) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='qnh'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(value), self.mission[i])
                break

    @property
    def clouds(self) -> dict:
        exp = re.compile(self.re_exp['genkey_value'])
        clouds = dict()
        for i in range(0, len(self.mission)):
            if '["clouds"] =' in self.mission[i]:
                j = 2
                while '}' not in self.mission[i + j]:
                    match = exp.search(self.mission[i + j])
                    if match:
                        clouds[match.group('key')] = self.parse(match.group('value'))
                        j += 1
                    else:
                        break
                break
        return clouds

    @clouds.setter
    def clouds(self, values: dict) -> None:
        elements = list(values.keys())
        # If we're using a preset, disable dynamic weather
        if self.atmosphere_type == 1 and 'preset' in values:
            self.atmosphere_type = 0
        for i in range(0, len(self.mission)):
            if '["clouds"] = ' in self.mission[i]:
                j = 2
                old_elements = elements.copy()
                while '}' not in self.mission[i + j]:
                    for e in elements:
                        if e in self.mission[i + j] and e in values:
                            self.mission[i + j] = re.sub(' = ([^,]*)', ' = {}'.format(self.unparse(values[e])),
                                                         self.mission[i + j])
                            j += 1
                            old_elements.remove(e)
                    elements = old_elements.copy()
                    j += 1
                # check for remaining elements
                for e in old_elements:
                    if e in values:
                        self.mission.insert(i + j - 1, f'            ["{e}"] = {self.unparse(values[e])},\n')
                break

    @property
    def enable_fog(self) -> bool:
        exp = re.compile(self.re_exp['key_value'].format(key='enable_fog'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                return self.parse(match.group('value'))

    @enable_fog.setter
    def enable_fog(self, value: bool) -> None:
        exp = re.compile(self.re_exp['key_value'].format(key='enable_fog'))
        for i in range(0, len(self.mission)):
            match = exp.search(self.mission[i])
            if match:
                self.mission[i] = re.sub(' = ([^,]*)', ' = {}'.format(self.unparse(value)), self.mission[i])
                break

    @property
    def fog(self) -> dict:
        exp = re.compile(self.re_exp['genkey_value'])
        fog = dict()
        for i in range(0, len(self.mission)):
            if '["fog"] =' in self.mission[i]:
                j = 2
                while '}' not in self.mission[i + j]:
                    match = exp.search(self.mission[i + j])
                    if match:
                        fog[match.group('key')] = self.parse(match.group('value'))
                        j += 1
                    else:
                        break
                break
        return fog

    @fog.setter
    def fog(self, values: dict):
        elements = list(values.keys())
        for i in range(0, len(self.mission)):
            if '["fog"] = ' in self.mission[i]:
                j = 2
                old_elements = elements.copy()
                while '}' not in self.mission[i + j]:
                    for e in elements:
                        if e in self.mission[i + j] and e in values:
                            self.mission[i + j] = re.sub(' = ([^,]*)', ' = {}'.format(self.unparse(values[e])),
                                                         self.mission[i + j])
                            j += 1
                            old_elements.remove(e)
                    elements = old_elements.copy()
                    j += 1
                # check for remaining elements
                for e in old_elements:
                    if e in values:
                        self.mission.insert(i + j - 1, f'            ["{e}"] = {self.unparse(values[e])},\n')
                break

    def clear_required_modules(self) -> None:
        for i in range(0, len(self.mission)):
            if '["requiredModules"] =' in self.mission[i]:
                i += 2
                while self.mission[i] != '}, -- end of ["requiredModules"]':
                    self.mission.pop(i)
