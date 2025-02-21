import asyncio
import os
import random
import logging

from core import Extension, utils, Server, YAMLError, DEFAULT_TAG, MizFile, ServerImpl
from datetime import datetime
from pathlib import Path
from typing import Union, cast

from ..realweather import RealWeather

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError

yaml = YAML()

logger = logging.getLogger(__name__)

__all__ = [
    "MizEdit"
]


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        presets_file = self.config.get('presets', os.path.join(server.node.config_dir, 'presets.yaml'))
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

    @staticmethod
    async def apply_presets(server: Server, filename: str, preset: Union[list, dict]) -> str:
        if preset and isinstance(preset, list):
            presetsWithoutRealWeather = [] # create a list of presets in which we'll copy all presets except RealWeather
            realWeatherAlreadyApplied = False # flag to check if a RealWeather preset was already applied
            for aPreset in preset:
                if 'RealWeather' in aPreset:
                    if realWeatherAlreadyApplied:
                        logger.warning(f"RealWeather preset found many times - ignoring preset {aPreset}")
                        continue
                    try:
                        await server.run_on_extension('RealWeather', 'is_running')
                        filename = await server.run_on_extension('RealWeather', 'apply_realweather',
                                                                    filename=filename, config=aPreset['RealWeather'])                        
                    except ValueError:
                        # TODO: this is really dirty
                        await server.config_extension("RealWeather", {"enabled": True})
                        ext = cast(ServerImpl, server).load_extension('RealWeather')
                        filename = await cast(RealWeather, ext).apply_realweather(filename, aPreset['RealWeather'])
                        await server.config_extension("RealWeather", {"enabled": False})
                    
                    realWeatherAlreadyApplied = True
                else:
                    presetsWithoutRealWeather.append(aPreset)
        else:
            presetsWithoutRealWeather = preset

        miz = await asyncio.to_thread(MizFile, filename)
        await asyncio.to_thread(miz.apply_preset, presetsWithoutRealWeather)
        # write new mission
        filename = utils.create_writable_mission(filename)
        await asyncio.to_thread(miz.save, filename)
        return filename

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return (await self.apply_presets(self.server, filename, await self.get_presets(self.config))), True

    def is_running(self) -> bool:
        return True

    def shutdown(self) -> bool:
        return True
