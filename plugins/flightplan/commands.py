import aiohttp
import discord
import json
import logging
import re

from core import Plugin, utils, Server, Status, Group, get_translation
from datetime import datetime, timedelta, timezone
from discord import app_commands
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Literal, Optional

from .listener import FlightPlanEventListener
from .utils import parse_waypoint_input, WaypointType, calculate_flight_plan_eta


# Theater bounding boxes for OpenAIP queries (minLon, minLat, maxLon, maxLat)
THEATER_BOUNDING_BOXES = {
    'Caucasus': (38.0, 41.0, 47.0, 45.0),      # Georgia, South Russia
    'Syria': (32.0, 32.0, 42.0, 37.5),          # Syria, Lebanon, Cyprus, parts of Turkey
    'PersianGulf': (48.0, 22.0, 60.0, 30.0),    # UAE, Iran, Oman, Strait of Hormuz
    'Nevada': (-120.0, 34.0, -114.0, 40.0),     # Nevada, California
    'Normandy': (-3.0, 48.0, 2.0, 51.0),        # Normandy, Northern France
    'TheChannel': (-2.0, 49.0, 3.0, 52.0),      # English Channel region
    'MarianaIslands': (144.0, 13.0, 146.5, 15.5),  # Guam, Mariana Islands
    'SouthAtlantic': (-65.0, -55.0, -55.0, -50.0),  # Falkland Islands
    'Sinai': (32.0, 27.0, 36.0, 32.0),          # Sinai Peninsula, Egypt
    'Afghanistan': (60.0, 29.0, 71.5, 38.5),    # Afghanistan and surrounding areas
    'Kola': (25.0, 66.0, 42.0, 72.0),           # Kola Peninsula, Northern Russia/Norway
}

# Map DCS internal map names to our standard theater names
# DCS reports maps with internal names that may differ from display names
DCS_MAP_TO_THEATER = {
    # Standard names (match database)
    'Caucasus': 'Caucasus',
    'Syria': 'Syria',
    'PersianGulf': 'PersianGulf',
    'Nevada': 'Nevada',
    'Normandy': 'Normandy',
    'TheChannel': 'TheChannel',
    'MarianaIslands': 'MarianaIslands',
    'SouthAtlantic': 'SouthAtlantic',
    'Sinai': 'Sinai',
    'Afghanistan': 'Afghanistan',
    'Kola': 'Kola',
    'Iraq': 'Iraq',
    'GermanyCW': 'GermanyCW',
    # Alternate DCS internal names
    'Falklands': 'SouthAtlantic',
    'Mariana': 'MarianaIslands',
    'SinaiMap': 'Sinai',
}


def get_theater_name(dcs_map_name: str) -> str:
    """Convert DCS internal map name to our standard theater name."""
    theater = DCS_MAP_TO_THEATER.get(dcs_map_name, dcs_map_name)
    if dcs_map_name != theater:
        log.debug(f"Mapped DCS map '{dcs_map_name}' to theater '{theater}'")
    return theater

_ = get_translation(__name__.split('.')[1])
log = logging.getLogger(__name__)


def parse_altitude(value: str) -> Optional[int]:
    """
    Parse altitude from various formats.

    Accepts:
    - Flight levels: "FL300", "FL 300", "fl300"
    - Feet: "30000", "30,000", "30000ft"

    Returns altitude in feet, or None if invalid.
    """
    if not value:
        return None

    value = value.strip().upper().replace(',', '').replace(' ', '')

    # Flight level format: FL300 -> 30000 feet
    fl_match = re.match(r'^FL(\d+)$', value)
    if fl_match:
        return int(fl_match.group(1)) * 100

    # Plain number or with ft suffix
    ft_match = re.match(r'^(\d+)(?:FT)?$', value)
    if ft_match:
        return int(ft_match.group(1))

    return None


def format_time_utc(dt: datetime, fmt: str = '%H:%M UTC') -> str:
    """
    Format a datetime as UTC time string.
    Handles both timezone-aware and naive datetimes.
    """
    if dt is None:
        return ""
    # If timezone-aware, convert to UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(fmt)


def parse_cruise_speed(value: str) -> Optional[str]:
    """
    Parse cruise speed from various formats.

    Accepts:
    - Knots: "450", "450kts", "450 kts"
    - Mach: "M0.85", "M 0.85", "0.85M", ".85M", "M.85"

    Returns normalized string: "450" for knots or "M0.85" for Mach.
    Returns None if invalid.
    """
    if not value:
        return None

    value = value.strip().upper().replace(' ', '')

    # Mach format: M0.85, M.85, 0.85M, .85M
    # Leading M
    mach_match = re.match(r'^M\.?(\d*\.?\d+)$', value)
    if mach_match:
        mach_num = mach_match.group(1)
        # Ensure it starts with 0 if it's just decimal
        if mach_num.startswith('.'):
            mach_num = '0' + mach_num
        return f"M{mach_num}"

    # Trailing M
    mach_match = re.match(r'^\.?(\d*\.?\d+)M$', value)
    if mach_match:
        mach_num = mach_match.group(1)
        if mach_num.startswith('.'):
            mach_num = '0' + mach_num
        return f"M{mach_num}"

    # Knots format: 450, 450KTS, 450KT
    kts_match = re.match(r'^(\d+)(?:KTS?)?$', value)
    if kts_match:
        return kts_match.group(1)

    return None


def format_cruise_speed(value) -> str:
    """
    Format cruise speed for display.

    Input: "450", 450, or "M0.85"
    Output: "450 kts" or "M0.85"
    """
    if not value:
        return ""
    # Convert to string in case it's stored as an integer
    value_str = str(value)
    if value_str.startswith('M'):
        return value_str  # Already in Mach format
    return f"{value_str} kts"


def parse_etd(value: str) -> Optional[datetime]:
    """
    Parse ETD time from various formats.

    Accepts:
    - With colon: "14:30", "14:30Z", "1430Z"
    - Without colon: "1430", "0930"

    Returns datetime for today (or tomorrow if time has passed), or None if invalid.
    """
    if not value:
        return None

    value = value.strip().upper().replace('Z', '').replace(' ', '')

    # Try with colon first
    for fmt in ['%H:%M', '%H%M']:
        try:
            now = datetime.now(timezone.utc)
            parsed = datetime.strptime(value, fmt).replace(
                year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc
            )
            # If time has passed today, assume tomorrow
            if parsed < now:
                parsed += timedelta(days=1)
            return parsed
        except ValueError:
            continue

    return None


# ==================== AUTOCOMPLETE FUNCTIONS ====================

