import pandas as pd
from core import const, utils, EventListener, PersistentReport, Plugin, Report
from core.const import Status


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
        self.bot.player_data = {}

    # Add or update a player from the internal list.
    def updatePlayer(self, data):
        server_name = data['server_name']
        # Player 1 is always inactive
        if data['id'] == 1:
            data['active'] = False
        new_df = pd.DataFrame([data], columns=['id', 'name', 'active', 'side', 'slot',
                                               'sub_slot', 'ucid', 'unit_callsign', 'unit_name', 'unit_type',
                                               'group_name', 'group_id', 'ipaddr'])
        new_df.set_index('id')
        if data['server_name'] not in self.bot.player_data:
            self.bot.player_data[server_name] = new_df
        else:
            df = self.bot.player_data[server_name]
            if len(df[df['id'] == data['id']]) == 1:
                if data['command'] == 'onPlayerChangeSlot':
                    df.loc[df['id'] == data['id'], ['active', 'side', 'slot', 'sub_slot', 'unit_callsign', 'unit_name',
                                                    'unit_type', 'group_name', 'group_id']] = \
                        [data['active'], data['side'], data['slot'], data['sub_slot'], data['unit_callsign'],
                         data['unit_name'], data['unit_type'], data['group_name'], data['group_id']]
                elif data['command'] in ['onPlayerConnect', 'onPlayerStart']:
                    df.loc[df['id'] == data['id'], ['name', 'active', 'side', 'slot', 'sub_slot', 'ucid',
                                                    'unit_callsign', 'unit_name', 'unit_type', 'group_name',
                                                    'group_id', 'ipaddr']] = \
                        [data['name'], data['active'], data['side'], '', 0, data['ucid'], '', '', '', '', '', data['ipaddr']]
            else:
                df = pd.concat([df, new_df])
            self.bot.player_data[server_name] = df

    # We don't remove players, we just invalidate them
    # due to the fact that DCS still sends kill events or changeSlot events
    # when the user has already left the server.
    def removePlayer(self, data):
        df = self.bot.player_data[data['server_name']]
        if data['command'] == 'onPlayerStop':
            player_id = data['id']
        elif data['command'] == 'onGameEvent':
            player_id = data['arg1']
        else:
            self.bot.log.warning('removePlayer() received unknown event: ' + str(data))
            return
        df.loc[df['id'] == player_id, 'active'] = False
        self.bot.player_data[data['server_name']] = df

    async def sendMessage(self, data):
        channel = self.bot.get_bot_channel(data, 'chat_channel' if (data['channel'] == '-1') else None)
        if channel:
            await channel.send(data['message'])

    async def sendEmbed(self, data):
        embed = utils.format_embed(data)
        if 'id' in data and len(data['id']) > 0:
            return await self.bot.setEmbed(data, data['id'], embed)
        else:
            return await self.bot.get_bot_channel(data, 'chat_channel' if (data['channel'] == '-1') else None).send(
                embed=embed)

    async def displayMissionEmbed(self, data):
        server = self.globals[data['server_name']]
        players = self.bot.player_data[data['server_name']]
        num_players = len(players[players['active'] == True]) + 1
        report = PersistentReport(self.bot, self.plugin_name, 'serverStatus.json', server, 'mission_embed')
        return await report.render(server=server, num_players=num_players)

    # Display the list of active players
    async def displayPlayerEmbed(self, data):
        server = self.globals[data['server_name']]
        if not self.config.getboolean(server['installation'], 'COALITIONS'):
            report = PersistentReport(self.bot, self.plugin_name, 'players.json', server, 'players_embed')
            return await report.render(server=server, sides=['Blue', 'Red'])

    async def callback(self, data):
        server = self.globals[data['server_name']]
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            self.bot.sendtoDCS(server, data)

    async def registerDCSServer(self, data):
        if 'players' not in data:
            data['players'] = []
            self.globals[data['server_name']]['status'] = Status.STOPPED
        self.bot.player_data[data['server_name']] = pd.DataFrame(data['players'], columns=[
            'id', 'name', 'active', 'side', 'slot', 'sub_slot', 'ucid', 'unit_callsign', 'unit_name', 'unit_type',
            'group_id', 'group_name'])
        self.bot.player_data[data['server_name']].set_index('id')
        if data['channel'].startswith('sync'):
            await self.displayMissionEmbed(data)
            await self.displayPlayerEmbed(data)

    async def onMissionLoadBegin(self, data):
        server = self.globals[data['server_name']]
        server['status'] = Status.LOADING
        # LotATC is initialized correctly on mission load
        if 'lotAtcSettings' in data:
            server['lotAtcSettings'] = data['lotAtcSettings']
        self.bot.player_data[data['server_name']] = pd.DataFrame(
            columns=['id', 'name', 'active', 'side', 'slot', 'sub_slot', 'ucid', 'unit_callsign', 'unit_name',
                     'unit_type', 'group_name'])
        await self.displayMissionEmbed(data)
        await self.displayPlayerEmbed(data)

    async def onMissionLoadEnd(self, data):
        self.globals[data['server_name']] = self.globals[data['server_name']] | data
        server = self.globals[data['server_name']]
        server['status'] = Status.PAUSED
        await self.displayMissionEmbed(data)

    async def onSimulationStop(self, data):
        data['current_map'] = '-'
        data['mission_time'] = 0
        server = self.globals[data['server_name']]
        server['status'] = Status.STOPPED
        await self.displayMissionEmbed(data)

    async def onSimulationPause(self, data):
        server = self.globals[data['server_name']]
        server['status'] = Status.PAUSED
        await self.displayMissionEmbed(data)

    async def onSimulationResume(self, data):
        server = self.globals[data['server_name']]
        server['status'] = Status.RUNNING
        await self.displayMissionEmbed(data)

    async def onPlayerConnect(self, data: dict) -> None:
        # The admin player connects only on an initial start of DCS
        if data['id'] == 1:
            self.globals[data['server_name']]['status'] = Status.LOADING
            return
        chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
        if chat_channel is not None:
            await chat_channel.send('{} connected to server'.format(data['name']))
        self.updatePlayer(data)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        self.updatePlayer(data)
        await self.displayMissionEmbed(data)
        await self.displayPlayerEmbed(data)

    async def onPlayerStop(self, data: dict) -> None:
        # ignore events that might have been sent in unclear bot states
        if data['server_name'] in self.bot.player_data:
            self.removePlayer(data)
            await self.displayMissionEmbed(data)
            await self.displayPlayerEmbed(data)

    async def onPlayerChangeSlot(self, data: dict) -> None:
        if 'side' in data:
            player = utils.get_player(self, data['server_name'], id=data['id'])
            if data['side'] != const.SIDE_SPECTATOR:
                if player is not None:
                    chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                    if chat_channel is not None:
                        await chat_channel.send('{} player {} occupied {} {}'.format(
                            const.PLAYER_SIDES[player['side'] if player['side'] != 0 else 3], data['name'],
                            const.PLAYER_SIDES[data['side']], data['unit_type']))
            elif player is not None:
                chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                if chat_channel is not None:
                    await chat_channel.send('{} player {} returned to Spectators'.format(
                        const.PLAYER_SIDES[player['side']], data['name']))
            self.updatePlayer(data)
            await self.displayPlayerEmbed(data)

    async def onGameEvent(self, data: dict) -> None:
        server_name = data['server_name']
        # ignore game events until the server is not initialized correctly
        if server_name not in self.bot.player_data:
            pass
        if data['eventName'] == 'mission_end':
            pass
        elif data['eventName'] in ['connect', 'change_slot']:  # these events are handled differently
            return None
        elif data['eventName'] == 'disconnect':
            if data['arg1'] != 1:
                player = utils.get_player(self, server_name, id=data['arg1'])
                chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                if chat_channel:
                    if ('side' not in player) or (player['side'] == const.SIDE_SPECTATOR):
                        await chat_channel.send('Player {} disconnected'.format(player['name']))
                    else:
                        await chat_channel.send('{} player {} disconnected'.format(
                            const.PLAYER_SIDES[player['side']], player['name']))
                self.removePlayer(data)
                await self.displayMissionEmbed(data)
                await self.displayPlayerEmbed(data)
        elif data['eventName'] == 'friendly_fire':
            player1 = utils.get_player(self, server_name, id=data['arg1'])
            if data['arg3'] != -1:
                player2 = utils.get_player(self, server_name, id=data['arg3'])
            else:
                player2 = None
            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
            if chat_channel is not None:
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    const.PLAYER_SIDES[player1['side']], 'player ' + player1['name'],
                    ('player ' + player2['name']) if player2 is not None else 'AI',
                    data['arg2'] if (len(data['arg2']) > 0) else 'Cannon'))
        elif data['eventName'] == 'kill':
            # Player is not an AI
            player1 = utils.get_player(self, server_name, id=data['arg1']) if data['arg1'] != -1 else None
            player2 = utils.get_player(self, server_name, id=data['arg4']) if data['arg4'] != -1 else None
            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
            if chat_channel is not None:
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    const.PLAYER_SIDES[data['arg3']],
                    ('player ' + player1['name']) if player1 is not None else 'AI',
                    data['arg2'], const.PLAYER_SIDES[data['arg6']],
                    ('player ' + player2['name']) if player2 is not None else 'AI',
                    data['arg5'], data['arg7']))
            # report teamkills from players to admins
            if (player1 is not None) and (data['arg1'] != data['arg4']) and (data['arg3'] == data['arg6']):
                await self.bot.get_bot_channel(data, 'admin_channel').send(
                    'Player {} (ucid={}) is killing team members. Please investigate.'.format(
                        player1['name'], player1['ucid']))
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            if data['arg1'] != -1:
                player = utils.get_player(self, server_name, id=data['arg1'])
                chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                if chat_channel is not None:
                    if data['eventName'] in ['takeoff', 'landing']:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            const.PLAYER_SIDES[player['side']], player['name'],
                            data['arg3'] if len(data['arg3']) > 0 else 'ground'))
                    else:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            const.PLAYER_SIDES[player['side']], player['name']))

    async def onChatCommand(self, data: dict) -> None:
        server = self.globals[data['server_name']]
        if data['subcommand'] == 'atis':
            if len(data['params']) == 0:
                utils.sendChatMessage(self, data['server_name'], data['from_id'], f"Usage: -atis <airbase/code>")
                return
            name = data['params'][0]
            for airbase in server['airbases']:
                if (name.casefold() in airbase['name'].casefold()) or (name.upper() == airbase['code']):
                    response = await self.bot.sendtoDCSSync(server, {
                        "command": "getWeatherInfo",
                        "lat": airbase['lat'],
                        "lng": airbase['lng'],
                        "alt": airbase['alt']
                    })
                    report = Report(self.bot, self.plugin_name, 'atis-ingame.json')
                    env = await report.render(airbase=airbase, data=response)
                    player = utils.get_player(self, server['server_name'], id=data['from_id'])
                    message = utils.embed_to_simpletext(env.embed)
                    utils.sendUserMessage(self, server, data['from_id'], message, 30)
                    return
            utils.sendChatMessage(self, data['server_name'], data['from_id'], f"No ATIS information found for {name}.")
