import random

from core import Extension, utils, Server, YAMLError, DEFAULT_TAG
from datetime import datetime
from pathlib import Path

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

__all__ = [
    "MizEdit"
]


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        presets_file = self.config.get('presets', 'config/presets.yaml')
        self.presets = {}
        if not isinstance(presets_file, list):
            presets_file = [presets_file]
        for file in presets_file:
            try:
                self.presets |= yaml.load(Path(file).read_text(encoding='utf-8'))
                if not isinstance(self.presets, dict):
                    raise ValueError("File must contain a dictionary. not a list!")
            except FileNotFoundError:
                self.log.error(f"MizEdit: File {file} not found!")
                continue
            except (MarkedYAMLError, ValueError) as ex:
                raise YAMLError(file, ex)

    async def get_presets(self, config: dict) -> list[dict]:
        # check for terrain-specific config
        if 'terrains' in config:
            theatre = await self.server.get_current_mission_theatre() or DEFAULT_TAG
            if theatre and theatre in config['terrains']:
                return await self.get_presets(config['terrains'][theatre])
            else:
                return []

        now = datetime.now()
        presets = config['settings']
        if isinstance(presets, dict):
            for key, value in presets.items():
                if utils.is_in_timeframe(now, key):
                    presets = value
                    break
            else:
                # no preset found for the current time, so don't change anything
                return []
        elif isinstance(presets, list):
            presets = random.choice(presets)
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
            self.log.info(f"  - Applying preset {preset} ...")
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

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return await self.server.modifyMission(filename, await self.get_presets(self.config)), True

    def is_running(self) -> bool:
        return True

    def shutdown(self) -> bool:
        return True
