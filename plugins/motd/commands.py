import discord
import json
from core import DCSServerBot, Plugin, PluginRequiredError, utils, Server, Player, TEventListener, Status, Coalition, \
    PluginInstallationError
from discord import app_commands
from discord.ext import tasks
from os import path
from typing import Optional, TYPE_CHECKING, Type, Literal
from .listener import MessageOfTheDayListener

if TYPE_CHECKING:
    from core import DCSServerBot


class MessageOfTheDay(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.json file found!", plugin=self.plugin_name)
        self.last_nudge = dict[str, int]()
        self.nudge.start()

    async def cog_unload(self):
        self.nudge.cancel()
        await super().cog_unload()

    def migrate(self, version: str):
        if version != '1.1' or not path.exists('config/motd.json'):
            return
        with open('config/motd.json') as file:
            old = json.load(file)
            if 'on_event' not in old['configs'][0]:
                return
        new = {
            "configs": []
        }
        for oldc in old['configs']:
            newc = dict()
            if 'installation' in oldc:
                newc['installation'] = oldc['installation']
            if 'on_event' in oldc:
                event = 'on_' + oldc['on_event']
                newc[event] = dict()
                if 'message' in oldc:
                    newc[event]['message'] = oldc['message']
                if 'display_type' in oldc:
                    newc[event]['display_type'] = oldc['display_type']
                if 'display_time' in oldc:
                    newc[event]['display_time'] = oldc['display_time']
            new['configs'].append(newc)
        with open('config/motd.json', 'w') as file:
            json.dump(new, file, indent=2)
            self.log.info('  => config/motd.json migrated to new format.')

    def send_message(self, message: str, server: Server, config: dict, player: Optional[Player] = None):
        if config['display_type'].lower() == 'chat':
            if player:
                player.sendChatMessage(message)
            else:
                server.sendChatMessage(Coalition.ALL, message)
        elif config['display_type'].lower() == 'popup':
            timeout = config['display_time'] if 'display_time' in config else self.bot.config['BOT']['MESSAGE_TIMEOUT']
            if player:
                player.sendPopupMessage(message, timeout)
            else:
                server.sendPopupMessage(Coalition.ALL, message, timeout)

    @staticmethod
    def get_recipients(server: Server, config: dict) -> list[Player]:
        recp = list[Player]()
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
            recp.append(player)
        return recp

    @app_commands.command(description='Test MOTD')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    @app_commands.autocomplete(server=utils.active_server_autocomplete)
    @app_commands.autocomplete(player=utils.active_player_autocomplete)
    async def motd(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer],
                   player: app_commands.Transform[Player, utils.PlayerTransformer],
                   option: Literal['join', 'birth', 'nudge']):
        config = self.get_config(server)
        if not config:
            await interaction.response.send_message('No configuration for MOTD found.', ephemeral=True)
            return
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            await interaction.response.send_message(f"Mission is {server.status.name.lower()}, can't test MOTD.",
                                                    ephemeral=True)
            return
        message = None
        if 'join' in option:
            message = self.eventlistener.on_join(config)
        elif 'birth' in option:
            message = await self.eventlistener.on_birth(config, server, player)
        elif 'nudge' in option:
            # TODO
            pass
        if message:
            await interaction.response.send_message.send(f"```{message}```")

    @tasks.loop(minutes=1.0)
    async def nudge(self):
        def process_message(message: str, server: Server, config: dict):
            if 'recipients' in config:
                for recp in self.get_recipients(server, config):
                    self.send_message(message, server, config, recp)
            else:
                self.send_message(message, server, config)

        try:
            for server_name, server in self.bot.servers.items():
                config = self.get_config(server)
                if server.status != Status.RUNNING or not config or 'nudge' not in config:
                    continue
                config = config['nudge']
                if server.name not in self.last_nudge:
                    self.last_nudge[server.name] = server.current_mission.mission_time
                elif server.current_mission.mission_time - self.last_nudge[server.name] > config['delay']:
                    if 'message' in config:
                        message = utils.format_string(config['message'], server=server)
                        process_message(message, server, config)
                    elif 'messages' in config:
                        for cfg in config['messages']:
                            message = utils.format_string(cfg['message'], server=server)
                            process_message(message, server, cfg)
                    self.last_nudge[server.name] = server.current_mission.mission_time
        except Exception as ex:
            self.log.exception(ex)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(MessageOfTheDay(bot, MessageOfTheDayListener))
