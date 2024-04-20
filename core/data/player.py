from __future__ import annotations
import discord
from contextlib import closing
from core import utils
from core.data.dataobject import DataObject, DataObjectFactory
from core.data.const import Side, Coalition
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from .server import Server
    from services import DCSServerBot

__all__ = ["Player"]


@dataclass
@DataObjectFactory.register()
class Player(DataObject):
    server: Server = field(compare=False)
    id: int = field(compare=False)
    active: bool = field(compare=False)
    side: Side = field(compare=False)
    ucid: str
    banned: bool = field(compare=False, default=False, init=False)
    slot: int = field(compare=False, default=0)
    sub_slot: int = field(compare=False, default=0)
    unit_callsign: str = field(compare=False, default='')
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
        from services import BotService

        super().__post_init__()
        self.bot = ServiceRegistry.get(BotService).bot
        if self.id == 1:
            self.active = False
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    cursor.execute("""
                        SELECT p.discord_id, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, 
                               p.manual, c.coalition, p.watchlist, p.vip 
                        FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                        LEFT OUTER JOIN coalitions c ON p.ucid = c.player_ucid 
                        WHERE p.ucid = %s 
                        AND COALESCE(b.banned_until, (now() AT TIME ZONE 'utc')) >= (now() AT TIME ZONE 'utc')
                    """, (self.ucid, ))
                    # existing member found?
                    if cursor.rowcount == 1:
                        row = cursor.fetchone()
                        if row[0] != -1:
                            self._member = self.bot.guilds[0].get_member(row[0])
                            self._verified = row[2]
                        self.banned = row[1]
                        if row[3]:
                            self.coalition = Coalition(row[3])
                        self._watchlist = row[4]
                        self._vip = row[5]
                    cursor.execute("""
                        INSERT INTO players (ucid, discord_id, name, last_seen) 
                        VALUES (%s, -1, %s, (now() AT TIME ZONE 'utc')) 
                        ON CONFLICT (ucid) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen
                        """, (self.ucid, self.name))
        # if automatch is enabled, try to match the user
        if not self.member and self.bot.locals.get('automatch', True):
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
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute('UPDATE players SET discord_id = %s WHERE ucid = %s',
                                 (member.id if member else -1, self.ucid))
            self._member = member

    @property
    def verified(self) -> bool:
        return self._verified

    @verified.setter
    def verified(self, verified: bool) -> None:
        if verified == self._verified:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET manual = %s WHERE ucid = %s', (verified, self.ucid))
                if verified:
                    # delete all old automated links (this will delete the token also)
                    conn.execute("DELETE FROM players WHERE ucid = %s AND manual = FALSE", (self.ucid,))
                    conn.execute("UPDATE players SET discord_id = -1 WHERE discord_id = %s AND manual = FALSE",
                                 (self.member.id,))
        self._verified = verified

    @property
    def watchlist(self) -> bool:
        return self._watchlist

    @watchlist.setter
    def watchlist(self, watchlist: bool):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET watchlist = %s WHERE ucid = %s', (watchlist, self.ucid))
        self._watchlist = watchlist

    @property
    def vip(self) -> bool:
        return self._vip

    @vip.setter
    def vip(self, vip: bool):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET vip = %s WHERE ucid = %s', (vip, self.ucid))
        self._vip = vip

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
        self.server.send_to_dcs({
            "command": "setUserCoalition",
            "ucid": self.ucid,
            "coalition": side.value
        })

    @property
    def display_name(self) -> str:
        return utils.escape_string(self.name)

    def update(self, data: dict):
        with self.pool.connection() as conn:
            with conn.transaction():
                if 'id' in data:
                    # if the ID has changed (due to reconnect), we need to update the server list
                    if self.id != data['id']:
                        del self.server.players[self.id]
                        self.server.players[data['id']] = self
                        self.id = data['id']
                if 'active' in data:
                    self.active = data['active']
                if 'name' in data and self.name != data['name']:
                    self.name = data['name']
                    conn.execute('UPDATE players SET name = %s WHERE ucid = %s', (self.name, self.ucid))
                if 'side' in data:
                    self.side = Side(data['side'])
                if 'slot' in data:
                    self.slot = int(data['slot'])
                if 'sub_slot' in data:
                    self.sub_slot = data['sub_slot']
                if 'unit_callsign' in data:
                    self.unit_callsign = data['unit_callsign']
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
                conn.execute("""
                    UPDATE players SET last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE ucid = %s
                """, (self.ucid, ))

    def has_discord_roles(self, roles: list[str]) -> bool:
        valid_roles = []
        for role in roles:
            valid_roles.extend(self.bot.roles[role])
        return self.verified and self._member is not None and utils.check_roles(set(valid_roles), self._member)

    def sendChatMessage(self, message: str, sender: str = None):
        for msg in message.split('\n'):
            self.server.send_to_dcs({
                "command": "sendChatMessage",
                "to": self.id,
                "from": sender,
                "message": msg
            })

    def sendUserMessage(self, message: str, timeout: Optional[int] = -1):
        [self.sendChatMessage(msg) for msg in message.splitlines()]
        self.sendPopupMessage(message, timeout)

    def sendPopupMessage(self, message: str, timeout: Optional[int] = -1, sender: str = None):
        if timeout == -1:
            timeout = self.server.locals.get('message_timeout', 10)
        self.server.send_to_dcs({
                "command": "sendPopupMessage",
                "from": sender,
                "to": "unit",
                "id": self.unit_name,
                "message": message,
                "time": timeout
        })

    def playSound(self, sound: str):
        self.server.send_to_dcs({
            "command": "playSound",
            "to": "unit",
            "id": self.unit_name,
            "sound": sound
        })

    async def add_role(self, role: Union[str, int]):
        if not self.member or not role:
            return
        try:
            await self.member.add_roles(self.bot.get_role(role))
        except discord.Forbidden:
            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)

    async def remove_role(self, role: Union[str, int]):
        if not self.member or not role:
            return
        try:
            await self.member.remove_roles(self.bot.get_role(role))
        except discord.Forbidden:
            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
