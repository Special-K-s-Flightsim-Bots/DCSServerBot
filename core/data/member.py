from __future__ import annotations
import discord
from core import DataObjectFactory, DataObject
from core.services.registry import ServiceRegistry
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services import DCSServerBot

__all__ = ["Member"]


@dataclass
@DataObjectFactory.register("Member")
class Member(DataObject):
    member: discord.Member
    ucid: Optional[str] = field(default=None, init=False)
    banned: bool = field(default=False, init=False)
    _verified: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.bot: DCSServerBot = ServiceRegistry.get("Bot")
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT p.ucid, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, manual 
                FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                WHERE p.discord_id = %s AND COALESCE(b.banned_until, NOW()) >= NOW()
            """, (self.member.id, )).fetchone()
            self.ucid = row[0]
            self.banned = row[1] is True
            self._verified = row[2]

    @property
    def verified(self):
        return self._verified

    @verified.setter
    def verified(self, flag: bool):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET manual = %s WHERE ucid = %s', (flag, self.ucid))
        self._verified = flag

    def link(self, ucid: str, verified: bool = True):
        self._verified = verified
        self.ucid = ucid
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                             (self.member.id, verified, ucid))

    def unlink(self, ucid):
        self.ucid = None
        self._verified = False
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
