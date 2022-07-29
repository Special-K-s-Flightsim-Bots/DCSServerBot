import discord
import psycopg2
from contextlib import closing
from core import report, Side
from datetime import datetime
from typing import Union


class Header(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT p.last_seen, CASE WHEN p.ucid = b.ucid THEN 1 ELSE 0 END AS banned ' \
              'FROM players p LEFT OUTER JOIN bans b ON (b.ucid = p.ucid) WHERE p.discord_id = '
        if isinstance(member, str):
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR LOWER(p.name) = '{member.casefold()}' "
        else:
            sql += f"'{member.id}'"
        sql += ' GROUP BY p.ucid, b.ucid'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql)
                if cursor.rowcount == 0:
                    self.embed.description = f'No data found for user "{member if isinstance(member, str) else member.display_name}".'
                    return
                rows = list(cursor.fetchall())
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            raise
        finally:
            self.bot.pool.putconn(conn)
        self.embed.description = f'Information about '
        if isinstance(member, discord.Member):
            self.embed.description += f'member **{member.display_name}**:'
            self.embed.add_field(name='Discord ID:', value=member.id)
        else:
            self.embed.description += 'a non-member user:'
        last_seen = datetime(1970, 1, 1)
        banned = False
        for row in rows:
            if row['last_seen'] and row['last_seen'] > last_seen:
                last_seen = row['last_seen']
            if row['banned'] == 1:
                banned = True
        if last_seen != datetime(1970, 1, 1):
            self.embed.add_field(name='Last seen:', value=last_seen.strftime("%m/%d/%Y, %H:%M:%S"))
        if banned:
            self.embed.add_field(name='Status', value='Banned')
        self.embed.add_field(name='▬' * 32, value='_ _', inline=False)


class UCIDs(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT p.ucid, p.manual, COALESCE(p.name, \'?\') AS name FROM players p WHERE p.discord_id = '
        if isinstance(member, str):
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR LOWER(p.name) = '{member.casefold()}' "
        else:
            sql += f"'{member.id}'"
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql)
                if not cursor.rowcount:
                    return
                rows = list(cursor.fetchall())
                self.embed.add_field(name='UCID', value='\n'.join([row['ucid'] for row in rows]))
                self.embed.add_field(name='DCS Name', value='\n'.join([row['name'] for row in rows]))
                self.embed.add_field(name='Validated', value='\n'.join(
                    ['Approved' if row['manual'] is True else 'Not Approved' for row in rows]))
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            raise
        finally:
            self.bot.pool.putconn(conn)
        self.embed.add_field(name='▬' * 32, value='_ _', inline=False)


class History(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                sql = 'SELECT name, ipaddr, max(time) AS time FROM players_hist WHERE discord_id = '
                if isinstance(member, str):
                    sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                           f"p.ucid = '{member}' OR LOWER(p.name) = '{member.casefold()}' "
                else:
                    sql += f"'{member.id}'"
                sql += ' GROUP BY name, ipaddr ORDER BY time DESC LIMIT 10'
                cursor.execute(sql)
                if not cursor.rowcount:
                    return
                rows = cursor.fetchall()
                self.embed.add_field(name='DCS Name', value='\n'.join([row['name'] for row in rows]))
                self.embed.add_field(name='IP Addr', value='\n'.join([row['ipaddr'] for row in rows]))
                self.embed.add_field(name='Time', value='\n'.join([f"{row['time']:%y-%m-%d %H:%M:%S}" for row in rows]))
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            raise
        finally:
            self.bot.pool.putconn(conn)
        self.embed.add_field(name='▬' * 32, value='_ _', inline=False)


class ServerInfo(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        player = None
        for server in self.bot.servers.values():
            if isinstance(member, discord.Member):
                player = server.get_player(discord_id=member.id)
                if player:
                    break
            else:
                player = server.get_player(ucid=member)
                if player:
                    break
        if player:
            self.embed.add_field(name='Active on Server', value=server.name)
            self.embed.add_field(name='DCS Name', value=player.name)
            self.embed.add_field(name='Slot', value=player.unit_type if player.side != Side.SPECTATOR else 'Spectator')
