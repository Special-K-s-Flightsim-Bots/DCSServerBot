from __future__ import annotations
import asyncio
from core import utils, EventListener, PersistentReport, Plugin, Report, Status, Side, Mission, Player, Coalition, \
    Channel, DataObjectFactory
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


class MissionEventListener(EventListener):
    EVENT_TEXTS = {
        Side.BLUE: {
            'takeoff': '```ansi\n\u001b[0;34mBLUE player {} took off from {}.```',
            'landing': '```ansi\n\u001b[0;34mBLUE player {} landed at {}.```',
            'eject': '```ansi\n\u001b[0;34mBLUE player {} ejected.```',
            'crash': '```ansi\n\u001b[0;34mBLUE player {} crashed.```',
            'pilot_death': '```ansi\n\u001b[0;34mBLUE player {} died.```',
            'kill': '```ansi\n\u001b[0;34mBLUE {} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33mBLUE {} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```ansi\n\u001b[0;34mBLUE player {} killed themselves - Ooopsie!```',
            'change_slot': '```ansi\n\u001b[0;34m{} player {} occupied {} {}```',
            'disconnect': '```ansi\n\u001b[0;34mBLUE player {} disconnected```'
        },
        Side.RED: {
            'takeoff': '```ansi\n\u001b[0;31mRED player {} took off from {}.```',
            'landing': '```ansi\n\u001b[0;31mRED player {} landed at {}.```',
            'eject': '```ansi\n\u001b[0;31mRED player {} ejected.```',
            'crash': '```ansi\n\u001b[0;31mRED player {} crashed.```',
            'pilot_death': '```ansi\n\u001b[0;31mRED player {} died.```',
            'kill': '```ansi\n\u001b[0;31mRED {} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33mRED {} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```ansi\n\u001b[0;31mRED player {} killed themselves - Ooopsie!```',
            'change_slot': '```ansi\n\u001b[0;31m{} player {} occupied {} {}```',
            'disconnect': '```ansi\n\u001b[0;31mRED player {} disconnected```'
        },
        Side.SPECTATOR: {
            'connect': '```\nPlayer {} connected to server```',
            'disconnect': '```\nPlayer {} disconnected```',
            'spectators': '```\n{} player {} returned to Spectators```',
            'crash': '```\nPlayer {} crashed.```',
            'pilot_death': '```\n[Player {} died.```',
            'kill': '```\n{} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33m{} FRIENDLY FIRE onto {} with {}.```'
        },
        Side.UNKNOWN: {
            'kill': '```\n{} in {} killed {} {} in {} with {}.```'
        }
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.afk: dict[Player, datetime] = dict()

    async def sendMessage(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if int(data['channel']) == -1:
            channel = server.get_channel(Channel.CHAT)
        else:
            channel = self.bot.get_channel(int(data['channel']))
        if channel:
            self.bot.loop.call_soon(asyncio.create_task, channel.send(data['message']))

    async def sendEmbed(self, data):
        server: Server = self.bot.servers[data['server_name']]
        embed = utils.format_embed(data)
        if 'id' in data and len(data['id']) > 0:
            channel = int(data['channel'])
            if channel == -1:
                channel = Channel.STATUS
            return self.bot.loop.call_soon(asyncio.create_task, server.setEmbed(data['id'], embed, channel_id=channel))
        else:
            if int(data['channel']) == -1:
                channel = server.get_channel(Channel.CHAT)
            else:
                channel = self.bot.get_channel(int(data['channel']))
            if channel:
                self.bot.loop.call_soon(asyncio.create_task, channel.send(embed=embed))

    def _send_chat_message(self, server: Server, message: str) -> None:
        chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel:
            self.bot.loop.call_soon(asyncio.create_task, chat_channel.send(message))

    def _display_mission_embed(self, server: Server):
        try:
            if not len(server.settings):
                return
            players = server.get_active_players()
            num_players = len(players) + 1
            report = PersistentReport(self.bot, self.plugin_name, 'serverStatus.json', server, 'mission_embed')
            self.bot.loop.call_soon(asyncio.create_task, report.render(server=server, num_players=num_players))
        except Exception as ex:
            self.log.exception(ex)

    # Display the list of active players
    def _display_player_embed(self, server: Server):
        if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
            report = PersistentReport(self.bot, self.plugin_name, 'players.json', server, 'players_embed')
            self.bot.loop.call_soon(asyncio.create_task,
                                    report.render(server=server, sides=[Coalition.BLUE, Coalition.RED]))

    async def callback(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            server.sendtoDCS(data)

    async def registerDCSServer(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        # the server is starting up
        if not data['channel'].startswith('sync-'):
            return
        # no mission is registered with the server, set the state to STOPPED
        if 'current_mission' not in data:
            server.status = Status.STOPPED
            return
        # the server was started already, but the bot wasn't
        if not server.current_mission:
            server.current_mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                             map=data['current_map'], name=data['current_mission'])

        server.status = Status.PAUSED if data['pause'] is True else Status.RUNNING
        server.current_mission.update(data)
        if 'players' not in data:
            data['players'] = []
            server.status = Status.STOPPED
        self.afk.clear()
        for p in data['players']:
            if p['id'] == 1:
                continue
            player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=p['id'],
                                                     name=p['name'], active=p['active'], side=Side(p['side']),
                                                     ucid=p['ucid'], slot=int(p['slot']), sub_slot=p['sub_slot'],
                                                     unit_callsign=p['unit_callsign'], unit_name=p['unit_name'],
                                                     unit_type=p['unit_type'], group_id=p['group_id'],
                                                     group_name=p['group_name'], banned=False)
            server.add_player(player)
            if Side(p['side']) == Side.SPECTATOR:
                self.afk[player] = datetime.now()
        self._display_mission_embed(server)
        self._display_player_embed(server)

    async def onMissionLoadBegin(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.LOADING
        if not server.current_mission:
            server.current_mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                             map=data['current_map'], name=data['current_mission'])
        server.current_mission.update(data)
        server.players = dict[int, Player]()
        if server.settings:
            self._display_mission_embed(server)
        self._display_player_embed(server)

    async def onMissionLoadEnd(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.current_mission.update(data)
        self._display_mission_embed(server)

    async def onSimulationStart(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.PAUSED
        self._display_mission_embed(server)

    async def onSimulationStop(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.STOPPED
        self._display_mission_embed(server)

    async def onSimulationPause(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.PAUSED
        self._display_mission_embed(server)

    async def onSimulationResume(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.RUNNING
        self._display_mission_embed(server)

    async def onPlayerConnect(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if data['id'] == 1:
            return
        self._send_chat_message(server, self.EVENT_TEXTS[Side.SPECTATOR]['connect'].format(data['name']))
        player: Player = server.get_player(ucid=data['ucid'])
        if not player or player.id == 1:
            player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=data['id'],
                                                     name=data['name'], active=data['active'], side=Side(data['side']),
                                                     ucid=data['ucid'], banned=False)
            server.add_player(player)
        else:
            player.update(data)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        # unlikely, but can happen if the bot was restarted during a mission restart
        if not player:
            player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=data['id'],
                                             name=data['name'], active=data['active'], side=Side(data['side']),
                                             ucid=data['ucid'], banned=False)
            server.add_player(player)
        else:
            player.update(data)
        if not player.member:
            player.sendChatMessage(self.bot.config['DCS']['GREETING_MESSAGE_UNMATCHED'].format(
                name=player.name, prefix=self.bot.config['BOT']['COMMAND_PREFIX']))
            # only warn for unknown users if it is a non-public server and automatch is on
            if self.bot.config.getboolean('BOT', 'AUTOMATCH') and len(server.settings['password']) > 0:
                await server.get_channel(Channel.ADMIN).send(
                    f'Player {player.name} (ucid={player.ucid}) can\'t be matched to a discord user.')
        else:
            player.sendChatMessage(self.bot.config['DCS']['GREETING_MESSAGE_MEMBERS'].format(player.name, server.name))
        # add the player to the afk list
        self.afk[player] = datetime.now()
        self._display_mission_embed(server)
        self._display_player_embed(server)

    async def onPlayerStop(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        if player:
            player.active = False
            if player in self.afk:
                del self.afk[player]
        self._display_mission_embed(server)
        self._display_player_embed(server)

    async def onPlayerChangeSlot(self, data: dict) -> None:
        if 'side' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        try:
            if Side(data['side']) != Side.SPECTATOR:
                if player in self.afk:
                    del self.afk[player]
                if player is not None:
                    self._send_chat_message(server, self.EVENT_TEXTS[Side(data['side'])]['change_slot'].format(
                        player.side.name if player.side != Side.SPECTATOR else 'NEUTRAL',
                        data['name'], Side(data['side']).name, data['unit_type']))
            elif player is not None:
                self.afk[player] = datetime.now()
                self._send_chat_message(server, self.EVENT_TEXTS[Side.SPECTATOR]['spectators'].format(player.side.name,
                                                                                                      data['name']))
        finally:
            if player:
                player.update(data)
            self._display_player_embed(server)

    async def onGameEvent(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        # ignore game events until the server is not initialized correctly
        if server.status not in [Status.RUNNING, Status.STOPPED]:
            return
        if data['eventName'] in ['mission_end', 'connect', 'change_slot']:  # these events are handled differently
            return
        elif data['eventName'] == 'disconnect':
            if data['arg1'] == 1:
                return
            player = server.get_player(id=data['arg1'])
            if not player:
                return
            try:
                self._send_chat_message(server, self.EVENT_TEXTS[player.side]['disconnect'].format(player.name))
            finally:
                player.active = False
                if player in self.afk:
                    del self.afk[player]
                self._display_mission_embed(server)
                self._display_player_embed(server)
        elif data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            player1 = server.get_player(id=data['arg1'])
            if data['arg3'] != -1:
                player2 = server.get_player(id=data['arg3'])
            # TODO: remove if issue with Forrestal is fixed
            elif data['arg2'] == player1.unit_type:
                return
            else:
                player2 = None
            self._send_chat_message(server, self.EVENT_TEXTS[player1.side][data['eventName']].format(
                'player ' + player1.name, ('player ' + player2.name) if player2 is not None else 'AI',
                data['arg2'] or 'Cannon/Bomblet'))
        elif data['eventName'] == 'self_kill':
            player = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            self._send_chat_message(server, self.EVENT_TEXTS[player.side][data['eventName']].format(player.name))
        elif data['eventName'] == 'kill':
            # Player is not an AI
            player1 = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            player2 = server.get_player(id=data['arg4']) if data['arg4'] != -1 else None
            self._send_chat_message(server, self.EVENT_TEXTS[Side(data['arg3'])][data['eventName']].format(
                ('player ' + player1.name) if player1 is not None else 'AI',
                data['arg2'] or 'SCENERY', Side(data['arg6']).name,
                ('player ' + player2.name) if player2 is not None else 'AI',
                data['arg5'] or 'SCENERY', data['arg7'] or 'Cannon/Bomblet'))
            # report teamkills from players to admins
            if (player1 is not None) and (data['arg1'] != data['arg4']) and (data['arg3'] == data['arg6']):
                if player1.member:
                    await server.get_channel(Channel.ADMIN).send(f'Member {player1.member.display_name} is killing '
                                                                 f'team members. Please investigate.')
                else:
                    await server.get_channel(Channel.ADMIN).send(f'Player {player1.name} (ucid={player1.ucid}) is '
                                                                 f'killing team members. Please investigate.')
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            if data['arg1'] != -1:
                player = server.get_player(id=data['arg1'])
                if not player:
                    return
                if data['eventName'] in ['takeoff', 'landing']:
                    self._send_chat_message(server, self.EVENT_TEXTS[player.side][data['eventName']].format(
                        player.name, data['arg3'] if len(data['arg3']) > 0 else 'ground'))
                else:
                    self._send_chat_message(server,
                                            self.EVENT_TEXTS[player.side][data['eventName']].format(player.name))

    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'])
        if not player:
            return
        if data['subcommand'] == 'atis':
            if len(data['params']) == 0:
                player.sendChatMessage(f"Usage: -atis <airbase/code>")
                return
            name = ' '.join(data['params'])
            for airbase in server.current_mission.airbases:
                if (name.casefold() in airbase['name'].casefold()) or (name.upper() == airbase['code']):
                    response = await server.sendtoDCSSync({
                        "command": "getWeatherInfo",
                        "x": airbase['position']['x'],
                        "y": airbase['position']['y'],
                        "z": airbase['position']['z']
                    })
                    report = Report(self.bot, self.plugin_name, 'atis-ingame.json')
                    env = await report.render(airbase=airbase, data=response)
                    message = utils.embed_to_simpletext(env.embed)
                    player.sendUserMessage(message, 30)
                    return
            player.sendChatMessage(f"No ATIS information found for {name}.")
        elif data['subcommand'] == 'restart' and player.has_discord_roles(['DCS Admin']):
            delay = data['params'][0] if len(data['params']) > 0 else 0
            if delay > 0:
                message = f'!!! Server will be restarted in {utils.format_time(delay)}!!!'
            else:
                message = '!!! Server will be restarted NOW !!!'
            server.sendPopupMessage(Coalition.ALL, message)
            self.bot.loop.call_soon(asyncio.create_task, server.current_mission.restart())
        elif data['subcommand'] == 'list' and player.has_discord_roles(['DCS Admin']):
            response = await server.sendtoDCSSync({"command": "listMissions"})
            missions = response['missionList']
            message = 'The following missions are available:\n'
            for i in range(0, len(missions)):
                mission = missions[i]
                mission = mission[(mission.rfind('\\') + 1):-4]
                message += f"{i+1} {mission}\n"
            message += f"\nUse {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}load <number> to load that mission"
            player.sendUserMessage(message, 30)
        elif data['subcommand'] == 'load' and player.has_discord_roles(['DCS Admin']):
            self.bot.loop.call_soon(asyncio.create_task, server.loadMission(data['params'][0]))
