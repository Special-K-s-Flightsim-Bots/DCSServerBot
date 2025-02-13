import discord
import psycopg

from core import Plugin, PluginRequiredError, utils, Report, Status, Server, command, get_translation
from discord import app_commands
from plugins.userstats.filter import StatisticsFilter, MissionStatisticsFilter, PeriodTransformer, PeriodFilter, \
    CampaignFilter, MissionFilter
from services.bot import DCSServerBot
from typing import Optional, Union

from .listener import MissionStatisticsEventListener

_ = get_translation(__name__.split('.')[1])


async def player_modules_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:

    async def get_modules(ucid: str) -> list[str]:
        async with interaction.client.apool.connection() as conn:
            return [row[0] async for row in await conn.execute("""
                SELECT DISTINCT slot, COUNT(*) FROM statistics 
                WHERE player_ucid =  %s 
                AND slot NOT IN ('', '?', '''forward_observer', 'instructor', 'observer', 'artillery_commander') 
                GROUP BY 1 ORDER BY 2 DESC
            """, (ucid, ))]

    try:
        user = await utils.UserTransformer().transform(interaction, utils.get_interaction_param(interaction, "user"))
        if not user:
            return []
        if isinstance(user, str):
            ucid = user
        else:
            ucid = await interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        ret = [
            app_commands.Choice(name=x, value=x)
            for x in await get_modules(ucid)
            if not current or current.casefold() in x.casefold()
        ]
        return ret[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class MissionStatistics(Plugin[MissionStatisticsEventListener]):

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Missionstats ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid,))
        elif days > -1:
            await conn.execute("DELETE FROM missionstats WHERE time < (DATE(NOW()) - %s::interval)", (f'{days} days', ))
        if server:
            await conn.execute("""
                DELETE FROM missionstats WHERE mission_id in (
                    SELECT id FROM missions WHERE server_name = %s
                )
            """, (server, ))
            await conn.execute("""
                DELETE FROM missionstats WHERE mission_id NOT IN (
                    SELECT id FROM missions
                )
            """)
        self.log.debug('Missionstats pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute("UPDATE missionstats SET init_id = %s WHERE init_id = %s", (new_ucid, old_ucid))
        await conn.execute("UPDATE missionstats SET target_id = %s WHERE target_id = %s", (new_ucid, old_ucid))

    @command(description=_('Display Mission Statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def missionstats(self, interaction: discord.Interaction,
                           server: app_commands.Transform[Server, utils.ServerTransformer(
                               status=[Status.RUNNING, Status.PAUSED])]):
        stats = self.eventlistener.mission_stats.get(server.name)
        if not stats:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Mission statistics not initialized yet or not active for this server."), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'missionstats.json')
        env = await report.render(stats=stats, mission_id=server.mission_id,
                                  sides=utils.get_sides(interaction.client, interaction, server))
        await interaction.followup.send(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Display statistics about sorties'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def sorties(self, interaction: discord.Interaction,
                      user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                      period: Optional[app_commands.Transform[
                          StatisticsFilter, PeriodTransformer(flt=[MissionStatisticsFilter])]
                      ] = MissionStatisticsFilter()):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'sorties.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Module statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(module=player_modules_autocomplete)
    async def modulestats(self, interaction: discord.Interaction,
                          user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                          module: Optional[str],
                          period: Optional[app_commands.Transform[
                              StatisticsFilter, PeriodTransformer(
                                  flt=[PeriodFilter, CampaignFilter, MissionFilter]
                              )]] = PeriodFilter()):
        if not user:
            user = interaction.user
        if not module:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('You need to chose a module!'), ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'modulestats.json')
        env = await report.render(member_name=name, ucid=ucid, module=module, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Refueling statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def refuelings(self, interaction: discord.Interaction,
                         user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]],
                         period: Optional[app_commands.Transform[
                             StatisticsFilter, PeriodTransformer(flt=[MissionStatisticsFilter])]
                         ] = MissionStatisticsFilter()):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'refuelings.json')
        env = await report.render(ucid=ucid, member_name=name, flt=period)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_('Find who killed you most'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def nemesis(self, interaction: discord.Interaction,
                      user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]]):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'nemesis.json')
        env = await report.render(ucid=ucid, member_name=name)
        await interaction.followup.send(embed=env.embed, ephemeral=True)

    @command(description=_("Find who you've killed the most"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def antagonist(self, interaction: discord.Interaction,
                         user: Optional[app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]]):
        if not user:
            user = interaction.user
        if isinstance(user, str):
            ucid = user
            user = await self.bot.get_member_or_name_by_ucid(ucid)
            if isinstance(user, discord.Member):
                name = user.display_name
            else:
                name = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=True)
        report = Report(self.bot, self.plugin_name, 'antagonist.json')
        env = await report.render(ucid=ucid, member_name=name)
        await interaction.followup.send(embed=env.embed, ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(MissionStatistics(bot, MissionStatisticsEventListener))
