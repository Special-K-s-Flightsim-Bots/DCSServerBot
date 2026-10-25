import asyncio
import logging
import os
import random
import re

from core import Extension, utils, Server, YAMLError, DEFAULT_TAG, MizFile, ServerImpl, Status
from datetime import datetime
from extensions.realweather import RealWeather
from pathlib import Path
from typing import cast
from typing_extensions import override
from zoneinfo import ZoneInfo

# ruamel YAML support
from core.utils.yaml_helper import new_yaml
from ruamel.yaml.error import MarkedYAMLError

logger = logging.getLogger(__name__)

__all__ = [
    "MizEdit"
]


class MizEdit(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        if server.status != Status.SHUTDOWN:
            self.presets = self._init_presets()
        else:
            self.presets = {}

    @override
    @property
    def hidden(self) -> bool:
        return True

    def _init_presets(self) -> dict:
        presets_file = self.config.get('presets', os.path.join(self.node.config_dir, 'presets.yaml'))
        presets = {}
        if not isinstance(presets_file, list):
            presets_file = [presets_file]

        validation = self.node.config.get('validation', 'lazy')
        if validation in ['strict', 'lazy']:
            schema_files = [
                'schemas/presets_schema.yaml',
                'extensions/realweather/schemas/realweather_schema.yaml'
            ]
        else:
            schema_files = []

        for file in presets_file:
            try:
                if schema_files:
                    utils.validate(file, schema_files, raise_exception=(validation == 'strict'))

                presets |= new_yaml().load(Path(file).read_text(encoding='utf-8'))
                if not isinstance(presets, dict):
                    raise ValueError("File must contain a dictionary, not a list!")
            except FileNotFoundError:
                self.log.error(f"MizEdit: File {file} not found!")
                continue
            except (MarkedYAMLError, ValueError) as ex:
                raise YAMLError(file, ex)
        return presets

    @override
    async def prepare(self) -> bool:
        self.presets = self._init_presets()
        return await super().prepare()

    async def get_presets(self, config: dict) -> list[dict]:
        """Resolve the preset NAMEs named by ``config['settings']`` into preset documents.

        A ``str`` value is a comma-separated list of NAMEs. A NAME that is absent from ``self.presets``
        is logged and skipped rather than raised, so the caller always receives a (possibly empty)
        list of documents.
        """
        # check for terrain-specific config
        if 'terrains' in config:
            theatre = await self.server.get_current_mission_theatre() or DEFAULT_TAG
            if theatre and theatre in config['terrains']:
                return await self.get_presets(config['terrains'][theatre])
            else:
                return []

        now = datetime.now()
        presets = config.get('settings')
        if not presets:
            return []

        if isinstance(presets, dict):
            tz: str | None = config.get('timezone')
            tzinfo = ZoneInfo(tz) if tz else None
            for key, value in presets.items():
                if utils.is_in_timeframe(now, key, tz=tzinfo):
                    presets = value
                    break
            else:
                # no preset found for the current time, so don't change anything
                return []
        elif isinstance(presets, list) and isinstance(presets[0], str):
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
                    await server.config_extension("RealWeather", {"Instance": {"enabled": True}})
                    ext = cast(ServerImpl, server).load_extension('RealWeather')
                    filename = await cast(RealWeather, ext).apply_realweather(
                        filename, rw_preset['RealWeather'], use_orig=False
                    )
                    await server.config_extension("RealWeather", {"Instance": {"enabled": True}})

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

    @override
    async def modify_mission(self, filename: str, *,
                             preset: dict | list | str | None = None) -> tuple[str, bool]:
        """Apply the weather presets to ``filename`` and report whether the file changed.

        ``preset`` is the per-load choice and REPLACES the instance's configured presets for this call.
        ``None`` keeps the configured behaviour; a ``str`` is a preset NAME resolved against a FRESH
        read of the node's preset file (never the cached ``self.presets``); a ``dict`` is one preset
        document; a ``list`` is a combination of NAMEs, each resolved like a ``str``, or a list of
        preset DOCUMENTs which is passed through unchanged. An absent NAME is logged and ignored by
        ``get_presets``, never raised here.
        """
        if 'filter' in self.config and not self._filter(filename):
            return filename, False
        if preset is not None:
            if isinstance(preset, str):
                # fresh read: a preset saved on the node takes effect on this load, no bot restart
                self.presets = self._init_presets()
                presets = await self.get_presets({'settings': preset})
            elif isinstance(preset, list) and any(isinstance(name, str) for name in preset):
                # a list of NAMEs: each resolved against the same fresh read as a single NAME, then
                # combined in order. An absent NAME is logged and ignored inside get_presets, never
                # raised from here.
                self.presets = self._init_presets()
                presets = []
                for name in preset:
                    if isinstance(name, str):
                        presets.extend(await self.get_presets({'settings': name}))
                    else:
                        presets.append(name)
            else:
                presets = preset
            if not presets:
                return filename, False
        else:
            presets = await self.get_presets(self.config)
            if not presets:
                self.log.error("MizEdit: No presets defined!")
                return filename, False
        await self.apply_presets(self.server, filename, presets, debug=self.config.get('debug', False))
        return filename, True

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        return await super().startup(quiet=True)

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        return super().shutdown(quiet=True)
