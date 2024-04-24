import discord
from core import Plugin, PluginRequiredError, utils, Server, Player, TEventListener, Status, Coalition, \
    PluginInstallationError, command
from discord import app_commands
from discord.ext import tasks
from services import DCSServerBot
from typing import Optional, Type, Literal, AsyncGenerator
from .listener import MOTDListener


class MOTD(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)
        self.last_nudge = dict[str, int]()
        self.nudge.start()

    async def cog_unload(self):
        self.nudge.cancel()
        await super().cog_unload()

    @staticmethod
    def send_message(message: str, server: Server, config: dict, player: Optional[Player] = None):
        if config['display_type'].lower() == 'chat':
            if player:
                player.sendChatMessage(message)
            else:
                server.sendChatMessage(Coalition.ALL, message)
        elif config['display_type'].lower() == 'popup':
            timeout = config.get('display_time', server.locals.get('message_timeout', 10))
            if player:
                player.sendPopupMessage(message, timeout)
                if 'sound' in config:
                    player.playSound(config['sound'])
            else:
                server.sendPopupMessage(Coalition.ALL, message, timeout)
                if 'sound' in config:
                    server.playSound(Coalition.ALL, config['sound'])

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

    @tasks.loop(minutes=1.0)
    async def nudge(self):
        async def process_message(config: dict, message: str):
            if 'recipients' in config:
                async for recp in self.get_recipients(server, config):
                    self.send_message(message, server, config, recp)
            else:
                self.send_message(message, server, config)

        try:
            for server_name, server in self.bot.servers.copy().items():
                config = self.get_config(server)
                if server.status != Status.RUNNING or not config or 'nudge' not in config:
                    continue
                config = config['nudge']
                if server.name not in self.last_nudge:
                    self.last_nudge[server.name] = server.current_mission.mission_time
                elif server.current_mission.mission_time - self.last_nudge[server.name] > config['delay']:
                    if 'message' in config:
                        message = utils.format_string(config['message'], server=server)
                        await process_message(config, message)
                    elif 'messages' in config:
                        for cfg in config['messages']:
                            message = utils.format_string(cfg['message'], server=server)
                            await process_message(cfg, message)
                    self.last_nudge[server.name] = server.current_mission.mission_time
        except Exception as ex:
            self.log.exception(ex)

    @nudge.before_loop
    async def before_nudge(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(MOTD(bot, MOTDListener))
