import json
from core import DCSServerBot, Plugin, PluginRequiredError, utils, Server, Player, TEventListener, Status
from datetime import datetime
from discord.ext import tasks
from os import path
from typing import Optional, TYPE_CHECKING, Type
from .listener import MessageOfTheDayListener

if TYPE_CHECKING:
    from core import DCSServerBot


class MessageOfTheDay(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.last_nudge = dict[str, datetime]()
        self.nudge.start()

    def cog_unload(self):
        self.nudge.cancel()
        super().cog_unload()

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

    def sendMessage(self, server: Server, config: dict, player: Optional[Player] = None):
        message = utils.format_string(config['message'], server=server)
        if config['display_type'].lower() == 'chat':
            player.sendChatMessage(message)
        elif config['display_type'].lower() == 'popup':
            player.sendPopupMessage(message, config['display_time'] if 'display_time' in config else self.bot.config['BOT']['MESSAGE_TIMEOUT'])

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

    @tasks.loop(minutes=1.0)
    async def nudge(self):
        def process_message(config: dict):
            if 'recipients' in config:
                for recp in self.get_recipients(server, config):
                    self.sendMessage(server, config, recp)
            else:
                self.sendMessage(server, config)

        for server_name, server in self.bot.servers.items():
            config = self.get_config(server)
            if server.status != Status.RUNNING or not config or 'nudge' not in config:
                continue
            config = config['nudge']
            if server.name not in self.last_nudge:
                self.last_nudge[server.name] = server.current_mission.mission_time
            elif server.current_mission.mission_time - self.last_nudge[server.name] > config['delay']:
                if 'message' in config:
                    process_message(config)
                elif 'messages' in config:
                    for cfg in config['messages']:
                        process_message(cfg)
                self.last_nudge[server.name] = server.current_mission.mission_time


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(MessageOfTheDay(bot, MessageOfTheDayListener))
