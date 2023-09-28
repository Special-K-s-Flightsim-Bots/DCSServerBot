import discord
from contextlib import closing
from core import report, Side, Player, DataObjectFactory, Member, utils
from datetime import datetime, timezone
from typing import Union, Optional
from psycopg.rows import dict_row


class Header(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        sql = """
            SELECT p.last_seen, 
                   CASE WHEN p.ucid = b.ucid THEN 1 ELSE 0 END AS banned, b.reason, b.banned_by, b.banned_until
            FROM players p 
            LEFT OUTER JOIN bans b ON (b.ucid = p.ucid) 
            WHERE p.discord_id = 
        """
        if isinstance(member, str):
            sql += f"""
                (
                    SELECT discord_id 
                    FROM players 
                    WHERE ucid = '{member}' AND discord_id != -1
                ) 
                OR p.ucid = '{member}'
            """
        else:
            sql += f"'{member.id}'"
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                rows = cursor.execute(sql).fetchall()
                if not rows:
                    self.embed.description = 'User "{}" is not linked or unknown.'.format(
                        utils.escape_string(member if isinstance(member, str) else member.display_name)
                    )
                    if isinstance(member, str) and utils.is_ucid(member):
                        rows = cursor.execute("""
                            SELECT 1 as banned, reason, banned_by, banned_until 
                            FROM bans WHERE ucid = %s
                        """, (member, )).fetchall()
                        if not rows:
                            return
        self.embed.description = f'Information about '
        if isinstance(member, discord.Member):
            self.embed.description += 'member **{}**:'.format(utils.escape_string(member.display_name))
            self.add_field(name='Discord ID:', value=member.id)
        else:
            self.embed.description += 'a non-member user:'
        last_seen = datetime(1970, 1, 1, tzinfo=timezone.utc)
        banned = False
        for row in rows:
            if row.get('last_seen') and row['last_seen'].astimezone(timezone.utc) > last_seen:
                last_seen = row['last_seen'].astimezone(timezone.utc)
            if row['banned'] == 1:
                banned = True
        if last_seen != datetime(1970, 1, 1):
            self.add_field(name='Last seen (UTC):', value=last_seen.strftime("%m/%d/%Y, %H:%M:%S"))
        if banned:
            if rows[0]['banned_until'].year == 9999:
                until = 'never'
            else:
                until = rows[0]['banned_until'].astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')
            self.add_field(name='Status', value='Banned')
            self.add_field(name='Ban expires (UTC)', value=until)
            self.add_field(name='Banned by', value=rows[0]['banned_by'])
            self.add_field(name='Reason', value=rows[0]['reason'])


class UCIDs(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT p.ucid, p.manual, COALESCE(p.name, \'?\') AS name FROM players p WHERE p.discord_id = '
        if isinstance(member, str):
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR LOWER(p.name) ILIKE '{member.casefold()}' "
        else:
            sql += f"'{member.id}'"
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                rows = cursor.execute(sql).fetchall()
                if not rows:
                    return
                self.add_field(name='▬' * 13 + ' Connected UCIDs ' + '▬' * 12, value='_ _', inline=False)
                self.add_field(name='UCID', value='\n'.join([row['ucid'] for row in rows]))
                self.add_field(name='DCS Name', value='\n'.join([utils.escape_string(row['name']) for row in rows]))
                if isinstance(member, discord.Member):
                    self.add_field(name='Validated', value='\n'.join(
                        ['Approved' if row['manual'] is True else 'Not Approved' for row in rows]))


class History(report.EmbedElement):
    def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT name, max(time) AS time FROM players_hist p WHERE p.ucid '
        if isinstance(member, discord.Member):
            sql += f"IN (SELECT ucid FROM players WHERE discord_id = {member.id})"
        else:
            sql += f"= '{member}'"
        sql += ' GROUP BY name ORDER BY time DESC LIMIT 10'
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                rows = cursor.execute(sql).fetchall()
                if not rows:
                    return
                self.add_field(name='▬' * 13 + ' Change History ' + '▬' * 13, value='_ _', inline=False)
                self.add_field(name='DCS Name', value='\n'.join([utils.escape_string(row['name'] or 'n/a') for row in rows]))
                self.add_field(name='Time (UTC)', value='\n'.join([f"{row['time'].astimezone(timezone.utc):%y-%m-%d %H:%M:%S}" for row in rows]))
                self.add_field(name='_ _', value='_ _')


class ServerInfo(report.EmbedElement):
    def render(self, member: Union[discord.Member, str], player: Optional[Player]):
        if player:
            self.add_field(name='▬' * 13 + ' Current Activity ' + '▬' * 13, value='_ _', inline=False)
            self.add_field(name='Active on Server', value=player.server.display_name)
            self.add_field(name='DCS Name', value=player.display_name)
            self.add_field(name='Slot', value=player.unit_type if player.side != Side.SPECTATOR else 'Spectator')


class Footer(report.EmbedElement):
    def render(self, member: Union[discord.Member, str], banned: bool, player: Optional[Player]):
        footer = ''
        if isinstance(member, discord.Member):
            _member: Member = DataObjectFactory().new('Member', node=self.bot.node, member=member)
            if len(_member.ucids):
                footer += '🔀 Unlink all DCS players from this user\n'
                if not _member.verified:
                    footer += '💯 Verify this DCS link\n'
        footer += '✅ Unban this user\n' if banned else '⛔ Ban this user (DCS only)\n'
        if player:
            footer += '⏏️ Kick this user from the active server'
        footer += '⏹️Cancel'
        self.embed.set_footer(text=footer)
