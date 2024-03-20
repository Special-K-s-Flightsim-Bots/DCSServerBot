import json
import os

from core import Plugin, Status, PersistentReport, Channel, utils
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
            interval = config.get('update_interval', 120)
            self.update_leaderboard.change_interval(seconds=interval)

    async def cog_unload(self) -> None:
        self.update_leaderboard.cancel()
        await super().cog_unload()

    @tasks.loop(seconds=120)
    async def update_leaderboard(self):
        for server in self.bot.servers.copy().values():
            try:
                if server.status != Status.RUNNING:
                    continue
                config = self.get_config(server) or {}
                json_file_path = config.get('json_file_path',
                                            os.path.join(await server.get_missions_dir(), 'Saves', "player_stats.json"))
                json_file_path = os.path.expandvars(utils.format_string(json_file_path, instance=server.instance))
                json_file_path = os.path.expandvars(json_file_path)
                try:
                    file_data = await server.node.read_file(json_file_path)
                except FileNotFoundError:
                    continue
                content = file_data.decode(encoding='utf-8')
                data = json.loads(content)
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
