from __future__ import annotations
from core import utils, EventListener, PersistentReport, Plugin, Report, Status, Side, Mission, Player, Coalition, \
    Channel, DataObjectFactory
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


class MissionEventListener(EventListener):
    EVENT_TEXTS = {
        'takeoff': '{} player {} took off from {}.',
        'landing': '{} player {} landed at {}.',
        'eject': '{} player {} ejected.',
        'crash': '{} player {} crashed.',
        'pilot_death': '{} player {} died.',
        'kill': '{} {} in {} killed {} {} in {} with {}.',
        'friendly_fire': '**{} {} FRIENDLY FIRE onto {} with {}.**'
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    async def sendMessage(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if data['channel'] == -1:
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
            if data['channel'] == -1:
                channel = server.get_channel(Channel.CHAT)
            else:
                channel = self.bot.get_channel(int(data['channel']))
            if channel:
                await channel.send(embed=embed)

    async def displayMissionEmbed(self, server: Server):
        try:
            players = server.get_active_players()
            num_players = len(players) + 1
            report = PersistentReport(self.bot, self.plugin_name, 'serverStatus.json', server, 'mission_embed')
            return await report.render(server=server, num_players=num_players)
        except Exception as ex:
            self.log.exception(ex)

    # Display the list of active players
    async def displayPlayerEmbed(self, server: Server):
        if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
            report = PersistentReport(self.bot, self.plugin_name, 'players.json', server, 'players_embed')
            return await report.render(server=server, sides=[Coalition.BLUE, Coalition.RED])

    async def callback(self, data):
        server: Server = self.bot.servers[data['server_name']]
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            server.sendtoDCS(data)

    async def registerDCSServer(self, data):
        if not data['channel'].startswith('sync-'):
            return
        server: Server = self.bot.servers[data['server_name']]
        mission: Mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                   map=data['current_map'], name=data['current_mission'])
        mission.update(data)
        server.current_mission = mission
        if 'players' not in data:
            data['players'] = []
            server.status = Status.STOPPED
        for p in data['players']:
            player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=p['id'],
                                                     name=p['name'], active=p['active'], side=Side(p['side']),
                                                     ucid=p['ucid'], ipaddr=p['ipaddr'], slot=p['slot'],
                                                     sub_slot=p['sub_slot'], unit_callsign=p['unit_callsign'],
                                                     unit_name=p['unit_name'], unit_type=p['unit_type'],
                                                     group_id=p['group_id'], group_name=p['group_name'], banned=False)
            server.add_player(player)
        await self.displayMissionEmbed(server)
        await self.displayPlayerEmbed(server)

    async def onMissionLoadBegin(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.LOADING
        mission: Mission = DataObjectFactory().new(Mission.__name__, bot=self.bot, server=server,
                                                   map=data['current_map'], name=data['current_mission'])
        mission.update(data)
        server.current_mission = mission
        server.players = dict[int, Player]()
        # avoid race condition on server start
        if server.settings:
            await self.displayMissionEmbed(server)
        await self.displayPlayerEmbed(server)

    async def onMissionLoadEnd(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.current_mission.update(data)
        server.status = Status.PAUSED
        await self.displayMissionEmbed(server)

    async def onSimulationStop(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.STOPPED
        server.current_mission = None
        await self.displayMissionEmbed(server)

    async def onSimulationPause(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.PAUSED
        await self.displayMissionEmbed(server)

    async def onSimulationResume(self, data):
        server: Server = self.bot.servers[data['server_name']]
        server.status = Status.RUNNING
        await self.displayMissionEmbed(server)

    async def onPlayerConnect(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        # The admin player connects only on an initial start of DCS
        if data['id'] == 1:
            server.status = Status.LOADING
            return
        try:
            chat_channel = server.get_channel(Channel.CHAT)
            if chat_channel is not None:
                await chat_channel.send('{} connected to server'.format(data['name']))
        finally:
            player: Player = server.get_player(ucid=data['ucid'])
            if not player:
                player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=data['id'],
                                                         name=data['name'], active=data['active'], side=Side(data['side']),
                                                         ucid=data['ucid'], ipaddr=data['ipaddr'], banned=False)
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
            player: Player = DataObjectFactory().new(Player.__name__, bot=self.bot, server=server, id=data['id'],
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
        await self.displayMissionEmbed(server)
        await self.displayPlayerEmbed(server)

    async def onPlayerStop(self, data: dict) -> None:
        if data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        if player:
            player.active = False
        await self.displayMissionEmbed(server)
        await self.displayPlayerEmbed(server)

    async def onPlayerChangeSlot(self, data: dict) -> None:
        if 'side' not in data:
            return
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['id'])
        try:
            if Side(data['side']) != Side.SPECTATOR:
                if player is not None:
                    chat_channel = server.get_channel(Channel.CHAT)
                    if chat_channel is not None:
                        await chat_channel.send('{} player {} occupied {} {}'.format(
                            player.side.name if player.side != Side.SPECTATOR else Side.NEUTRAL.name, data['name'],
                            Side(data['side']).name, data['unit_type']))
            elif player is not None:
                chat_channel = server.get_channel(Channel.CHAT)
                if chat_channel is not None:
                    await chat_channel.send('{} player {} returned to Spectators'.format(
                        player.side.name, data['name']))
        finally:
            player.update(data)
            await self.displayPlayerEmbed(server)

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
                chat_channel = server.get_channel(Channel.CHAT)
                if chat_channel:
                    if player.side == Side.SPECTATOR:
                        await chat_channel.send('Player {} disconnected'.format(player.name))
                    else:
                        await chat_channel.send('{} player {} disconnected'.format(player.side.name, player.name))
            finally:
                player.active = False
                await self.displayMissionEmbed(server)
                await self.displayPlayerEmbed(server)
        elif data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            player1 = server.get_player(id=data['arg1'])
            if data['arg3'] != -1:
                player2 = server.get_player(id=data['arg3'])
            else:
                player2 = None
            chat_channel = server.get_channel(Channel.CHAT)
            if chat_channel is not None:
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    player1.side.name, 'player ' + player1.name,
                    ('player ' + player2.name) if player2 is not None else 'AI',
                    data['arg2'] if (len(data['arg2']) > 0) else 'Cannon'))
        elif data['eventName'] == 'kill':
            # Player is not an AI
            player1 = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            player2 = server.get_player(id=data['arg4']) if data['arg4'] != -1 else None
            chat_channel = server.get_channel(Channel.CHAT)
            if chat_channel is not None:
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    Side(data['arg3']).name,
                    ('player ' + player1.name) if player1 is not None else 'AI',
                    data['arg2'], Side(data['arg6']).name,
                    ('player ' + player2.name) if player2 is not None else 'AI',
                    data['arg5'], data['arg7']))
            # report teamkills from players to admins
            if (player1 is not None) and (data['arg1'] != data['arg4']) and (data['arg3'] == data['arg6']):
                await server.get_channel(Channel.ADMIN).send(
                    'Player {} (ucid={}) is killing team members. Please investigate.'.format(
                        player1.name, player1.ucid))
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            if data['arg1'] != -1:
                player = server.get_player(id=data['arg1'])
                chat_channel = server.get_channel(Channel.CHAT)
                if chat_channel is not None:
                    if data['eventName'] in ['takeoff', 'landing']:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            player.side.name, player.name,
                            data['arg3'] if len(data['arg3']) > 0 else 'ground'))
                    else:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            player.side.name, player.name))

    async def onChatCommand(self, data: dict) -> None:
        server: Server = self.bot.servers[data['server_name']]
        player: Player = server.get_player(id=data['from_id'])
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
            server.current_mission.restart()
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
            server.loadMission(data['params'][0])