def _point_to_line_distance_latlon(lat: float, lon: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate approximate distance from point (lat, lon) to line segment (lat1,lon1)-(lat2,lon2).
    Returns distance in nautical miles (approximate, using simple equirectangular projection).
    """
    import math

    # Convert to approximate x/y using equirectangular projection
    # Use average latitude for scaling
    avg_lat = (lat1 + lat2) / 2
    cos_lat = math.cos(math.radians(avg_lat))

    # Scale longitude by cos(lat) to approximate equal distances
    px = lon * cos_lat
    pz = lat
    x1 = lon1 * cos_lat
    z1 = lat1
    x2 = lon2 * cos_lat
    z2 = lat2

    # Vector from start to end
    dx = x2 - x1
    dz = z2 - z1

    # Length squared of the line segment
    len_sq = dx * dx + dz * dz
    if len_sq == 0:
        # Line segment is a point
        dist_deg = math.sqrt((px - x1) ** 2 + (pz - z1) ** 2)
    else:
        # Parameter t for projection onto line
        t = max(0, min(1, ((px - x1) * dx + (pz - z1) * dz) / len_sq))

        # Closest point on line segment
        proj_x = x1 + t * dx
        proj_z = z1 + t * dz

        # Distance from point to projection (in degrees)
        dist_deg = math.sqrt((px - proj_x) ** 2 + (pz - proj_z) ** 2)

    # Convert degrees to nautical miles (1 degree latitude ~ 60nm)
    return dist_deg * 60


async def waypoints_corridor_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """
    Autocomplete for waypoints along the route corridor.
    Shows navigation fixes within 50nm of the direct route between departure and destination.
    Supports multiple waypoints - type comma to add more (e.g., "KBL,KDH,BASTN").
    """
    try:
        server: Server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        if not server or not server.current_mission:
            return []

        # Handle multiple waypoints - split by comma and work with the last one
        existing_waypoints = []
        search_term = current
        if ',' in current:
            parts = current.split(',')
            existing_waypoints = [p.strip() for p in parts[:-1] if p.strip()]
            search_term = parts[-1].strip()

        # Prefix to prepend to results (existing waypoints + comma)
        prefix = ','.join(existing_waypoints) + ',' if existing_waypoints else ''

        # Get departure and destination indices from namespace
        dep_idx = getattr(interaction.namespace, 'departure_idx', None)
        dest_idx = getattr(interaction.namespace, 'destination_idx', None)

        if dep_idx is None or dest_idx is None:
            # Fall back to showing all fixes for the theater
            return await _fallback_fixes_autocomplete(interaction, search_term, server, prefix, existing_waypoints)

        # Get airbase positions
        try:
            dep_airbase = server.current_mission.airbases[dep_idx]
            dest_airbase = server.current_mission.airbases[dest_idx]
        except (IndexError, TypeError):
            return await _fallback_fixes_autocomplete(interaction, search_term, server, prefix, existing_waypoints)

        dep_pos = dep_airbase.get('position', {})
        dest_pos = dest_airbase.get('position', {})

        # Need lat/lon for corridor calculation
        if not dep_pos.get('lat') or not dest_pos.get('lat'):
            return await _fallback_fixes_autocomplete(interaction, search_term, server, prefix, existing_waypoints)

        # Corridor width: 50nm on each side
        corridor_width_nm = 50

        theater = get_theater_name(server.current_mission.map)

        async with interaction.client.apool.connection() as conn:
            # Get all fixes for this theater that have lat/lon
            cursor = await conn.execute("""
                SELECT identifier, name, fix_type, latitude, longitude, frequency
                FROM flightplan_navigation_fixes
                WHERE map_theater = %s
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND (identifier ILIKE %s OR name ILIKE %s)
            """, (theater, '%' + search_term + '%', '%' + search_term + '%'))

            choices = []
            async for row in cursor:
                fix_id, fix_name, fix_type, fix_lat, fix_lon, freq = row

                # Skip if already in the list
                if fix_id in existing_waypoints:
                    continue

                # Calculate distance from fix to route line (in nm)
                dist_nm = _point_to_line_distance_latlon(
                    fix_lat, fix_lon,
                    dep_pos['lat'], dep_pos['lon'],
                    dest_pos['lat'], dest_pos['lon']
                )

                if dist_nm <= corridor_width_nm:
                    # Format display name
                    display = fix_id
                    if fix_name and fix_name != fix_id:
                        display = f"{fix_id} ({fix_name})"
                    if fix_type in ('VOR', 'NDB', 'TACAN') and freq:
                        display = f"{display} - {freq}"

                    # Store distance for sorting
                    choices.append((dist_nm, fix_id, display))

            # Sort by distance from route centerline, closest first
            choices.sort(key=lambda x: x[0])

            # Build choices with prefix for existing waypoints
            return [
                app_commands.Choice(
                    name=f"{prefix}{c[1]}" if not prefix else f"... {c[2][:80]}",
                    value=f"{prefix}{c[1]}"
                )
                for c in choices[:25]
            ]

    except Exception as e:
        log.warning(f"Waypoints corridor autocomplete error: {e}")
        return []


async def _fallback_fixes_autocomplete(
    interaction: discord.Interaction,
    current: str,
    server: Server,
    prefix: str = '',
    existing_waypoints: list = None
) -> list[app_commands.Choice[str]]:
    """Fallback autocomplete when departure/destination not yet selected."""
    if existing_waypoints is None:
        existing_waypoints = []

    try:
        dcs_map = server.current_mission.map if server.current_mission else None
        if not dcs_map:
            return []
        theater = get_theater_name(dcs_map)

        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT identifier, name, fix_type, frequency
                FROM flightplan_navigation_fixes
                WHERE map_theater = %s
                AND (identifier ILIKE %s OR name ILIKE %s)
                ORDER BY identifier
                LIMIT 50
            """, (theater, '%' + current + '%', '%' + current + '%'))

            choices = []
            async for row in cursor:
                fix_id = row[0]
                # Skip if already in the list
                if fix_id in existing_waypoints:
                    continue

                display = f"{fix_id} - {row[1] or row[2]}" + (f" ({row[3]})" if row[3] else "")
                choices.append((fix_id, display))

            return [
                app_commands.Choice(
                    name=f"{prefix}{c[0]}" if not prefix else f"... {c[1][:80]}",
                    value=f"{prefix}{c[0]}"
                )
                for c in choices[:25]
            ]
    except Exception as e:
        log.warning(f"Fallback fixes autocomplete error: {e}")
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
                FROM flightplan_plans
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


