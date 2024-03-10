from __future__ import annotations
import discord

from core import DataObjectFactory, DataObject, utils
from dataclasses import dataclass, field
from typing import Optional


__all__ = ["Member"]


@dataclass
@DataObjectFactory.register()
class Member(DataObject):
    member: discord.Member
    _ucid: Optional[str] = field(default=None, init=False)
    banned: bool = field(default=False, init=False)
    _verified: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT p.ucid, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, manual 
                FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                WHERE p.discord_id = %s AND p.name IS NOT NULL 
                AND COALESCE(b.banned_until, now() AT TIME ZONE 'utc') >= (now() AT TIME ZONE 'utc')
                ORDER BY manual DESC LIMIT 1
            """, (self.member.id, )).fetchone()
            if row:
                self._ucid = row[0] if row[0] and utils.is_ucid(row[0]) else None
                self.banned = row[1] is True
                self._verified = row[2]

    @property
    def ucid(self) -> str:
        return self._ucid

    @ucid.setter
    def ucid(self, ucid: str):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (self.member.id, ucid))
                conn.execute('UPDATE players SET discord_id = -1 WHERE ucid = %s AND discord_id = %s',
                             (self._ucid, self.member.id))
        self._ucid = ucid

    @property
    def verified(self) -> bool:
        return self._verified

    @verified.setter
    def verified(self, flag: bool):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET manual = %s WHERE ucid = %s', (flag, self._ucid))
        self._verified = flag

    async def link(self, ucid: str, verified: bool = True):
        self._verified = verified
        self._ucid = ucid
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                                   (self.member.id, verified, ucid))

    async def unlink(self, ucid):
        self._ucid = None
        self._verified = False
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
