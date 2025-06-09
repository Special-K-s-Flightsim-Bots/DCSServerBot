from __future__ import annotations

import asyncio
import discord

from contextlib import closing
from core import utils
from core.data.dataobject import DataObject, DataObjectFactory
from core.data.const import Side, Coalition
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union, AsyncGenerator

from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from .server import Server
    from services.bot import DCSServerBot

__all__ = ["Player"]


@dataclass
@DataObjectFactory.register()
class Player(DataObject):
    server: Server = field(compare=False)
    id: int = field(compare=False)
    active: bool = field(compare=False)
    side: Side = field(compare=False)
    ucid: str
    ipaddr: str
    banned: bool = field(compare=False, default=False, init=False)
    slot: int = field(compare=False, default=0)
    sub_slot: int = field(compare=False, default=0)
    unit_callsign: str = field(compare=False, default='')
    unit_id: int = field(compare=False, default=0)
    unit_name: str = field(compare=False, default='')
    unit_display_name: str = field(compare=False, default='')
    unit_type: str = field(compare=False, default='')
    group_id: int = field(compare=False, default=0)
    group_name: str = field(compare=False, default='')
    _member: discord.Member = field(compare=False, repr=False, default=None, init=False)
    _verified: bool = field(compare=False, default=False)
    _coalition: Coalition = field(compare=False, default=None)
    _watchlist: bool = field(compare=False, default=False)
    _vip: bool = field(compare=False, default=False)
    bot: DCSServerBot = field(compare=False, init=False)

    def __post_init__(self):
        from services.bot import BotService

        super().__post_init__()
        self.bot = ServiceRegistry.get(BotService).bot
        if self.id == 1:
            self.active = False
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    cursor.execute("""
                        SELECT DISTINCT p.discord_id, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, 
                               p.manual, c.coalition, 
                               CASE WHEN w.player_ucid IS NOT NULL THEN TRUE ELSE FALSE END AS watchlict, p.vip 
                        FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                        LEFT OUTER JOIN coalitions c ON p.ucid = c.player_ucid AND c.server_name = %s
                        LEFT OUTER JOIN watchlist w ON p.ucid = w.player_ucid
                        WHERE p.ucid = %s 
                        AND COALESCE(b.banned_until, (now() AT TIME ZONE 'utc')) >= (now() AT TIME ZONE 'utc')
                    """, (self.server.name, self.ucid))
                    # existing member found?
                    if cursor.rowcount == 1:
                        row = cursor.fetchone()
                        self._member = self.bot.get_member_by_ucid(self.ucid)
                        if self._member:
                            # special handling for discord-less bots
                            if isinstance(self._member, discord.Member):
                                self._verified = row[2]
                            else:
                                self._verified = True
                        self.banned = row[1]
                        if row[3]:
                            self.coalition = Coalition(row[3])
                        self._watchlist = row[4]
                        self._vip = row[5]
                    else:
                        rules = self.server.locals.get('rules')
                        if rules:
                            cursor.execute("""
                                INSERT INTO messages (sender, player_ucid, message, ack) 
                                VALUES (%s, %s, %s, %s)
                            """, (self.server.locals.get('server_user', 'Admin'), self.ucid, rules,
                                  self.server.locals.get('accept_rules_on_join', False)))

                    cursor.execute("""
                        INSERT INTO players (ucid, discord_id, name, last_seen) 
                        VALUES (%s, -1, %s, (now() AT TIME ZONE 'utc')) 
                        ON CONFLICT (ucid) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen
                        """, (self.ucid, self.name))
        # if automatch is enabled, try to match the user
        if not self.member and self.bot.locals.get('automatch', False):
            discord_user = self.bot.match_user({"ucid": self.ucid, "name": self.name})
            if discord_user:
                self.member = discord_user

    def is_active(self) -> bool:
        return self.active

    def is_multicrew(self) -> bool:
        return self.sub_slot != 0

    def is_banned(self) -> bool:
        return self.banned

    @property
    def member(self) -> discord.Member:
        return self._member

    @member.setter
    def member(self, member: discord.Member) -> None:
        if member != self._member:
            self.update_member(member)
            self._member = member

    def update_member(self, member: discord.Member) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s WHERE ucid = %s',
                             (member.id if member else -1, self.ucid))

    @property
    def verified(self) -> bool:
        return self._verified

    @verified.setter
    def verified(self, verified: bool) -> None:
        if verified == self._verified:
            return
        self.update_verified(verified)
        self._verified = verified

    def update_verified(self, verified: bool) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET manual = %s WHERE ucid = %s', (verified, self.ucid))
                if verified:
                    # delete all old automated links (this will delete the token also)
                    conn.execute("DELETE FROM players WHERE ucid = %s AND manual = FALSE", (self.ucid,))
                    conn.execute("DELETE FROM players WHERE discord_id = %s AND length(ucid) = 4",
                                 (self.member.id,))
                    conn.execute("UPDATE players SET discord_id = -1 WHERE discord_id = %s AND manual = FALSE",
                                 (self.member.id,))

    @property
    def watchlist(self) -> bool:
        return self._watchlist

    @property
    def vip(self) -> bool:
        return self._vip

    @vip.setter
    def vip(self, vip: bool):
        self.update_vip(vip)
        self._vip = vip

    def update_vip(self, vip: bool) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET vip = %s WHERE ucid = %s', (vip, self.ucid))

    @property
    def coalition(self) -> Coalition:
        return self._coalition

    @coalition.setter
    def coalition(self, coalition: Coalition):
        self._coalition = coalition
        if coalition == Coalition.BLUE:
            side = Side.BLUE
        elif coalition == Coalition.RED:
            side = Side.RED
        elif coalition == Coalition.NEUTRAL:
            side = Side.NEUTRAL
        else:
            side = Side.SPECTATOR
        self.bot.loop.create_task(self.server.send_to_dcs({
            "command": "setUserCoalition",
            "ucid": self.ucid,
            "coalition": side.value
        }))

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    async def update(self, data: dict):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if 'id' in data:
                    # if the ID has changed (due to reconnect), we need to update the server list
                    if self.id != data['id']:
                        self.server.players[data['id']] = self.server.players.pop(self.id)
                        self.id = data['id']
                if 'active' in data:
                    self.active = data['active']
                if 'name' in data and self.name != data['name']:
                    self.name = data['name']
                    await conn.execute('UPDATE players SET name = %s WHERE ucid = %s', (self.name, self.ucid))
                if 'side' in data:
                    self.side = Side(data['side'])
                if 'slot' in data:
                    self.slot = int(data['slot'])
                if 'sub_slot' in data:
                    self.sub_slot = data['sub_slot']
                if 'unit_callsign' in data:
                    self.unit_callsign = data['unit_callsign']
                if 'unit_id' in data:
                    self.unit_id = data['unit_id']
                if 'unit_name' in data:
                    self.unit_name = data['unit_name']
                if 'unit_type' in data:
                    self.unit_type = data['unit_type']
                if 'group_name' in data:
                    self.group_name = data['group_name']
                if 'group_id' in data:
                    self.group_id = data['group_id']
                if 'unit_display_name' in data:
                    self.unit_display_name = data['unit_display_name']
                if 'ipaddr' in data:
                    self.ipaddr = data['ipaddr']
                await conn.execute("""
                    UPDATE players SET last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE ucid = %s
                """, (self.ucid, ))

    def has_discord_roles(self, roles: list[Union[str, int]]) -> bool:
        valid_roles = []
        for role in roles:
            valid_roles.extend(self.bot.roles[role])
        return self.verified and self._member is not None and utils.check_roles(set(valid_roles), self._member)

    async def sendChatMessage(self, message: str, sender: str = None):
        async def message_lines(m: str) -> AsyncGenerator[str, None]:
            for line in m.splitlines():
                yield line

        async for msg in message_lines(message):
            await self.server.send_to_dcs({
                "command": "sendChatMessage",
                "to": self.id,
                "from": sender,
                "message": msg
            })

    async def sendUserMessage(self, message: str, timeout: Optional[int] = -1):
        asyncio.create_task(self.sendPopupMessage(message, timeout))
        asyncio.create_task(self.sendChatMessage(message))

    async def sendPopupMessage(self, message: str, timeout: Optional[int] = -1, sender: str = None):
        if timeout == -1:
            timeout = self.server.locals.get('message_timeout', 10)
        await self.server.send_to_dcs({
                "command": "sendPopupMessage",
                "from": sender,
                "to": "unit",
                "id": self.unit_name,
                "message": message,
                "time": timeout
        })

    async def playSound(self, sound: str):
        await self.server.send_to_dcs({
            "command": "playSound",
            "to": "unit",
            "id": self.unit_name,
            "sound": sound
        })

    async def add_role(self, role: Union[str, int]):
        if not self.member or not role:
            return
        try:
            _role = self.bot.get_role(role)
            if not _role:
                self.log.error(f'Role {role} not found!')
                return
            await self.member.add_roles(_role)
        except discord.Forbidden:
            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
        except discord.DiscordException as ex:
            self.log.error(f"Error while adding role {role}: {ex}")

    async def remove_role(self, role: Union[str, int]):
        if not self.member or not role:
            return
        try:
            _role = self.bot.get_role(role)
            if not _role:
                self.log.error(f'Role {role} not found!')
                return
            await self.member.remove_roles(_role)
        except discord.Forbidden:
            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
        except discord.DiscordException as ex:
            self.log.error(f"Error while removing role {role}: {ex}")

    def check_exemptions(self, exemptions: Union[dict, list]) -> bool:
        def _check_exemption(exemption: dict) -> bool:
            if 'ucid' in exemption:
                if not isinstance(exemption['ucid'], list):
                    ucids = [exemption['ucid']]
                else:
                    ucids = exemption['ucid']
                if self.ucid in ucids:
                    return True
            if 'discord' in exemption:
                if not self.member:
                    return False
                if not isinstance(exemption['discord'], list):
                    roles = [exemption['discord']]
                else:
                    roles = exemption['discord']
                if utils.check_roles(roles, self.member):
                    return True
            return False

        if isinstance(exemptions, list):
            ret = False
            for exemption in exemptions:
                ret = _check_exemption(exemption) | ret
        else:
            ret = _check_exemption(exemptions)
        return ret

    async def makeScreenshot(self) -> None:
        await self.server.send_to_dcs({
            "command": "makeScreenshot",
            "id": self.id
        })

    async def getScreenshots(self) -> list[str]:
        data = await self.server.send_to_dcs_sync({
            "command": "getScreenshots",
            "id": self.id
        })
        return data.get('screens', [])

    async def deleteScreenshot(self, key: str) -> None:
        await self.server.send_to_dcs({
            "command": "deleteScreenshot",
            "id": self.id,
            "key": key
        })

    async def lock(self) -> None:
        await self.server.send_to_dcs({
            "command": "lock_player",
            "ucid": self.ucid
        })

    async def unlock(self) -> None:
        await self.server.send_to_dcs({
            "command": "unlock_player",
            "ucid": self.ucid
        })
