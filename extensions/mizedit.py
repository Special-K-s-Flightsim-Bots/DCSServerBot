import random

from core import Extension, utils, Server, YAMLError, DEFAULT_TAG
from datetime import datetime
from pathlib import Path
from typing import Tuple

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
yaml = YAML()


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        try:
            self.presets = yaml.load(Path("config/presets.yaml").read_text(encoding='utf-8'))
        except (ParserError, ScannerError) as ex:
            raise YAMLError('config/presets.yaml', ex)

    async def get_presets(self, config: dict):
        # check for terrain-specific config
        if 'terrains' in config:
            theatre = await self.server.get_current_mission_theatre() or DEFAULT_TAG
            if theatre and theatre in config['terrains']:
                return await self.get_presets(config['terrains'][theatre])
            else:
                return []

        presets = []
        now = datetime.now()
        _presets = config['settings']
        if isinstance(_presets, dict):
            for key, value in _presets.items():
                if utils.is_in_timeframe(now, key):
                    _presets = presets = value
                    break
            if not presets:
                # no preset found for the current time, so don't change anything
                return True
        if isinstance(_presets, list):
            presets = random.choice(_presets)
        if isinstance(presets, str):
            all_presets = [x.strip() for x in presets.split(',')]
        else:
            all_presets = presets
        modifications = []
        for preset in all_presets:
            if isinstance(preset, list):
                preset = random.choice(preset)
            if preset not in self.presets:
                self.log.error(f'Preset {preset} not found, ignored.')
                continue
            value = self.presets[preset]
            if isinstance(value, list):
                for inner_preset in value:
                    if inner_preset not in self.presets:
                        self.log.error(f'Preset {inner_preset} not found, ignored.')
                        continue
                    inner_value = self.presets[inner_preset]
                    modifications.append(inner_value)
            elif isinstance(value, dict):
                modifications.append(value)
        return modifications

    async def beforeMissionLoad(self, filename: str) -> Tuple[str, bool]:
        return await self.server.modifyMission(filename, await self.get_presets(self.config)), True

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
