import discord

from core import report, Side, Player, DataObjectFactory, Member, utils
from datetime import datetime, timezone
from psycopg.rows import dict_row
from plugins.srs.commands import SRS
from typing import cast


class Header(report.EmbedElement):

    async def render(self, member: discord.Member | str):
        sql = """
            SELECT p.first_seen, p.last_seen, 
                   CASE WHEN b.ucid IS NOT NULL THEN TRUE ELSE FALSE END AS banned, b.reason as ban_reason, b.banned_by, 
                   b.banned_until,
                   CASE WHEN w.player_ucid IS NOT NULL THEN TRUE ELSE FALSE END AS watchlist, w.reason as watch_reason, 
                   w.created_by, w.created_at, p.vip
            FROM players p 
            LEFT OUTER JOIN bans b ON (b.ucid = p.ucid AND b.banned_until > NOW() AT TIME ZONE 'utc') 
            LEFT OUTER JOIN watchlist w ON (w.player_ucid = p.ucid)
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
                    # do we maybe have a permanent ban without a user?
                    if isinstance(member, str) and utils.is_ucid(member):
                        await cursor.execute("""
                            SELECT TRUE as banned, b.reason AS ban_reason, b.banned_by, b.banned_until, 
                                   CASE WHEN w.player_ucid IS NOT NULL THEN TRUE ELSE FALSE END AS watchlist, 
                                   w.reason as watch_reason, w.created_by, w.created_at, FALSE as vip 
                            FROM bans b LEFT OUTER JOIN watchlist w
                            ON b.ucid = w.player_ucid 
                            WHERE ucid = %s AND b.banned_until > NOW() AT TIME ZONE 'utc'
                        """, (member,))
                        rows = await cursor.fetchall()
                        if not rows:
                            return
        self.embed.description = f'Information about '
        if isinstance(member, discord.Member):
            self.embed.description += 'member **{}**:'.format(utils.escape_string(member.display_name))
            self.add_field(name='Discord', value=f"{member.mention}\nID: {member.id}")
        else:
            self.embed.description += 'a non-member user:'
        first_seen = datetime(2999, 12, 31)
        last_seen = datetime(1970, 1, 1)
        banned = watchlist = False
        if not rows:
            first_seen = last_seen = None
        else:
            for row in rows:
                if row.get('first_seen') and row['first_seen'] < first_seen:
                    first_seen = row['first_seen']
                if row.get('last_seen') and row['last_seen'] > last_seen:
                    last_seen = row['last_seen']
                banned = row['banned'] or banned
                watchlist = row['watchlist'] or watchlist
        if first_seen < datetime(2999, 12, 31) and last_seen > datetime(1970, 1, 1):
            self.add_datetime_field('Last seen', last_seen.replace(tzinfo=timezone.utc))
            self.add_datetime_field('First seen', first_seen.replace(tzinfo=timezone.utc))
        if rows:
            if rows[0]['vip']:
                self.add_field(name="VIP", value="⭐")
            if banned or watchlist:
                self.add_field(name='▬' * 13 + ' Bans & Watches ' + '▬' * 13, value='_ _', inline=False)
                if banned:
                    banned_until = rows[0]['banned_until']
                    if banned_until.year != 9999:
                        banned_until = banned_until
                    self.add_field(name='Reason', value=rows[0]['ban_reason'])
                    self.add_field(name='Banned by', value=rows[0]['banned_by'])
                    self.add_datetime_field('Ban expires', banned_until.replace(tzinfo=timezone.utc))
                if watchlist:
                    self.add_field(name='Reason', value=rows[0]['watch_reason'])
                    self.add_field(name='Watched by', value=rows[0]['created_by'])
                    self.add_datetime_field('Watched at', rows[0]['created_at'].replace(tzinfo=timezone.utc))
        else:
            self.add_field(name="Link status", value="Unlinked")


class UCIDs(report.EmbedElement):
    async def render(self, member: discord.Member | str):
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
    async def render(self, member: discord.Member | str):
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


class ServerInfo(report.EmbedElement):
    async def render(self, member: discord.Member | str, player: Player | None):
        if player:
            self.add_field(name='▬' * 13 + ' Current Activity ' + '▬' * 13, value='_ _', inline=False)
            self.add_field(name='Active on Server', value=player.server.display_name)
            self.add_field(name='DCS Name', value=player.display_name)
            self.add_field(name='Slot', value=player.unit_type if player.side != Side.NEUTRAL else 'Spectator')


class Footer(report.EmbedElement):
    async def render(self, member: discord.Member | str, banned: bool, watchlist: bool, player: Player | None):
        self.add_field(name='▬' * 33, value='_ _', inline=False)
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


class PlayerInfo(report.EmbedElement):
    async def render(self, player: Player):
        self.add_field(name="DCS-Name", value=player.display_name)
        if player.member and player.verified:
            self.add_field(name="Discord", value=f"<@{player.member.id}>")
        else:
            self.add_field(name='Not Linked', value='_ _')
        self.add_field(name='_ _', value='_ _')

        self.add_field(name="Server", value=player.server.display_name)
        self.add_field(name="Side",
                       value='Blue' if player.side == Side.BLUE else 'Red' if player.side == Side.RED else '_ _')
        if player.slot != -1:
            self.add_field(name="Slot", value=player.unit_callsign)

            self.add_field(name="Module", value=player.unit_display_name)
            srs_plugin = cast(SRS, self.bot.cogs.get('SRS'))
            if srs_plugin:
                srs_users = srs_plugin.eventlistener.srs_users.get(player.server.name, {})
                if player.name in srs_users:
                    radios = srs_users[player.name].get('radios', [])
                    self.add_field(name="Radios", value='\n'.join([utils.format_frequency(x) for x in radios]))
                else:
                    self.add_field(name="Radios", value='n/a')
            else:
                self.add_field(name='_ _', value='_ _')
            self.add_field(name='_ _', value='_ _')
        else:
            self.add_field(name="Slot", value="Spectator")

