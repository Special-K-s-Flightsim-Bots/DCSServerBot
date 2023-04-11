import discord
from contextlib import closing
from core import DataObjectFactory, DataObject
from dataclasses import dataclass, field


@dataclass
@DataObjectFactory.register("Member")
class Member(DataObject):
    member: discord.Member
    ucids: dict[str] = field(default_factory=dict)
    banned: bool = field(default=False, init=False)

    def __post_init__(self):
        super().__post_init__()
        with self.pool.connection() as conn:
            banned = False
            for row in conn.execute('SELECT p.ucid, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, '
                                    'manual FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid WHERE '
                                    'p.discord_id = %s', (self.member.id, )).fetchall():
                self.ucids[row[0]] = row[2]
                if row[1] is True:
                    banned = True
            self.banned = banned

    @property
    def verified(self):
        for verified in self.ucids.values():
            if not verified:
                return False
        return True

    @verified.setter
    def verified(self, flag: bool):
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    ucids = list(self.ucids.keys())
                    for ucid in ucids:
                        cursor.execute('UPDATE players SET manual = %s WHERE ucid = %s', (flag, ucid))
                        self.ucids[ucid] = flag

    def link(self, ucid: str, validated: bool = True):
        self.ucids[ucid] = validated
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                             (self.member.id, validated, ucid))

    def unlink(self, ucid):
        if ucid not in self.ucids:
            return
        del self.ucids[ucid]
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
