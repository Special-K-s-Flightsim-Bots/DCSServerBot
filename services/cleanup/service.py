import asyncio
import os

from core import ServiceRegistry, Service, utils, Instance
from datetime import timedelta, datetime
from discord.ext import tasks
from pathlib import Path
from services.bot import BotService
from services.scheduler.actions import purge_channel

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

    async def do_directory_cleanup(self, instance: Instance, config: dict) -> None:

        async def check_and_delete(file_path: Path) -> None:
            try:
                file_mtime = await asyncio.to_thread(os.path.getmtime, file_path)
                if file_mtime < threshold_timestamp:
                    self.log.debug(f"  => {file_path.name} is older than {delete_after} days, deleting ...")
                    await asyncio.to_thread(utils.safe_rmtree, file_path)
            except Exception as ex:
                self.log.error(f"Could not delete {file_path}: {ex}")

        directory = Path(
            os.path.expandvars(utils.format_string(config['directory'], node=self.node, instance=instance)))
        delete_after = int(config.get('delete_after', 30))
        threshold_time = datetime.now() - timedelta(days=delete_after)
        threshold_timestamp = threshold_time.timestamp()
        patterns = config['pattern']
        recursive = config.get('recursive', False)
        if not isinstance(patterns, list):
            patterns = [patterns]
        tasks = []
        for pattern in patterns:
            search_pattern = f"**/{pattern}" if recursive else pattern
            for file_path in directory.glob(search_pattern):
                tasks.append(check_and_delete(file_path))
        await asyncio.gather(*tasks)

    async def do_channel_cleanup(self, config: dict):
        try:
            await purge_channel(self.node, config['channel'], int(config.get('delete_after', 0)), config.get('ignore'))
        except Exception as ex:
            self.log.error(f"Could not purge channel {config['channel']}: {ex}")

    async def do_cleanup(self, instance: Instance) -> None:
        for name, config in self.get_config(instance.server).items():
            self.log.debug(f"- Running cleanup for {name} ...")
            if 'directory' in config:
                await self.do_directory_cleanup(instance, config)
            elif 'channel' in config:
                await self.do_channel_cleanup(config)

    @tasks.loop(hours=12)
    async def schedule(self):
        if not self.locals:
            return
        for instance in self.node.instances:
            # noinspection PyAsyncCall
            asyncio.create_task(self.do_cleanup(instance))

    @schedule.before_loop
    async def before_schedule(self):
        if self.node.master:
            bot = ServiceRegistry.get(BotService).bot
            await bot.wait_until_ready()
