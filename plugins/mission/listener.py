import discord
import pandas as pd
import psycopg2
from core import const, utils, EventListener, PersistentReport, Plugin
from core.const import Status
from contextlib import closing


class MissionEventListener(EventListener):
    EVENT_TEXTS = {
        'takeoff': '{} player {} took off from {}.',
        'landing': '{} player {} landed at {}.',
        'eject': '{} player {} ejected.',
        'crash': '{} player {} crashed.',
        'pilot_death': '{} player {} died.',
        'kill': '{} {} in {} killed {} {} in {} with {}.',
        'friendly_fire': '{} {} FRIENDLY FIRE onto {} with {}.'
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
                                               'group_name', 'group_id'])
        new_df.set_index('id')
        if data['server_name'] not in self.bot.player_data:
            self.bot.player_data[server_name] = new_df
        else:
            df = self.bot.player_data[server_name]
            if len(df[df['id'] == data['id']]) == 1:
                if data['command'] == 'onPlayerChangeSlot':
                    df.loc[df['id'] == data['id'], ['active', 'side', 'slot', 'sub_slot', 'unit_callsign', 'unit_name',
                                                    'unit_type', 'group_name', 'group_id']] = [data['active'],
                                                                                               data['side'],
                                                                                               data['slot'],
                                                                                               data['sub_slot'],
                                                                                               data['unit_callsign'],
                                                                                               data['unit_name'],
                                                                                               data['unit_type'],
                                                                                               data['group_name'],
                                                                                               data['group_id']]
                elif data['command'] in ['onPlayerConnect', 'onPlayerStart']:
                    df.loc[df['id'] == data['id'], ['name', 'active', 'side', 'slot', 'sub_slot', 'ucid',
                                                    'unit_callsign', 'unit_name', 'unit_type', 'group_name',
                                                    'group_id']] = \
                        [data['name'], data['active'], data['side'], '', 0, data['ucid'], '', '', '', '', '']
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
        embed = discord.Embed(color=discord.Color.blue())
        if 'title' in data and len(data['title']) > 0:
            embed.title = data['title']
        if 'description' in data and len(data['description']) > 0:
            embed.description = data['description']
        if 'img' in data and len(data['img']) > 0:
            embed.set_image(url=data['img'])
        if 'footer' in data and len(data['footer']) > 0:
            embed.set_footer(text=data['footer'])
        if 'fields' in data:
            for name, value in data['fields'].items():
                embed.add_field(name=name, value=value)
        if 'id' in data and len(data['id']) > 0:
            return await self.bot.setEmbed(data, data['id'], embed)
        else:
            return await self.bot.get_bot_channel(data, 'chat_channel' if (data['channel'] == '-1') else None).send(
                embed=embed)

    async def displayMissionEmbed(self, data):
        server = self.globals[data['server_name']]
        players = self.bot.player_data[data['server_name']]
        num_players = len(players[players['active'] == True]) + 1
        report = PersistentReport(self.bot, self.plugin, 'serverStatus.json', server, 'mission_embed')
        return await report.render(server=server, num_players=num_players)

    # Display the list of active players
    async def displayPlayerEmbed(self, data):
        players = self.bot.player_data[data['server_name']]
        players = players[players['active'] == True]
        embed = discord.Embed(title='Active Players', color=discord.Color.blue())
        names = units = sides = '' if (len(players) > 0) else '_ _'
        for idx, player in players.iterrows():
            side = player['side']
            names += player['name'] + '\n'
            units += (player['unit_type'] if (side != 0) else '_ _') + '\n'
            sides += const.PLAYER_SIDES[side] + '\n'
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Unit', value=units)
        embed.add_field(name='Side', value=sides)
        await self.bot.setEmbed(data, 'players_embed', embed)

    async def callback(self, data):
        server = self.globals[data['server_name']]
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            self.bot.sendtoDCS(server, data)

    async def registerDCSServer(self, data):
        # check for protocol incompatibilities
        if data['hook_version'] != self.bot.version:
            self.log.error(
                'Server {} has wrong Hook version installed. Please update lua files and restart server. Registration '
                'ignored.'.format(
                    data['server_name']))
            return
        server = self.globals[data['server_name']]
        self.bot.player_data[data['server_name']] = pd.DataFrame(data['players'], columns=[
            'id', 'name', 'active', 'side', 'slot', 'sub_slot', 'ucid', 'unit_callsign', 'unit_name', 'unit_type',
            'group_id', 'group_name'])
        self.bot.player_data[data['server_name']].set_index('id')
        if 'sync' in data['channel']:
            await self.displayMissionEmbed(data)
            await self.displayPlayerEmbed(data)

    async def getMissionUpdate(self, data):
        server = self.globals[data['server_name']]
        if server['status'] not in [Status.RESTART_PENDING, Status.SHUTDOWN_PENDING]:
            server['status'] = Status.PAUSED if data['pause'] is True else Status.RUNNING
        server['mission_time'] = data['mission_time']
        server['real_time'] = data['real_time']
        await self.displayMissionEmbed(data)

    async def listMissions(self, data):
        return data

    async def getMissionDetails(self, data):
        return data

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
        if server['status'] != Status.SHUTDOWN:
            server['status'] = Status.STOPPED
        await self.displayMissionEmbed(data)

    async def onSimulationPause(self, data):
        server = self.globals[data['server_name']]
        if server['status'] not in [Status.RESTART_PENDING, Status.SHUTDOWN_PENDING]:
            server['status'] = Status.PAUSED
        await self.displayMissionEmbed(data)

    async def onSimulationResume(self, data):
        server = self.globals[data['server_name']]
        if server['status'] not in [Status.RESTART_PENDING, Status.SHUTDOWN_PENDING]:
            server['status'] = Status.RUNNING
        await self.displayMissionEmbed(data)

    async def onPlayerConnect(self, data):
        if data['id'] != 1:
            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
            if chat_channel is not None:
                await chat_channel.send('{} connected to server'.format(data['name']))
            self.updatePlayer(data)

    async def onPlayerStart(self, data):
        if data['id'] != 1:
            SQL_PLAYERS = 'INSERT INTO players (ucid, discord_id) VALUES (%s, %s) ON CONFLICT (ucid) DO UPDATE SET ' \
                          'discord_id = %s WHERE players.discord_id = -1 '
            SQL_PLAYER_NAME = 'UPDATE players SET name = %s, last_seen = NOW() WHERE ucid = %s'
            discord_user = utils.match_user(self, data)
            discord_id = discord_user.id if discord_user else -1
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute(SQL_PLAYERS, (data['ucid'], discord_id, discord_id))
                    cursor.execute(SQL_PLAYER_NAME, (data['name'], data['ucid']))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
            server = self.globals[data['server_name']]
            if discord_user is None:
                self.bot.sendtoDCS(server, {
                    "command": "sendChatMessage",
                    "message": self.bot.config['DCS']['GREETING_MESSAGE_UNKNOWN'].format(data['name']),
                    "to": data['id']
                })
                # only warn for unknown users if it is a non-public server
                if len(self.globals[data['server_name']]['serverSettings']['password']) > 0:
                    await self.bot.get_bot_channel(data, 'admin_channel').send(
                        'Player {} (ucid={}) can\'t be matched to a discord user.'.format(data['name'], data['ucid']))
            else:
                name = discord_user.nick if discord_user.nick else discord_user.name
                self.bot.sendtoDCS(server, {
                    "command": "sendChatMessage",
                    "message": self.bot.config['DCS']['GREETING_MESSAGE_MEMBERS'].format(name, data['server_name']),
                    "to": int(data['id'])
                })
            self.updatePlayer(data)
            await self.displayMissionEmbed(data)
            await self.displayPlayerEmbed(data)
        return None

    async def onPlayerStop(self, data):
        # ignore events that might have been sent in unclear bot states
        if data['server_name'] in self.bot.player_data:
            self.removePlayer(data)
            await self.displayMissionEmbed(data)
            await self.displayPlayerEmbed(data)

    async def onPlayerChangeSlot(self, data):
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

    async def onGameEvent(self, data):
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
            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
            if chat_channel is not None:
                player1 = utils.get_player(self, server_name, id=data['arg1']) if data['arg1'] != -1 else None
                player2 = utils.get_player(self, server_name, id=data['arg4']) if data['arg4'] != -1 else None
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    const.PLAYER_SIDES[data['arg3']],
                    ('player ' + player1['name']) if player1 is not None else 'AI',
                    data['arg2'], const.PLAYER_SIDES[data['arg6']],
                    ('player ' + player2['name']) if player2 is not None else 'AI',
                    data['arg5'], data['arg7']))
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            if data['arg1'] != -1:
                player = utils.get_player(self, server_name, id=data['arg1'])
                chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                if chat_channel is not None:
                    if data['eventName'] in ['takeoff', 'landing']:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            const.PLAYER_SIDES[player['side']], player['name'], data['arg3']))
                    else:
                        await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                            const.PLAYER_SIDES[player['side']], player['name']))
        else:
            self.log.debug(f"MissionEventListener: Unhandled event: {data['eventName']}")

    async def listMizFiles(self, data):
        return data

    async def getWeatherInfo(self, data):
        return data

    async def rename(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE missions SET server_name = %s WHERE server_name = %s',
                               (data['newname'], data['server_name']))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
