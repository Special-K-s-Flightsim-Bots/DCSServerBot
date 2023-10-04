import discord

from core import Plugin, PluginRequiredError, utils, Report, Status, Server, command
from discord import app_commands
from plugins.userstats.filter import StatisticsFilter, MissionStatisticsFilter
from services import DCSServerBot
from typing import Optional, Union

from .listener import MissionStatisticsEventListener


class MissionStatistics(Plugin):

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Missionstats ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid,))
        elif days > -1:
            conn.execute(f"DELETE FROM missionstats WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Missionstats pruned.')

    @command(description='Display Mission Statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def missionstats(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(
                               status=[Status.RUNNING, Status.PAUSED])]):
        if server.name not in self.bot.mission_stats:
            await interaction.response.send_message(
                "Mission statistics not initialized yet or not active for this server.", ephemeral=True)
            return
        stats = self.bot.mission_stats[server.name]
        report = Report(self.bot, self.plugin_name, 'missionstats.json')
        env = await report.render(stats=stats, mission_id=server.mission_id,
                                  sides=utils.get_sides(interaction.client, interaction, server))
        await interaction.response.send_message(embed=env.embed, ephemeral=True)

    @command(description='Display statistics about sorties')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sorties(self, interaction: discord.Interaction,
                      user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                      period: Optional[str]):
        if not user:
            user = interaction.user
        flt = MissionStatisticsFilter()
        if period and not flt.supports(self.bot, period):
            await interaction.response.send_message('Please provide a valid period.', ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
            user = self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = self.bot.get_ucid_by_member(user)
            name = user.display_name
        report = Report(self.bot, self.plugin_name, 'sorties.json')
        env = await report.render(ucid=ucid, member_name=name, period=period, flt=flt)
        await interaction.response.send_message(embed=env.embed, ephemeral=True)

    @staticmethod
    def format_modules(data):
        embed = discord.Embed(title=f"Select a module from the list", color=discord.Color.blue())
        ids = modules = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            modules += f"{data[i]}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Module', value=modules)
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to display detailed stats about that specific module.')
        return embed

    @command(description='Module statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(module=utils.player_modules_autocomplete)
    async def modulestats(self, interaction: discord.Interaction,
                          user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                          module: Optional[str], period: Optional[str]):
        if not user:
            user = interaction.user
        if not module:
            await interaction.response.send_message('You need to chose a module!', ephemeral=True)
            return
        flt = StatisticsFilter.detect(self.bot, period)
        if period and not flt:
            await interaction.response.send_message('Please provide a valid period or campaign name!', ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
            user = self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = self.bot.get_ucid_by_member(user)
            name = user.display_name
        report = Report(self.bot, self.plugin_name, 'modulestats.json')
        env = await report.render(member_name=name, ucid=ucid, period=period, module=module, flt=flt)
        await interaction.response.send_message(embed=env.embed, ephemeral=True)

    @command(description='Refueling statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def refuelings(self, interaction: discord.Interaction,
                         user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                         period: Optional[str]):
        if not user:
            user = interaction.user
        flt = MissionStatisticsFilter()
        if period and not flt.supports(self.bot, period):
            await interaction.response.send_message('Please provide a valid period.', ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
            user = self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = self.bot.get_ucid_by_member(user)
            name = user.display_name
        report = Report(self.bot, self.plugin_name, 'refuelings.json')
        env = await report.render(ucid=ucid, member_name=name, period=period, flt=flt)
        await interaction.response.send_message(embed=env.embed, ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener))
