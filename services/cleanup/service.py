import os
import time

from core import ServiceRegistry, Service, utils, DEFAULT_TAG, Instance
from discord.ext import tasks
from pathlib import Path


@ServiceRegistry.register("Cleanup")
class CleanupService(Service):
    async def start(self, *args, **kwargs):
        await super().start()
        self.schedule.start()

    async def stop(self, *args, **kwargs):
        self.schedule.cancel()
        await super().stop()

    def get_cfg_by_instance(self, instance: Instance) -> dict:
        if instance.name not in self._config:
            self._config[instance.name] = (self.locals.get(DEFAULT_TAG, {}) | self.locals.get(instance.name, {}))
        return self._config[instance.name]

    @tasks.loop(hours=12)
    async def schedule(self):
        if not self.locals:
            return
        now = time.time()
        for instance in self.node.instances:
            for name, config in self.get_cfg_by_instance(instance).items():
                self.log.debug(f"- Running cleanup for {name} ...")
                directory = Path(utils.format_string(config['directory'], node=self.node, instance=instance))
                delete_after = config.get('delete_after', 30)
                for f in directory.glob(config['pattern']):
                    if f.stat().st_mtime < (now - delete_after * 86400):
                        if os.path.isfile(f):
                            self.log.debug(f"  => {f.name} is older then {delete_after} days, deleted.")
                            os.remove(f)
