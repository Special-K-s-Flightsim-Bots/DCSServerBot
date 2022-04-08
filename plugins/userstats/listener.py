# listener.py
import psycopg2
from contextlib import closing, suppress
from core import const, EventListener, Plugin, utils
from typing import Union, Any


class UserStatisticsEventListener(EventListener):

    SQL_EVENT_UPDATES = {
        'takeoff': 'UPDATE statistics SET takeoffs = takeoffs + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'landing': 'UPDATE statistics SET landings = landings + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'eject': 'UPDATE statistics SET ejections = ejections + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'crash': 'UPDATE statistics SET crashes = crashes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'pilot_death': 'UPDATE statistics SET deaths = deaths + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'pvp_planes': 'UPDATE statistics SET kills = kills + 1, pvp = pvp + 1, kills_planes = kills_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'pvp_helicopters': 'UPDATE statistics SET kills = kills + 1, pvp = pvp + 1, kills_helicopters = kills_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'teamkill': 'UPDATE statistics SET teamkills = teamkills + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_planes': 'UPDATE statistics SET kills = kills + 1, kills_planes = kills_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_helicopters': 'UPDATE statistics SET kills = kills + 1, kills_helicopters = kills_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_ships': 'UPDATE statistics SET kills = kills + 1, kills_ships = kills_ships + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_sams': 'UPDATE statistics SET kills = kills + 1, kills_sams = kills_sams + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'kill_ground': 'UPDATE statistics SET kills = kills + 1, kills_ground = kills_ground + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_pvp_planes': 'UPDATE statistics SET deaths_pvp = deaths_pvp + 1, deaths_planes = deaths_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_pvp_helicopters': 'UPDATE statistics SET deaths_pvp = deaths_pvp + 1, deaths_helicopters = deaths_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_planes': 'UPDATE statistics SET deaths_planes = deaths_planes + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_helicopters': 'UPDATE statistics SET deaths_helicopters = deaths_helicopters + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_ships': 'UPDATE statistics SET deaths_ships = deaths_ships + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_sams': 'UPDATE statistics SET deaths_sams = deaths_sams + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'deaths_ground': 'UPDATE statistics SET deaths_ground = deaths_ground + 1 WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL'
    }

    SQL_MISSION_HANDLING = {
        'start_mission': 'INSERT INTO missions (server_name, mission_name, mission_theatre) VALUES (%s, %s, %s)',
        'current_mission_id': 'SELECT id, mission_name FROM missions WHERE server_name = %s AND mission_end IS NULL',
        'close_statistics': 'UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND hop_off IS NULL',
        'close_all_statistics': 'UPDATE statistics SET hop_off = NOW() WHERE mission_id IN (SELECT id FROM missions '
                                'WHERE server_name = %s AND mission_end IS NULL) AND hop_off IS NULL',
        'close_mission': 'UPDATE missions SET mission_end = NOW() WHERE id = %s',
        'close_all_missions': 'UPDATE missions SET mission_end = NOW() WHERE server_name = %s AND mission_end IS NULL',
        'check_player': 'SELECT slot FROM statistics WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'start_player': 'INSERT INTO statistics (mission_id, player_ucid, slot) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
        'stop_player': 'UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'all_players': 'SELECT player_ucid FROM statistics WHERE mission_id = %s AND hop_off IS NULL'
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.statistics = set()

    async def processEvent(self, data: dict[str, Union[str, int]]) -> Any:
        if (data['command'] == 'registerDCSServer') or \
                (data['server_name'] in self.statistics and data['command'] in self.registeredEvents()):
            return await super().processEvent(data)
        else:
            return None

    @staticmethod
    def get_unit_type(data: dict) -> str:
        unit_type = data['unit_type']
        if int(data['sub_slot']) not in [-1, 0]:
            unit_type += ' (Crew)'
        return unit_type

    async def registerDCSServer(self, data):
        if data['statistics']:
            server_name = data['server_name']
            self.statistics.add(server_name)
            # registering a running instance
            if data['channel'].startswith('sync-'):
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        mission_id = -1
                        cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server_name,))
                        if cursor.rowcount == 1:
                            row = cursor.fetchone()
                            if row[1] == data['current_mission']:
                                mission_id = row[0]
                            else:
                                self.log.warning('The mission in the database does not match the mission that is live '
                                                 'on this server. Fixing...')
                        if mission_id == -1:
                            # close ambiguous missions
                            if cursor.rowcount >= 1:
                                cursor.execute(self.SQL_MISSION_HANDLING['close_all_statistics'], (server_name,))
                                cursor.execute(self.SQL_MISSION_HANDLING['close_all_missions'], (server_name,))
                            # create a new mission
                            cursor.execute(self.SQL_MISSION_HANDLING['start_mission'], (server_name,
                                                                                        data['current_mission'],
                                                                                        data['current_map']))
                            cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server_name,))
                            if cursor.rowcount == 1:
                                mission_id = cursor.fetchone()[0]
                            else:
                                self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                                               'gathered for this session.')
                        self.globals[server_name]['mission_id'] = mission_id
                        if mission_id != -1:
                            # initialize active players
                            players = self.bot.player_data[data['server_name']]
                            players = players[players['active'] == True]
                            ucids = []
                            for _, player in players.iterrows():
                                ucids.append(player['ucid'])
                                # make sure we get slot changes that might have occurred in the meantime
                                cursor.execute(self.SQL_MISSION_HANDLING['check_player'], (mission_id, player['ucid']))
                                player_started = False
                                if cursor.rowcount == 1:
                                    # the player is there already ...
                                    if cursor.fetchone()[0] != player['unit_type']:
                                        # ... but with a different aircraft, so close the old session
                                        cursor.execute(self.SQL_MISSION_HANDLING['stop_player'],
                                                       (mission_id, player['ucid']))
                                    else:
                                        # session will be kept
                                        player_started = True
                                if not player_started and player['side'] != const.SIDE_SPECTATOR:
                                    cursor.execute(self.SQL_MISSION_HANDLING['start_player'],
                                                   (mission_id, player['ucid'], self.get_unit_type(player)))
                            # close dead entries in the database (if existent)
                            cursor.execute(self.SQL_MISSION_HANDLING['all_players'], (mission_id, ))
                            for row in cursor.fetchall():
                                if row[0] not in ucids:
                                    cursor.execute(self.SQL_MISSION_HANDLING['stop_player'], (mission_id, row[0]))
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    conn.rollback()
                    self.log.exception(error)
                finally:
                    self.pool.putconn(conn)

    async def onMissionLoadEnd(self, data):
        conn = self.pool.getconn()
        try:
            server_name = data['server_name']
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['close_all_statistics'], (server_name,))
                cursor.execute(self.SQL_MISSION_HANDLING['close_all_missions'], (server_name,))
                cursor.execute(self.SQL_MISSION_HANDLING['start_mission'], (server_name,
                                                                            data['current_mission'],
                                                                            data['current_map']))
                cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server_name,))
                if cursor.rowcount == 1:
                    self.globals[server_name]['mission_id'] = cursor.fetchone()[0]
                else:
                    self.globals[server_name]['mission_id'] = -1
                    self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                                   'gathered for this session.')
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    async def onSimulationStop(self, data):
        conn = self.pool.getconn()
        try:
            mission_id = self.globals[data['server_name']]['mission_id']
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['close_statistics'], (mission_id,))
                cursor.execute(self.SQL_MISSION_HANDLING['close_mission'], (mission_id,))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    async def onPlayerStart(self, data: dict) -> None:
        if data['id'] == 1:
            return
        if self.config.getboolean('BOT', 'AUTOMATCH'):
            # if automatch is enabled, try to match the user
            discord_user = utils.match_user(self, data)
        else:
            # else only true matches will return a member
            discord_user = utils.get_member_by_ucid(self, data['ucid'])
        discord_id = discord_user.id if discord_user else -1
        # update the database
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if discord_id != -1:
                    cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (discord_id, ))
                    # if this is a new match
                    if cursor.rowcount == 0:
                        await self.bot.audit(
                            f"Member \"{discord_user.display_name}\" auto-linked to DCS user \"{data['name']}\" (ucid={data['ucid']}).")
                cursor.execute('INSERT INTO players (ucid, discord_id, name, ipaddr, last_seen) VALUES (%s, %s, %s, '
                               '%s, NOW()) ON CONFLICT (ucid) DO UPDATE SET discord_id = EXCLUDED.discord_id, '
                               'name = EXCLUDED.name, ipaddr = EXCLUDED.ipaddr, last_seen = NOW()',
                               (data['ucid'], discord_id, data['name'], data['ipaddr']))
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
                "message": self.bot.config['DCS']['GREETING_MESSAGE_UNMATCHED'].format(
                    name=data['name'], prefix=self.config['BOT']['COMMAND_PREFIX']),
                "to": data['id']
            })
            # only warn for unknown users if it is a non-public server and automatch is on
            if self.config.getboolean('BOT', 'AUTOMATCH') and \
                    len(self.globals[data['server_name']]['serverSettings']['password']) > 0:
                await self.bot.get_bot_channel(data, 'admin_channel').send(
                    'Player {} (ucid={}) can\'t be matched to a discord user.'.format(data['name'], data['ucid']))
        else:
            name = discord_user.nick if discord_user.nick else discord_user.name
            self.bot.sendtoDCS(server, {
                "command": "sendChatMessage",
                "message": self.bot.config['DCS']['GREETING_MESSAGE_MEMBERS'].format(name, data['server_name']),
                "to": int(data['id'])
            })

    async def onPlayerChangeSlot(self, data):
        if 'side' in data:
            conn = self.pool.getconn()
            try:
                mission_id = self.globals[data['server_name']]['mission_id']
                with closing(conn.cursor()) as cursor:
                    cursor.execute(self.SQL_MISSION_HANDLING['stop_player'], (mission_id, data['ucid']))
                    if data['side'] != const.SIDE_SPECTATOR:
                        cursor.execute(self.SQL_MISSION_HANDLING['start_player'], (mission_id, data['ucid'],
                                                                                   self.get_unit_type(data)))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)

    async def disableUserStats(self, data):
        self.statistics.discard(data['server_name'])
        conn = self.pool.getconn()
        try:
            mission_id = self.globals[data['server_name']]['mission_id']
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['close_statistics'], (mission_id,))
                cursor.execute(self.SQL_MISSION_HANDLING['close_mission'], (mission_id,))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    async def onGameEvent(self, data: dict) -> None:
        mission_id = self.globals[data['server_name']]['mission_id']
        # ignore game events until the server is not initialized correctly
        if data['server_name'] not in self.bot.player_data:
            pass
        if data['eventName'] == 'disconnect':
            if data['arg1'] != 1:
                player = utils.get_player(self, data['server_name'], id=data['arg1'])
                if not player:
                    self.log.warning(f"Player id={data['arg1']} not found. Can't close their statistics.")
                    return
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute(self.SQL_MISSION_HANDLING['stop_player'],
                                       (mission_id, player['ucid']))
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
        elif data['eventName'] == 'kill':
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    # Player is not an AI
                    if data['arg1'] != -1:
                        if data['arg4'] != -1:
                            # selfkill
                            if data['arg1'] == data['arg4']:
                                kill_type = 'self_kill'
                            # teamkills
                            elif data['arg3'] == data['arg6']:
                                kill_type = 'teamkill'
                            # PVP
                            elif data['victimCategory'] == 'Planes':
                                kill_type = 'pvp_planes'
                            elif data['victimCategory'] == 'Helicopters':
                                kill_type = 'pvp_helicopters'
                        elif data['victimCategory'] == 'Planes':
                            kill_type = 'kill_planes'
                        elif data['victimCategory'] == 'Helicopters':
                            kill_type = 'kill_helicopters'
                        elif data['victimCategory'] == 'Ships':
                            kill_type = 'kill_ships'
                        elif data['victimCategory'] == 'Air Defence':
                            kill_type = 'kill_sams'
                        elif data['victimCategory'] in ['Unarmed', 'Armor', 'Infantry', 'Fortification', 'Artillery',
                                                        'MissilesSS']:
                            kill_type = 'kill_ground'
                        else:
                            kill_type = 'kill_other'  # Static objects
                        if kill_type in self.SQL_EVENT_UPDATES.keys():
                            for player in utils.get_crew_members(self, data['server_name'], data['arg1']):
                                cursor.execute(self.SQL_EVENT_UPDATES[kill_type], (mission_id, player['ucid']))
                        else:
                            self.log.debug(f'No SQL for kill_type {kill_type} found!.')

                    # Victim is not an AI
                    if data['arg4'] != -1:
                        if data['arg1'] != -1:
                            if data['arg1'] == data['arg4']:  # self kill
                                death_type = 'self_kill'
                            elif data['arg3'] == data['arg6']:  # killed by team member - no death counted
                                death_type = 'teamdeath'
                            # PVP
                            elif data['killerCategory'] == 'Planes':
                                death_type = 'deaths_pvp_planes'
                            elif data['killerCategory'] == 'Helicopters':
                                death_type = 'deaths_pvp_helicopters'
                        elif data['killerCategory'] == 'Planes':
                            death_type = 'deaths_planes'
                        elif data['killerCategory'] == 'Helicopters':
                            death_type = 'deaths_helicopters'
                        elif data['killerCategory'] == 'Ships':
                            death_type = 'deaths_ships'
                        elif data['killerCategory'] == 'Air Defence':
                            death_type = 'deaths_sams'
                        elif data['killerCategory'] in ['Armor', 'Infantry' 'Fortification', 'Artillery',
                                                        'MissilesSS']:
                            death_type = 'deaths_ground'
                        else:
                            death_type = 'other'
                        if death_type in self.SQL_EVENT_UPDATES.keys():
                            for player in utils.get_crew_members(self, data['server_name'], data['arg4']):
                                cursor.execute(self.SQL_EVENT_UPDATES[death_type], (mission_id, player['ucid']))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'pilot_death']:
            if data['arg1'] != -1:
                if data['eventName'] in self.SQL_EVENT_UPDATES.keys():
                    conn = self.pool.getconn()
                    try:
                        with closing(conn.cursor()) as cursor:
                            player = utils.get_player(self, data['server_name'], id=data['arg1'])
                            cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                           (mission_id, player['ucid']))
                            conn.commit()
                    except (Exception, psycopg2.DatabaseError) as error:
                        self.log.exception(error)
                        conn.rollback()
                    finally:
                        self.pool.putconn(conn)
        elif data['eventName'] in ['eject']:
            if data['arg1'] != -1:
                if data['eventName'] in self.SQL_EVENT_UPDATES.keys():
                    # TODO: when DCS bug wih multicrew eject gets fixed, change this to single player only
                    players = utils.get_crew_members(self, data['server_name'], data['arg1'])
                    if len(players) == 1:
                        conn = self.pool.getconn()
                        try:
                            with closing(conn.cursor()) as cursor:
                                cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                               (mission_id, players[0]['ucid']))
                                conn.commit()
                        except (Exception, psycopg2.DatabaseError) as error:
                            self.log.exception(error)
                            conn.rollback()
                        finally:
                            self.pool.putconn(conn)

    async def onChatCommand(self, data: dict) -> None:
        if data['message'].startswith('-linkme'):
            items = data['message'].split(' ')
            if len(items) > 1:
                token = items[1]
                player = utils.get_player(self, data['server_name'], id=data['from_id'])
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('SELECT discord_id FROM players WHERE ucid = %s', (token, ))
                        if cursor.rowcount == 0:
                            utils.sendChatMessage(self, data['server_name'], data['from_id'], 'Invalid token.')
                            await self.bot.get_bot_channel(data, 'admin_channel').send(
                                'Player {} (ucid={}) entered a non-existent linking token.'.format(
                                    player['name'], player['ucid']))
                        else:
                            discord_id = cursor.fetchone()[0]
                            cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (discord_id, player['ucid']))
                            cursor.execute('DELETE FROM players WHERE ucid = %s', (token, ))
                            utils.sendChatMessage(self, data['server_name'], data['from_id'], 'Your user has been linked!')
                            with suppress(Exception):
                                member = self.bot.guilds[0].get_member(discord_id)
                                await self.bot.audit(f"Member \"{member.display_name}\" self-linked to DCS user \"{player['name']}\" (ucid={player['ucid']}).")
                        conn.commit()
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
            else:
                utils.sendChatMessage(self, data['server_name'], data['from_id'], 'Syntax: -linkme token\nYou get the token with {}linkme in our Discord.'.format(self.config['BOT']['COMMAND_PREFIX']))
