from __future__ import annotations
from core import utils, EventListener, PersistentReport, Plugin, Report, Status, Side, Mission, Player, Coalition, \
    Channel, DataObjectFactory
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


class MissionEventListener(EventListener):
    EVENT_TEXTS = {
        Side.BLUE: {
            'takeoff': '```ini\n[BLUE player {} took off from {}.]```',
            'landing': '```ini\n[BLUE player {} landed at {}.]```',
            'eject': '```ini\n[BLUE player {} ejected.]```',
            'crash': '```ini\n[BLUE player {} crashed.]```',
            'pilot_death': '```ini\n[BLUE player {} died.]```',
            'kill': '```ini\n[BLUE {} in {} killed {} {} in {} with {}.]```',
            'friendly_fire': '```fix\n[BLUE {} FRIENDLY FIRE onto {} with {}.]```',
            'self_kill': '```ini\n[BLUE player {} killed themselves - Ooopsie!]```',
            'change_slot': '```ini\n[{} player {} occupied {} {}]```',
            'disconnect': '```ini\n[BLUE player {} disconnected]```'
        },
        Side.RED: {
            'takeoff': '```css\n[RED player {} took off from {}.]```',
            'landing': '```css\n[RED player {} landed at {}.]```',
            'eject': '```css\n[RED player {} ejected.]```',
            'crash': '```css\n[RED player {} crashed.]```',
            'pilot_death': '```css\n[RED player {} died.]```',
            'kill': '```css\n[RED {} in {} killed {} {} in {} with {}.]```',
            'friendly_fire': '```fix\n[RED {} FRIENDLY FIRE onto {} with {}.]```',
            'self_kill': '```css\n[RED player {} killed themselves - Ooopsie!]```',
            'change_slot': '```css\n[{} player {} occupied {} {}]```',
            'disconnect': '```css\n[RED player {} disconnected]```'
        },
        Side.SPECTATOR: {
            'connect': '```\n[Player {} connected to server]```',
            'disconnect': '```\n[Player {} disconnected]```',
            'spectators': '```\n[{} player {} returned to Spectators]```',
            'crash': '```ini\n[Player {} crashed.]```',
            'pilot_death': '```ini\n[Player {} died.]```',
            'kill': '```ini\n[{} in {} killed {} {} in {} with {}.]```',
            'friendly_fire': '```fix\n[{} FRIENDLY FIRE onto {} with {}.]```'
        },
        Side.UNKNOWN: {
            'kill': '```ini\n[{} in {} killed {} {} in {} with {}.]```'
        }
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    async def sendMessage(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if int(data['channel']) == -1:
            channel = server.get_channel(Channel.CHAT)
        else:
            channel = self.bot.get_channel(int(data['channel']))
        if channel:
            await channel.send(data['message'])

    async def sendEmbed(self, data):
        server: Server = self.bot.servers[data['server_name']]
        embed = utils.format_embed(data)
        if 'id' in data and len(data['id']) > 0:
            return await server.setEmbed(data['id'], embed)
        else:
            if int(data['channel']) == -1:
                channel = server.get_channel(Channel.CHAT)
            else:
                channel = self.bot.get_channel(int(data['channel']))
            if channel:
                await channel.send(embed=embed)

    @staticmethod
    async def _send_chat_message(server: Server, message: str) -> None:
        chat_channel = server.get_channel(Channel.CHAT)
        if chat_channel:
            await chat_channel.send(message)

    async def _display_mission_embed(self, server: Server):
        try:
            if not len(server.settings):
                return
            players = server.get_active_players()
            num_players = len(players) + 1
            report = PersistentReport(self.bot, self.plugin_name, 'serverStatus.json', server, 'mission_embed')
            return await report.render(server=server, num_players=num_players)
        except Exception as ex:
            self.log.exception(ex)

    # Display the list of active players
    async def _display_player_embed(self, server: Server):
        if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
            report = PersistentReport(self.bot, self.plugin_name, 'players.json', server, 'players_embed')
            return await report.render(server=server, sides=[Coalition.BLUE, Coalition.RED])

    async def callback(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            server.sendtoDCS(data)

    async def registerDCSServer(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if not server.current_mission:
            mission: Mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                       map=data['current_map'], name=data['current_mission'])
            server.current_mission = mission
        server.current_mission.update(data)
        server.status = Status.PAUSED if 'pause' in data and data['pause'] is True else Status.RUNNING
        if data['channel'].startswith('sync-'):
            if 'players' not in data:
                data['players'] = []
                server.status = Status.STOPPED
            for p in data['players']:
                player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=p['id'],
                                                         name=p['name'], active=p['active'], side=Side(p['side']),
                                                         ucid=p['ucid'], ipaddr=p['ipaddr'], slot=p['slot'],
                                                         sub_slot=p['sub_slot'], unit_callsign=p['unit_callsign'],
                                                         unit_name=p['unit_name'], unit_type=p['unit_type'],
                                                         group_id=p['group_id'], group_name=p['group_name'],
                                                         banned=False)
                server.add_player(player)
        await self._display_mission_embed(server)
        await self._display_player_embed(server)

    async def onMissionLoadBegin(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.LOADING
        mission: Mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                   map=data['current_map'], name=data['current_mission'])
        mission.update(data)
        server.current_mission = mission
        server.players = dict[int, Player]()
        if server.settings:
            await self._display_mission_embed(server)
        await self._display_player_embed(server)

    async def onMissionLoadEnd(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.current_mission.update(data)
        server.status = Status.PAUSED
        await self._display_mission_embed(server)

    async def onSimulationStop(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.STOPPED
        server.current_mission = None
        await self._display_mission_embed(server)

    async def onSimulationPause(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.PAUSED
        await self._display_mission_embed(server)

    async def onSimulationResume(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.RUNNING
        await self._display_mission_embed(server)

    async def onPlayerConnect(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        if data['id'] == 1:
            return
        try:
            await self._send_chat_message(server, self.EVENT_TEXTS[Side.SPECTATOR]['connect'].format(data['name']))
        finally:
            player: Player = server.get_player(ucid=data['ucid'])
            if not player or player.id == 1:
                player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=data['id'],
                                                         name=data['name'], active=data['active'],
                                                         side=Side(data['side']), ucid=data['ucid'],
                                                         ipaddr=data['ipaddr'], banned=False)
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
                                             ucid=data['ucid'], ipaddr=data['ipaddr'], banned=False)
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
        await self._display_mission_embed(server)
        await self._display_player_embed(server)

    async def onPlayerStop(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        if player:
            player.active = False
        await self._display_mission_embed(server)
        await self._display_player_embed(server)

    async def onPlayerChangeSlot(self, data: dict) -> None:
        if 'side' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        try:
            if Side(data['side']) != Side.SPECTATOR:
                if player is not None:
                    await self._send_chat_message(server, self.EVENT_TEXTS[Side(data['side'])]['change_slot'].format(
                        player.side.name if player.side != Side.SPECTATOR else 'NEUTRAL',
                        data['name'], Side(data['side']).name, data['unit_type']))
            elif player is not None:
                await self._send_chat_message(server,
                                              self.EVENT_TEXTS[Side.SPECTATOR]['spectators'].format(player.side.name,
                                                                                                    data['name']))
        finally:
            if player:
                player.update(data)
            await self._display_player_embed(server)

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
                await self._send_chat_message(server, self.EVENT_TEXTS[player.side]['disconnect'].format(player.name))
            finally:
                player.active = False
                await self._display_mission_embed(server)
                await self._display_player_embed(server)
        elif data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            player1 = server.get_player(id=data['arg1'])
            if data['arg3'] != -1:
                player2 = server.get_player(id=data['arg3'])
            else:
                player2 = None
            await self._send_chat_message(server, self.EVENT_TEXTS[player1.side][data['eventName']].format(
                'player ' + player1.name, ('player ' + player2.name) if player2 is not None else 'AI',
                data['arg2'] or 'Cannon'))
        elif data['eventName'] == 'self_kill':
            player = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            await self._send_chat_message(server, self.EVENT_TEXTS[player.side][data['eventName']].format(player.name))
        elif data['eventName'] == 'kill':
            # Player is not an AI
            player1 = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            player2 = server.get_player(id=data['arg4']) if data['arg4'] != -1 else None
            await self._send_chat_message(server, self.EVENT_TEXTS[Side(data['arg3'])][data['eventName']].format(
                ('player ' + player1.name) if player1 is not None else 'AI',
                data['arg2'] or 'SCENERY', Side(data['arg6']).name,
                ('player ' + player2.name) if player2 is not None else 'AI',
                data['arg5'] or 'SCENERY', data['arg7'] or 'Cannon'))
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
                if data['eventName'] in ['takeoff', 'landing']:
                    await self._send_chat_message(server, self.EVENT_TEXTS[player.side][data['eventName']].format(
                        player.name, data['arg3'] if len(data['arg3']) > 0 else 'ground'))
                else:
                    await self._send_chat_message(server,
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
            await server.current_mission.restart()
        elif data['subcommand'] == 'list' and player.has_discord_roles(['DCS Admin']):
            response = await server.sendtoDCSSync({"command": "listMissions"})
            missions = response['missionList']
            message = 'The following missions are available:\n'
            for i in range(0, len(missions)):
                mission = missions[i]
                mission = mission[(mission.rfind('\\') + 1):-4]
                message += f"{i+1} {mission}\n"
            message += "\nUse -load <number> to load that mission"
            player.sendUserMessage(message, 30)
        elif data['subcommand'] == 'load' and player.has_discord_roles(['DCS Admin']):
            await server.loadMission(data['params'][0])
