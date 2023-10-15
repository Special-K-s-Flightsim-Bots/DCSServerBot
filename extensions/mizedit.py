import random
from typing import Tuple

from core import Extension, utils, Server
from datetime import datetime
from pathlib import Path

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.presets = yaml.load(Path("config/presets.yaml").read_text(encoding='utf-8'))

    def get_presets(self):
        presets = []
        now = datetime.now()
        _presets = self.config['settings']
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
        return await self.server.modifyMission(filename, self.get_presets()), True

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
