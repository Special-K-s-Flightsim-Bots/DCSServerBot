from __future__ import annotations
import discord
import psycopg2
from contextlib import closing
from core import utils
from core.data.dataobject import DataObject, DataObjectFactory
from core.data.const import Side, Coalition
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .server import Server


@dataclass
@DataObjectFactory.register("Player")
class Player(DataObject):
    server: Server = field(compare=False)
    id: int = field(compare=False)
    name: str = field(compare=False)
    active: bool = field(compare=False)
    side: Side = field(compare=False)
    ucid: str
    ipaddr: str = field(compare=False)
    banned: bool = field(compare=False)
    slot: str = field(compare=False, default='')
    sub_slot: int = field(compare=False, default=0)
    unit_callsign: str = field(compare=False, default='')
    unit_name: str = field(compare=False, default='')
    unit_type: str = field(compare=False, default='')
    group_id: int = field(compare=False, default=0)
    group_name: str = field(compare=False, default='')
    _member: discord.Member = field(compare=False, repr=False, default=None, init=False)
    coalition: Coalition = field(compare=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        if self.id == 1:
            self.active = False
            return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT p.discord_id, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned '
                               'FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid WHERE p.ucid = %s',
                               (self.ucid, ))
                # existing member found?
                if cursor.rowcount == 1:
                    row = cursor.fetchone()
                    if row[0] != -1:
                        self.member = self.bot.guilds[0].get_member(row[0])
                    self.banned = row[1]
                    cursor.execute('UPDATE players SET name = %s, ipaddr = %s, last_seen = NOW() WHERE ucid = %s',
                                   (self.name, self.ipaddr, self.ucid))
                # no, add a new player
                else:
                    cursor.execute('INSERT INTO players (ucid, name, ipaddr, last_seen) VALUES (%s, %s, %s, NOW())',
                                   (self.ucid, self.name, self.ipaddr))
                # if automatch is enabled, try to match the user
                if not self.member and self.bot.config.getboolean('BOT', 'AUTOMATCH'):
                    discord_user = self.bot.match_user({"ucid": self.ucid, "name": self.name})
                    if discord_user:
                        self.member = discord_user
                        cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s',
                                       (discord_user.id, self.ucid))
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def is_active(self):
        return self.active

    def is_multicrew(self):
        return self.sub_slot != 0

    def is_banned(self):
        return self.banned

    @property
    def member(self) -> discord.Member:
        return self._member

    @member.setter
    def member(self, member: discord.Member):
        self._member = member
        roles = [x.name for x in member.roles]
        self.server.sendtoDCS({
            'command': 'uploadUserRoles',
            'id': self.id,
            'ucid': self.ucid,
            'roles': roles
        })

    def update(self, data: dict):
        if 'side' in data:
            self.side = Side(data['side'])
        if 'slot' in data:
            self.slot = data['slot']
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

    def has_discord_roles(self, roles: list[str]) -> bool:
        return self.member is not None and utils.check_roles(roles, self.member)

    def sendChatMessage(self, message: str, sender: str = None):
        self.server.sendtoDCS({
            "command": "sendChatMessage",
            "to": self.id,
            "from": sender,
            "message": message
        })

    def sendUserMessage(self, message: str, timeout: Optional[int] = -1):
        if self.side == Side.SPECTATOR:
            [self.sendChatMessage(msg) for msg in message.splitlines()]
        else:
            self.sendPopupMessage(message, timeout)

    def sendPopupMessage(self, message: str, timeout: Optional[int] = -1, sender: str = None):
        if timeout == -1:
            timeout = self.bot.config['BOT']['MESSAGE_TIMEOUT']
        self.server.sendtoDCS({
            "command": "sendPopupMessage",
            "to": self.slot,
            "from": sender,
            "message": message,
            "time": timeout
        })
