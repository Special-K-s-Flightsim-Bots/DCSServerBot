import asyncio
import psycopg2
from core import EventListener, Server, Player, Side
from contextlib import closing


class CloudListener(EventListener):

    async def onPlayerChangeSlot(self, data: dict) -> None:
        if 'side' not in data or data['id'] == 1:
            return
        server: Server = self.bot.servers[data['server_name']]
        config = self.plugin.get_config(server)
        if 'token' not in config:
            return
        player: Player = server.get_player(id=data['id'])
        if not player:
            return
        if player.side == Side.SPECTATOR:
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute('SELECT s.player_ucid, m.mission_theatre, s.slot, SUM(s.kills) as kills, '
                               'SUM(s.pvp) as pvp, SUM(deaths) as deaths, SUM(ejections) as ejections, '
                               'SUM(crashes) as crashes, SUM(teamkills) as teamkills, SUM(kills_planes) AS '
                               'kills_planes, SUM(kills_helicopters) AS kills_helicopters, SUM(kills_ships) AS '
                               'kills_ships, SUM(kills_sams) AS kills_sams, SUM(kills_ground) AS kills_ground, '
                               'SUM(deaths_pvp) as deaths_pvp, SUM(deaths_planes) AS deaths_planes, '
                               'SUM(deaths_helicopters) AS deaths_helicopters, SUM(deaths_ships) AS deaths_ships, '
                               'SUM(deaths_sams) AS deaths_sams, SUM(deaths_ground) AS deaths_ground, '
                               'SUM(takeoffs) as takeoffs, SUM(landings) as landings, ROUND(SUM( '
                               'EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))) AS playtime FROM statistics s, '
                               'missions m WHERE s.player_ucid = %s AND m.mission_theatre = %s AND s.slot = %s '
                               'AND s.hop_off IS NOT null AND s.mission_id = m.id GROUP BY 1, 2, 3',
                               (player.ucid, server.current_mission.map, player.unit_type))
                if cursor.rowcount > 0:
                    row = cursor.fetchone()
                    row['client'] = self.plugin.client
                    self.bot.loop.call_soon(asyncio.create_task, self.plugin.post('upload', row))
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
