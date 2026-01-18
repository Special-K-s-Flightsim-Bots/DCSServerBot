import discord
import io
import json
import logging

from core import Plugin, PluginRequiredError, utils, get_translation, Group
from datetime import datetime, timedelta, timezone
from discord import app_commands
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Optional

from .listener import LogbookEventListener
from .utils.ribbon import create_ribbon_rack, HAS_IMAGING

_ = get_translation(__name__.split('.')[1])
log = logging.getLogger(__name__)


# Autocomplete functions
async def logbook_squadron_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for logbook squadrons."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name FROM logbook_squadrons WHERE name ILIKE %s ORDER BY name LIMIT 25",
                ('%' + current + '%',)
            )
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def logbook_squadron_admin_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for squadrons user can administer (CO/XO or DCS Admin)."""
    try:
        async with interaction.client.apool.connection() as conn:
            if not utils.check_roles(interaction.client.roles.get("DCS Admin", []), interaction.user):
                ucid = await interaction.client.get_ucid_by_member(interaction.user)
                if not ucid:
                    return []
                cursor = await conn.execute("""
                    SELECT id, name FROM logbook_squadrons
                    WHERE (co_ucid = %s OR xo_ucid = %s) AND name ILIKE %s
                    ORDER BY name LIMIT 25
                """, (ucid, ucid, '%' + current + '%'))
            else:
                cursor = await conn.execute(
                    "SELECT id, name FROM logbook_squadrons WHERE name ILIKE %s ORDER BY name LIMIT 25",
                    ('%' + current + '%',)
                )
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def squadron_member_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for members of a squadron."""
    try:
        squadron_id = interaction.namespace.squadron
        if not squadron_id:
            return []
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT p.name, sm.player_ucid
                FROM logbook_squadron_members sm
                JOIN players p ON sm.player_ucid = p.ucid
                WHERE sm.squadron_id = %s AND p.name ILIKE %s
                ORDER BY p.name LIMIT 25
            """, (squadron_id, '%' + current + '%'))
            return [
                app_commands.Choice(name=row[0], value=row[1])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def unassigned_player_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for players not in any logbook squadron."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT p.name, p.ucid
                FROM players p
                WHERE p.ucid NOT IN (SELECT player_ucid FROM logbook_squadron_members)
                AND p.name ILIKE %s
                ORDER BY p.name LIMIT 25
            """, ('%' + current + '%',))
            return [
                app_commands.Choice(name=row[0], value=row[1])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def qualification_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for qualifications."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name FROM logbook_qualifications WHERE name ILIKE %s ORDER BY name LIMIT 25",
                ('%' + current + '%',)
            )
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def pilot_qualification_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for qualifications a specific pilot has."""
    try:
        # Get player UCID from the user parameter in the namespace
        user = interaction.namespace.user
        if not user:
            return []
        ucid = await interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT q.id, q.name
                FROM logbook_qualifications q
                JOIN logbook_pilot_qualifications pq ON q.id = pq.qualification_id
                WHERE pq.player_ucid = %s AND q.name ILIKE %s
                ORDER BY q.name LIMIT 25
            """, (ucid, '%' + current + '%'))
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def player_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for all players."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT name, ucid FROM players
                WHERE name ILIKE %s
                ORDER BY name LIMIT 25
            """, ('%' + current + '%',))
            return [
                app_commands.Choice(name=row[0], value=row[1])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def award_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for awards."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, name FROM logbook_awards WHERE name ILIKE %s ORDER BY name LIMIT 25",
                ('%' + current + '%',)
            )
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def pilot_award_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for awards a specific pilot has."""
    try:
        user = interaction.namespace.user
        if not user:
            return []
        ucid = await interaction.client.get_ucid_by_member(user)
        if not ucid:
            return []
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT DISTINCT a.id, a.name
                FROM logbook_awards a
                JOIN logbook_pilot_awards pa ON a.id = pa.award_id
                WHERE pa.player_ucid = %s AND a.name ILIKE %s
                ORDER BY a.name LIMIT 25
            """, (ucid, '%' + current + '%'))
            return [
                app_commands.Choice(name=row[1], value=row[0])
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def flightplan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for flight plans (user's own active plans)."""
    try:
        ucid = await interaction.client.get_ucid_by_member(interaction.user)
        if not ucid:
            return []
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT id, callsign, departure, destination
                FROM logbook_flight_plans
                WHERE player_ucid = %s AND status IN ('filed', 'active')
                AND (callsign ILIKE %s OR departure ILIKE %s OR destination ILIKE %s)
                ORDER BY filed_at DESC LIMIT 25
            """, (ucid, '%' + current + '%', '%' + current + '%', '%' + current + '%'))
            return [
                app_commands.Choice(
                    name=f"{row[1] or 'N/A'}: {row[2] or '?'} -> {row[3] or '?'}",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


def format_hours(seconds: float) -> str:
    """Format seconds as hours with 1 decimal place."""
    if seconds is None:
        return "0.0h"
    hours = seconds / 3600 if seconds > 100 else seconds  # Handle both seconds and hours
    return f"{hours:.1f}h"


class Logbook(Plugin[LogbookEventListener]):

    # Command group "/logbook"
    logbook = Group(name="logbook", description=_("Commands to display pilot logbook statistics"))

    # Subgroup "/logbook squadron" - avoids conflict with userstats /squadron
    squadron = Group(name="squadron", description=_("Commands to manage logbook squadrons"), parent=logbook)

    # Command group "/qualification"
    qualification = Group(name="qualification", description=_("Commands to manage pilot qualifications"))

    # Command group "/award"
    award = Group(name="award", description=_("Commands to manage pilot awards"))

    # Command group "/flightplan"
    flightplan = Group(name="flightplan", description=_("Commands to manage flight plans"))

    # NOTE: /stores commands have been moved to the logistics plugin

    # ==================== LOGBOOK COMMANDS ====================

    @logbook.command(description=_('Show pilot logbook statistics'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def stats(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            ucid = await self.bot.get_ucid_by_member(interaction.user)
            name = interaction.user.display_name
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name

        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('User is not linked!'), ephemeral=True)
            return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM pilot_logbook_stats WHERE ucid = %s
                """, (ucid,))
                row = await cursor.fetchone()

        if not row:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No logbook data found for this user.'), ephemeral=True)
            return

        embed = discord.Embed(
            title=_('Pilot Logbook'),
            description=_('Statistics for {}').format(utils.escape_string(name)),
            color=discord.Color.blue()
        )

        # Format specific fields nicely
        if row.get('total_hours') is not None:
            embed.add_field(name=_('Total Flight Time'), value=format_hours(row['total_hours']), inline=True)
        if row.get('total_kills') is not None:
            embed.add_field(name=_('Total Kills'), value=str(int(row['total_kills'])), inline=True)
        if row.get('total_deaths') is not None:
            embed.add_field(name=_('Deaths'), value=str(int(row['total_deaths'])), inline=True)
        if row.get('total_takeoffs') is not None:
            embed.add_field(name=_('Takeoffs'), value=str(int(row['total_takeoffs'])), inline=True)
        if row.get('total_landings') is not None:
            embed.add_field(name=_('Landings'), value=str(int(row['total_landings'])), inline=True)
        if row.get('total_ejections') is not None:
            embed.add_field(name=_('Ejections'), value=str(int(row['total_ejections'])), inline=True)
        if row.get('total_crashes') is not None:
            embed.add_field(name=_('Crashes'), value=str(int(row['total_crashes'])), inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @logbook.command(description=_('Show unified pilot information'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def pilot(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        """Display comprehensive pilot info: service, rank, hours, qualifications, awards, and ribbon rack."""
        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            ucid = await self.bot.get_ucid_by_member(interaction.user)
            name = interaction.user.display_name
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name

        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('User is not linked!'), ephemeral=True)
            return

        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get basic stats from pilot_logbook_stats view
                await cursor.execute("""
                    SELECT total_hours FROM pilot_logbook_stats WHERE ucid = %s
                """, (ucid,))
                stats_row = await cursor.fetchone()
                total_hours = stats_row['total_hours'] if stats_row else 0

                # Get last_seen from players table
                await cursor.execute("""
                    SELECT last_seen FROM players WHERE ucid = %s
                """, (ucid,))
                player_row = await cursor.fetchone()
                last_seen = player_row['last_seen'] if player_row else None

                # Get squadron membership (service and rank)
                await cursor.execute("""
                    SELECT s.abbreviation, s.name as squadron_name, m.rank, m.position
                    FROM logbook_squadron_members m
                    JOIN logbook_squadrons s ON m.squadron_id = s.id
                    WHERE m.player_ucid = %s
                    ORDER BY m.joined_at DESC
                    LIMIT 1
                """, (ucid,))
                squadron_row = await cursor.fetchone()

                # Get qualifications
                await cursor.execute("""
                    SELECT q.name, pq.granted_at, pq.expires_at
                    FROM logbook_pilot_qualifications pq
                    JOIN logbook_qualifications q ON pq.qualification_id = q.id
                    WHERE pq.player_ucid = %s
                    ORDER BY pq.granted_at DESC
                """, (ucid,))
                qualifications = await cursor.fetchall()

                # Get awards (with ribbon colors for image generation)
                await cursor.execute("""
                    SELECT a.name, a.ribbon_colors, pa.granted_at
                    FROM logbook_pilot_awards pa
                    JOIN logbook_awards a ON pa.award_id = a.id
                    WHERE pa.player_ucid = %s
                    ORDER BY pa.granted_at DESC
                """, (ucid,))
                awards = await cursor.fetchall()

        # Build embed
        embed = discord.Embed(
            title=_('Pilot Information: {}').format(utils.escape_string(name)),
            color=discord.Color.blue()
        )

        # Service (squadron abbreviation) and Rank
        service = squadron_row['abbreviation'] if squadron_row and squadron_row.get('abbreviation') else '-'
        rank = squadron_row['rank'] if squadron_row and squadron_row.get('rank') else '-'
        embed.add_field(name=_('Service'), value=service, inline=True)
        embed.add_field(name=_('Rank'), value=rank, inline=True)

        # Total Hours
        hours_str = f"{float(total_hours):.1f}" if total_hours else "0.0"
        embed.add_field(name=_('Total Hours'), value=hours_str, inline=True)

        # Last Joined (last_seen)
        if last_seen:
            last_joined_str = last_seen.strftime('%Y-%m-%d')
        else:
            last_joined_str = '-'
        embed.add_field(name=_('Last Joined'), value=last_joined_str, inline=True)

        # Qualifications
        if qualifications:
            qual_lines = []
            for q in qualifications:
                issued = q['granted_at'].strftime('%d %b %y') if q['granted_at'] else '-'
                if q.get('expires_at'):
                    expires = q['expires_at'].strftime('%d %b %y')
                    qual_lines.append(f"**{q['name']}** (Issued: {issued}, Expires: {expires})")
                else:
                    qual_lines.append(f"**{q['name']}** (Issued: {issued})")
            embed.add_field(name=_('Qualifications'), value='\n'.join(qual_lines), inline=False)
        else:
            embed.add_field(name=_('Qualifications'), value=_('None'), inline=False)

        # Awards
        if awards:
            award_lines = []
            for a in awards:
                issued = a['granted_at'].strftime('%d %b %y') if a['granted_at'] else '-'
                award_lines.append(f"**{a['name']}** (Issued: {issued})")
            embed.add_field(name=_('Awards'), value='\n'.join(award_lines), inline=False)
        else:
            embed.add_field(name=_('Awards'), value=_('None'), inline=False)

        # Generate ribbon rack image if there are awards
        file = None
        if awards and HAS_IMAGING:
            # Build awards list for ribbon rack: (name, colors, count)
            ribbon_awards = []
            for a in awards:
                colors = a.get('ribbon_colors')
                if isinstance(colors, str):
                    import json as json_module
                    try:
                        colors = json_module.loads(colors)
                    except (json_module.JSONDecodeError, TypeError):
                        colors = None
                ribbon_awards.append((a['name'], colors, 1))

            ribbon_bytes = create_ribbon_rack(ribbon_awards)
            if ribbon_bytes:
                file = discord.File(io.BytesIO(ribbon_bytes), filename='ribbons.png')
                embed.set_image(url='attachment://ribbons.png')

        if file:
            await interaction.followup.send(embed=embed, file=file, ephemeral=ephemeral)
        else:
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    # ==================== SQUADRON COMMANDS ====================

    @squadron.command(name='create', description=_('Create a new squadron'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(
        name=_('Squadron name (e.g., "801 NAS")'),
        abbreviation=_('Short abbreviation (e.g., "801")'),
        description=_('Squadron motto or description')
    )
    async def squadron_create(self, interaction: discord.Interaction,
                              name: str,
                              abbreviation: Optional[str] = None,
                              description: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            # Use INSERT ... ON CONFLICT to avoid race condition
            cursor = await conn.execute("""
                INSERT INTO logbook_squadrons (name, abbreviation, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
            """, (name, abbreviation, description))
            result = await cursor.fetchone()

            if not result:
                # Squadron already exists
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('Squadron "{}" already exists!').format(name),
                    ephemeral=True
                )
                return
            squadron_id = result[0]

        embed = discord.Embed(
            title=_('Squadron Created'),
            description=_('Squadron "{}" has been created.').format(name),
            color=discord.Color.green()
        )
        embed.add_field(name=_('ID'), value=str(squadron_id), inline=True)
        if abbreviation:
            embed.add_field(name=_('Abbreviation'), value=abbreviation, inline=True)
        if description:
            embed.add_field(name=_('Description'), value=description, inline=False)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'created squadron {name}', user=interaction.user)

    @squadron.command(name='info', description=_('Show squadron information'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(squadron=logbook_squadron_autocomplete)
    async def squadron_info(self, interaction: discord.Interaction, squadron: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get squadron details
                await cursor.execute("""
                    SELECT s.*,
                           co.name as co_name,
                           xo.name as xo_name,
                           (SELECT COUNT(*) FROM logbook_squadron_members WHERE squadron_id = s.id) as member_count
                    FROM logbook_squadrons s
                    LEFT JOIN players co ON s.co_ucid = co.ucid
                    LEFT JOIN players xo ON s.xo_ucid = xo.ucid
                    WHERE s.id = %s
                """, (squadron,))
                row = await cursor.fetchone()

        if not row:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Squadron not found!'), ephemeral=True)
            return

        embed = discord.Embed(
            title=row['name'],
            description=row.get('description') or _('No description'),
            color=discord.Color.blue()
        )

        if row.get('abbreviation'):
            embed.add_field(name=_('Abbreviation'), value=row['abbreviation'], inline=True)
        embed.add_field(name=_('Members'), value=str(row['member_count']), inline=True)

        if row.get('co_name'):
            embed.add_field(name=_('Commanding Officer'), value=row['co_name'], inline=True)
        if row.get('xo_name'):
            embed.add_field(name=_('Executive Officer'), value=row['xo_name'], inline=True)

        if row.get('logo_url'):
            embed.set_thumbnail(url=row['logo_url'])

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @squadron.command(name='roster', description=_('Show squadron roster'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(squadron=logbook_squadron_autocomplete)
    async def squadron_roster(self, interaction: discord.Interaction, squadron: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get squadron name
                await cursor.execute("SELECT name FROM logbook_squadrons WHERE id = %s", (squadron,))
                squadron_row = await cursor.fetchone()
                if not squadron_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron not found!'), ephemeral=True)
                    return

                # Get roster with stats
                await cursor.execute("""
                    SELECT
                        sm.rank,
                        sm.position,
                        p.name,
                        sm.joined_at,
                        COALESCE(pls.total_hours, 0) as total_hours
                    FROM logbook_squadron_members sm
                    JOIN players p ON sm.player_ucid = p.ucid
                    LEFT JOIN pilot_logbook_stats pls ON sm.player_ucid = pls.ucid
                    WHERE sm.squadron_id = %s
                    ORDER BY sm.rank DESC, sm.joined_at ASC
                """, (squadron,))
                members = await cursor.fetchall()

        if not members:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('Squadron "{}" has no members.').format(squadron_row['name']),
                ephemeral=ephemeral
            )
            return

        embed = discord.Embed(
            title=_('Squadron Roster: {}').format(squadron_row['name']),
            color=discord.Color.blue()
        )

        roster_lines = []
        for member in members:
            rank = member.get('rank') or ''
            name = member['name']
            position = f" ({member['position']})" if member.get('position') else ''
            hours = format_hours(member['total_hours'])
            roster_lines.append(f"**{rank}** {name}{position} - {hours}")

        # Split into chunks if too long
        roster_text = '\n'.join(roster_lines)
        if len(roster_text) > 1024:
            # Truncate and add note
            roster_text = roster_text[:1000] + f"\n... and {len(members) - roster_text.count(chr(10))} more"

        embed.add_field(name=_('Members ({})').format(len(members)), value=roster_text, inline=False)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @squadron.command(name='assign', description=_('Assign a player to a squadron'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_admin_autocomplete)
    @app_commands.describe(
        squadron=_('Squadron to assign to'),
        user=_('Discord user to assign'),
        rank=_('Military rank (e.g., "Lt", "Cdr", "Wg Cdr")'),
        position=_('Position in squadron (e.g., "Pilot", "WSO")')
    )
    async def squadron_assign(self, interaction: discord.Interaction,
                              squadron: int,
                              user: discord.Member,
                              rank: Optional[str] = None,
                              position: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get squadron name
                await cursor.execute("SELECT name FROM logbook_squadrons WHERE id = %s", (squadron,))
                squadron_row = await cursor.fetchone()
                if not squadron_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron not found!'), ephemeral=True)
                    return

                # Check if already assigned
                await cursor.execute("""
                    SELECT squadron_id FROM logbook_squadron_members WHERE player_ucid = %s
                """, (ucid,))
                existing = await cursor.fetchone()

                async with conn.transaction():
                    if existing:
                        # Remove from old squadron
                        await conn.execute(
                            "DELETE FROM logbook_squadron_members WHERE player_ucid = %s",
                            (ucid,)
                        )

                    # Add to new squadron
                    await conn.execute("""
                        INSERT INTO logbook_squadron_members (squadron_id, player_ucid, rank, position)
                        VALUES (%s, %s, %s, %s)
                    """, (squadron, ucid, rank, position))

        embed = discord.Embed(
            title=_('Member Assigned'),
            description=_('{} has been assigned to {}.').format(user.display_name, squadron_row['name']),
            color=discord.Color.green()
        )
        if rank:
            embed.add_field(name=_('Rank'), value=rank, inline=True)
        if position:
            embed.add_field(name=_('Position'), value=position, inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'assigned {user.display_name} to squadron {squadron_row["name"]}',
            user=interaction.user
        )

    @squadron.command(name='remove', description=_('Remove a player from their squadron'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_admin_autocomplete, member=squadron_member_autocomplete)
    @app_commands.describe(
        squadron=_('Squadron to remove from'),
        member=_('Member to remove')
    )
    async def squadron_remove(self, interaction: discord.Interaction,
                              squadron: int,
                              member: str):  # member is UCID from autocomplete
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get squadron and player names
                await cursor.execute("SELECT name FROM logbook_squadrons WHERE id = %s", (squadron,))
                squadron_row = await cursor.fetchone()

                await cursor.execute("SELECT name FROM players WHERE ucid = %s", (member,))
                player_row = await cursor.fetchone()

                if not squadron_row or not player_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron or member not found!'), ephemeral=True)
                    return

                # Remove membership
                await conn.execute("""
                    DELETE FROM logbook_squadron_members
                    WHERE squadron_id = %s AND player_ucid = %s
                """, (squadron, member))

        embed = discord.Embed(
            title=_('Member Removed'),
            description=_('{} has been removed from {}.').format(player_row['name'], squadron_row['name']),
            color=discord.Color.orange()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'removed {player_row["name"]} from squadron {squadron_row["name"]}',
            user=interaction.user
        )

    @squadron.command(name='promote', description=_('Update a member\'s rank'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_admin_autocomplete, member=squadron_member_autocomplete)
    @app_commands.describe(
        squadron=_('Squadron'),
        member=_('Member to promote'),
        rank=_('New rank')
    )
    async def squadron_promote(self, interaction: discord.Interaction,
                               squadron: int,
                               member: str,
                               rank: str):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get player name
                await cursor.execute("SELECT name FROM players WHERE ucid = %s", (member,))
                player_row = await cursor.fetchone()

                if not player_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Member not found!'), ephemeral=True)
                    return

                # Update rank
                await conn.execute("""
                    UPDATE logbook_squadron_members
                    SET rank = %s
                    WHERE squadron_id = %s AND player_ucid = %s
                """, (rank, squadron, member))

        embed = discord.Embed(
            title=_('Rank Updated'),
            description=_('{} has been promoted to {}.').format(player_row['name'], rank),
            color=discord.Color.gold()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'promoted {player_row["name"]} to {rank}',
            user=interaction.user
        )

    @squadron.command(name='setco', description=_('Set squadron commanding officer'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_autocomplete, member=squadron_member_autocomplete)
    async def squadron_setco(self, interaction: discord.Interaction,
                             squadron: int,
                             member: str):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name FROM players WHERE ucid = %s", (member,))
                player_row = await cursor.fetchone()

                await cursor.execute("SELECT name FROM logbook_squadrons WHERE id = %s", (squadron,))
                squadron_row = await cursor.fetchone()

                if not player_row or not squadron_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron or member not found!'), ephemeral=True)
                    return

                await conn.execute(
                    "UPDATE logbook_squadrons SET co_ucid = %s WHERE id = %s",
                    (member, squadron)
                )

        embed = discord.Embed(
            title=_('CO Assigned'),
            description=_('{} is now Commanding Officer of {}.').format(player_row['name'], squadron_row['name']),
            color=discord.Color.gold()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'set {player_row["name"]} as CO of {squadron_row["name"]}',
            user=interaction.user
        )

    @squadron.command(name='setxo', description=_('Set squadron executive officer'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_autocomplete, member=squadron_member_autocomplete)
    async def squadron_setxo(self, interaction: discord.Interaction,
                             squadron: int,
                             member: str):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name FROM players WHERE ucid = %s", (member,))
                player_row = await cursor.fetchone()

                await cursor.execute("SELECT name FROM logbook_squadrons WHERE id = %s", (squadron,))
                squadron_row = await cursor.fetchone()

                if not player_row or not squadron_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron or member not found!'), ephemeral=True)
                    return

                await conn.execute(
                    "UPDATE logbook_squadrons SET xo_ucid = %s WHERE id = %s",
                    (member, squadron)
                )

        embed = discord.Embed(
            title=_('XO Assigned'),
            description=_('{} is now Executive Officer of {}.').format(player_row['name'], squadron_row['name']),
            color=discord.Color.gold()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'set {player_row["name"]} as XO of {squadron_row["name"]}',
            user=interaction.user
        )

    @squadron.command(name='list', description=_('List all squadrons'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def squadron_list(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT s.id, s.name, s.abbreviation,
                           (SELECT COUNT(*) FROM logbook_squadron_members WHERE squadron_id = s.id) as member_count
                    FROM logbook_squadrons s
                    ORDER BY s.name
                """)
                squadrons = await cursor.fetchall()

        if not squadrons:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No squadrons have been created yet.'), ephemeral=ephemeral)
            return

        embed = discord.Embed(
            title=_('Squadrons'),
            description=_('All registered squadrons'),
            color=discord.Color.blue()
        )

        for sq in squadrons:
            abbr = f" ({sq['abbreviation']})" if sq.get('abbreviation') else ""
            embed.add_field(
                name=f"{sq['name']}{abbr}",
                value=_("{} members").format(sq['member_count']),
                inline=True
            )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @squadron.command(name='delete', description=_('Delete a squadron'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(squadron=logbook_squadron_autocomplete)
    async def squadron_delete(self, interaction: discord.Interaction, squadron: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Use DELETE ... RETURNING to atomically delete and get the name
                await cursor.execute(
                    "DELETE FROM logbook_squadrons WHERE id = %s RETURNING name",
                    (squadron,)
                )
                squadron_row = await cursor.fetchone()

                if not squadron_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Squadron not found!'), ephemeral=True)
                    return

        embed = discord.Embed(
            title=_('Squadron Deleted'),
            description=_('Squadron "{}" has been deleted.').format(squadron_row['name']),
            color=discord.Color.red()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'deleted squadron {squadron_row["name"]}', user=interaction.user)

    # ==================== QUALIFICATION COMMANDS ====================

    @qualification.command(name='create', description=_('Create a new qualification'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(
        name=_('Qualification name (e.g., "Carrier Qualified", "Night Vision")'),
        description=_('Description of the qualification'),
        aircraft_type=_('Aircraft type this applies to (leave empty for all)'),
        valid_days=_('Days until expiration (leave empty for permanent)')
    )
    async def qualification_create(self, interaction: discord.Interaction,
                                   name: str,
                                   description: Optional[str] = None,
                                   aircraft_type: Optional[str] = None,
                                   valid_days: Optional[int] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            # Use INSERT ... ON CONFLICT to avoid race condition
            cursor = await conn.execute("""
                INSERT INTO logbook_qualifications (name, description, aircraft_type, valid_days)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
            """, (name, description, aircraft_type, valid_days))
            result = await cursor.fetchone()

            if not result:
                # Qualification already exists
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('Qualification "{}" already exists!').format(name),
                    ephemeral=True
                )
                return
            qual_id = result[0]

        embed = discord.Embed(
            title=_('Qualification Created'),
            description=_('Qualification "{}" has been created.').format(name),
            color=discord.Color.green()
        )
        embed.add_field(name=_('ID'), value=str(qual_id), inline=True)
        if description:
            embed.add_field(name=_('Description'), value=description, inline=False)
        if aircraft_type:
            embed.add_field(name=_('Aircraft Type'), value=aircraft_type, inline=True)
        if valid_days:
            embed.add_field(name=_('Valid for'), value=_("{} days").format(valid_days), inline=True)
        else:
            embed.add_field(name=_('Valid for'), value=_("Permanent"), inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'created qualification {name}', user=interaction.user)

    @qualification.command(name='info', description=_('Show qualification details'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(qualification=qualification_autocomplete)
    async def qualification_info(self, interaction: discord.Interaction, qualification: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT q.*,
                           (SELECT COUNT(*) FROM logbook_pilot_qualifications WHERE qualification_id = q.id) as holder_count
                    FROM logbook_qualifications q
                    WHERE q.id = %s
                """, (qualification,))
                row = await cursor.fetchone()

        if not row:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Qualification not found!'), ephemeral=True)
            return

        embed = discord.Embed(
            title=row['name'],
            description=row.get('description') or _('No description'),
            color=discord.Color.blue()
        )

        if row.get('aircraft_type'):
            embed.add_field(name=_('Aircraft Type'), value=row['aircraft_type'], inline=True)
        if row.get('valid_days'):
            embed.add_field(name=_('Valid for'), value=_("{} days").format(row['valid_days']), inline=True)
        else:
            embed.add_field(name=_('Valid for'), value=_("Permanent"), inline=True)
        embed.add_field(name=_('Holders'), value=str(row['holder_count']), inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @qualification.command(name='grant', description=_('Grant a qualification to a pilot'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(qualification=qualification_autocomplete)
    @app_commands.describe(
        user=_('Discord user to grant qualification to'),
        qualification=_('Qualification to grant')
    )
    async def qualification_grant(self, interaction: discord.Interaction,
                                  user: discord.Member,
                                  qualification: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        granter_ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get qualification details
                await cursor.execute("SELECT * FROM logbook_qualifications WHERE id = %s", (qualification,))
                qual_row = await cursor.fetchone()

                if not qual_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Qualification not found!'), ephemeral=True)
                    return

                # Check if already has this qualification
                await cursor.execute("""
                    SELECT 1 FROM logbook_pilot_qualifications
                    WHERE player_ucid = %s AND qualification_id = %s
                """, (ucid, qualification))
                if await cursor.fetchone():
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('{} already has qualification "{}".').format(user.display_name, qual_row['name']),
                        ephemeral=True
                    )
                    return

                # Calculate expiration if applicable
                expires_at = None
                if qual_row.get('valid_days'):
                    expires_at = datetime.now(timezone.utc) + timedelta(days=qual_row['valid_days'])

                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO logbook_pilot_qualifications
                        (player_ucid, qualification_id, granted_by, expires_at)
                        VALUES (%s, %s, %s, %s)
                    """, (ucid, qualification, granter_ucid, expires_at))

        embed = discord.Embed(
            title=_('Qualification Granted'),
            description=_('{} has been granted "{}"').format(user.display_name, qual_row['name']),
            color=discord.Color.green()
        )
        if expires_at:
            embed.add_field(name=_('Expires'), value=expires_at.strftime('%Y-%m-%d'), inline=True)
        else:
            embed.add_field(name=_('Expires'), value=_("Never"), inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'granted qualification {qual_row["name"]} to {user.display_name}',
            user=interaction.user
        )

    @qualification.command(name='revoke', description=_('Revoke a qualification from a pilot'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(qualification=pilot_qualification_autocomplete)
    @app_commands.describe(
        user=_('Discord user to revoke qualification from'),
        qualification=_('Qualification to revoke')
    )
    async def qualification_revoke(self, interaction: discord.Interaction,
                                   user: discord.Member,
                                   qualification: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get qualification name
                await cursor.execute("SELECT name FROM logbook_qualifications WHERE id = %s", (qualification,))
                qual_row = await cursor.fetchone()

                if not qual_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Qualification not found!'), ephemeral=True)
                    return

                # Remove the qualification
                await conn.execute("""
                    DELETE FROM logbook_pilot_qualifications
                    WHERE player_ucid = %s AND qualification_id = %s
                """, (ucid, qualification))

        embed = discord.Embed(
            title=_('Qualification Revoked'),
            description=_('"{}" has been revoked from {}').format(qual_row['name'], user.display_name),
            color=discord.Color.orange()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'revoked qualification {qual_row["name"]} from {user.display_name}',
            user=interaction.user
        )

    @qualification.command(name='refresh', description=_('Refresh a pilot\'s qualification expiration'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(qualification=pilot_qualification_autocomplete)
    @app_commands.describe(
        user=_('Discord user to refresh qualification for'),
        qualification=_('Qualification to refresh')
    )
    async def qualification_refresh(self, interaction: discord.Interaction,
                                    user: discord.Member,
                                    qualification: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get qualification details
                await cursor.execute("SELECT * FROM logbook_qualifications WHERE id = %s", (qualification,))
                qual_row = await cursor.fetchone()

                if not qual_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Qualification not found!'), ephemeral=True)
                    return

                if not qual_row.get('valid_days'):
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Qualification "{}" is permanent and does not expire.').format(qual_row['name']),
                        ephemeral=True
                    )
                    return

                # Calculate new expiration
                new_expires_at = datetime.now(timezone.utc) + timedelta(days=qual_row['valid_days'])

                # Update the expiration
                result = await conn.execute("""
                    UPDATE logbook_pilot_qualifications
                    SET expires_at = %s, granted_at = NOW() AT TIME ZONE 'utc'
                    WHERE player_ucid = %s AND qualification_id = %s
                """, (new_expires_at, ucid, qualification))

                if result.rowcount == 0:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('{} does not have qualification "{}".').format(user.display_name, qual_row['name']),
                        ephemeral=True
                    )
                    return

        embed = discord.Embed(
            title=_('Qualification Refreshed'),
            description=_('"{}" has been refreshed for {}').format(qual_row['name'], user.display_name),
            color=discord.Color.green()
        )
        embed.add_field(name=_('New Expiration'), value=new_expires_at.strftime('%Y-%m-%d'), inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'refreshed qualification {qual_row["name"]} for {user.display_name}',
            user=interaction.user
        )

    @qualification.command(name='list', description=_('List qualifications'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        user=_('Show qualifications for a specific pilot (leave empty for all qualifications)')
    )
    async def qualification_list(self, interaction: discord.Interaction,
                                 user: Optional[discord.Member] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if user:
                    # Show pilot's qualifications
                    ucid = await self.bot.get_ucid_by_member(user)
                    if not ucid:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('User {} is not linked to DCS!').format(user.display_name),
                            ephemeral=True
                        )
                        return

                    await cursor.execute("""
                        SELECT q.name, q.description, q.aircraft_type, pq.granted_at, pq.expires_at,
                               g.name as granted_by_name
                        FROM logbook_pilot_qualifications pq
                        JOIN logbook_qualifications q ON pq.qualification_id = q.id
                        LEFT JOIN players g ON pq.granted_by = g.ucid
                        WHERE pq.player_ucid = %s
                        ORDER BY q.name
                    """, (ucid,))
                    quals = await cursor.fetchall()

                    if not quals:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('{} has no qualifications.').format(user.display_name),
                            ephemeral=ephemeral
                        )
                        return

                    embed = discord.Embed(
                        title=_('Qualifications for {}').format(user.display_name),
                        color=discord.Color.blue()
                    )

                    now = datetime.now(timezone.utc)
                    for qual in quals:
                        status = ""
                        if qual.get('expires_at'):
                            expires_at = qual['expires_at']
                            if expires_at.tzinfo is None:
                                expires_at = expires_at.replace(tzinfo=timezone.utc)
                            if expires_at < now:
                                status = " ⚠️ EXPIRED"
                            elif expires_at < now + timedelta(days=30):
                                days_left = (expires_at - now).days
                                status = f" ⚠️ {days_left}d left"
                            else:
                                status = f" (expires {expires_at.strftime('%Y-%m-%d')})"
                        else:
                            status = " (permanent)"

                        aircraft = f" [{qual['aircraft_type']}]" if qual.get('aircraft_type') else ""
                        embed.add_field(
                            name=f"{qual['name']}{aircraft}{status}",
                            value=qual.get('description') or _('No description'),
                            inline=False
                        )

                else:
                    # Show all qualification definitions
                    await cursor.execute("""
                        SELECT q.*,
                               (SELECT COUNT(*) FROM logbook_pilot_qualifications WHERE qualification_id = q.id) as holder_count
                        FROM logbook_qualifications q
                        ORDER BY q.name
                    """)
                    quals = await cursor.fetchall()

                    if not quals:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('No qualifications have been created yet.'),
                            ephemeral=ephemeral
                        )
                        return

                    embed = discord.Embed(
                        title=_('Qualifications'),
                        description=_('All defined qualifications'),
                        color=discord.Color.blue()
                    )

                    for qual in quals:
                        aircraft = f" [{qual['aircraft_type']}]" if qual.get('aircraft_type') else ""
                        validity = _("{} days").format(qual['valid_days']) if qual.get('valid_days') else _("Permanent")
                        embed.add_field(
                            name=f"{qual['name']}{aircraft}",
                            value=_("{} holders, valid: {}").format(qual['holder_count'], validity),
                            inline=True
                        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @qualification.command(name='expiring', description=_('List qualifications expiring soon'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(
        days=_('Show qualifications expiring within this many days (default: 30)')
    )
    async def qualification_expiring(self, interaction: discord.Interaction,
                                     days: Optional[int] = 30):
        ephemeral = utils.get_ephemeral(interaction)

        cutoff_date = datetime.now(timezone.utc) + timedelta(days=days)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name as pilot_name, p.discord_id, q.name as qual_name, pq.expires_at
                    FROM logbook_pilot_qualifications pq
                    JOIN players p ON pq.player_ucid = p.ucid
                    JOIN logbook_qualifications q ON pq.qualification_id = q.id
                    WHERE pq.expires_at IS NOT NULL AND pq.expires_at <= %s
                    ORDER BY pq.expires_at ASC
                """, (cutoff_date,))
                expiring = await cursor.fetchall()

        if not expiring:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('No qualifications expiring within {} days.').format(days),
                ephemeral=ephemeral
            )
            return

        embed = discord.Embed(
            title=_('Expiring Qualifications'),
            description=_('Qualifications expiring within {} days').format(days),
            color=discord.Color.orange()
        )

        now = datetime.now(timezone.utc)
        lines = []
        for exp in expiring:
            expires_at = exp['expires_at']
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            days_left = (expires_at - now).days
            if days_left < 0:
                status = "**EXPIRED**"
            else:
                status = f"{days_left}d"
            lines.append(f"**{exp['pilot_name']}** - {exp['qual_name']} ({status})")

        # Split into chunks if needed
        text = '\n'.join(lines)
        if len(text) > 1024:
            text = text[:1000] + f"\n... and {len(expiring) - text.count(chr(10))} more"

        embed.add_field(name=_('Pilots'), value=text, inline=False)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @qualification.command(name='delete', description=_('Delete a qualification'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(qualification=qualification_autocomplete)
    async def qualification_delete(self, interaction: discord.Interaction, qualification: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Use DELETE ... RETURNING to atomically delete and get the name
                await cursor.execute(
                    "DELETE FROM logbook_qualifications WHERE id = %s RETURNING name",
                    (qualification,)
                )
                qual_row = await cursor.fetchone()

                if not qual_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Qualification not found!'), ephemeral=True)
                    return

        embed = discord.Embed(
            title=_('Qualification Deleted'),
            description=_('Qualification "{}" has been deleted.').format(qual_row['name']),
            color=discord.Color.red()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'deleted qualification {qual_row["name"]}', user=interaction.user)

    # ==================== AWARD COMMANDS ====================

    @award.command(name='create', description=_('Create a new award'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(
        name=_('Award name (e.g., "Distinguished Flying Cross")'),
        description=_('Description of what the award is for'),
        ribbon_colors=_('Ribbon colors as JSON array (e.g., ["#FF0000", "#FFFFFF", "#0000FF"])'),
        image_url=_('URL to award image (optional)')
    )
    async def award_create(self, interaction: discord.Interaction,
                           name: str,
                           description: Optional[str] = None,
                           ribbon_colors: Optional[str] = None,
                           image_url: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        # Parse and validate ribbon colors if provided
        colors_json = None
        if ribbon_colors:
            try:
                colors_json = json.loads(ribbon_colors)
                if not isinstance(colors_json, list):
                    raise ValueError("Must be a JSON array")
                if len(colors_json) > 20:
                    raise ValueError("Maximum 20 colors allowed")
                for color in colors_json:
                    if not isinstance(color, str):
                        raise ValueError("Each color must be a string")
                    if len(color) > 50:
                        raise ValueError("Color strings must be under 50 characters")
            except (json.JSONDecodeError, ValueError) as e:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('Invalid ribbon_colors format. Must be a JSON array of color strings (max 20). Error: {}').format(str(e)),
                    ephemeral=True
                )
                return

        async with self.apool.connection() as conn:
            # Use INSERT ... ON CONFLICT to avoid race condition
            cursor = await conn.execute("""
                INSERT INTO logbook_awards (name, description, image_url, ribbon_colors)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
            """, (name, description, image_url, json.dumps(colors_json) if colors_json else None))
            result = await cursor.fetchone()

            if not result:
                # Award already exists
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('Award "{}" already exists!').format(name),
                    ephemeral=True
                )
                return
            award_id = result[0]

        embed = discord.Embed(
            title=_('Award Created'),
            description=_('Award "{}" has been created.').format(name),
            color=discord.Color.green()
        )
        embed.add_field(name=_('ID'), value=str(award_id), inline=True)
        if description:
            embed.add_field(name=_('Description'), value=description, inline=False)
        if colors_json:
            # Show color preview
            color_display = ' '.join([f"`{c}`" for c in colors_json[:5]])
            embed.add_field(name=_('Ribbon Colors'), value=color_display, inline=False)
        if image_url:
            embed.set_thumbnail(url=image_url)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'created award {name}', user=interaction.user)

    @award.command(name='info', description=_('Show award details'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(award=award_autocomplete)
    async def award_info(self, interaction: discord.Interaction, award: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT a.*,
                           (SELECT COUNT(*) FROM logbook_pilot_awards WHERE award_id = a.id) as recipient_count
                    FROM logbook_awards a
                    WHERE a.id = %s
                """, (award,))
                row = await cursor.fetchone()

        if not row:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Award not found!'), ephemeral=True)
            return

        embed = discord.Embed(
            title=row['name'],
            description=row.get('description') or _('No description'),
            color=discord.Color.gold()
        )

        embed.add_field(name=_('Recipients'), value=str(row['recipient_count']), inline=True)
        if row.get('auto_grant'):
            embed.add_field(name=_('Auto-Grant'), value=_('Yes'), inline=True)

        # Generate ribbon image
        file = None
        if HAS_IMAGING:
            colors = None
            if row.get('ribbon_colors'):
                try:
                    colors = json.loads(row['ribbon_colors']) if isinstance(row['ribbon_colors'], str) else row['ribbon_colors']
                except Exception:
                    pass
            # Generate single ribbon using create_ribbon_rack with count=1
            ribbon_bytes = create_ribbon_rack([(row['name'], colors, 1)], scale=2.0)
            if ribbon_bytes:
                file = discord.File(io.BytesIO(ribbon_bytes), filename='ribbon.png')
                embed.set_image(url='attachment://ribbon.png')

        if row.get('image_url'):
            embed.set_thumbnail(url=row['image_url'])

        # noinspection PyUnresolvedReferences
        if file:
            await interaction.response.send_message(embed=embed, file=file, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @award.command(name='grant', description=_('Grant an award to a pilot'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(award=award_autocomplete)
    @app_commands.describe(
        user=_('Discord user to grant award to'),
        award=_('Award to grant'),
        citation=_('Citation or reason for the award (optional)')
    )
    async def award_grant(self, interaction: discord.Interaction,
                          user: discord.Member,
                          award: int,
                          citation: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        granter_ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get award details
                await cursor.execute("SELECT * FROM logbook_awards WHERE id = %s", (award,))
                award_row = await cursor.fetchone()

                if not award_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Award not found!'), ephemeral=True)
                    return

                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO logbook_pilot_awards
                        (player_ucid, award_id, granted_by, citation)
                        VALUES (%s, %s, %s, %s)
                    """, (ucid, award, granter_ucid, citation))

        embed = discord.Embed(
            title=_('Award Granted'),
            description=_('{} has been awarded "{}"').format(user.display_name, award_row['name']),
            color=discord.Color.gold()
        )
        if citation:
            embed.add_field(name=_('Citation'), value=citation, inline=False)
        if award_row.get('image_url'):
            embed.set_thumbnail(url=award_row['image_url'])

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'granted award {award_row["name"]} to {user.display_name}',
            user=interaction.user
        )

    @award.command(name='revoke', description=_('Revoke an award from a pilot'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(award=pilot_award_autocomplete)
    @app_commands.describe(
        user=_('Discord user to revoke award from'),
        award=_('Award to revoke')
    )
    async def award_revoke(self, interaction: discord.Interaction,
                           user: discord.Member,
                           award: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(user.display_name),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get award name
                await cursor.execute("SELECT name FROM logbook_awards WHERE id = %s", (award,))
                award_row = await cursor.fetchone()

                if not award_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Award not found!'), ephemeral=True)
                    return

                # Get the most recent instance of this award for the pilot
                await cursor.execute("""
                    SELECT granted_at FROM logbook_pilot_awards
                    WHERE player_ucid = %s AND award_id = %s
                    ORDER BY granted_at DESC LIMIT 1
                """, (ucid, award))
                pilot_award = await cursor.fetchone()

                if not pilot_award:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('{} does not have award "{}".').format(user.display_name, award_row['name']),
                        ephemeral=True
                    )
                    return

                # Remove the most recent instance
                await conn.execute("""
                    DELETE FROM logbook_pilot_awards
                    WHERE player_ucid = %s AND award_id = %s AND granted_at = %s
                """, (ucid, award, pilot_award['granted_at']))

        embed = discord.Embed(
            title=_('Award Revoked'),
            description=_('"{}" has been revoked from {}').format(award_row['name'], user.display_name),
            color=discord.Color.orange()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(
            f'revoked award {award_row["name"]} from {user.display_name}',
            user=interaction.user
        )

    @award.command(name='list', description=_('List awards'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        user=_('Show awards for a specific pilot (leave empty for all awards)')
    )
    async def award_list(self, interaction: discord.Interaction,
                         user: Optional[discord.Member] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if user:
                    # Show pilot's awards
                    ucid = await self.bot.get_ucid_by_member(user)
                    if not ucid:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('User {} is not linked to DCS!').format(user.display_name),
                            ephemeral=True
                        )
                        return

                    await cursor.execute("""
                        SELECT a.name, a.description, pa.granted_at, pa.citation,
                               g.name as granted_by_name,
                               (SELECT COUNT(*) FROM logbook_pilot_awards
                                WHERE player_ucid = %s AND award_id = a.id) as count
                        FROM logbook_pilot_awards pa
                        JOIN logbook_awards a ON pa.award_id = a.id
                        LEFT JOIN players g ON pa.granted_by = g.ucid
                        WHERE pa.player_ucid = %s
                        ORDER BY pa.granted_at DESC
                    """, (ucid, ucid))
                    awards = await cursor.fetchall()

                    if not awards:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('{} has no awards.').format(user.display_name),
                            ephemeral=ephemeral
                        )
                        return

                    # Group awards by name and build embeds with pagination
                    embeds = []
                    current_embed = discord.Embed(
                        title=_('Awards for {}').format(user.display_name),
                        color=discord.Color.gold()
                    )
                    field_count = 0
                    seen_awards = {}

                    for award in awards:
                        name = award['name']
                        if name not in seen_awards:
                            if field_count >= 25:
                                embeds.append(current_embed)
                                current_embed = discord.Embed(
                                    title=_('Awards for {} (continued)').format(user.display_name),
                                    color=discord.Color.gold()
                                )
                                field_count = 0

                            count_str = f" x{award['count']}" if award['count'] > 1 else ""
                            value = award.get('description') or _('No description')
                            if award.get('citation'):
                                value += f"\n*\"{award['citation']}\"*"
                            current_embed.add_field(
                                name=f"{name}{count_str}",
                                value=value,
                                inline=False
                            )
                            field_count += 1
                            seen_awards[name] = True

                    embeds.append(current_embed)

                else:
                    # Show all award definitions
                    await cursor.execute("""
                        SELECT a.*,
                               (SELECT COUNT(*) FROM logbook_pilot_awards WHERE award_id = a.id) as recipient_count
                        FROM logbook_awards a
                        ORDER BY a.name
                    """)
                    awards = await cursor.fetchall()

                    if not awards:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(
                            _('No awards have been created yet.'),
                            ephemeral=ephemeral
                        )
                        return

                    # Discord embeds have a max of 25 fields, so paginate if needed
                    embeds = []
                    current_embed = discord.Embed(
                        title=_('Awards'),
                        description=_('All defined awards ({} total)').format(len(awards)),
                        color=discord.Color.gold()
                    )
                    field_count = 0

                    for award in awards:
                        if field_count >= 25:
                            embeds.append(current_embed)
                            current_embed = discord.Embed(
                                title=_('Awards (continued)'),
                                color=discord.Color.gold()
                            )
                            field_count = 0

                        current_embed.add_field(
                            name=award['name'],
                            value=_("{} recipients").format(award['recipient_count']),
                            inline=True
                        )
                        field_count += 1

                    embeds.append(current_embed)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embeds=embeds, ephemeral=ephemeral)

    @award.command(name='delete', description=_('Delete an award'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(award=award_autocomplete)
    async def award_delete(self, interaction: discord.Interaction, award: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Use DELETE ... RETURNING to atomically delete and get the name
                await cursor.execute(
                    "DELETE FROM logbook_awards WHERE id = %s RETURNING name",
                    (award,)
                )
                award_row = await cursor.fetchone()

                if not award_row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Award not found!'), ephemeral=True)
                    return

        embed = discord.Embed(
            title=_('Award Deleted'),
            description=_('Award "{}" has been deleted.').format(award_row['name']),
            color=discord.Color.red()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        await self.bot.audit(f'deleted award {award_row["name"]}', user=interaction.user)

    @award.command(name='ribbon', description=_('Generate a ribbon rack for a pilot'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        user=_('Discord user to generate ribbon rack for')
    )
    async def award_ribbon(self, interaction: discord.Interaction,
                           user: Optional[discord.Member] = None):
        ephemeral = utils.get_ephemeral(interaction)

        if not HAS_IMAGING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('Ribbon generation is not available. Missing PIL/numpy libraries.'),
                ephemeral=True
            )
            return

        target_user = user or interaction.user
        ucid = await self.bot.get_ucid_by_member(target_user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('User {} is not linked to DCS!').format(target_user.display_name),
                ephemeral=True
            )
            return

        # Defer the response since image generation may take time
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get all awards for this pilot with their ribbon colors
                await cursor.execute("""
                    SELECT a.name, a.ribbon_colors,
                           COUNT(*) as count
                    FROM logbook_pilot_awards pa
                    JOIN logbook_awards a ON pa.award_id = a.id
                    WHERE pa.player_ucid = %s
                    GROUP BY a.name, a.ribbon_colors
                    ORDER BY MIN(pa.granted_at)
                """, (ucid,))
                awards = await cursor.fetchall()

        if not awards:
            await interaction.followup.send(
                _('{} has no awards.').format(target_user.display_name),
                ephemeral=ephemeral
            )
            return

        # Prepare award data for ribbon rack generation
        award_data = []
        for award in awards:
            colors = None
            if award.get('ribbon_colors'):
                try:
                    colors = json.loads(award['ribbon_colors']) if isinstance(award['ribbon_colors'], str) else award['ribbon_colors']
                except Exception:
                    pass
            award_data.append((award['name'], colors, award['count']))

        # Generate the ribbon rack
        ribbon_bytes = create_ribbon_rack(award_data, scale=0.5)

        if not ribbon_bytes:
            await interaction.followup.send(
                _('Failed to generate ribbon rack.'),
                ephemeral=ephemeral
            )
            return

        # Send the image
        file = discord.File(io.BytesIO(ribbon_bytes), filename='ribbon_rack.png')
        embed = discord.Embed(
            title=_('Ribbon Rack'),
            description=_('Awards for {}').format(target_user.display_name),
            color=discord.Color.gold()
        )
        embed.set_image(url='attachment://ribbon_rack.png')
        embed.set_footer(text=_("{} awards").format(sum(a['count'] for a in awards)))

        await interaction.followup.send(embed=embed, file=file, ephemeral=ephemeral)

    # ==================== FLIGHT PLAN COMMANDS ====================

    @flightplan.command(name='file', description=_('File a flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        callsign=_('Your callsign for this flight'),
        aircraft_type=_('Aircraft type (e.g., F-16C, F/A-18C)'),
        departure=_('Departure airfield'),
        destination=_('Destination airfield'),
        alternate=_('Alternate airfield (optional)'),
        route=_('Planned route (optional)'),
        remarks=_('Additional remarks (optional)')
    )
    async def flightplan_file(self, interaction: discord.Interaction,
                              callsign: str,
                              aircraft_type: str,
                              departure: str,
                              destination: str,
                              alternate: Optional[str] = None,
                              route: Optional[str] = None,
                              remarks: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('You are not linked to DCS!'),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO logbook_flight_plans
                (player_ucid, callsign, aircraft_type, departure, destination, alternate, route, remarks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (ucid, callsign, aircraft_type, departure, destination, alternate, route, remarks))
            result = await cursor.fetchone()
            plan_id = result[0]

        embed = discord.Embed(
            title=_('Flight Plan Filed'),
            description=_('Flight plan #{} has been filed.').format(plan_id),
            color=discord.Color.green()
        )
        embed.add_field(name=_('Callsign'), value=callsign, inline=True)
        embed.add_field(name=_('Aircraft'), value=aircraft_type, inline=True)
        embed.add_field(name=_('Route'), value=f"{departure} → {destination}", inline=True)
        if alternate:
            embed.add_field(name=_('Alternate'), value=alternate, inline=True)
        if route:
            embed.add_field(name=_('Via'), value=route, inline=False)
        if remarks:
            embed.add_field(name=_('Remarks'), value=remarks, inline=False)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='view', description=_('View a flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    @app_commands.describe(plan=_('Flight plan to view'))
    async def flightplan_view(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT fp.*, p.name as pilot_name
                    FROM logbook_flight_plans fp
                    JOIN players p ON fp.player_ucid = p.ucid
                    WHERE fp.id = %s
                """, (plan,))
                fp = await cursor.fetchone()

        if not fp:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Flight plan not found!'), ephemeral=True)
            return

        status_colors = {
            'filed': discord.Color.blue(),
            'active': discord.Color.green(),
            'completed': discord.Color.dark_green(),
            'cancelled': discord.Color.red()
        }

        embed = discord.Embed(
            title=_('Flight Plan #{}').format(plan),
            description=_('Filed by {}').format(fp['pilot_name']),
            color=status_colors.get(fp['status'], discord.Color.blue())
        )

        embed.add_field(name=_('Status'), value=fp['status'].upper(), inline=True)
        if fp.get('callsign'):
            embed.add_field(name=_('Callsign'), value=fp['callsign'], inline=True)
        if fp.get('aircraft_type'):
            embed.add_field(name=_('Aircraft'), value=fp['aircraft_type'], inline=True)
        embed.add_field(name=_('Departure'), value=fp.get('departure') or _('N/A'), inline=True)
        embed.add_field(name=_('Destination'), value=fp.get('destination') or _('N/A'), inline=True)
        if fp.get('alternate'):
            embed.add_field(name=_('Alternate'), value=fp['alternate'], inline=True)
        if fp.get('route'):
            embed.add_field(name=_('Route'), value=fp['route'], inline=False)
        if fp.get('remarks'):
            embed.add_field(name=_('Remarks'), value=fp['remarks'], inline=False)

        filed_at = fp['filed_at']
        if filed_at:
            embed.set_footer(text=_('Filed at {}').format(filed_at.strftime('%Y-%m-%d %H:%M UTC')))

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='list', description=_('List flight plans'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        status=_('Filter by status (leave empty for all active plans)'),
        user=_('Show flight plans for a specific pilot')
    )
    @app_commands.choices(status=[
        app_commands.Choice(name='Filed', value='filed'),
        app_commands.Choice(name='Active', value='active'),
        app_commands.Choice(name='Completed', value='completed'),
        app_commands.Choice(name='Cancelled', value='cancelled'),
        app_commands.Choice(name='All', value='all'),
    ])
    async def flightplan_list(self, interaction: discord.Interaction,
                              status: Optional[str] = None,
                              user: Optional[discord.Member] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                query_parts = ["SELECT fp.*, p.name as pilot_name FROM logbook_flight_plans fp"]
                query_parts.append("JOIN players p ON fp.player_ucid = p.ucid")
                conditions = []
                params = []

                if user:
                    ucid = await self.bot.get_ucid_by_member(user)
                    if ucid:
                        conditions.append("fp.player_ucid = %s")
                        params.append(ucid)

                if status and status != 'all':
                    conditions.append("fp.status = %s")
                    params.append(status)
                elif not status:
                    # Default to active plans
                    conditions.append("fp.status IN ('filed', 'active')")

                if conditions:
                    query_parts.append("WHERE " + " AND ".join(conditions))

                query_parts.append("ORDER BY fp.filed_at DESC LIMIT 20")
                query = " ".join(query_parts)

                await cursor.execute(query, tuple(params))
                plans = await cursor.fetchall()

        if not plans:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('No flight plans found.'),
                ephemeral=ephemeral
            )
            return

        embed = discord.Embed(
            title=_('Flight Plans'),
            color=discord.Color.blue()
        )

        for fp in plans[:10]:  # Limit display
            status_emoji = {
                'filed': '📝',
                'active': '✈️',
                'completed': '✅',
                'cancelled': '❌'
            }.get(fp['status'], '❓')

            route = f"{fp.get('departure', '?')} → {fp.get('destination', '?')}"
            embed.add_field(
                name=f"{status_emoji} #{fp['id']} - {fp.get('callsign', 'N/A')} ({fp['pilot_name']})",
                value=f"{fp.get('aircraft_type', 'N/A')} | {route}",
                inline=False
            )

        if len(plans) > 10:
            embed.set_footer(text=_("Showing 10 of {} flight plans").format(len(plans)))

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='activate', description=_('Activate a filed flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_activate(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM logbook_flight_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] != 'filed':
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Only filed flight plans can be activated. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                await conn.execute(
                    "UPDATE logbook_flight_plans SET status = 'active' WHERE id = %s",
                    (plan,)
                )

        embed = discord.Embed(
            title=_('Flight Plan Activated'),
            description=_('Flight plan #{} is now active.').format(plan),
            color=discord.Color.green()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='complete', description=_('Mark a flight plan as completed'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_complete(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM logbook_flight_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] not in ('filed', 'active'):
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Flight plan cannot be completed. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                await conn.execute(
                    "UPDATE logbook_flight_plans SET status = 'completed' WHERE id = %s",
                    (plan,)
                )

        embed = discord.Embed(
            title=_('Flight Plan Completed'),
            description=_('Flight plan #{} has been marked as completed.').format(plan),
            color=discord.Color.dark_green()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='cancel', description=_('Cancel a flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_cancel(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM logbook_flight_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] not in ('filed', 'active'):
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('Flight plan cannot be cancelled. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                await conn.execute(
                    "UPDATE logbook_flight_plans SET status = 'cancelled' WHERE id = %s",
                    (plan,)
                )

        embed = discord.Embed(
            title=_('Flight Plan Cancelled'),
            description=_('Flight plan #{} has been cancelled.').format(plan),
            color=discord.Color.red()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    # NOTE: Stores commands have been moved to the logistics plugin


async def setup(bot: DCSServerBot):
    # Logbook depends on userstats for the statistics table used by pilot_logbook_stats view
    if 'userstats' not in bot.plugins:
        raise PluginRequiredError('userstats')
    await bot.add_cog(Logbook(bot, LogbookEventListener))
