# listener.py
import discord
import pandas as pd
import psycopg2
import re
import sched
from core import const, utils, EventListener, PersistentReport, Plugin
from core.const import Status
from contextlib import closing
from datetime import timedelta, datetime


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
        # TODO: change this to globals
        self.bot.player_data = {}

    # Return a player from the internal list
    # TODO: change player data handling!
    def get_player(self, server_name, id):
        df = self.bot.player_data[server_name]
        row = df[df['id'] == id]
        if not row.empty:
            return df[df['id'] == id].to_dict('records')[0]
        else:
            return None

    # Add or update a player from the internal list.
    def updatePlayer(self, data):
        # Player 1 is always inactive
        if data['id'] == 1:
            data['active'] = False
        new_df = pd.DataFrame([data], columns=['id', 'name', 'active', 'side', 'slot',
                                               'sub_slot', 'ucid', 'unit_callsign', 'unit_name', 'unit_type',
                                               'group_name', 'group_id'])
        new_df.set_index('id')
        if data['server_name'] not in self.bot.player_data:
            self.bot.player_data[data['server_name']] = new_df
        else:
            df = self.bot.player_data[data['server_name']]
            if len(df[df['id'] == data['id']]) == 1:
                if data['command'] == 'onPlayerChangeSlot':
                    df.loc[df['id'] == data['id'], ['active', 'side', 'slot', 'sub_slot', 'unit_callsign', 'unit_name',
                                                    'unit_type', 'group_name']] = [data['active'], data['side'],
                                                                                   data['slot'], data['sub_slot'],
                                                                                   data['unit_callsign'], data['unit_name'],
                                                                                   data['unit_type'], data['group_name'],
                                                                                   data['group_id']]
                elif data['command'] in ['onPlayerConnect', 'onPlayerStart']:
                    df.loc[df['id'] == data['id'], ['name', 'active', 'side', 'slot', 'sub_slot', 'ucid',
                                                    'unit_callsign', 'unit_name', 'unit_type', 'group_name', 'group_id']] = \
                        [data['name'], data['active'], data['side'], '', 0, data['ucid'], '', '', '', '', '']
            else:
                df = df.append(new_df)
            self.bot.player_data[data['server_name']] = df

    # We don't remove players for now, we just invalidate them
    # due to the fact that DCS still sends kill events or changeSlot events
    # when the user has already left the server.
    def removePlayer(self, data):
        df = self.bot.player_data[data['server_name']]
        if data['command'] == 'onPlayerStop':
            id = data['id']
        elif data['command'] == 'onGameEvent':
            id = data['arg1']
        else:
            self.bot.log.warning('removePlayer() received unknown event: ' + str(data))
            return
        df.loc[df['id'] == id, 'active'] = False
        self.bot.player_data[data['server_name']] = df

    def do_scheduled_restart(self, server, method, restart_in_seconds=0):
        self.log.debug('Scheduling restart for server {} in {} seconds.'.format(
            server['server_name'], restart_in_seconds))
        installation = server['installation']
        restart_warn_times = [int(x) for x in self.config[installation]['RESTART_WARN_TIMES'].split(
            ',')] if 'RESTART_WARN_TIMES' in self.config[installation] else []
        if len(restart_warn_times) > 0:
            if restart_in_seconds < max(restart_warn_times):
                restart_in_seconds = max(restart_warn_times)
        s = sched.scheduler()
        for warn_time in restart_warn_times:
            s.enter(restart_in_seconds - warn_time, 1, self.bot.sendtoDCS, kwargs={
                'server': server,
                'message': {
                    'command': 'sendPopupMessage',
                    'message': self.config[installation]['RESTART_WARN_TEXT'].format(warn_time),
                    'to': 'all'
                }
            })
        if method == 'restart':
            if 'RESTART_OPTIONS' in self.config[installation] and 'RESTART_SERVER' in [x.upper().strip() for x in self.config[installation]['RESTART_OPTIONS'].split(',')]:
                s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                    'server': server,
                    'message': {
                        "command": "shutdown"
                    }
                })
                s.enter(restart_in_seconds + 10, 1, utils.start_dcs, (self, installation))
            else:
                s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                    'server': server,
                    'message': {
                        "command": "restartMission"
                    }
                })
        elif method == 'rotate':
            s.enter(restart_in_seconds, 1, self.bot.sendtoDCS, kwargs={
                'server': server,
                'message': {"command": "startNextMission"}
            })
        server['restartScheduler'] = s
        self.loop.run_in_executor(self.bot.executor, s.run)

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
            return await self.bot.get_bot_channel(data, 'chat_channel' if (data['channel'] == '-1') else None).send(embed=embed)

    # Display the list of active players
    async def displayPlayerList(self, data):
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
        channel = self.bot.get_channel(int(self.globals[data['server_name']]['status_channel']))
        # name changes of the status channel will only happen with the correct permission
        if channel.permissions_for(self.bot.guilds[0].get_member(self.bot.user.id)).manage_channels:
            name = channel.name
            # if the server owner leaves, the server is shut down
            if ('id' in data) and (data['id'] == 1):
                if name.find('［') == -1:
                    name = name + '［-］'
                else:
                    name = re.sub('［.*］', f'［-］', name)
            else:
                current = len(players) + 1
                max_players = self.globals[data['server_name']]['serverSettings']['maxPlayers']
                if name.find('［') == -1:
                    name = name + f'［{current}／{max_players}］'
                else:
                    name = re.sub('［.*］', f'［{current}／{max_players}］', name)
            await channel.edit(name=name)

    def updateMission(self, data):
        server = self.globals[data['server_name']]
        self.bot.sendtoDCS(server, {"command": "getRunningMission", "channel": data['channel']})

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
        server['airbases'] = data['airbases']
        server['restart_pending'] = False
        self.bot.sendtoDCS(server, {"command": "getRunningMission", "channel": server['status_channel']})

    async def getRunningMission(self, data):
        if data['channel'].startswith('sync'):
            return data
        server_name = data['server_name']
        server = self.globals[server_name]
        if 'pause' in data:
            server['status'] = Status.PAUSED if data['pause'] is True else Status.RUNNING
        # There are very rare conditions where the installation is not set yet (onMissionLoad() being called before
        # onPlayerStart(id=1))
        if 'installation' in server:
            installation = server['installation']
            # check if we have to restart the mission
            if 'restartScheduler' not in server and not server['restart_pending']:
                # check if a restart should be executed relative to mission start
                if ('RESTART_MISSION_TIME' in self.config[installation]) and (
                        data['mission_time'] > int(self.config[installation]['RESTART_MISSION_TIME']) * 60):
                    # TODO: change player data handling
                    if server_name in self.bot.player_data:
                        if 'RESTART_OPTIONS' in self.config[installation]:
                            options = [x.upper().strip() for x in self.config[installation]['RESTART_OPTIONS'].split(',')]
                            players = self.bot.player_data[server_name]
                            if 'NOT_POPULATED' in options and len(players[players['active'] == True]) > 0:
                                self.log.debug(f"Scheduled restart of server \"{server_name}\""
                                               f" postponed due to server population.")
                                server['restart_pending'] = True
                        if not server['restart_pending']:
                            self.do_scheduled_restart(server, self.config[installation]['RESTART_METHOD'])
                elif 'RESTART_LOCAL_TIMES' in self.config[installation]:
                    now = datetime.now()
                    times = []
                    for t in self.config[installation]['RESTART_LOCAL_TIMES'].split(','):
                        d = datetime.strptime(t.strip(), '%H:%M')
                        check = now.replace(hour=d.hour, minute=d.minute)
                        if check.time() > now.time():
                            times.insert(0, check)
                            break
                        else:
                            times.append(check + timedelta(days=1))
                    if len(times):
                        self.do_scheduled_restart(
                            server, self.config[installation]['RESTART_METHOD'], int((times[0] - now).total_seconds()))
                    else:
                        self.log.warning(
                            f'Configuration mismatch! RESTART_LOCAL_TIMES not set correctly for server {server_name}.')
        report = PersistentReport(self.bot, self.plugin, 'serverStatus.json', data, 'mission_embed')
        return await report.render(server=server, mission=data)

    async def getCurrentPlayers(self, data):
        if data['server_name'] not in self.bot.player_data:
            self.bot.player_data[data['server_name']] = pd.DataFrame(data['players'], columns=[
                'id', 'name', 'active', 'side', 'slot', 'sub_slot', 'ucid', 'unit_callsign', 'unit_name', 'unit_type',
                'group_id', 'group_name'])
            self.bot.player_data[data['server_name']].set_index('id')
        await self.displayPlayerList(data)

    async def listMissions(self, data):
        return data

    async def getMissionDetails(self, data):
        return data

    async def onMissionLoadBegin(self, data):
        self.globals[data['server_name']]['status'] = Status.LOADING
        self.bot.player_data[data['server_name']] = pd.DataFrame(
            columns=['id', 'name', 'active', 'side', 'slot', 'sub_slot', 'ucid', 'unit_callsign', 'unit_name',
                     'unit_type', 'group_name'])
        await self.getRunningMission(data)
        await self.displayPlayerList(data)

    async def onMissionLoadEnd(self, data):
        server = self.globals[data['server_name']]
        server['status'] = Status.PAUSED
        server['airbases'] = data['airbases']
        return await self.getRunningMission(data)

    async def onSimulationStop(self, data):
        data['num_players'] = 0
        data['current_map'] = '-'
        data['mission_time'] = 0
        server = self.globals[data['server_name']]
        if server['status'] != Status.SHUTDOWN:
            server['status'] = Status.STOPPED
        await self.getRunningMission(data)
        # stop all restart events
        if 'restartScheduler' in server:
            s = server['restartScheduler']
            # Don't pull the one and only restart event from the queue, as we want it to be executed.
            if len(s.queue) > 0 and s.queue[0].action != utils.start_dcs:
                for event in s.queue:
                    s.cancel(event)
            del server['restartScheduler']

    async def onSimulationPause(self, data):
        self.globals[data['server_name']]['status'] = Status.PAUSED
        self.updateMission(data)

    async def onSimulationResume(self, data):
        self.globals[data['server_name']]['status'] = Status.RUNNING
        self.updateMission(data)

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
                    "to": data['id']
                })
            self.updatePlayer(data)
            self.updateMission(data)
            await self.displayPlayerList(data)
        return None

    async def onPlayerStop(self, data):
        # ignore events that might have been sent in unclear bot states
        if data['server_name'] in self.bot.player_data:
            self.removePlayer(data)
            await self.displayPlayerList(data)

    async def onPlayerChangeSlot(self, data):
        if 'side' in data:
            player = self.get_player(data['server_name'], data['id'])
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
            await self.displayPlayerList(data)
        return None

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
                player = self.get_player(server_name, data['arg1'])
                chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
                if chat_channel:
                    if ('side' not in player) or (player['side'] == const.SIDE_SPECTATOR):
                        await chat_channel.send('Player {} disconnected'.format(player['name']))
                    else:
                        await chat_channel.send('{} player {} disconnected'.format(
                            const.PLAYER_SIDES[player['side']], player['name']))
                self.removePlayer(data)
                self.updateMission(data)
                # if no player is in the server anymore and we have a pending restart, restart the server
                players = self.bot.player_data[data['server_name']]
                if len(players[players['active'] == True]) == 0 and self.globals[server_name]['restart_pending']:
                    server = self.globals[server_name]
                    self.bot.sendtoDCS(server, {"command": "restartMission", "channel": "-1"})
                await self.displayPlayerList(data)
        elif data['eventName'] == 'friendly_fire':
            player1 = self.get_player(server_name, data['arg1'])
            if data['arg3'] != -1:
                player2 = self.get_player(server_name, data['arg3'])
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
            player1 = self.get_player(server_name, data['arg1']) if data['arg1'] != -1 else None
            player2 = self.get_player(server_name, data['arg4']) if data['arg4'] != -1 else None

            chat_channel = self.bot.get_bot_channel(data, 'chat_channel')
            if chat_channel is not None:
                await chat_channel.send(self.EVENT_TEXTS[data['eventName']].format(
                    const.PLAYER_SIDES[data['arg3']],
                    ('player ' + player1['name']) if player1 is not None else 'AI',
                    data['arg2'], const.PLAYER_SIDES[data['arg6']],
                    ('player ' + player2['name']) if player2 is not None else 'AI',
                    data['arg5'], data['arg7']))
            # report teamkills from unknown players to admins
            # TODO: move that to the punishment plugin
            if (player1 is not None) and (data['arg3'] == data['arg6']) and (data['arg1'] != data['arg4']):
                discord_user = utils.match_user(self, player1)
                if discord_user is None:
                    await self.bot.get_bot_channel(data, 'admin_channel').send(
                        'Unknown player {} (ucid={}) is killing team members. Please investigate.'.format(
                            player1['name'], player1['ucid']))
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            if data['arg1'] != -1:
                player = self.get_player(server_name, data['arg1'])
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
        return None

    async def listMizFiles(self, data):
        return data

    async def getWeatherInfo(self, data):
        return data