async def all_flightplan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    """Autocomplete for all flight plans (admin view)."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT fp.id, fp.callsign, fp.departure, fp.destination, p.name
                FROM flightplan_plans fp
                JOIN players p ON fp.player_ucid = p.ucid
                WHERE fp.status IN ('filed', 'active')
                AND (fp.callsign ILIKE %s OR fp.departure ILIKE %s OR fp.destination ILIKE %s OR p.name ILIKE %s)
                ORDER BY fp.filed_at DESC LIMIT 25
            """, ('%' + current + '%', '%' + current + '%', '%' + current + '%', '%' + current + '%'))
            return [
                app_commands.Choice(
                    name=f"#{row[0]} {row[1] or 'N/A'}: {row[2] or '?'} -> {row[3] or '?'} ({row[4]})",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def waypoint_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for user-defined waypoints."""
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT name, map_theater
                FROM flightplan_waypoints
                WHERE name ILIKE %s AND is_public = TRUE
                ORDER BY name LIMIT 25
            """, ('%' + current + '%',))
            return [
                app_commands.Choice(
                    name=f"{row[0]} ({row[1] or 'all'})",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


async def nav_fix_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for navigation fixes, filtered by server's theater if available."""
    try:
        # Try to get server context for theater filtering
        theater = None
        if hasattr(interaction.namespace, 'server') and interaction.namespace.server:
            try:
                server: Server = await utils.ServerTransformer().transform(
                    interaction, interaction.namespace.server
                )
                if server and server.current_mission:
                    theater = get_theater_name(server.current_mission.map)
            except Exception:
                pass  # Fall back to showing all theaters

        async with interaction.client.apool.connection() as conn:
            if theater:
                # Filter by server's theater
                cursor = await conn.execute("""
                    SELECT identifier, name, fix_type, map_theater
                    FROM flightplan_navigation_fixes
                    WHERE map_theater = %s AND (identifier ILIKE %s OR name ILIKE %s)
                    ORDER BY identifier LIMIT 25
                """, (theater, '%' + current + '%', '%' + current + '%'))
            else:
                # No server context - show all theaters
                cursor = await conn.execute("""
                    SELECT identifier, name, fix_type, map_theater
                    FROM flightplan_navigation_fixes
                    WHERE identifier ILIKE %s OR name ILIKE %s
                    ORDER BY identifier LIMIT 25
                """, ('%' + current + '%', '%' + current + '%'))

            return [
                app_commands.Choice(
                    name=f"{row[0]} - {row[1] or row[2]} ({row[3]})",
                    value=row[0]
                )
                async for row in cursor
            ]
    except Exception as e:
        log.warning(f"Autocomplete error: {e}")
        return []


def _calculate_stale_at(etd: Optional[datetime], filed_at: datetime, stale_hours: int = 24) -> datetime:
    """Calculate when a flight plan becomes stale."""
    if etd:
        return etd + timedelta(hours=2)
    return filed_at + timedelta(hours=stale_hours)


def _create_flightplan_embed(fp: dict, title: str = None) -> discord.Embed:
    """Create a Discord embed for a flight plan."""
    status_colors = {
        'filed': discord.Color.blue(),
        'active': discord.Color.green(),
        'completed': discord.Color.dark_green(),
        'cancelled': discord.Color.red()
    }

    embed = discord.Embed(
        title=title or _('Flight Plan #{}').format(fp['id']),
        description=_('Callsign: {}').format(fp.get('callsign') or 'N/A'),
        color=status_colors.get(fp['status'], discord.Color.blue())
    )

    embed.add_field(name=_('Status'), value=fp['status'].upper(), inline=True)
    if fp.get('aircraft_type'):
        embed.add_field(name=_('Aircraft'), value=fp['aircraft_type'], inline=True)

    route_str = f"{fp.get('departure') or '?'} → {fp.get('destination') or '?'}"
    embed.add_field(name=_('Route'), value=route_str, inline=True)

    if fp.get('alternate'):
        embed.add_field(name=_('Alternate'), value=fp['alternate'], inline=True)

    if fp.get('cruise_altitude'):
        fl = fp['cruise_altitude'] // 100
        embed.add_field(name=_('Cruise'), value=f"FL{fl:03d}", inline=True)

    if fp.get('cruise_speed'):
        embed.add_field(name=_('Speed'), value=format_cruise_speed(fp['cruise_speed']), inline=True)

    if fp.get('etd'):
        etd_str = format_time_utc(fp['etd'])
        embed.add_field(name=_('ETD'), value=etd_str, inline=True)

    if fp.get('eta'):
        eta_str = format_time_utc(fp['eta'])
        embed.add_field(name=_('ETA'), value=eta_str, inline=True)

    # Waypoints
    if fp.get('waypoints'):
        waypoints = fp['waypoints'] if isinstance(fp['waypoints'], list) else json.loads(fp['waypoints'])
        if waypoints:
            wp_names = [wp.get('name', '?') for wp in waypoints[:5]]
            wp_str = ' → '.join(wp_names)
            if len(waypoints) > 5:
                wp_str += f" (+{len(waypoints) - 5} more)"
            embed.add_field(name=_('Waypoints'), value=wp_str, inline=False)

    if fp.get('route'):
        embed.add_field(name=_('Route Notes'), value=fp['route'][:200], inline=False)

    if fp.get('remarks'):
        embed.add_field(name=_('Remarks'), value=fp['remarks'][:200], inline=False)

    if fp.get('server_name'):
        embed.set_footer(text=f"Server: {fp['server_name']}")

    return embed


class FlightPlan(Plugin[FlightPlanEventListener]):
    """
    Flight Plan plugin for IFR-style flight planning.

    Features:
    - File flight plans with waypoints, altitude, and timing
    - F10 map visualization
    - Discord publishing
    - In-game chat commands
    - Auto-cancel stale plans
    """

    # Command group "/flightplan"
    flightplan = Group(name="flightplan", description=_("Commands to manage flight plans"))

    # Subgroup "/flightplan waypoint"
    waypoint = Group(name="waypoint", description=_("Manage user-defined waypoints"), parent=flightplan)

    # Subgroup "/flightplan fix"
    fix = Group(name="fix", description=_("Manage navigation fixes"), parent=flightplan)

    # ==================== FLIGHT PLAN COMMANDS ====================

    @flightplan.command(name='file', description=_('File a flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(departure_idx='departure', destination_idx='destination', alternate_idx='alternate')
    @app_commands.describe(
        server=_('Server for this flight plan'),
        callsign=_('Your callsign for this flight'),
        aircraft_type=_('Aircraft type (e.g., F-16C, F/A-18C)'),
        departure_idx=_('Departure airfield, FARP, or ship'),
        destination_idx=_('Destination airfield, FARP, or ship'),
        alternate_idx=_('Alternate airfield (optional)'),
        waypoints=_('Comma-separated waypoints (e.g., "PANTHER,38TLN123,@MYPOINT")'),
        cruise_altitude=_('Cruise altitude (e.g., FL300 or 30000)'),
        cruise_speed=_('Cruise speed in knots or Mach (e.g., 450 or M0.85)'),
        etd=_('Estimated departure time in HH:MM UTC'),
        remarks=_('Additional remarks (optional)')
    )
    @app_commands.autocomplete(departure_idx=utils.airbase_autocomplete, destination_idx=utils.airbase_autocomplete, alternate_idx=utils.airbase_autocomplete, waypoints=waypoints_corridor_autocomplete)
    async def flightplan_file(
        self,
        interaction: discord.Interaction,
        server: app_commands.Transform[Server, utils.ServerTransformer],
        callsign: str,
        aircraft_type: str,
        departure_idx: int,
        destination_idx: int,
        alternate_idx: Optional[int] = None,
        waypoints: Optional[str] = None,
        cruise_altitude: Optional[str] = None,
        cruise_speed: Optional[str] = None,
        etd: Optional[str] = None,
        remarks: Optional[str] = None
    ):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            await interaction.followup.send(_('You are not linked to DCS!'), ephemeral=True)
            return

        # Get airbases from server
        if not server.current_mission or not server.current_mission.airbases:
            await interaction.followup.send(_('Server has no mission loaded or no airbases available.'), ephemeral=True)
            return

        try:
            dep_airbase = server.current_mission.airbases[departure_idx]
            dest_airbase = server.current_mission.airbases[destination_idx]
        except IndexError:
            await interaction.followup.send(_('Invalid airbase selection.'), ephemeral=True)
            return

        # Get departure position
        departure = dep_airbase['name']
        dep_position = dep_airbase.get('position')

        # Get destination position
        destination = dest_airbase['name']
        dest_position = dest_airbase.get('position')

        # Get alternate position
        alternate = None
        alt_position = None
        if alternate_idx is not None:
            try:
                alt_airbase = server.current_mission.airbases[alternate_idx]
                alternate = alt_airbase['name']
                alt_position = alt_airbase.get('position')
            except IndexError:
                pass

        # Get theater from server
        theater = get_theater_name(server.current_mission.map) if server.current_mission else None

        async with self.apool.connection() as conn:

            # Parse waypoints
            parsed_waypoints = []
            if waypoints:
                from .utils.coordinates import parse_waypoint_list
                wp_list = await parse_waypoint_list(waypoints, server, conn, theater)
                parsed_waypoints = [wp.to_dict() for wp in wp_list]

            # Parse cruise altitude (accepts FL300 or 30000)
            cruise_alt_feet = None
            if cruise_altitude:
                cruise_alt_feet = parse_altitude(cruise_altitude)
                if cruise_alt_feet is None:
                    await interaction.followup.send(
                        _('Invalid altitude format. Use FL300 or 30000.'),
                        ephemeral=True
                    )
                    return

            # Parse cruise speed (accepts 450, 450kts, M0.85, etc.)
            parsed_cruise_speed = None
            if cruise_speed:
                parsed_cruise_speed = parse_cruise_speed(cruise_speed)
                if parsed_cruise_speed is None:
                    await interaction.followup.send(
                        _('Invalid speed format. Use knots (e.g., 450) or Mach (e.g., M0.85).'),
                        ephemeral=True
                    )
                    return

            # Parse ETD (accepts 14:30 or 1430)
            etd_dt = None
            if etd:
                etd_dt = parse_etd(etd)
                if etd_dt is None:
                    await interaction.followup.send(
                        _('Invalid ETD format. Use HH:MM or HHMM (UTC).'),
                        ephemeral=True
                    )
                    return

            # Get config for stale calculation
            config = self.get_config(server)
            stale_hours = config.get('stale_hours', 24)
            filed_at = datetime.now(timezone.utc)
            stale_at = _calculate_stale_at(etd_dt, filed_at, stale_hours)

            # Calculate ETA from ETD, route distance, and cruise speed
            eta_dt = calculate_flight_plan_eta(
                etd=etd_dt,
                dep_position=dep_position,
                dest_position=dest_position,
                waypoints=parsed_waypoints,
                cruise_speed=parsed_cruise_speed,
                cruise_altitude=cruise_alt_feet
            )

            # Get coalition from player if they're online, otherwise default to 0
            coalition = 0
            player = server.get_player(ucid=ucid, active=True)
            if player and player.side:
                coalition = player.side.value

            cursor = await conn.execute("""
                INSERT INTO flightplan_plans
                (player_ucid, server_name, callsign, aircraft_type, departure, destination,
                 alternate, route, remarks, waypoints, departure_position, destination_position,
                 alternate_position, cruise_altitude, cruise_speed, etd, eta, stale_at, coalition)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                ucid, server.name, callsign, aircraft_type, departure, destination,
                alternate, None, remarks,
                json.dumps(parsed_waypoints) if parsed_waypoints else None,
                json.dumps(dep_position) if dep_position else None,
                json.dumps(dest_position) if dest_position else None,
                json.dumps(alt_position) if alt_position else None,
                cruise_alt_feet, parsed_cruise_speed, etd_dt, eta_dt, stale_at, coalition
            ))
            result = await cursor.fetchone()
            plan_id = result[0]

            # Publish to status channel if configured
            config = self.get_config(server)
            if config.get('publish_on_file', True):
                # Build flight plan dict for publishing
                fp_data = {
                    'id': plan_id,
                    'player_ucid': ucid,
                    'server_name': server.name,
                    'callsign': callsign,
                    'aircraft_type': aircraft_type,
                    'departure': departure,
                    'destination': destination,
                    'alternate': alternate,
                    'waypoints': parsed_waypoints,
                    'cruise_altitude': cruise_alt_feet,
                    'cruise_speed': parsed_cruise_speed,
                    'etd': etd_dt,
                    'eta': eta_dt,
                    'remarks': remarks,
                    'status': 'filed',
                    'filed_at': filed_at,
                    'discord_message_id': None
                }
                await self.eventlistener.publish_flight_plan(fp_data, 'filed')

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
        if cruise_alt_feet:
            embed.add_field(name=_('Cruise'), value=f"FL{cruise_alt_feet // 100:03d}", inline=True)
        if parsed_cruise_speed:
            embed.add_field(name=_('Speed'), value=format_cruise_speed(parsed_cruise_speed), inline=True)
        if etd_dt:
            embed.add_field(name=_('ETD'), value=format_time_utc(etd_dt), inline=True)
        if eta_dt:
            embed.add_field(name=_('ETA'), value=format_time_utc(eta_dt), inline=True)
        if parsed_waypoints:
            wp_names = [wp['name'] for wp in parsed_waypoints[:3]]
            embed.add_field(name=_('Waypoints'), value=' → '.join(wp_names), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

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
                    FROM flightplan_plans fp
                    JOIN players p ON fp.player_ucid = p.ucid
                    WHERE fp.id = %s
                """, (plan,))
                fp = await cursor.fetchone()

        if not fp:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Flight plan not found!'), ephemeral=True)
            return

        embed = _create_flightplan_embed(fp, _('Flight Plan #{} - {}').format(plan, fp['pilot_name']))

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
    async def flightplan_list(
        self,
        interaction: discord.Interaction,
        status: Optional[str] = None,
        user: Optional[discord.Member] = None
    ):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                query_parts = ["SELECT fp.*, p.name as pilot_name FROM flightplan_plans fp"]
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
                    conditions.append("fp.status IN ('filed', 'active')")

                if conditions:
                    query_parts.append("WHERE " + " AND ".join(conditions))

                query_parts.append("ORDER BY fp.filed_at DESC LIMIT 20")
                query = " ".join(query_parts)

                await cursor.execute(query, tuple(params))
                plans = await cursor.fetchall()

        if not plans:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No flight plans found.'), ephemeral=ephemeral)
            return

        embed = discord.Embed(title=_('Flight Plans'), color=discord.Color.blue())

        for fp in plans[:10]:
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
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    await interaction.followup.send(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] != 'filed':
                    await interaction.followup.send(
                        _('Only filed flight plans can be activated. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'active', activated_at = %s WHERE id = %s",
                    (now, plan)
                )

        # Get server and create F10 markers
        server_name = fp.get('server_name')
        if server_name:
            server = self.bot.servers.get(server_name)
            if server and server.status == Status.RUNNING:
                await self.eventlistener.create_flight_plan_markers(server, fp)

        # Publish to Discord if configured
        config = self.get_config()
        if config.get('publish_on_activate', True):
            await self.eventlistener.publish_flight_plan(fp, 'activated')

        embed = discord.Embed(
            title=_('Flight Plan Activated'),
            description=_('Flight plan #{} is now active.').format(plan),
            color=discord.Color.green()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='complete', description=_('Mark a flight plan as completed'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_complete(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    await interaction.followup.send(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] not in ('filed', 'active'):
                    await interaction.followup.send(
                        _('Flight plan cannot be completed. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                now = datetime.now(timezone.utc)
                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'completed', completed_at = %s WHERE id = %s",
                    (now, plan)
                )

        # Remove F10 markers
        server_name = fp.get('server_name')
        if server_name:
            server = self.bot.servers.get(server_name)
            if server and server.status == Status.RUNNING:
                await self.eventlistener.remove_flight_plan_markers(server, plan)

        # Update Discord message if published
        await self.eventlistener.publish_flight_plan(fp, 'completed')

        embed = discord.Embed(
            title=_('Flight Plan Completed'),
            description=_('Flight plan #{} has been marked as completed.').format(plan),
            color=discord.Color.dark_green()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='cancel', description=_('Cancel a flight plan'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_cancel(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

                if not fp:
                    await interaction.followup.send(
                        _('Flight plan not found or not yours!'),
                        ephemeral=True
                    )
                    return

                if fp['status'] not in ('filed', 'active'):
                    await interaction.followup.send(
                        _('Flight plan cannot be cancelled. Current status: {}').format(fp['status']),
                        ephemeral=True
                    )
                    return

                await conn.execute(
                    "UPDATE flightplan_plans SET status = 'cancelled' WHERE id = %s",
                    (plan,)
                )

        # Remove F10 markers
        server_name = fp.get('server_name')
        if server_name:
            server = self.bot.servers.get(server_name)
            if server and server.status == Status.RUNNING:
                await self.eventlistener.remove_flight_plan_markers(server, plan)

        # Update Discord message if published
        await self.eventlistener.publish_flight_plan(fp, 'cancelled')

        embed = discord.Embed(
            title=_('Flight Plan Cancelled'),
            description=_('Flight plan #{} has been cancelled.').format(plan),
            color=discord.Color.red()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='plot', description=_('Plot flight plan on F10 map'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=all_flightplan_autocomplete)
    @app_commands.describe(
        plan=_('Flight plan to plot'),
        duration=_('Duration in seconds (default 30, 0 for permanent)')
    )
    async def flightplan_plot(
        self,
        interaction: discord.Interaction,
        plan: int,
        duration: Optional[int] = 30
    ):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM flightplan_plans WHERE id = %s", (plan,))
                fp = await cursor.fetchone()

        if not fp:
            await interaction.followup.send(_('Flight plan not found!'), ephemeral=True)
            return

        server_name = fp.get('server_name')
        if not server_name:
            await interaction.followup.send(_('Flight plan has no associated server!'), ephemeral=True)
            return

        server = self.bot.servers.get(server_name)
        if not server or server.status != Status.RUNNING:
            await interaction.followup.send(_('Server is not running!'), ephemeral=True)
            return

        await self.eventlistener.create_flight_plan_markers(server, fp, timeout=duration)

        if duration > 0:
            msg = _('Flight plan #{} plotted on F10 map for {} seconds.').format(plan, duration)
        else:
            msg = _('Flight plan #{} plotted on F10 map (permanent).').format(plan)

        embed = discord.Embed(
            title=_('Flight Plan Plotted'),
            description=msg,
            color=discord.Color.blue()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='publish', description=_('Publish flight plan to Discord'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(plan=flightplan_autocomplete)
    async def flightplan_publish(self, interaction: discord.Interaction, plan: int):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        ucid = await self.bot.get_ucid_by_member(interaction.user)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT * FROM flightplan_plans WHERE id = %s AND player_ucid = %s",
                    (plan, ucid)
                )
                fp = await cursor.fetchone()

        if not fp:
            await interaction.followup.send(
                _('Flight plan not found or not yours!'),
                ephemeral=True
            )
            return

        await self.eventlistener.publish_flight_plan(fp, fp['status'])

        embed = discord.Embed(
            title=_('Flight Plan Published'),
            description=_('Flight plan #{} has been published to the flight plans channel.').format(plan),
            color=discord.Color.green()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @flightplan.command(name='stale', description=_('Cancel stale flight plans'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(hours=_('Consider plans stale after this many hours (default 24)'))
    async def flightplan_stale(self, interaction: discord.Interaction, hours: Optional[int] = 24):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                UPDATE flightplan_plans
                SET status = 'cancelled'
                WHERE status IN ('filed', 'active')
                AND (stale_at < NOW() OR filed_at < %s)
                RETURNING id, server_name
            """, (cutoff,))
            cancelled = await cursor.fetchall()

        # Remove markers for cancelled plans
        for plan_id, server_name in cancelled:
            if server_name:
                server = self.bot.servers.get(server_name)
                if server and server.status == Status.RUNNING:
                    await self.eventlistener.remove_flight_plan_markers(server, plan_id)

        embed = discord.Embed(
            title=_('Stale Plans Cancelled'),
            description=_('Cancelled {} stale flight plans.').format(len(cancelled)),
            color=discord.Color.orange()
        )

        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    # ==================== WAYPOINT COMMANDS ====================

    @waypoint.command(name='add', description=_('Add a user-defined waypoint'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(
        name=_('Waypoint name (e.g., PANTHER)'),
        coordinates=_('Coordinates (MGRS, DMS, or decimal lat/lon)'),
        theater=_('Map theater (e.g., Caucasus, Syria)'),
        altitude=_('Altitude in feet (optional)'),
        description=_('Description (optional)')
    )
    async def waypoint_add(
        self,
        interaction: discord.Interaction,
        name: str,
        coordinates: str,
        theater: str,
        altitude: Optional[int] = None,
        description: Optional[str] = None
    ):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('You are not linked to DCS!'), ephemeral=True)
            return

        # Parse coordinates
        wp = await parse_waypoint_input(coordinates)
        if not wp.latitude or not wp.longitude:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('Could not parse coordinates: {}').format(coordinates),
                ephemeral=True
            )
            return

        async with self.apool.connection() as conn:
            try:
                await conn.execute("""
                    INSERT INTO flightplan_waypoints
                    (name, created_by_ucid, position_x, position_z, latitude, longitude,
                     altitude, description, map_theater)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    name.upper(), ucid, wp.position_x or 0, wp.position_z or 0,
                    wp.latitude, wp.longitude, altitude, description, theater
                ))
            except Exception as e:
                if 'unique' in str(e).lower():
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('A waypoint with that name already exists for this theater!'),
                        ephemeral=True
                    )
                    return
                raise

        embed = discord.Embed(
            title=_('Waypoint Created'),
            description=_('Waypoint {} has been created.').format(name.upper()),
            color=discord.Color.green()
        )
        embed.add_field(name=_('Coordinates'), value=f"{wp.latitude:.4f}, {wp.longitude:.4f}", inline=True)
        embed.add_field(name=_('Theater'), value=theater, inline=True)
        if altitude:
            embed.add_field(name=_('Altitude'), value=f"{altitude} ft", inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @waypoint.command(name='list', description=_('List user-defined waypoints'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(theater=_('Filter by theater (optional)'))
    async def waypoint_list(self, interaction: discord.Interaction, theater: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            if theater:
                cursor = await conn.execute("""
                    SELECT w.name, w.latitude, w.longitude, w.altitude, w.map_theater, p.name as creator
                    FROM flightplan_waypoints w
                    LEFT JOIN players p ON w.created_by_ucid = p.ucid
                    WHERE w.map_theater = %s AND w.is_public = TRUE
                    ORDER BY w.name LIMIT 25
                """, (theater,))
            else:
                cursor = await conn.execute("""
                    SELECT w.name, w.latitude, w.longitude, w.altitude, w.map_theater, p.name as creator
                    FROM flightplan_waypoints w
                    LEFT JOIN players p ON w.created_by_ucid = p.ucid
                    WHERE w.is_public = TRUE
                    ORDER BY w.map_theater, w.name LIMIT 25
                """)
            waypoints = await cursor.fetchall()

        if not waypoints:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No waypoints found.'), ephemeral=ephemeral)
            return

        embed = discord.Embed(title=_('User Waypoints'), color=discord.Color.blue())

        for wp in waypoints:
            alt_str = f" @ {wp[3]}ft" if wp[3] else ""
            embed.add_field(
                name=f"@{wp[0]} ({wp[4] or 'all'})",
                value=f"{wp[1]:.4f}, {wp[2]:.4f}{alt_str}",
                inline=True
            )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @waypoint.command(name='delete', description=_('Delete a user-defined waypoint'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.autocomplete(name=waypoint_autocomplete)
    async def waypoint_delete(self, interaction: discord.Interaction, name: str):
        ephemeral = utils.get_ephemeral(interaction)

        ucid = await self.bot.get_ucid_by_member(interaction.user)
        is_admin = utils.check_roles(self.bot.roles.get("DCS Admin", []), interaction.user)

        async with self.apool.connection() as conn:
            if is_admin:
                result = await conn.execute(
                    "DELETE FROM flightplan_waypoints WHERE LOWER(name) = LOWER(%s) RETURNING id",
                    (name,)
                )
            else:
                result = await conn.execute(
                    "DELETE FROM flightplan_waypoints WHERE LOWER(name) = LOWER(%s) AND created_by_ucid = %s RETURNING id",
                    (name, ucid)
                )
            deleted = await result.fetchone()

        if not deleted:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('Waypoint not found or you do not have permission to delete it.'),
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=_('Waypoint Deleted'),
            description=_('Waypoint {} has been deleted.').format(name.upper()),
            color=discord.Color.orange()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    # ==================== NAVIGATION FIX COMMANDS ====================

    @fix.command(name='list', description=_('List navigation fixes'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(theater=_('Filter by theater'))
    async def fix_list(self, interaction: discord.Interaction, theater: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            if theater:
                cursor = await conn.execute("""
                    SELECT identifier, name, fix_type, frequency, map_theater
                    FROM flightplan_navigation_fixes
                    WHERE map_theater = %s
                    ORDER BY identifier LIMIT 25
                """, (theater,))
            else:
                cursor = await conn.execute("""
                    SELECT identifier, name, fix_type, frequency, map_theater
                    FROM flightplan_navigation_fixes
                    ORDER BY map_theater, identifier LIMIT 25
                """)
            fixes = await cursor.fetchall()

        if not fixes:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No navigation fixes found.'), ephemeral=ephemeral)
            return

        embed = discord.Embed(title=_('Navigation Fixes'), color=discord.Color.blue())

        for fix in fixes:
            freq_str = f" ({fix[3]})" if fix[3] else ""
            embed.add_field(
                name=f"{fix[0]} - {fix[2]} ({fix[4]})",
                value=f"{fix[1] or fix[0]}{freq_str}",
                inline=True
            )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @fix.command(name='add', description=_('Add a navigation fix'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(
        identifier=_('Fix identifier (e.g., ADLER, TSK)'),
        latitude=_('Latitude in decimal degrees'),
        longitude=_('Longitude in decimal degrees'),
        fix_type=_('Fix type'),
        theater=_('Map theater'),
        name=_('Full name (optional)'),
        frequency=_('Frequency (optional, for VOR/NDB/TACAN)')
    )
    @app_commands.choices(fix_type=[
        app_commands.Choice(name='Waypoint', value='WYP'),
        app_commands.Choice(name='VOR', value='VOR'),
        app_commands.Choice(name='NDB', value='NDB'),
        app_commands.Choice(name='TACAN', value='TACAN'),
        app_commands.Choice(name='Intersection', value='INT'),
    ])
    async def fix_add(
        self,
        interaction: discord.Interaction,
        identifier: str,
        latitude: float,
        longitude: float,
        fix_type: str,
        theater: str,
        name: Optional[str] = None,
        frequency: Optional[str] = None
    ):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            try:
                await conn.execute("""
                    INSERT INTO flightplan_navigation_fixes
                    (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'user')
                """, (identifier.upper(), name, fix_type, latitude, longitude, theater, frequency))
            except Exception as e:
                if 'unique' in str(e).lower():
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _('A fix with that identifier already exists for this theater!'),
                        ephemeral=True
                    )
                    return
                raise

        embed = discord.Embed(
            title=_('Navigation Fix Added'),
            description=_('Fix {} has been added.').format(identifier.upper()),
            color=discord.Color.green()
        )
        embed.add_field(name=_('Type'), value=fix_type, inline=True)
        embed.add_field(name=_('Theater'), value=theater, inline=True)
        embed.add_field(name=_('Position'), value=f"{latitude:.4f}, {longitude:.4f}", inline=True)
        if frequency:
            embed.add_field(name=_('Frequency'), value=frequency, inline=True)

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @fix.command(name='delete', description=_('Delete a navigation fix'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(identifier=nav_fix_autocomplete)
    @app_commands.describe(
        identifier=_('Fix identifier'),
        theater=_('Map theater')
    )
    async def fix_delete(self, interaction: discord.Interaction, identifier: str, theater: str):
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM flightplan_navigation_fixes WHERE UPPER(identifier) = UPPER(%s) AND map_theater = %s RETURNING id",
                (identifier, theater)
            )
            deleted = await result.fetchone()

        if not deleted:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Navigation fix not found.'), ephemeral=True)
            return

        embed = discord.Embed(
            title=_('Navigation Fix Deleted'),
            description=_('Fix {} has been deleted from {}.').format(identifier.upper(), theater),
            color=discord.Color.orange()
        )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @fix.command(name='sync', description=_('Sync navigation fixes from OpenAIP for a theater'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(theater=_('Map theater to sync fixes for'))
    @app_commands.choices(theater=[
        app_commands.Choice(name='Afghanistan', value='Afghanistan'),
        app_commands.Choice(name='Caucasus', value='Caucasus'),
        app_commands.Choice(name='Kola', value='Kola'),
        app_commands.Choice(name='Mariana Islands', value='MarianaIslands'),
        app_commands.Choice(name='Nevada', value='Nevada'),
        app_commands.Choice(name='Normandy', value='Normandy'),
        app_commands.Choice(name='Persian Gulf', value='PersianGulf'),
        app_commands.Choice(name='Sinai', value='Sinai'),
        app_commands.Choice(name='South Atlantic', value='SouthAtlantic'),
        app_commands.Choice(name='Syria', value='Syria'),
        app_commands.Choice(name='The Channel', value='TheChannel'),
    ])
    async def fix_sync(self, interaction: discord.Interaction, theater: str):
        """Sync navigation fixes from OpenAIP API for a specific theater."""
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        # Get API key from config
        config = self.get_config()
        openaip_config = config.get('openaip', {})
        api_key = openaip_config.get('api_key')

        if not api_key:
            await interaction.followup.send(
                _('OpenAIP API key not configured. Add `openaip.api_key` to flightplan plugin config.'),
                ephemeral=True
            )
            return

        # Get bounding box for theater
        bbox = THEATER_BOUNDING_BOXES.get(theater)
        if not bbox:
            await interaction.followup.send(
                _('Unknown theater: {}. Available: {}').format(theater, ', '.join(THEATER_BOUNDING_BOXES.keys())),
                ephemeral=True
            )
            return

        min_lon, min_lat, max_lon, max_lat = bbox

        try:
            # Query OpenAIP API for navaids in this bounding box
            headers = {
                'x-openaip-api-key': api_key,
                'Accept': 'application/json'
            }

            # OpenAIP uses bbox format: minx,miny,maxx,maxy (lon,lat,lon,lat)
            bbox_str = f"{min_lon},{min_lat},{max_lon},{max_lat}"

            async with aiohttp.ClientSession() as session:
                # Fetch navaids (VOR, NDB, TACAN, DME)
                url = f"https://api.core.openaip.net/api/navaids?bbox={bbox_str}&limit=500"
                log.info(f"Fetching navaids from OpenAIP: {url}")

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:
                        await interaction.followup.send(
                            _('OpenAIP API authentication failed. Check your API key.'),
                            ephemeral=True
                        )
                        return
                    elif resp.status != 200:
                        await interaction.followup.send(
                            _('OpenAIP API error: {} {}').format(resp.status, await resp.text()),
                            ephemeral=True
                        )
                        return

                    data = await resp.json()

            # Parse and insert navaids
            navaids = data.get('items', [])
            log.info(f"Received {len(navaids)} navaids from OpenAIP for {theater}")

            inserted = 0
            updated = 0

            async with self.apool.connection() as conn:
                for navaid in navaids:
                    try:
                        # Extract data from OpenAIP format
                        identifier = navaid.get('identifier', '').upper()
                        if not identifier or len(identifier) > 10:
                            continue

                        name = navaid.get('name', '')
                        navaid_type = navaid.get('type', 0)

                        # Map OpenAIP type codes to our fix types
                        # OpenAIP types: 0=UNKNOWN, 1=DME, 2=NDB, 3=TACAN, 4=VOR, 5=VORDME, 6=VORTAC, 7=NDB_DME
                        type_map = {
                            1: 'DME',
                            2: 'NDB',
                            3: 'TACAN',
                            4: 'VOR',
                            5: 'VORDME',
                            6: 'VORTAC',
                            7: 'NDB',
                        }
                        fix_type = type_map.get(navaid_type, 'WYP')

                        # Get coordinates from geometry
                        geometry = navaid.get('geometry', {})
                        coords = geometry.get('coordinates', [])
                        if len(coords) < 2:
                            continue

                        longitude = coords[0]
                        latitude = coords[1]

                        # Get frequency if available
                        frequency = navaid.get('frequency')
                        if frequency:
                            # Format frequency nicely
                            freq_val = frequency.get('value')
                            freq_unit = frequency.get('unit', '')
                            if freq_val:
                                if freq_unit == 'kHz':
                                    frequency_str = f"{freq_val}"
                                else:
                                    frequency_str = f"{freq_val:.2f}"
                            else:
                                frequency_str = None
                        else:
                            frequency_str = None

                        # Upsert into database
                        result = await conn.execute("""
                            INSERT INTO flightplan_navigation_fixes
                            (identifier, name, fix_type, latitude, longitude, map_theater, frequency, source)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'openaip')
                            ON CONFLICT (identifier, map_theater) DO UPDATE SET
                                name = EXCLUDED.name,
                                fix_type = EXCLUDED.fix_type,
                                latitude = EXCLUDED.latitude,
                                longitude = EXCLUDED.longitude,
                                frequency = EXCLUDED.frequency,
                                source = 'openaip'
                            RETURNING (xmax = 0) as inserted
                        """, (identifier, name, fix_type, latitude, longitude, theater, frequency_str))

                        row = await result.fetchone()
                        if row and row[0]:
                            inserted += 1
                        else:
                            updated += 1

                    except Exception as e:
                        log.warning(f"Error processing navaid {navaid.get('identifier', '?')}: {e}")
                        continue

                # Also try to fetch reporting points / waypoints
                try:
                    rp_url = f"https://api.core.openaip.net/api/reporting-points?bbox={bbox_str}&limit=500"
                    log.info(f"Fetching reporting points from OpenAIP: {rp_url}")

                    async with aiohttp.ClientSession() as session:
                        async with session.get(rp_url, headers=headers) as resp:
                            if resp.status == 200:
                                rp_data = await resp.json()
                                reporting_points = rp_data.get('items', [])
                                log.info(f"Received {len(reporting_points)} reporting points from OpenAIP for {theater}")

                                for rp in reporting_points:
                                    try:
                                        identifier = rp.get('identifier', '').upper()
                                        if not identifier or len(identifier) > 10:
                                            continue

                                        name = rp.get('name', '')

                                        geometry = rp.get('geometry', {})
                                        coords = geometry.get('coordinates', [])
                                        if len(coords) < 2:
                                            continue

                                        longitude = coords[0]
                                        latitude = coords[1]

                                        result = await conn.execute("""
                                            INSERT INTO flightplan_navigation_fixes
                                            (identifier, name, fix_type, latitude, longitude, map_theater, source)
                                            VALUES (%s, %s, 'WYP', %s, %s, %s, 'openaip')
                                            ON CONFLICT (identifier, map_theater) DO UPDATE SET
                                                name = EXCLUDED.name,
                                                latitude = EXCLUDED.latitude,
                                                longitude = EXCLUDED.longitude,
                                                source = 'openaip'
                                            RETURNING (xmax = 0) as inserted
                                        """, (identifier, name, latitude, longitude, theater))

                                        row = await result.fetchone()
                                        if row and row[0]:
                                            inserted += 1
                                        else:
                                            updated += 1

                                    except Exception as e:
                                        log.warning(f"Error processing reporting point {rp.get('identifier', '?')}: {e}")
                                        continue
                except Exception as e:
                    log.warning(f"Error fetching reporting points: {e}")

            embed = discord.Embed(
                title=_('Navigation Fixes Synced'),
                description=_('Synced navigation fixes from OpenAIP for {}.').format(theater),
                color=discord.Color.green()
            )
            embed.add_field(name=_('Inserted'), value=str(inserted), inline=True)
            embed.add_field(name=_('Updated'), value=str(updated), inline=True)
            embed.add_field(name=_('Total Navaids'), value=str(len(navaids)), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        except aiohttp.ClientError as e:
            log.error(f"OpenAIP API request failed: {e}")
            await interaction.followup.send(
                _('Failed to connect to OpenAIP API: {}').format(str(e)),
                ephemeral=True
            )
        except Exception as e:
            log.error(f"Error syncing fixes: {e}")
            await interaction.followup.send(
                _('Error syncing fixes: {}').format(str(e)),
                ephemeral=True
            )

    @fix.command(name='count', description=_('Count navigation fixes by theater'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def fix_count(self, interaction: discord.Interaction):
        """Show count of navigation fixes per theater."""
        ephemeral = utils.get_ephemeral(interaction)

        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT map_theater, fix_type, COUNT(*)
                FROM flightplan_navigation_fixes
                GROUP BY map_theater, fix_type
                ORDER BY map_theater, fix_type
            """)
            rows = await cursor.fetchall()

        if not rows:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('No navigation fixes in database.'), ephemeral=ephemeral)
            return

        # Organize by theater
        theaters = {}
        for theater, fix_type, count in rows:
            if theater not in theaters:
                theaters[theater] = {}
            theaters[theater][fix_type] = count

        embed = discord.Embed(title=_('Navigation Fix Counts'), color=discord.Color.blue())

        for theater, types in sorted(theaters.items()):
            total = sum(types.values())
            type_str = ', '.join(f"{t}: {c}" for t, c in sorted(types.items()))
            embed.add_field(
                name=f"{theater} ({total} total)",
                value=type_str,
                inline=False
            )

        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @fix.command(name='seed', description=_('Load seed navigation fixes from bundled data'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def fix_seed(self, interaction: discord.Interaction):
        """Load navigation fixes from the bundled seed SQL file."""
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)

        # Find the seed SQL file
        import os
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        seed_file = os.path.join(plugin_dir, 'db', 'seed_navigation_fixes.sql')

        if not os.path.exists(seed_file):
            await interaction.followup.send(
                _('Seed file not found: {}').format(seed_file),
                ephemeral=True
            )
            return

        try:
            with open(seed_file, 'r', encoding='utf-8') as f:
                seed_sql = f.read()

            async with self.apool.connection() as conn:
                await conn.execute(seed_sql)

                # Count what we loaded
                cursor = await conn.execute("""
                    SELECT map_theater, COUNT(*) FROM flightplan_navigation_fixes
                    GROUP BY map_theater ORDER BY map_theater
                """)
                rows = await cursor.fetchall()

            embed = discord.Embed(
                title=_('Seed Data Loaded'),
                description=_('Navigation fixes loaded from seed file.'),
                color=discord.Color.green()
            )

            for theater, count in rows:
                embed.add_field(name=theater, value=str(count), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=ephemeral)

        except Exception as e:
            log.error(f"Error loading seed data: {e}")
            await interaction.followup.send(
                _('Error loading seed data: {}').format(str(e)),
                ephemeral=True
            )


async def setup(bot: DCSServerBot):
    await bot.add_cog(FlightPlan(bot, FlightPlanEventListener))
