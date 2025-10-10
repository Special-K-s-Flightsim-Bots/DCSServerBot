import asyncio
import logging
import os
import random
import re

from core import Extension, utils, Server, YAMLError, DEFAULT_TAG, MizFile, ServerImpl
from datetime import datetime
from extensions.realweather import RealWeather
from pathlib import Path
from typing import cast

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

logger = logging.getLogger(__name__)

__all__ = [
    "MizEdit"
]


class MizEdit(Extension):

    async def prepare(self) -> bool:
        presets_file = self.config.get('presets', os.path.join(self.node.config_dir, 'presets.yaml'))
        self.presets = {}
        if not isinstance(presets_file, list):
            presets_file = [presets_file]
        for file in presets_file:
            try:
                self.presets |= yaml.load(Path(file).read_text(encoding='utf-8'))
                if not isinstance(self.presets, dict):
                    raise ValueError("File must contain a dictionary, not a list!")
            except FileNotFoundError:
                self.log.error(f"MizEdit: File {file} not found!")
                continue
            except (MarkedYAMLError, ValueError) as ex:
                raise YAMLError(file, ex)
        return await super().prepare()

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
    async def apply_presets(server: Server, filename: str, preset: list | dict,
                            debug: bool | None = False) -> None:
        if preset and isinstance(preset, list):
            rw_preset = next((p for p in preset if 'RealWeather'in p), None)
            if rw_preset:
                try:
                    await server.run_on_extension('RealWeather', 'is_running')
                    filename = await server.run_on_extension(
                        'RealWeather', 'apply_realweather', filename=filename,
                        config=rw_preset['RealWeather'], use_orig=False
                    )
                except ValueError:
                    # TODO: this is really dirty
                    await server.config_extension("RealWeather", {"enabled": True})
                    ext = cast(ServerImpl, server).load_extension('RealWeather')
                    filename = await cast(RealWeather, ext).apply_realweather(
                        filename, rw_preset['RealWeather'], use_orig=False
                    )
                    await server.config_extension("RealWeather", {"enabled": False})

                # remove all RealWeather presets
                count = 0
                while rw_preset:
                    count += 1
                    preset.remove(rw_preset)
                    rw_preset = next((p for p in preset if 'RealWeather' in p), None)
                if count > 1:
                    logger.error("Your preset contained more than one RealWeather preset. Only the first one was run.")

        miz = await asyncio.to_thread(MizFile, filename)
        if debug:
            if isinstance(preset, list):
                for p in preset:
                    p.get('modify', {}).update(debug=True)
            else:
                preset.get('modify', {}).update(debug=True)
        await asyncio.to_thread(miz.apply_preset, preset)
        await asyncio.to_thread(miz.save, filename)

    def _filter(self, filename: str) -> bool:
        return re.search(self.config['filter'], os.path.basename(filename)) is not None

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        if 'filter' in self.config and not self._filter(filename):
            return filename, False
        await self.apply_presets(self.server, filename, await self.get_presets(self.config),
                                 debug=self.config.get('debug', False))
        return filename, True

    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    def shutdown(self, *, quiet: bool = False) -> bool:
        return super().shutdown(quiet=True)
