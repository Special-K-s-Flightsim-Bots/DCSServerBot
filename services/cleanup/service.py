import asyncio
import discord
import os

from core import ServiceRegistry, Service, utils, Instance
from datetime import timedelta, datetime, timezone
from discord.ext import tasks
from pathlib import Path
from services.bot import BotService


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
            file_ctime = await asyncio.to_thread(os.path.getctime, file_path)
            if file_ctime < threshold_timestamp:
                self.log.debug(f"  => {file_path.name} is older than {delete_after} days, deleting ...")
                await asyncio.to_thread(utils.safe_rmtree, file_path)

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
        # channel cleanup only runs on the master node
        if not self.node.master:
            return
        bot = ServiceRegistry.get(BotService).bot
        delete_after = int(config.get('delete_after', 30))
        now = datetime.now(tz=timezone.utc)
        threshold_time = now - timedelta(days=delete_after)

        if isinstance(config['channel'], str):
            channels = [config['channel']]
        else:
            channels = config['channel']
        for c in channels:
            channel = bot.get_channel(c)
            if not channel:
                self.log.warning(f"Channel {c} not found!")
                return

            try:
                # Bulk delete messages that are less than 14 days old and match the criteria
                self.log.debug(f"Deleting messages older than {delete_after} days in channel {channel.name} ...")
                deleted_messages = await channel.purge(limit=None, before=threshold_time, bulk=True)
                self.log.debug(f"Purged {len(deleted_messages)} messages from channel {channel.name}.")
            except discord.NotFound:
                self.log.warning(f"Can't delete messages in channel {channel.name}: Not found")
            except discord.Forbidden:
                self.log.warning(f"Can't delete messages in channel {channel.name}: Missing permissions")
            except discord.HTTPException:
                self.log.error(f"Failed to delete message in channel {channel.name}", exc_info=True)

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
