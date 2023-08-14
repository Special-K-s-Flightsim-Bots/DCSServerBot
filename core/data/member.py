import discord
import psycopg2
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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT p.ucid, CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, manual 
                    FROM players p LEFT OUTER JOIN bans b ON p.ucid = b.ucid 
                    WHERE p.discord_id = %s AND COALESCE(b.banned_until, NOW()) >= NOW()
                """, (self.member.id, ))
                banned = False
                for row in cursor.fetchall():
                    self.ucids[row[0]] = row[2]
                    if row[1] is True:
                        banned = True
                self.banned = banned
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @property
    def verified(self):
        for verified in self.ucids.values():
            if not verified:
                return False
        return True

    @verified.setter
    def verified(self, flag: bool):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                ucids = list(self.ucids.keys())
                for ucid in ucids:
                    cursor.execute('UPDATE players SET manual = %s WHERE ucid = %s', (flag, ucid))
                    self.ucids[ucid] = flag
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def link(self, ucid: str, validated: bool = True):
        self.ucids[ucid] = validated
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                               (self.member.id, validated, ucid))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    def unlink(self, ucid):
        if ucid not in self.ucids:
            return
        del self.ucids[ucid]
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
