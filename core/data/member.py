from __future__ import annotations
import discord

from core import DataObjectFactory, DataObject
from dataclasses import dataclass, field


__all__ = ["Member"]


@dataclass
@DataObjectFactory.register()
class Member(DataObject):
    member: discord.Member
    _ucid: str | None = field(default=None, init=False)
    banned: bool = field(default=False, init=False)
    _verified: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        self.is_remote = False
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT p.ucid, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, manual 
                FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                WHERE p.discord_id = %s 
                AND COALESCE(b.banned_until, now() AT TIME ZONE 'utc') >= (now() AT TIME ZONE 'utc')
                ORDER BY manual DESC LIMIT 1
            """, (self.member.id, )).fetchone()
            if row:
                self._ucid = row[0]
                self.banned = row[1] is True
                self._verified = row[2]

    @property
    def ucid(self) -> str:
        return self._ucid

    @ucid.setter
    def ucid(self, ucid: str):
        if ucid == self._ucid:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                # if there was an old link, delete it
                if self._ucid:
                    conn.execute('UPDATE players SET discord_id = -1 WHERE ucid = %s AND discord_id = %s',
                                 (self._ucid, self.member.id))
                if ucid:
                    conn.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (self.member.id, ucid))
                self._ucid = ucid

    @property
    def verified(self) -> bool:
        return self._verified

    @verified.setter
    def verified(self, flag: bool):
        if flag == self._verified:
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                # verify the link
                conn.execute('UPDATE players SET manual = %s WHERE ucid = %s', (flag, self._ucid))
                if flag:
                    # delete all old automated links
                    conn.execute("DELETE FROM players WHERE ucid = %s AND manual = FALSE", (self.ucid,))
                    conn.execute("DELETE FROM players WHERE discord_id = %s AND length(ucid) = 4", (self.member.id,))
                    conn.execute("UPDATE players SET discord_id = -1 WHERE discord_id = %s AND manual = FALSE",
                                 (self.member.id,))
        self._verified = flag

    def link(self, ucid: str, verified: bool = True):
        self.ucid = ucid
        self.verified = verified

    def unlink(self):
        self.verified = False
        self.ucid = None
