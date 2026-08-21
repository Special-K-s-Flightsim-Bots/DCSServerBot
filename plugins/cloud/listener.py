import aiohttp
import asyncio
import discord

from core import EventListener, Server, Player, Side, event, get_translation, utils
from datetime import datetime, timezone
from discord import ButtonStyle
from discord.ui import View, Button
from psycopg.rows import dict_row
from typing import TYPE_CHECKING, Any

from services.bot.dummy import DummyBot

if TYPE_CHECKING:
    from .commands import Cloud

_ = get_translation(__name__.split('.')[1])


class CloudListener(EventListener["Cloud"]):

    def __init__(self, plugin: "Cloud"):
        super().__init__(plugin)
        self.updates: dict[str, datetime] = {}

    async def _cloud_register(self, *, ucid: str, name: str, discord_id: int) -> None:
        """
        Link a player to a discord account on the cloud service.
        """
        try:
            await self.plugin.post('register_player', {
                'ucid': ucid,
                'name': name,
                'discord_id': discord_id,
                'linked_at': datetime.now(tz=timezone.utc).isoformat(),
                'last_seen': datetime.now(tz=timezone.utc).isoformat()
            })
        except aiohttp.ClientError:
            pass

    async def _cloud_unregister(self, *, ucid: str) -> None:
        """
        Link a player to a discord account on the cloud service.
        """
        try:
            await self.plugin.post('unregister_player', {'ucid': ucid})
        except aiohttp.ClientError:
            pass

    async def _propagate_event(self, *, command: str, server: str, data: dict[str, Any],) -> None:
        """
        Push an event to the ServiceBus so other nodes can react.
        """
        await self.bus.send_to_node(
            {
                "command": "rpc",
                "service": "ServiceBus",
                "method": "propagate_event",
                "params": {"command": command, "server": server, "data": data},
            }
        )

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        async def get_member(discord_id: int) -> discord.Member | None:
            try:
                guild = self.bot.guilds[0]
                m = guild.get_member(discord_id)
                if m:
                    return m
                return await guild.fetch_member(discord_id)
            except discord.NotFound:
                return None

        if isinstance(self.bot, DummyBot):
           return
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player | None = server.get_player(ucid=data['ucid'])
        if not player:
            return

        config = self.get_config(server)
        try:
            if config.get('token'):
                link = await self.plugin.get(f'player?ucid={player.ucid}')
                # we have a cloud link already
                if link:
                    discord_id = link[0]['discord_id']
                    # we have a cloud link, but the player is not verified yet
                    if not player.verified and discord_id:
                        member = await get_member(discord_id)
                        if member:
                            player.member = member
                            player.verified = True
                        else:
                            async with self.apool.connection() as conn:
                                await conn.execute("""
                                    UPDATE players
                                    SET discord_id = %s,
                                        manual     = TRUE
                                    WHERE ucid = %s
                               """, (discord_id, player.ucid))

                        asyncio.create_task(self._propagate_event(
                            command="onMemberLinked",
                            server=server.name,
                            data={
                                "ucid": player.ucid,
                                "discord_id": discord_id,
                                "origin": "cloud"
                            }
                        ))
                    # we have a verified player, but the cloud link is not valid anymore
                    elif player.verified and not discord_id:
                        player.verified = False

                        asyncio.create_task(self._propagate_event(
                            command="onMemberUnlinked",
                            server=server.name,
                            data={
                                "ucid": player.ucid,
                                "origin": "cloud"
                            }
                        ))
                    # we have a verified player, but the cloud link has changed
                    elif player.verified and discord_id != player.member.id:
                        asyncio.create_task(self._propagate_event(
                            command="onMemberUnlinked",
                            server=server.name,
                            data={
                                "ucid": player.ucid,
                                "origin": "cloud"
                            }
                        ))
                        member = await get_member(discord_id)
                        if member:
                            player.member = member
                            player.verified = True
                        else:
                            async with self.apool.connection() as conn:
                                await conn.execute("""
                                    UPDATE players
                                    SET discord_id = %s,
                                        manual     = TRUE
                                    WHERE ucid = %s
                               """, (discord_id, player.ucid))

                        asyncio.create_task(self._propagate_event(
                            command="onMemberLinked",
                            server=server.name,
                            data={
                                "ucid": player.ucid,
                                "discord_id": discord_id,
                                "origin": "cloud"
                            }
                        ))

                # we do not have a cloud link yet, but the player is verified
                elif player.verified:
                    asyncio.create_task(
                        self._cloud_register(ucid=player.ucid, name=player.name, discord_id=player.member.id)
                    )

            if not player.verified:
                asyncio.create_task(player.sendChatMessage(
                    server.locals['messages']['greeting_message_unmatched'].format(server=server, player=player)))

            # Check if the player is a known troublemaker
            troublemaker = self.plugin.troublemakers.get(data['ucid'])
            if not troublemaker:
                return

            # Check the whitelist
            async with self.apool.connection() as conn:
                cursor = await conn.execute("SELECT * FROM whitelist WHERE player_ucid = %s", (data['ucid'], ))
                if await cursor.fetchone():
                    return

            trouble_config = config.get('troublemakers', {})
            admin_channel = self.bot.get_admin_channel(server)
            if trouble_config.get('warn', True) and troublemaker['num_bans'] >= trouble_config.get('warn_threshold', 3):
                if not admin_channel:
                    return

                embed = utils.create_warning_embed(
                    title=_("A potential Troublemaker joined your Server"),
                    text=_("Player {} is banned on {} other multiplayer servers for\n{}").format(
                        player.display_name,
                        troublemaker['num_bans'],
                        "\n{}".format('\n'.join(f"- {x}" for x in troublemaker['reason']))
                    ),
                    fields=[
                        ("Server", server.display_name),
                        ("UCID", player.ucid)
                    ]
                )
                embed.set_footer(text=_("You might want to watch them closely."))
                view = View(timeout=None)
                button = Button(
                    label="Ban", style=ButtonStyle.red, custom_id=f"tm_ban_{player.ucid}"
                )
                view.add_item(button)
                button = Button(
                    label="Whitelist", style=ButtonStyle.green, custom_id=f"tm_whitelist_{player.ucid}"
                )
                view.add_item(button)
                button = Button(label="Cancel", style=ButtonStyle.secondary, custom_id="cancel")
                view.add_item(button)
                mentions = self.bot.mention_admin(server)
                await admin_channel.send(content=mentions, embed=embed, view=view)
            elif trouble_config.get('kick', False) and troublemaker['num_bans'] >= trouble_config.get('kick_threshold', 5):
                await server.kick(
                    player,
                    reason=config.get('troublemaker', {}).get('message', 'You are not welcome on this server.')
                )

        except aiohttp.ClientError:
            pass

    @event(name="onMemberLinked")
    async def onMemberLinked(self, server: Server, data: dict) -> None:
        if data.get('origin') == 'cloud':
            return

        if not self.get_config(server).get('token'):
            return

        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT name FROM players WHERE ucid = %s
            """, (data['ucid'],))
            row: dict | None = await cursor.fetchone()

        if row:
            await self._cloud_register(ucid=data['ucid'], name=row[0], discord_id=data['discord_id'])

    @event(name="onMemberUnlinked")
    async def onMemberUnlinked(self, server: Server, data: dict) -> None:
        if data.get('origin') == 'cloud':
            return
        if self.get_config(server).get('token'):
            await self._cloud_unregister(ucid=data['ucid'])

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
                    FROM statistics s JOIN missions m ON s.mission_id = m.id 
                    WHERE s.player_ucid = %s 
                      AND m.mission_theatre = %s 
                      AND s.slot = %s 
                      AND s.hop_off IS NOT null 
                    GROUP BY 1, 2, 3
                """, (player.ucid, server.current_mission.map, player.unit_type))
                row: dict | None = await cursor.fetchone()
        if row:
            row['client'] = self.plugin.client
            try:
                await self.plugin.post('upload', row)
            except aiohttp.ClientError:
                self.log.debug('Cloud service not available atm, skipping statistics upload.')

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        config = self.plugin.get_config(server)
        if 'register' not in config:
            return
        player = server.get_player(ucid=data['ucid'])
        if not player or player.side == Side.NEUTRAL:
            return
        asyncio.create_task(self.update_cloud_data(server, player))

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

    @event(name="onPlayerUnbanned")
    async def onPlayerUnbanned(self, _server: Server, data: dict) -> None:
        await self.plugin.post('unregister_ban', {
            "guild_id": self.bot.guilds[0].id,
            "ucid": data['ucid']
        })
