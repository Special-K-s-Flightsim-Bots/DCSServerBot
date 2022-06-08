import json
from typing import Optional

from core import DCSServerBot, Plugin, PluginRequiredError, utils
from core.const import Status
from discord.ext import tasks
from os import path
from .listener import MessageOfTheDayListener


class MessageOfTheDay(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.nudge.start()

    def cog_unload(self):
        self.nudge.cancel()
        super().cog_unload()

    def migrate(self, version: str):
        if version != 'v1.1' or not path.exists('config/motd.json'):
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

    def sendMessage(self, server: dict, config: dict, player: Optional[dict] = None):
        message = utils.format_string(config['message'], server=server)
        if config['display_type'].lower() == 'chat':
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "message": message,
                "to": player['id']
            })
        elif config['display_type'].lower() == 'popup':
            if 'display_time' in config:
                display_time = config['display_time']
            else:
                display_time = self.config['BOT']['MESSAGE_TIMEOUT']
            self.bot.sendtoDCS(server, {
                "command": "sendPopupMessage",
                "message": message,
                "time": display_time,
                "to": player['slot']
            })

    def get_recipients(self, server: dict, config: dict) -> list[dict]:
        recp = []
        players = self.bot.player_data[server['server_name']]
        players = players[players['active'] == True]
        in_roles = []
        out_roles = []
        for role in config['recipients'].split(','):
            if not role.startswith('!'):
                in_roles.append(role)
            else:
                out_roles.append(role[1:])
        for idx, player in players.iterrows():
            member = utils.get_member_by_ucid(self, player['ucid'], True)
            if len(in_roles):
                if not member or not utils.check_roles(in_roles, member):
                    continue
            if len(out_roles):
                if member and utils.check_roles(out_roles, member):
                    continue
            recp.append(player)
        return recp

    @tasks.loop(minutes=1.0)
    async def nudge(self):
        def process_message(config):
            if 'recipients' in config:
                for recp in self.get_recipients(server, config):
                    self.sendMessage(server, config, recp)
            else:
                self.sendMessage(server, config)

        for server_name, server in self.globals.items():
            if self.plugin_name not in server or \
                    server['status'] != Status.RUNNING or \
                    'nudge' not in server[self.plugin_name]:
                continue
            config = server[self.plugin_name]['nudge']
            if 'last_nudge' not in server:
                server['last_nudge'] = server['mission_time']
            elif server['mission_time'] - server['last_nudge'] > config['delay']:
                if 'message' in config:
                    process_message(config)
                elif 'messages' in config:
                    for cfg in config['messages']:
                        process_message(cfg)
                server['last_nudge'] = server['mission_time']


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(MessageOfTheDay(bot, MessageOfTheDayListener))
