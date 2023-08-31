import random
import yaml

from core import Extension, utils, Server
from datetime import datetime
from pathlib import Path
from typing import Optional


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.presets = yaml.safe_load(Path("config/presets.yaml").read_text(encoding='utf-8'))

    @property
    def version(self) -> str:
        return "1.0.0"

    async def beforeMissionLoad(self) -> bool:
        presets = []
        now = datetime.now()
        if isinstance(self.config['settings'], dict):
            for key, value in self.config['settings'].items():
                if utils.is_in_timeframe(now, key):
                    presets = value
                    break
            if not presets:
                # no preset found for the current time, so don't change anything
                return True
        elif isinstance(self.config['settings'], list):
            presets = random.choice(self.config['settings'])
        modifications = []
        for preset in [x.strip() for x in presets.split(',')]:
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
            self.log.info(f"  - Preset {preset} applied.")
        await self.server.modifyMission(modifications)
        return True

    def is_installed(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
