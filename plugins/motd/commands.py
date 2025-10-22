import asyncio
import discord
import random

from core import Plugin, PluginRequiredError, utils, Server, Player, Status, Coalition, \
    PluginInstallationError, command
from discord import app_commands
from discord.ext import tasks
from functools import partial
from services.bot import DCSServerBot
from typing import Type, Literal, AsyncGenerator
from .listener import MOTDListener


class MOTD(Plugin[MOTDListener]):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[MOTDListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)
        self.nudge_active: dict[str, dict[int, asyncio.TimerHandle]] = {}
        self.nudge.start()

    async def cog_unload(self):
        self.nudge.cancel()
        for server in self.bot.servers.values():
            await self._cancel_handles(server)
        await super().cog_unload()

    @staticmethod
    async def send_message(message: str, server: Server, config: dict, player: Player | None = None):
        if config['display_type'].lower() == 'chat':
            if player:
                await player.sendChatMessage(message)
            else:
                await server.sendChatMessage(Coalition.ALL, message)
        elif config['display_type'].lower() == 'popup':
            timeout = config.get('display_time', server.locals.get('message_timeout', 10))
            if player:
                await player.sendPopupMessage(message, timeout)
                if 'sound' in config:
                    await player.playSound(config['sound'])
            else:
                await server.sendPopupMessage(Coalition.ALL, message, timeout)
                if 'sound' in config:
                    await server.playSound(Coalition.ALL, config['sound'])

    @staticmethod
    async def get_recipients(server: Server, config: dict) -> AsyncGenerator[Player, None]:
        players: list[Player] = server.get_active_players()
        in_roles = []
        out_roles = []
        for role in config['recipients'].split(','):
            if not role.startswith('!'):
                in_roles.append(role)
            else:
                out_roles.append(role[1:])
        for player in players:
            if len(in_roles):
                if not player.member or not utils.check_roles(in_roles, player.member):
                    continue
            if len(out_roles):
                if player.member and utils.check_roles(out_roles, player.member):
                    continue
            yield player

    @command(description='Test MOTD')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    async def motd(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   option: Literal['on_join', 'on_birth']):
        config = self.get_config(server)
        if not config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message('No configuration for MOTD found.', ephemeral=True)
            return
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Mission is {server.status.name.lower()}, can't test MOTD.",
                                                    ephemeral=True)
            return
        message = None
        if option == 'on_join':
            if not config.get(option):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message("on_join not set in your motd.yaml.", ephemeral=True)
            else:
                message = await self.eventlistener.on_join(config[option], server, player)
        elif option == 'on_birth':
            if not config.get(option):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message("on_join not set in your motd.yaml.", ephemeral=True)
            message = await self.eventlistener.on_birth(config[option], server, player)
        elif option == 'nudge':
            # TODO
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Not implemented."), ephemeral=True)
        if message:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"```{message}```")

    async def _cancel_handles(self, server: Server):
        # cancel open timers
        for handle in self.nudge_active.get(server.name, {}).values():
            handle.cancel()
        self.nudge_active[server.name] = {}

    @tasks.loop(minutes=1.0)
    async def nudge(self):
        async def process_message(server: Server, config: dict, message: str):
            if 'recipients' in config:
                async for recp in self.get_recipients(server, config):
                    await self.send_message(message, server, config, recp)
            else:
                await self.send_message(message, server, config)

        def process_nudge(server: Server, config: dict):
            delay = config['delay']
            if server.status != Status.RUNNING:
                self.nudge_active[server.name].pop(delay, None)
                return
            if 'message' in config:
                message = utils.format_string(config['message'], server=server)
                asyncio.create_task(process_message(server, config, message))
            elif 'messages' in config:
                if config.get('random', False):
                    cfg = random.choice(config['messages'])
                    message = utils.format_string(cfg['message'], server=server)
                    asyncio.create_task(process_message(server, cfg, message))
                else:
                    for cfg in config['messages']:
                        message = utils.format_string(cfg['message'], server=server)
                        asyncio.create_task(process_message(server, cfg, message))
            # schedule next run
            t = self.loop.call_later(delay, partial(process_nudge, server, config))
            self.nudge_active[server.name][delay] = t

        try:
            for server_name, server in self.bot.servers.items():
                config = self.get_config(server)
                if not config or 'nudge' not in config:
                    continue
                handles = self.nudge_active.get(server_name, {})
                if server.status != Status.RUNNING:
                    if handles:
                        await self._cancel_handles(server)
                    continue
                elif handles:
                    continue
                config: dict = config['nudge']
                self.nudge_active[server_name] = {}
                if isinstance(config, list):
                    for c in config:
                        t = self.loop.call_later(int(c['delay']), partial(process_nudge, server, c))
                        self.nudge_active[server_name][c['delay']] = t
                else:
                    t = self.loop.call_later(int(config['delay']), partial(process_nudge, server, config))
                    self.nudge_active[server_name][config['delay']] = t
        except Exception as ex:
            self.log.exception(ex)

    @nudge.before_loop
    async def before_nudge(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(MOTD(bot, MOTDListener))
