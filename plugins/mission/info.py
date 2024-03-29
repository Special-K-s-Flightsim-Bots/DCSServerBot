import discord

from core import report, Side, Player, DataObjectFactory, Member, utils
from datetime import datetime, timezone
from typing import Union, Optional
from psycopg.rows import dict_row


class Header(report.EmbedElement):

    def add_datetime_field(self, name: str, time_obj: datetime):
        if time_obj != datetime(1970, 1, 1):
            if time_obj.year == 9999:
                value = 'never'
            else:
                value = f'<t:{int(time_obj.timestamp())}:R>\n({time_obj.strftime("%y-%m-%d %H:%Mz")})'
            self.add_field(name=f'{name}:', value=value)

    async def render(self, member: Union[discord.Member, str]):
        sql = """
            SELECT p.first_seen, p.last_seen, 
                   CASE WHEN p.ucid = b.ucid THEN 1 ELSE 0 END AS banned, b.reason, b.banned_by, b.banned_until,
                   p.watchlist, p.vip
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
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()
                if not rows:
                    self.embed.description = 'User "{}" is not linked or unknown.'.format(
                        utils.escape_string(member if isinstance(member, str) else member.display_name)
                    )
                    # do we maybe have an permanent ban without a user?
                    if isinstance(member, str) and utils.is_ucid(member):
                        await cursor.execute("""
                                                    SELECT 1 as banned, reason, banned_by, banned_until 
                                                    FROM bans WHERE ucid = %s
                                                """, (member,))
                        rows = await cursor.fetchall()
                        if not rows:
                            return
        self.embed.description = f'Information about '
        if isinstance(member, discord.Member):
            self.embed.description += 'member **{}**:'.format(utils.escape_string(member.display_name))
            self.add_field(name='Discord ID:', value=member.id)
        else:
            self.embed.description += 'a non-member user:'
        first_seen = datetime(2999, 12, 31)
        last_seen = datetime(1970, 1, 1)
        banned = False
        for row in rows:
            if row.get('first_seen') and row['first_seen'] < first_seen:
                first_seen = row['first_seen']
            if row.get('last_seen') and row['last_seen'] > last_seen:
                last_seen = row['last_seen']
            if row['banned'] == 1:
                banned = True
        else:
            first_seen = last_seen = None
        if first_seen and last_seen:
            self.add_datetime_field('Last seen', last_seen.replace(tzinfo=timezone.utc))
            self.add_datetime_field('First seen', first_seen.replace(tzinfo=timezone.utc))
        if rows:
            if rows[0]['watchlist']:
                self.add_field(name='Watchlist', value="🔍")
            if rows[0]['vip']:
                self.add_field(name="VIP", value="⭐")
            if banned:
                banned_until = rows[0]['banned_until']
                if banned_until.year != 9999:
                    banned_until = banned_until
                self.add_datetime_field('Ban expires', banned_until.replace(tzinfo=timezone.utc))
                self.add_field(name='Banned by', value=rows[0]['banned_by'])
                self.add_field(name='Reason', value=rows[0]['reason'])
        else:
            self.add_field(name="Link status", value="Unlinked")


class UCIDs(report.EmbedElement):
    async def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT p.ucid, p.manual, COALESCE(p.name, \'?\') AS name FROM players p WHERE p.discord_id = '
        if isinstance(member, str):
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR LOWER(p.name) ILIKE '{member.casefold()}' "
        else:
            sql += f"'{member.id}'"
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()
        if rows:
            self.add_field(name='▬' * 13 + ' Connected UCIDs ' + '▬' * 12, value='_ _', inline=False)
            self.add_field(name='UCID', value='\n'.join([row['ucid'] for row in rows]))
            self.add_field(name='DCS Name', value='\n'.join([utils.escape_string(row['name']) for row in rows]))
            if isinstance(member, discord.Member):
                self.add_field(name='Validated', value='\n'.join(
                    ['Approved' if row['manual'] is True else 'Not Approved' for row in rows]))


class History(report.EmbedElement):
    async def render(self, member: Union[discord.Member, str]):
        sql = 'SELECT name, max(time) AS time FROM players_hist p WHERE p.ucid '
        if isinstance(member, discord.Member):
            sql += f"IN (SELECT ucid FROM players WHERE discord_id = {member.id})"
        else:
            sql += f"= '{member}'"
        sql += ' GROUP BY name ORDER BY time DESC LIMIT 10'
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()
        if rows:
            self.add_field(name='▬' * 13 + ' Change History ' + '▬' * 13, value='_ _', inline=False)
            self.add_field(name='DCS Name', value='\n'.join([
                utils.escape_string(row['name'] or 'n/a') for row in rows
            ]))
            self.add_field(name='Time (UTC)', value='\n'.join([
                f'{row["time"].replace(tzinfo=timezone.utc).strftime("%y-%m-%d %H:%Mz")} / '
                f'<t:{int(row["time"].replace(tzinfo=timezone.utc).timestamp())}:R>' for row in rows
            ]))
            self.add_field(name='_ _', value='_ _')


class ServerInfo(report.EmbedElement):
    async def render(self, member: Union[discord.Member, str], player: Optional[Player]):
        if player:
            self.add_field(name='▬' * 13 + ' Current Activity ' + '▬' * 13, value='_ _', inline=False)
            self.add_field(name='Active on Server', value=player.server.display_name)
            self.add_field(name='DCS Name', value=player.display_name)
            self.add_field(name='Slot', value=player.unit_type if player.side != Side.SPECTATOR else 'Spectator')


class Footer(report.EmbedElement):
    async def render(self, member: Union[discord.Member, str], banned: bool, watchlist: bool, player: Optional[Player]):
        footer = ''
        if isinstance(member, discord.Member):
            _member = DataObjectFactory().new(Member, name=member.name, node=self.node, member=member)
            if _member.ucid:
                footer += '🔀 Unlink their DCS-account\n'
                if not _member.verified:
                    footer += '💯 Verify their DCS-link\n'
        else:
            _member = None
        if not _member or _member.ucid:
            footer += '✅ Unban them\n' if banned else '⛔ Ban them (DCS only)\n'
            footer += '🆓 Unwatch them\n' if watchlist else '🔍 Put them on the watchlist\n'
            if player:
                footer += f'⏏️ Kick them from {player.server.name}'
        self.embed.set_footer(text=footer)
