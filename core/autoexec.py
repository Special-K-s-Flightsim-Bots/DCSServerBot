from __future__ import annotations

import ast
import os.path
import re
import shutil

from dataclasses import dataclass, field
from os import path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core import InstanceImpl

__all__ = ["Autoexec"]


@dataclass
class Autoexec:
    instance: InstanceImpl
    values: dict = field(init=False, default_factory=dict)

    def __post_init__(self):
        file = path.join(self.instance.home, 'Config', 'autoexec.cfg')
        if not path.exists(file):
            return
        exp = re.compile('(?P<key>.*)=(?P<value>.*)')
        mydict = dict()
        with open(file, mode='r', encoding='utf-8') as cfg:
            for line in [x.strip() for x in cfg.readlines()]:   # type: str
                if line.startswith('if ') or line.startswith('--'):
                    continue
                if '--' in line:
                    line = line[0:line.find('--')].strip()
                match = exp.search(line)
                if match:
                    key = match.group('key').strip()
                    value = self.parse(match.group('value').strip())
                    if '.' in key:
                        keys = key.split('.')
                        if keys[0] not in mydict or not isinstance(mydict[keys[0]], dict):
                            mydict[keys[0]] = {}
                        if len(keys) == 3:
                            if keys[1] not in mydict[keys[0]]:
                                mydict[keys[0]][keys[1]] = {}
                            mydict[keys[0]][keys[1]][keys[2]] = value
                        else:
                            mydict[keys[0]][keys[1]] = value
                    else:
                        mydict[key] = value
                elif line.startswith('log'):
                    if 'log' not in mydict:
                        mydict['log'] = []
                    mydict['log'].append(line[4:])
                elif line.startswith('table'):
                    if 'table' not in mydict:
                        mydict['table'] = []
                    mydict['table'].append(line[6:])
        self.values = mydict

    def __getattribute__(self, item):
        return super(Autoexec, self).__getattribute__(item)

    def __getattr__(self, item):
        if item not in self.values:
            return super(Autoexec, self).__setattr__(item, None)
        else:
            return self.values[item]

    def __setattr__(self, key, value):
        if key in ['bot', 'instance', 'values']:
            super(Autoexec, self).__setattr__(key, value)
        elif self.values.get(key) != value:
            self.values[key] = value
            self.update()

    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('"'):
            return value.strip('"')
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        else:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value

    @staticmethod
    def unparse(value: Any) -> str:
        if isinstance(value, bool):
            return value.__repr__().lower()
        elif isinstance(value, str):
            return '"' + value + '"'
        elif isinstance(value, list):
            return repr(set(value))
        else:
            return value

    def update(self):
        outfile = path.join(self.instance.home, 'Config', 'autoexec.cfg')
        if path.exists(outfile):
            shutil.copy(outfile, outfile + '.bak')
        os.makedirs(os.path.dirname(outfile), exist_ok=True)
        with open(outfile, mode='w', encoding='utf-8') as outcfg:
            for key, value in self.values.items():
                if key == 'log':
                    for log in value:
                        outcfg.write(f"{key}.{log}\n")
                    continue
                elif key == 'net':
                    outcfg.write('if not net then net = {} end\n')
                if isinstance(value, dict):
                    for subkey, subval in value.items():
                        if isinstance(subval, dict):
                            for subkey2, subval2 in subval.items():
                                outcfg.write(f"{key}.{subkey}.{subkey2} = {self.unparse(subval2)}\n")
                        else:
                            outcfg.write(f"{key}.{subkey} = {self.unparse(subval)}\n")
                elif isinstance(value, list):
                    for x in value:
                        outcfg.write(f"{key}.{x}\n")
                else:
                    outcfg.write(f"{key} = {self.unparse(value)}\n")
