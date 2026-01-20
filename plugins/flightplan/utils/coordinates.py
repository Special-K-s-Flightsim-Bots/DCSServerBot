"""
Coordinate parsing utilities for flight plan waypoints.

Supports multiple input formats:
- Airbase names: "Batumi", "Senaki-Kolkhi"
- MGRS coordinates: "38TLN1234567890"
- DMS coordinates: "N41°30'00\" E044°15'00\"" or "N413000 E0441500"
- Decimal degrees: "41.5, 44.25"
- User-defined waypoints: "@PANTHER" (from flightplan_waypoints table)
- Navigation fixes: "ADLER", "TSK" (from flightplan_navigation_fixes table)
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, TYPE_CHECKING

from core.utils.dcs import dms_to_dd, mgrs_to_dd

if TYPE_CHECKING:
    from core import Server
    from psycopg import AsyncConnection


class WaypointType(Enum):
    AIRBASE = "airbase"
    MGRS = "mgrs"
    DMS = "dms"
    DECIMAL = "decimal"
    USER_WAYPOINT = "user_waypoint"
    NAV_FIX = "nav_fix"
    UNKNOWN = "unknown"


@dataclass
class ParsedWaypoint:
    """Result of parsing a waypoint input."""
    name: str
    waypoint_type: WaypointType
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    position_x: Optional[float] = None
    position_z: Optional[float] = None
    altitude: Optional[int] = None
    frequency: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'name': self.name,
            'type': self.waypoint_type.value,
            'lat': self.latitude,
            'lon': self.longitude,
            'x': self.position_x,
            'z': self.position_z,
            'altitude': self.altitude,
            'frequency': self.frequency,
        }


# Regex patterns
MGRS_PATTERN = re.compile(r'^(\d{1,2}[A-Z])([A-Z]{2})(\d{2,10})$', re.IGNORECASE)
DECIMAL_PATTERN = re.compile(r'^(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)$')
DMS_COMPACT_PATTERN = re.compile(
    r'^([NS])(\d{2})(\d{2})(\d{2}(?:\.\d+)?)\s*([EW])(\d{3})(\d{2})(\d{2}(?:\.\d+)?)$',
    re.IGNORECASE
)
DMS_SYMBOL_PATTERN = re.compile(
    r'^([NS])\s*(\d+)[°]\s*(\d+)[\'′]\s*(\d+(?:\.\d+)?)[\"″]?\s*'
    r'([EW])\s*(\d+)[°]\s*(\d+)[\'′]\s*(\d+(?:\.\d+)?)[\"″]?$',
    re.IGNORECASE
)


def _parse_mgrs(value: str) -> Optional[tuple[float, float]]:
    """Parse MGRS coordinate string to lat/lon."""
    match = MGRS_PATTERN.match(value.replace(' ', ''))
    if not match:
        return None
    try:
        lat, lon = mgrs_to_dd(value.replace(' ', ''))
        return lat, lon
    except Exception:
        return None


def _parse_decimal(value: str) -> Optional[tuple[float, float]]:
    """Parse decimal degree coordinate string to lat/lon."""
    match = DECIMAL_PATTERN.match(value.strip())
    if not match:
        return None
    try:
        lat = float(match.group(1))
        lon = float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except ValueError:
        pass
    return None


def _parse_dms(value: str) -> Optional[tuple[float, float]]:
    """Parse DMS coordinate string to lat/lon."""
    # Try compact format first: N413000 E0441500
    match = DMS_COMPACT_PATTERN.match(value.replace(' ', ''))
    if match:
        try:
            lat_dir, lat_d, lat_m, lat_s = match.group(1), match.group(2), match.group(3), match.group(4)
            lon_dir, lon_d, lon_m, lon_s = match.group(5), match.group(6), match.group(7), match.group(8)

            lat = float(lat_d) + float(lat_m) / 60 + float(lat_s) / 3600
            if lat_dir.upper() == 'S':
                lat = -lat

            lon = float(lon_d) + float(lon_m) / 60 + float(lon_s) / 3600
            if lon_dir.upper() == 'W':
                lon = -lon

            return lat, lon
        except ValueError:
            pass

    # Try symbol format: N 41°30'00" E 044°15'00"
    match = DMS_SYMBOL_PATTERN.match(value.strip())
    if match:
        try:
            lat_dir, lat_d, lat_m, lat_s = match.group(1), match.group(2), match.group(3), match.group(4)
            lon_dir, lon_d, lon_m, lon_s = match.group(5), match.group(6), match.group(7), match.group(8)

            lat = float(lat_d) + float(lat_m) / 60 + float(lat_s) / 3600
            if lat_dir.upper() == 'S':
                lat = -lat

            lon = float(lon_d) + float(lon_m) / 60 + float(lon_s) / 3600
            if lon_dir.upper() == 'W':
                lon = -lon

            return lat, lon
        except ValueError:
            pass

    return None


async def _lookup_airbase(name: str, server: "Server") -> Optional[ParsedWaypoint]:
    """Look up airbase by name from current mission."""
    if not server or not server.current_mission or not server.current_mission.airbases:
        return None

    name_lower = name.lower()
    for airbase in server.current_mission.airbases:
        if airbase.get('name', '').lower() == name_lower:
            pos = airbase.get('position', {})
            return ParsedWaypoint(
                name=airbase['name'],
                waypoint_type=WaypointType.AIRBASE,
                latitude=pos.get('lat'),
                longitude=pos.get('lon'),
                position_x=pos.get('x'),
                position_z=pos.get('z'),
            )

    # Partial match
    for airbase in server.current_mission.airbases:
        if name_lower in airbase.get('name', '').lower():
            pos = airbase.get('position', {})
            return ParsedWaypoint(
                name=airbase['name'],
                waypoint_type=WaypointType.AIRBASE,
                latitude=pos.get('lat'),
                longitude=pos.get('lon'),
                position_x=pos.get('x'),
                position_z=pos.get('z'),
            )

    return None


async def _lookup_user_waypoint(name: str, theater: str, conn: "AsyncConnection") -> Optional[ParsedWaypoint]:
    """Look up user-defined waypoint from database."""
    cursor = await conn.execute("""
        SELECT name, position_x, position_z, latitude, longitude, altitude
        FROM flightplan_waypoints
        WHERE LOWER(name) = LOWER(%s)
        AND (map_theater = %s OR map_theater IS NULL)
        AND is_public = TRUE
        ORDER BY map_theater DESC NULLS LAST
        LIMIT 1
    """, (name, theater))
    row = await cursor.fetchone()

    if row:
        return ParsedWaypoint(
            name=row[0],
            waypoint_type=WaypointType.USER_WAYPOINT,
            position_x=row[1],
            position_z=row[2],
            latitude=row[3],
            longitude=row[4],
            altitude=row[5],
        )
    return None


async def _lookup_nav_fix(identifier: str, theater: str, conn: "AsyncConnection") -> Optional[ParsedWaypoint]:
    """Look up navigation fix from database."""
    cursor = await conn.execute("""
        SELECT identifier, name, position_x, position_z, latitude, longitude, frequency
        FROM flightplan_navigation_fixes
        WHERE UPPER(identifier) = UPPER(%s)
        AND map_theater = %s
        LIMIT 1
    """, (identifier, theater))
    row = await cursor.fetchone()

    if row:
        return ParsedWaypoint(
            name=row[0],
            waypoint_type=WaypointType.NAV_FIX,
            position_x=row[2],
            position_z=row[3],
            latitude=row[4],
            longitude=row[5],
            frequency=row[6],
        )
    return None


async def parse_waypoint_input(
    value: str,
    server: Optional["Server"] = None,
    conn: Optional["AsyncConnection"] = None,
    theater: Optional[str] = None
) -> ParsedWaypoint:
    """
    Parse waypoint input in various formats.

    Resolution order:
    1. User-defined waypoints (with @ prefix)
    2. MGRS coordinates
    3. DMS coordinates
    4. Decimal coordinates
    5. DCS Airbases (from mission)
    6. Navigation fixes (from database)

    Parameters
    ----------
    value : str
        The waypoint input string to parse
    server : Server, optional
        DCS server for airbase lookup
    conn : AsyncConnection, optional
        Database connection for waypoint/fix lookups
    theater : str, optional
        Map theater for database lookups (e.g., "Caucasus", "Syria")

    Returns
    -------
    ParsedWaypoint
        Parsed waypoint with coordinates, or error if parsing failed
    """
    value = value.strip()

    if not value:
        return ParsedWaypoint(
            name="",
            waypoint_type=WaypointType.UNKNOWN,
            error="Empty waypoint value"
        )

    # Determine theater from server if not provided
    if not theater and server and server.current_mission:
        theater = server.current_mission.map

    # 1. User-defined waypoint (starts with @)
    if value.startswith('@') and conn and theater:
        wp_name = value[1:].strip()
        result = await _lookup_user_waypoint(wp_name, theater, conn)
        if result:
            return result
        return ParsedWaypoint(
            name=wp_name,
            waypoint_type=WaypointType.UNKNOWN,
            error=f"User waypoint '@{wp_name}' not found"
        )

    # 2. Try MGRS
    coords = _parse_mgrs(value)
    if coords:
        return ParsedWaypoint(
            name=value.upper(),
            waypoint_type=WaypointType.MGRS,
            latitude=coords[0],
            longitude=coords[1],
        )

    # 3. Try DMS
    coords = _parse_dms(value)
    if coords:
        return ParsedWaypoint(
            name=value,
            waypoint_type=WaypointType.DMS,
            latitude=coords[0],
            longitude=coords[1],
        )

    # 4. Try decimal degrees
    coords = _parse_decimal(value)
    if coords:
        return ParsedWaypoint(
            name=f"{coords[0]:.4f}, {coords[1]:.4f}",
            waypoint_type=WaypointType.DECIMAL,
            latitude=coords[0],
            longitude=coords[1],
        )

    # 5. Try airbase lookup
    if server:
        result = await _lookup_airbase(value, server)
        if result:
            return result

    # 6. Try navigation fix lookup
    if conn and theater:
        result = await _lookup_nav_fix(value, theater, conn)
        if result:
            return result

    # If nothing matched, return as unknown (could be a simple name for route text)
    return ParsedWaypoint(
        name=value,
        waypoint_type=WaypointType.UNKNOWN,
        error=f"Could not parse waypoint: {value}"
    )


async def parse_waypoint_list(
    waypoints_str: str,
    server: Optional["Server"] = None,
    conn: Optional["AsyncConnection"] = None,
    theater: Optional[str] = None
) -> list[ParsedWaypoint]:
    """
    Parse a comma-separated list of waypoints.

    Parameters
    ----------
    waypoints_str : str
        Comma-separated waypoint strings
    server : Server, optional
        DCS server for airbase lookup
    conn : AsyncConnection, optional
        Database connection for waypoint/fix lookups
    theater : str, optional
        Map theater for database lookups

    Returns
    -------
    list[ParsedWaypoint]
        List of parsed waypoints
    """
    if not waypoints_str:
        return []

    waypoints = []
    for wp_str in waypoints_str.split(','):
        wp_str = wp_str.strip()
        if wp_str:
            result = await parse_waypoint_input(wp_str, server, conn, theater)
            waypoints.append(result)

    return waypoints
