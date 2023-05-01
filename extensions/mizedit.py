import random
from datetime import datetime
from typing import Optional

from core import Extension, utils, Server


class MizEdit(Extension):

    @property
    def version(self) -> str:
        return "1.0.0"

    async def change_mizfile(self, server: Server, config: dict, presets: Optional[str] = None):
        now = datetime.now()
        if not presets:
            if isinstance(config['settings'], dict):
                for key, value in config['restart']['settings'].items():
                    if utils.is_in_timeframe(now, key):
                        presets = value
                        break
                if not presets:
                    # no preset found for the current time, so don't change anything
                    return
            elif isinstance(config['settings'], list):
                presets = random.choice(config['settings'])
        modifications = []
        for preset in [x.strip() for x in presets.split(',')]:
            if preset not in config['presets']:
                self.log.error(f'Preset {preset} not found, ignored.')
                continue
            value = config['presets'][preset]
            if isinstance(value, list):
                for inner_preset in value:
                    if inner_preset not in config['presets']:
                        self.log.error(f'Preset {inner_preset} not found, ignored.')
                        continue
                    inner_value = config['presets'][inner_preset]
                    modifications.append(inner_value)
            elif isinstance(value, dict):
                modifications.append(value)
            self.log.info(f"  => Preset {preset} added to list.")
        await self.server.modifyMission(modifications)
        self.log.info(f"  => Mission {server.current_mission} modified.")

    async def beforeMissionLoad(self) -> bool:
        await self.change_mizfile(self.server, self.config)
        return True
