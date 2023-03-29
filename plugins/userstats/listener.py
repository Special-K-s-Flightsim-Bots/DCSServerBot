import psycopg2
from contextlib import closing
from core import EventListener, Plugin, Status, Server, Side, Player, Channel, event, chat_command
from typing import Union


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
        'start_player': 'INSERT INTO statistics (mission_id, player_ucid, slot, side) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
        'stop_player': 'UPDATE statistics SET hop_off = NOW() WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'all_players': 'SELECT player_ucid FROM statistics WHERE mission_id = %s AND hop_off IS NULL'
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.statistics = set()

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name == 'registerDCSServer' or server.name in self.statistics:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    @staticmethod
    def get_unit_type(player: Union[Player, dict]) -> str:
        unit_type: str = player.unit_type if isinstance(player, Player) else player['unit_type']
        sub_slot: int = player.sub_slot if isinstance(player, Player) else player['sub_slot']
        if int(sub_slot) not in [-1, 0]:
            unit_type += ' (Crew)'
        return unit_type

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if data['statistics']:
            self.statistics.add(server.name)
        if server.status == Status.STOPPED:
            return
        # registering a running instance
        if data['channel'].startswith('sync-') and 'current_mission' in data:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    mission_id = -1
                    cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server.name,))
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
                            cursor.execute(self.SQL_MISSION_HANDLING['close_all_statistics'], (server.name,))
                            cursor.execute(self.SQL_MISSION_HANDLING['close_all_missions'], (server.name,))
                        # create a new mission
                        cursor.execute(self.SQL_MISSION_HANDLING['start_mission'], (server.name,
                                                                                    data['current_mission'],
                                                                                    data['current_map']))
                        cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server.name,))
                        if cursor.rowcount == 1:
                            mission_id = cursor.fetchone()[0]
                        else:
                            self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                                           'gathered for this session.')
                    server.mission_id = mission_id
                    if mission_id != -1:
                        # initialize active players
                        players = server.get_active_players()
                        ucids = []
                        for player in players:
                            ucids.append(player.ucid)
                            # make sure we get slot changes that might have occurred in the meantime
                            cursor.execute(self.SQL_MISSION_HANDLING['check_player'], (mission_id, player.ucid))
                            player_started = False
                            if cursor.rowcount == 1:
                                # the player is there already ...
                                if cursor.fetchone()[0] != player.unit_type:
                                    # ... but with a different aircraft, so close the old session
                                    cursor.execute(self.SQL_MISSION_HANDLING['stop_player'],
                                                   (mission_id, player.ucid))
                                else:
                                    # session will be kept
                                    player_started = True
                            if not player_started and player.side != Side.SPECTATOR:
                                # only warn for unknown users if it is a non-public server and automatch is on
                                if not player.member and self.bot.config.getboolean('BOT', 'AUTOMATCH') and \
                                        len(server.settings['password']) > 0:
                                    await server.get_channel(Channel.ADMIN).send(
                                        'Player {} (ucid={}) can\'t be matched to a discord user.'.format(
                                            data['name'], data['ucid']))
                                cursor.execute(self.SQL_MISSION_HANDLING['start_player'],
                                               (mission_id, player.ucid, self.get_unit_type(player),
                                                player.side.value))
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

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['close_all_statistics'], (server.name,))
                cursor.execute(self.SQL_MISSION_HANDLING['close_all_missions'], (server.name,))
                cursor.execute(self.SQL_MISSION_HANDLING['start_mission'], (server.name,
                                                                            data['current_mission'],
                                                                            data['current_map']))
                cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server.name,))
                if cursor.rowcount == 1:
                    server.mission_id = cursor.fetchone()[0]
                else:
                    server.mission_id = -1
                    self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                                   'gathered for this session.')
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, data: dict) -> None:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['close_statistics'], (server.mission_id,))
                cursor.execute(self.SQL_MISSION_HANDLING['close_mission'], (server.mission_id,))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if 'side' not in data:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute(self.SQL_MISSION_HANDLING['stop_player'], (server.mission_id, data['ucid']))
                if Side(data['side']) != Side.SPECTATOR:
                    cursor.execute(self.SQL_MISSION_HANDLING['start_player'],
                                   (server.mission_id, data['ucid'], self.get_unit_type(data), data['side']))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @event(name="disableUserStats")
    async def disableUserStats(self, server: Server, data: dict) -> None:
        self.statistics.discard(server.name)
        await self.onSimulationStop(server, data)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        # ignore game events until the server is not initialized correctly
        if server.status != Status.RUNNING:
            return
        if data['eventName'] == 'disconnect':
            if data['arg1'] != 1:
                player: Player = server.get_player(id=data['arg1'])
                if not player:
                    self.log.warning(f"Player id={data['arg1']} not found. Can't close their statistics.")
                    return
                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute(self.SQL_MISSION_HANDLING['stop_player'],
                                       (server.mission_id, player.ucid))
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
                            pilot: Player = server.get_player(id=data['arg1'])
                            for crew_member in server.get_crew_members(pilot):
                                cursor.execute(self.SQL_EVENT_UPDATES[kill_type], (server.mission_id, crew_member.ucid))

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
                            pilot: Player = server.get_player(id=data['arg4'])
                            for crew_member in server.get_crew_members(pilot):
                                cursor.execute(self.SQL_EVENT_UPDATES[death_type], (server.mission_id, crew_member.ucid))
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
                            player: Player = server.get_player(id=data['arg1'])
                            if not player:
                                return
                            cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                           (server.mission_id, player.ucid))
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
                    pilot: Player = server.get_player(id=data['arg1'])
                    crew_members = server.get_crew_members(pilot)
                    if len(crew_members) == 1:
                        conn = self.pool.getconn()
                        try:
                            with closing(conn.cursor()) as cursor:
                                cursor.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                               (server.mission_id, crew_members[0].ucid))
                                conn.commit()
                        except (Exception, psycopg2.DatabaseError) as error:
                            self.log.exception(error)
                            conn.rollback()
                        finally:
                            self.pool.putconn(conn)

    @chat_command(name="linkme", roles=['DCS Admin'], usage="<token>", help="link your user to Discord")
    async def linkme(self, server: Server, player: Player, params: list[str]):
        if not params:
            player.sendChatMessage(f"Syntax: {self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}linkme token\n"
                                   f"You get the token with {self.bot.config['BOT']['COMMAND_PREFIX']}linkme in "
                                   f"our Discord.")
            return

        token = params[0]
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT discord_id FROM players WHERE ucid = %s', (token,))
                if cursor.rowcount == 0:
                    player.sendChatMessage('Invalid token.')
                    await server.get_channel(Channel.ADMIN).send(f'Player {player.display_name} '
                                                                 f'(ucid={player.ucid}) entered a '
                                                                 f'non-existent linking token.')
                else:
                    discord_id = cursor.fetchone()[0]
                    player.member = self.bot.guilds[0].get_member(discord_id)
                    player.verified = True
                    cursor.execute('DELETE FROM players WHERE ucid = %s', (token,))
                    await self.bot.audit(
                        f'self-linked to DCS user "{player.display_name}" (ucid={player.ucid}).',
                        user=player.member)
                    player.sendChatMessage('Your user has been linked!')
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
