import asyncio
import os
import time

from core import ServiceRegistry, Service, utils, Instance
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

    def do_cleanup(self, instance: Instance, now: time) -> None:
        for name, config in self.get_config(instance.server).items():
            self.log.debug(f"- Running cleanup for {name} ...")
            directory = Path(
                os.path.expandvars(utils.format_string(config['directory'], node=self.node, instance=instance)))
            delete_after = int(config.get('delete_after', 30))
            threshold_time = now - delete_after * 86400
            patterns = config['pattern']
            recursive = config.get('recursive', False)
            if not isinstance(patterns, list):
                patterns = [patterns]
            for pattern in patterns:
                search_pattern = f"**/{pattern}" if recursive else pattern
                for file_path in directory.glob(search_pattern):
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
