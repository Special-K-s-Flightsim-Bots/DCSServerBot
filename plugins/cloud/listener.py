import aiohttp
import asyncio

from core import EventListener, Server, Player, Side, event
from datetime import datetime, timezone
from psycopg.rows import dict_row
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Cloud


class CloudListener(EventListener["Cloud"]):

    def __init__(self, plugin: "Cloud"):
        super().__init__(plugin)
        self.updates: dict[str, datetime] = {}

    async def update_cloud_data(self, server: Server, player: Player):
        if not server.current_mission:
            return
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT s.player_ucid, m.mission_theatre, s.slot, SUM(s.kills) as kills, 
                           SUM(s.pvp) as pvp, SUM(deaths) as deaths, SUM(ejections) as ejections, 
                           SUM(crashes) as crashes, SUM(teamkills) as teamkills, SUM(kills_planes) AS kills_planes, 
                           SUM(kills_helicopters) AS kills_helicopters, SUM(kills_ships) AS kills_ships, 
                           SUM(kills_sams) AS kills_sams, SUM(kills_ground) AS kills_ground, 
                           SUM(deaths_pvp) as deaths_pvp, SUM(deaths_planes) AS deaths_planes, 
                           SUM(deaths_helicopters) AS deaths_helicopters, SUM(deaths_ships) AS deaths_ships, 
                           SUM(deaths_sams) AS deaths_sams, SUM(deaths_ground) AS deaths_ground, 
                           SUM(takeoffs) as takeoffs, SUM(landings) as landings, 
                           ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))))::INTEGER AS playtime 
                    FROM statistics s, missions m 
                    WHERE s.player_ucid = %s AND m.mission_theatre = %s AND s.slot = %s AND s.hop_off IS NOT null 
                    AND s.mission_id = m.id 
                    GROUP BY 1, 2, 3
                """, (player.ucid, server.current_mission.map, player.unit_type))
                row = await cursor.fetchone()
        if row:
            row['client'] = self.plugin.client
            try:
                await self.plugin.post('upload', row)
            except aiohttp.ClientError:
                self.log.warning('Cloud service not available atm, skipping statistics upload.')

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if 'side' not in data or data['id'] == 1:
            return
        config = self.plugin.get_config(server)
        if 'token' not in config:
            return
        player: Player = server.get_player(ucid=data['ucid'])
        if not player:
            return
        if player.side == Side.NEUTRAL:
            return
        asyncio.create_task(self.update_cloud_data(server, player))

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] != 1:
            await server.run_on_extension(extension='Cloud', method='cloud_register')
            self.updates[server.name] = datetime.now(tz=timezone.utc)

    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        if data['id'] != 1:
            await server.run_on_extension(extension='Cloud', method='cloud_register')
            self.updates[server.name] = datetime.now(tz=timezone.utc)

    @event(name="getMissionUpdate")
    async def getMissionUpdate(self, server: Server, _: dict) -> None:
        if not self.updates.get(server.name):
            self.updates[server.name] = datetime.now(tz=timezone.utc)
        if (datetime.now(tz=timezone.utc) - self.updates[server.name]).total_seconds() > 240:
            try:
                await server.run_on_extension(extension='Cloud', method='cloud_register')
            except ValueError:
                self.log.debug("Cloud extension disabled, no cloud registration sent.")
                pass
            self.updates[server.name] = datetime.now(tz=timezone.utc)
