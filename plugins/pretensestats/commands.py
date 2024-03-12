import json
import os

from core import Plugin, Status, PersistentReport, Channel
from discord.ext import tasks
from services import DCSServerBot


class PretenseStats(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.last_mtime = dict()

    async def cog_load(self) -> None:
        await super().cog_load()
        self.update_leaderboard.start()
        config = self.get_config()
        if config:
            interval = config.get('update_interval', 60)
            self.update_leaderboard.change_interval(seconds=interval)

    async def cog_unload(self) -> None:
        self.update_leaderboard.cancel()
        await super().cog_unload()

    @tasks.loop(seconds=60)
    async def update_leaderboard(self):
        for server in self.bot.servers.copy().values():
            try:
                if server.status != Status.RUNNING:
                    continue
                config = self.get_config(server) or {}
                json_file_path = os.path.expandvars(
                    config.get('json_file_path',
                               os.path.join(await server.get_missions_dir(), 'Saves', "player_stats.json"))
                )
                if not os.path.exists(json_file_path):
                    continue
                # only update, if the pretense file has been updated
                mtime = os.path.getmtime(json_file_path)
                if self.last_mtime.get(server.name, 0) == mtime:
                    continue
                self.last_mtime[server.name] = mtime
                with open(json_file_path, mode='r', encoding='utf8') as json_file:
                    data = json.load(json_file)

                report = PersistentReport(self.bot, self.plugin_name, "pretense.json", embed_name="leaderboard",
                                          channel_id=config.get('channel', server.channels[Channel.STATUS]),
                                          server=server)
                await report.render(data=data, server=server)
            except Exception as ex:
                self.log.exception(ex)

    @update_leaderboard.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    await bot.add_cog(PretenseStats(bot))
