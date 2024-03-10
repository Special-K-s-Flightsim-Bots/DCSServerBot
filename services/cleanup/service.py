import asyncio
import os
import time

from core import ServiceRegistry, Service, utils, DEFAULT_TAG, Instance
from discord.ext import tasks
from pathlib import Path


@ServiceRegistry.register()
class CleanupService(Service):
    def __init__(self, node):
        super().__init__(node=node, name="Cleanup")

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

    def do_cleanup(self, instance: Instance, now: time) -> None:
        for name, config in self.get_cfg_by_instance(instance).items():
            self.log.debug(f"- Running cleanup for {name} ...")
            directory = Path(utils.format_string(config['directory'], node=self.node, instance=instance))
            delete_after = int(config.get('delete_after', 30))
            threshold_time = now - delete_after * 86400
            for file_path in directory.glob(config['pattern']):
                if os.path.getctime(file_path) < threshold_time:
                    self.log.debug(f"  => {file_path.name} is older then {delete_after} days, deleting ...")
                    utils.safe_rmtree(file_path)

    @tasks.loop(hours=12)
    async def schedule(self):
        if not self.locals:
            return
        now = time.time()
        await asyncio.gather(*[
            asyncio.create_task(asyncio.to_thread(self.do_cleanup, instance, now))
            for instance in self.node.instances
        ])
