import asyncio

from core import EventListener, Status, Server, Side, Player, event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import UserStatistics


class UserStatisticsEventListener(EventListener["UserStatistics"]):

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
        'start_mission': 'INSERT INTO missions (server_name, mission_name, mission_theatre) VALUES (%s, %s, %s) RETURNING id',
        'current_mission_id': 'SELECT id, mission_name FROM missions WHERE server_name = %s AND mission_end IS NULL',
        'close_statistics': "UPDATE statistics SET hop_off = GREATEST((hop_on + INTERVAL '1 second'), (now() AT TIME ZONE 'utc')) WHERE mission_id = %s AND hop_off IS NULL",
        'close_mission': "UPDATE missions SET mission_end = (now() AT TIME ZONE 'utc') WHERE id = %s",
        'check_player': 'SELECT slot FROM statistics WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL',
        'start_player': 'INSERT INTO statistics (mission_id, player_ucid, slot, tail_no, side) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
        'stop_player': "UPDATE statistics SET hop_off = GREATEST((hop_on + INTERVAL '1 second'), (now() AT TIME ZONE 'utc')) WHERE mission_id = %s AND player_ucid = %s AND hop_off IS NULL",
        'all_players': 'SELECT player_ucid FROM statistics WHERE mission_id = %s AND hop_off IS NULL'
    }

    def __init__(self, plugin: "UserStatistics"):
        super().__init__(plugin)
        self.active_servers: set[str] = set()

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            if name in [
                'registerDCSServer', 'onMemberLinked', 'onMemberUnlinked'
            ] or server.name in self.active_servers:
                await super().processEvent(name, server, data)
        except Exception as ex:
            self.log.exception(ex)

    @staticmethod
    def get_unit_type(player: Player | dict) -> str:
        unit_type: str = player.unit_type if isinstance(player, Player) else player['unit_type']
        sub_slot: int = player.sub_slot if isinstance(player, Player) else player['sub_slot']
        if int(sub_slot) not in [-1, 0]:
            unit_type += ' (Crew)'
        return unit_type

    @staticmethod
    def get_unit_callsign(player: Player | dict) -> str:
        return player.unit_callsign if isinstance(player, Player) else player['unit_callsign']

    async def close_all_statistics(self, server: Server):
        async with self.apool.connection() as conn:
            await conn.execute("""
                UPDATE missions m1 SET mission_end = (
                    SELECT mission_start - INTERVAL '1 second' FROM missions m2 
                    WHERE m1.server_name = m2.server_name
                    AND m2.id > m1.id
                    ORDER BY 1 LIMIT 1)
                WHERE m1.server_name = %s AND m1.mission_end IS NULL
            """, (server.name,))
            await conn.execute("""
                UPDATE missions SET mission_end = (now() AT TIME ZONE 'utc') WHERE server_name = %s AND mission_end IS NULL
            """, (server.name,))

            cursor = await conn.execute("""
                        SELECT mission_id, player_ucid, slot 
                        FROM statistics 
                        WHERE mission_id IN (
                            SELECT id FROM missions WHERE server_name = %s
                        ) AND hop_off IS NULL
                    """, (server.name,))
            rows = await cursor.fetchall()
            for row in rows:
                await conn.execute("""
                    UPDATE statistics SET hop_off = (SELECT mission_end FROM missions WHERE id = %s)
                    WHERE mission_id = %s AND player_ucid = %s AND slot = %s AND hop_off IS NULL
                """, (row[0], row[0], row[1], row[2]))
            await conn.execute("""
                UPDATE statistics SET hop_off = (now() AT TIME ZONE 'utc') WHERE mission_id IN (
                    SELECT id FROM missions WHERE server_name = %s
                ) AND hop_off IS NULL
            """, (server.name,))

    async def _setup_database(self, server: Server, data: dict) -> None:
        async with self.apool.connection() as conn:
            async with conn.cursor() as cursor:
                mission_id = -1
                await cursor.execute(self.SQL_MISSION_HANDLING['current_mission_id'], (server.name,))
                if cursor.rowcount == 1:
                    row = await cursor.fetchone()
                    if row[1] == data['current_mission']:
                        mission_id = row[0]
                    else:
                        self.log.warning('The mission in the database does not match the mission that is live '
                                         'on this server. Fixing...')
                if mission_id == -1:
                    # close ambiguous missions
                    if cursor.rowcount >= 1:
                        await self.close_all_statistics(server)
                    # create a new mission
                    await cursor.execute(self.SQL_MISSION_HANDLING['start_mission'],
                                         (server.name, data['current_mission'], data['current_map']))
                    if cursor.rowcount == 1:
                        mission_id = (await cursor.fetchone())[0]
                    else:
                        self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                                       'gathered for this session.')

                if mission_id == -1:
                    return

                server.mission_id = mission_id
                # initialize active players
                players = server.get_active_players()
                ucids = []
                for player in players:
                    ucids.append(player.ucid)
                    # make sure we get slot changes that might have occurred in the meantime
                    await cursor.execute(self.SQL_MISSION_HANDLING['check_player'], (mission_id, player.ucid))
                    player_started = False
                    if cursor.rowcount == 1:
                        # the player is there already ...
                        if (await cursor.fetchone())[0] != player.unit_type:
                            # ... but with a different aircraft, so close the old session
                            await cursor.execute(self.SQL_MISSION_HANDLING['stop_player'],
                                                 (mission_id, player.ucid))
                        else:
                            # session will be kept
                            player_started = True
                    if not player_started and player.side != Side.NEUTRAL:
                        await cursor.execute(
                            self.SQL_MISSION_HANDLING['start_player'],
                            (
                                mission_id,
                                player.ucid,
                                self.get_unit_type(player),
                                self.get_unit_callsign(player),
                                player.side.value)
                            )
                # close dead entries in the database (if existent)
                await cursor.execute(self.SQL_MISSION_HANDLING['all_players'], (mission_id,))
                rows = await cursor.fetchall()
                for row in rows:
                    if row[0] not in ucids:
                        await cursor.execute(self.SQL_MISSION_HANDLING['stop_player'], (mission_id, row[0]))

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if self.get_config(server).get('enabled', True):
            self.active_servers.add(server.name)
        else:
            self.active_servers.discard(server.name)
            return
        if server.status == Status.STOPPED or not data['channel'].startswith('sync-') or 'current_mission' not in data:
            return
        # set up the database
        asyncio.create_task(self._setup_database(server, data))

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        async with self.apool.connection() as conn:
            await self.close_all_statistics(server)
            cursor = await conn.execute(self.SQL_MISSION_HANDLING['start_mission'],
                                        (server.name, data['current_mission'], data['current_map']))
            if cursor.rowcount == 1:
                server.mission_id = (await cursor.fetchone())[0]
            else:
                server.mission_id = -1
                self.log.error('FATAL: Initialization of mission table failed. Statistics will not be '
                               'gathered for this session.')

    async def close_mission_stats(self, server: Server):
        async with self.apool.connection() as conn:
            await conn.execute(self.SQL_MISSION_HANDLING['close_statistics'], (server.mission_id,))
            await conn.execute(self.SQL_MISSION_HANDLING['close_mission'], (server.mission_id,))

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        asyncio.create_task(self.close_mission_stats(server))

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if 'side' not in data or data['id'] == 1:
            return
        async with self.apool.connection() as conn:
            await conn.execute(self.SQL_MISSION_HANDLING['stop_player'], (server.mission_id, data['ucid']))
            if Side(data['side']) != Side.NEUTRAL:
                await conn.execute(
                    self.SQL_MISSION_HANDLING['start_player'],
                    (
                        server.mission_id,
                        data['ucid'],
                        self.get_unit_type(data),
                        self.get_unit_callsign(data),
                        data['side'])
                    )

    @event(name="disableUserStats")
    async def disableUserStats(self, server: Server, _: dict) -> None:
        self.active_servers.discard(server.name)
        asyncio.create_task(self.close_mission_stats(server))

    async def _handle_disconnect_event(self, server: Server, data: dict) -> None:
        if data['arg1'] == 1:
            return
        player: Player = server.get_player(id=data['arg1'])
        if not player:
            self.log.warning(f"Player id={data['arg1']} not found. Can't close their statistics.")
            return
        async with self.apool.connection() as conn:
            await conn.execute(self.SQL_MISSION_HANDLING['stop_player'], (server.mission_id, player.ucid))

    async def _handle_kill_killer(self, server: Server, data: dict) -> None:
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
            else:
                kill_type = 'kill_other'
        elif data['victimCategory'] == 'Planes':
            kill_type = 'kill_planes'
        elif data['victimCategory'] == 'Helicopters':
            kill_type = 'kill_helicopters'
        elif data['victimCategory'] == 'Ships':
            kill_type = 'kill_ships'
        elif data['victimCategory'] == 'Air Defence':
            kill_type = 'kill_sams'
        elif data['victimCategory'] in ['Unarmed', 'Armor', 'Infantry', 'Fortification', 'Artillery', 'MissilesSS']:
            kill_type = 'kill_ground'
        else:
            kill_type = 'kill_other'  # Static objects
        if kill_type in self.SQL_EVENT_UPDATES.keys():
            pilot: Player = server.get_player(id=data['arg1'])
            async with self.apool.connection() as conn:
                for crew_member in server.get_crew_members(pilot):
                    await conn.execute(self.SQL_EVENT_UPDATES[kill_type], (server.mission_id, crew_member.ucid))

    async def _handle_kill_victim(self, server: Server, data: dict) -> None:
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
            else:
                death_type = 'other'
        elif data['killerCategory'] == 'Planes':
            death_type = 'deaths_planes'
        elif data['killerCategory'] == 'Helicopters':
            death_type = 'deaths_helicopters'
        elif data['killerCategory'] == 'Ships':
            death_type = 'deaths_ships'
        elif data['killerCategory'] == 'Air Defence':
            death_type = 'deaths_sams'
        elif data['killerCategory'] in ['Armor', 'Infantry' 'Fortification', 'Artillery', 'MissilesSS']:
            death_type = 'deaths_ground'
        else:
            death_type = 'other'
        if death_type in self.SQL_EVENT_UPDATES.keys():
            pilot: Player = server.get_player(id=data['arg4'])
            async with self.apool.connection() as conn:
                for crew_member in server.get_crew_members(pilot):
                    await conn.execute(self.SQL_EVENT_UPDATES[death_type], (server.mission_id, crew_member.ucid))

    async def _handle_kill_event(self, server: Server, data: dict) -> None:
        # Player is an AI => return
        if data['arg1'] != -1:
            await self._handle_kill_killer(server, data)
        # Victim is an AI => return
        if data['arg4'] != -1:
            await self._handle_kill_victim(server, data)

    async def _handle_common_event(self, server: Server, data: dict) -> None:
        if data['arg1'] == -1:
            return
        if data['eventName'] in self.SQL_EVENT_UPDATES.keys():
            player: Player = server.get_player(id=data['arg1'])
            if not player:
                return
            async with self.apool.connection() as conn:
                await conn.execute(self.SQL_EVENT_UPDATES[data['eventName']], (server.mission_id, player.ucid))

    async def _handle_eject_event(self, server: Server, data: dict) -> None:
        if data['arg1'] == -1:
            return
        if data['eventName'] in self.SQL_EVENT_UPDATES.keys():
            # TODO: when DCS bug wih multicrew eject gets fixed, change this to single player only
            pilot: Player = server.get_player(id=data['arg1'])
            crew_members = server.get_crew_members(pilot)
            if len(crew_members) == 1:
                async with self.apool.connection() as conn:
                    await conn.execute(self.SQL_EVENT_UPDATES[data['eventName']],
                                       (server.mission_id, crew_members[0].ucid))

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        # we cannot write statistics for this session
        if server.mission_id == -1:
            return

        event_name = data['eventName']
        if event_name == 'disconnect':
            await self._handle_disconnect_event(server, data)
        elif event_name == 'kill':
            await self._handle_kill_event(server, data)
        elif event_name in ['takeoff', 'landing', 'crash', 'pilot_death']:
            await self._handle_common_event(server, data)
        elif event_name == 'eject':
            await self._handle_eject_event(server, data)
        elif event_name == 'mission_end':
            config = self.get_config(server)
            if 'highscore' in config:
                asyncio.create_task(self.plugin.render_highscore(config['highscore'], server=server, mission_end=True))

    @event(name="onMemberLinked")
    async def onMemberLinked(self, _server: Server, data: dict) -> None:
        member = self.bot.guilds[0].get_member(data['discord_id'])
        if not member:
            return

        roles = [x.id for x in member.roles]
        try:
            # get possible squadron roles
            async with self.apool.connection() as conn:
                cursor = await conn.execute('SELECT id, role FROM squadrons WHERE role IS NOT NULL')
                rows = await cursor.fetchall()
                for row in rows:
                    # do we have to add the member to a squadron?
                    if row[1] in roles:
                        await conn.execute("""
                            INSERT INTO squadron_members VALUES (%s, %s) 
                            ON CONFLICT (squadron_id, player_ucid) DO NOTHING
                        """, (row[0], data['ucid']))
                    if self.get_config().get('squadrons', {}).get('persist_list', False):
                        await self.plugin.persist_squadron_list(row[0])
        except Exception as ex:
            self.log.exception(ex)

    @event(name="onMemberUnlinked")
    async def onMemberUnlinked(self, _server: Server, data: dict) -> None:
        try:
            async with self.apool.connection() as conn:
                cursor = await conn.execute("""
                    SELECT DISTINCT s.id
                    FROM squadrons s,
                    squadron_members sm
                    WHERE s.id = sm.squadron_id
                    AND sm.player_ucid = %s
                """, (data['ucid'],))
                rows = await cursor.fetchall()
                for row in rows:
                    await conn.execute("""
                        DELETE FROM squadron_members WHERE squadron_id = %s AND player_ucid = %s 
                    """, (row[0], data['ucid']))
                    if self.get_config().get('squadrons', {}).get('persist_list', False):
                        await self.plugin.persist_squadron_list(row[0])
        except Exception as ex:
            self.log.exception(ex)
