import aiohttp
import certifi
import json
import pandas as pd
import psycopg
import ssl
import unicodedata

from core import Plugin, PluginInstallationError, PersistentReport
from discord.ext import tasks
from psycopg.rows import dict_row
from services import DCSServerBot


def filter_non_printable(_input: str) -> str:
    return ''.join(c for c in _input if unicodedata.category(c) in {'Lu', 'Ll', 'Zs', 'Nd', 'Pc', 'Pd'})


class Community(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)
        self.render.start()

    async def cog_unload(self):
        self.render.stop()

    async def get_all_servers(self) -> list[dict]:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
            await session.post("https://www.digitalcombatsimulator.com/gameapi/login/",
                               data={
                                   "login": self.locals['dcs_user'],
                                   "password": self.locals['dcs_password']
                               })
            async with session.get("https://www.digitalcombatsimulator.com/gameapi/serverlist/") as response:
                if response.status == 200:
                    return json.loads(await response.text(encoding='utf-8'))
                else:
                    return []

    async def render_community_stats(self, all_servers: list[dict]):
        try:
            async with await psycopg.AsyncConnection.connect(self.locals['database']) as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute("""
                        SELECT guild_id, bot_version, variant, dcs_version, 
                               substr(python_version, 1, length(python_version) - position('.' in reverse(python_version))) as "python_version", 
                               num_bots, num_servers, last_seen 
                        FROM dcssb_installations 
                        WHERE last_seen > (DATE(now() AT TIME ZONE 'utc') - interval '1 week') AND num_bots > 0
                    """)
                    bots = pd.DataFrame.from_dict(await cursor.fetchall())
                    await cursor.execute("""
                        SELECT * FROM dcssb_plugin_installations 
                        WHERE guild_id IN (
                            SELECT guild_id FROM dcssb_installations 
                            WHERE last_seen > (DATE(now() AT TIME ZONE 'utc') - interval '1 week') AND num_bots > 0
                        )
                    """)
                    plugins = pd.DataFrame.from_dict(await cursor.fetchall())
                    report = PersistentReport(self.bot, self.plugin_name, "communitystats.json",
                                              embed_name="communitystats", channel_id=int(self.locals['channel']))
                    await report.render(bots=bots, plugins=plugins, all_servers=all_servers)
        except Exception as ex:
            self.log.exception(ex)

    async def render_dcs_stats(self, all_servers: list[dict]):
        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        await cursor.executemany("""
                            INSERT INTO all_servers (server_name, address, port, num_players, max_players, geocontinent, 
                                                     geocountry) 
                            VALUES (%(serverName)s, %(address)s, %(port)s, %(numPlayers)s, %(maxPlayers)s, 
                                    %(geoContinent)s, %(geoCountry)s)
                        """, all_servers)
            report = PersistentReport(self.bot, self.plugin_name, "dcsstats.json",
                                      embed_name="dcsstats", channel_id=int(self.locals['channel']))
            await report.render(all_servers=all_servers)
        except Exception as ex:
            self.log.exception(ex)

    @tasks.loop(hours=1)
    async def render(self):
        try:
            all_servers = await self.get_all_servers()
            await self.render_community_stats(all_servers)
            await self.render_dcs_stats(all_servers)
        except Exception as ex:
            self.log.exception(ex)

    @render.before_loop
    async def before_render(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    await bot.add_cog(Community(bot))
