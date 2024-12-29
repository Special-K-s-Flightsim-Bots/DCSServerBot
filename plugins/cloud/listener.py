from contextlib import suppress

import aiohttp
import asyncio

from core import EventListener, Server, Player, Side, event, Status
from datetime import datetime, timezone
from discord.ext import tasks
from psycopg.rows import dict_row


class CloudListener(EventListener):

    def __init__(self, plugin):
        super().__init__(plugin)
        if self.plugin.get_config().get('register', True):
            self.update_registration.start()

    async def shutdown(self) -> None:
        if self.plugin.get_config().get('register', True):
            self.update_registration.cancel()
        await super().shutdown()

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        if self.plugin.get_config().get('register', True) and data['channel'].startswith('sync'):
            await self.cloud_register(server)

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
            # noinspection PyUnresolvedReferences
            row['client'] = self.plugin.client
            try:
                # noinspection PyUnresolvedReferences
                await self.plugin.post('upload', row)
            except aiohttp.ClientError:
                self.log.warn('Cloud service not available atm, skipping statistics upload.')

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
        if player.side == Side.SPECTATOR:
            return
        # noinspection PyAsyncCall
        asyncio.create_task(self.update_cloud_data(server, player))

    async def cloud_register(self, server: Server):
        try:
            # noinspection PyUnresolvedReferences
            await self.plugin.post('register_server', {
                "guild_id": self.node.guild_id,
                "server_name": server.name,
                "ipaddr": server.instance.dcs_host,
                "port": server.instance.dcs_port,
                "password": (server.settings['password'] != ""),
                "theatre": server.current_mission.map,
                "dcs_version": server.node.dcs_version,
                "num_players": len(server.get_active_players()) + 1,
                "max_players": int(server.settings.get('maxPlayers', 16)),
                "mission": server.current_mission.name if server.current_mission else "",
                "time_in_mission": int(server.current_mission.mission_time if server.current_mission else 0),
                "time_to_restart": (server.restart_time - datetime.now(tz=timezone.utc)).total_seconds() if server.restart_time else -1,
            })
            self.log.info(f"Server {server.name} registered with the cloud.")
        except aiohttp.ClientError as ex:
            self.log.error(f"Could not register server {server.name} with the cloud.", exc_info=ex)

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, _: dict) -> None:
        if self.plugin.get_config().get('register', True):
            await self.cloud_register(server)

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, _: dict) -> None:
        if self.plugin.get_config().get('register', True):
            try:
                # noinspection PyUnresolvedReferences
                await self.plugin.post('unregister_server', {
                    "guild_id": self.node.guild_id,
                    "server_name": server.name,
                })
                self.log.info(f"Server {server.name} unregistered from the cloud.")
            except aiohttp.ClientError as ex:
                self.log.error(f"Could not unregister server {server.name} from the cloud.", exc_info=ex)

    @tasks.loop(minutes=5)
    async def update_registration(self):
        for server in self.bot.servers.values():
            if server.status in [Status.RUNNING, Status.PAUSED]:
                with suppress(Exception):
                    await self.cloud_register(server)
