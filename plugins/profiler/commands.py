import asyncio
import discord

from core import Plugin, Group, utils, Server, Status, get_translation
from discord import app_commands
from services.bot import DCSServerBot
from typing import Literal, Type

from .listener import ProfilerListener

_ = get_translation(__name__.split('.')[1])


class Profiler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[ProfilerListener] = None):
        super().__init__(bot, eventlistener)
        self.profilers: dict[str, str] = {}

    # New command group "/profile"
    profile = Group(name="profiler", description=_("Commands to control the profiling mechanism"))

    @profile.command(description=_('Start profiler'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_roles(['DCS Admin'])
    async def start(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED])],
                    profiler: Literal['Chrome', 'Callgrind'] | None = 'Chrome',
                    verbose: bool | None = False):
        p = self.profilers.get(server.name)
        if not p:
            await server.send_to_dcs({
                'command': 'loadProfiler',
                'profiler': profiler.lower()
            })
            self.profilers[server.name] = profiler.lower()

        elif p != profiler.lower():
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Profiler {} is already loaded.").format(p.title()),
                                                    ephemeral=True)
            return

        asyncio.create_task(server.send_to_dcs({
            'command': 'startProfiling',
            'channel': interaction.channel.id,
            'verbose': verbose
        }))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Starting profiler ..."), ephemeral=utils.get_ephemeral(interaction))

    @profile.command(description=_('Stop profiler'))
    @app_commands.guild_only()
    @app_commands.check(utils.restricted_check)
    @utils.app_has_roles(['DCS Admin'])
    async def stop(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED])]):
        asyncio.create_task(server.send_to_dcs({
            'command': 'stopProfiling',
            'channel': interaction.channel.id
        }))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_("Stopping profiler ..."), ephemeral=utils.get_ephemeral(interaction))


async def setup(bot):
    plugin = Profiler(bot, ProfilerListener)
    plugin.log.warning(f"The {plugin.__cog_name__} plugin is activated. This can result in performance degradation.")
    await bot.add_cog(plugin)
