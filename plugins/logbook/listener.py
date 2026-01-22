from core import EventListener, Server, Player, event, get_translation
from datetime import datetime, timedelta, timezone
from psycopg.rows import dict_row
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands import Logbook

_ = get_translation(__name__.split('.')[1])


class LogbookEventListener(EventListener["Logbook"]):
    """Event listener for logbook plugin - handles auto-grant qualifications."""

    async def check_qualification_requirements(self, player_ucid: str) -> list[dict]:
        """
        Check if a player meets requirements for any qualifications they don't have.
        Returns list of qualifications that should be granted.
        """
        qualifications_to_grant = []

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get all qualifications with requirements that the player doesn't have
                await cursor.execute("""
                    SELECT q.id, q.name, q.requirements, q.valid_days
                    FROM logbook_qualifications q
                    WHERE q.requirements IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM logbook_pilot_qualifications pq
                        WHERE pq.player_ucid = %s AND pq.qualification_id = q.id
                    )
                """, (player_ucid,))
                qualifications = await cursor.fetchall()

                if not qualifications:
                    return []

                # Get player stats from the view
                await cursor.execute("""
                    SELECT * FROM pilot_logbook_stats WHERE ucid = %s
                """, (player_ucid,))
                stats = await cursor.fetchone()

                if not stats:
                    return []

                # Get additional stats that might be needed for qualifications
                # Carrier landings from traps table (greenieboard plugin) if available
                carrier_landings = 0
                try:
                    await cursor.execute("""
                        SELECT COUNT(*) as count FROM traps
                        WHERE player_ucid = %s AND grade IS NOT NULL
                    """, (player_ucid,))
                    row = await cursor.fetchone()
                    if row:
                        carrier_landings = row['count']
                except Exception:
                    pass  # traps table might not exist (greenieboard plugin not installed)

                # PvP kills from missionstats table (kills where target is another player)
                pvp_kills = 0
                try:
                    await cursor.execute("""
                        SELECT COUNT(*) as count FROM missionstats
                        WHERE init_id = %s
                          AND target_id IS NOT NULL
                          AND target_id != %s
                          AND event = 'S_EVENT_KILL'
                    """, (player_ucid, player_ucid))
                    row = await cursor.fetchone()
                    if row:
                        pvp_kills = row['count']
                except Exception:
                    pass  # missionstats table might not exist

                # Build stats dictionary for requirement checking
                player_stats = {
                    'flight_hours': float(stats.get('total_hours', 0) or 0),
                    'total_kills': int(stats.get('total_kills', 0) or 0),
                    'pvp_kills': pvp_kills,
                    'deaths': int(stats.get('total_deaths', 0) or 0),
                    'takeoffs': int(stats.get('total_takeoffs', 0) or 0),
                    'landings': int(stats.get('total_landings', 0) or 0),
                    'ejections': int(stats.get('total_ejections', 0) or 0),
                    'crashes': int(stats.get('total_crashes', 0) or 0),
                    'carrier_landings': carrier_landings,
                }

                # Check each qualification's requirements
                for qual in qualifications:
                    requirements = qual.get('requirements')
                    if not requirements:
                        continue

                    if self._check_requirements(player_stats, requirements):
                        qualifications_to_grant.append(qual)

        return qualifications_to_grant

    def _check_requirements(self, player_stats: dict, requirements: dict) -> bool:
        """
        Check if player stats meet all requirements.
        Requirements format: {"flight_hours": 10, "carrier_landings": 5}
        """
        for req_key, req_value in requirements.items():
            player_value = player_stats.get(req_key, 0)

            # Handle comparison operators in key (e.g., "deaths_max": 10)
            if req_key.endswith('_max'):
                actual_key = req_key[:-4]
                player_value = player_stats.get(actual_key, 0)
                if player_value > req_value:
                    return False
            elif req_key.endswith('_min'):
                actual_key = req_key[:-4]
                player_value = player_stats.get(actual_key, 0)
                if player_value < req_value:
                    return False
            else:
                # Default: player must have at least this value
                if player_value < req_value:
                    return False

        return True

    async def auto_grant_qualifications(self, player_ucid: str) -> list[str]:
        """
        Auto-grant qualifications to a player if they meet the requirements.
        Returns list of qualification names granted.
        """
        qualifications = await self.check_qualification_requirements(player_ucid)
        granted = []

        if not qualifications:
            return granted

        async with self.apool.connection() as conn:
            for qual in qualifications:
                # Calculate expiration if applicable
                expires_at = None
                if qual.get('valid_days'):
                    expires_at = datetime.now(timezone.utc) + timedelta(days=qual['valid_days'])

                try:
                    await conn.execute("""
                        INSERT INTO logbook_pilot_qualifications
                        (player_ucid, qualification_id, granted_by, expires_at)
                        VALUES (%s, %s, NULL, %s)
                        ON CONFLICT (player_ucid, qualification_id) DO NOTHING
                    """, (player_ucid, qual['id'], expires_at))
                    granted.append(qual['name'])
                    self.log.info(f"Auto-granted qualification '{qual['name']}' to player {player_ucid}")
                except Exception as e:
                    self.log.error(f"Failed to auto-grant qualification: {e}")

        return granted

    async def revoke_expired_qualifications(self, player_ucid: str) -> list[str]:
        """
        Revoke any qualifications that have expired for a player.
        Returns list of qualification names revoked.
        """
        revoked = []

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Find all expired qualifications for this player
                await cursor.execute("""
                    SELECT pq.qualification_id, q.name, q.requirements
                    FROM logbook_pilot_qualifications pq
                    JOIN logbook_qualifications q ON pq.qualification_id = q.id
                    WHERE pq.player_ucid = %s
                    AND pq.expires_at IS NOT NULL
                    AND pq.expires_at < NOW()
                """, (player_ucid,))
                expired = await cursor.fetchall()

                for qual in expired:
                    try:
                        await conn.execute("""
                            DELETE FROM logbook_pilot_qualifications
                            WHERE player_ucid = %s AND qualification_id = %s
                        """, (player_ucid, qual['qualification_id']))
                        revoked.append(qual['name'])
                        self.log.info(f"Revoked expired qualification '{qual['name']}' from player {player_ucid}")
                    except Exception as e:
                        self.log.error(f"Failed to revoke expired qualification: {e}")

        return revoked

    async def refresh_renewable_qualifications(self, player_ucid: str) -> list[str]:
        """
        Check expiring qualifications and refresh them if the player still meets requirements.
        Qualifications with requirements can be auto-renewed if requirements are still met.
        Returns list of qualification names refreshed.
        """
        refreshed = []

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Find qualifications expiring within 7 days that have auto-grant requirements
                await cursor.execute("""
                    SELECT pq.qualification_id, q.name, q.requirements, q.valid_days
                    FROM logbook_pilot_qualifications pq
                    JOIN logbook_qualifications q ON pq.qualification_id = q.id
                    WHERE pq.player_ucid = %s
                    AND pq.expires_at IS NOT NULL
                    AND pq.expires_at < NOW() + INTERVAL '7 days'
                    AND q.requirements IS NOT NULL
                """, (player_ucid,))
                expiring = await cursor.fetchall()

                if not expiring:
                    return refreshed

                # Get player stats for requirement checking
                await cursor.execute("""
                    SELECT * FROM pilot_logbook_stats WHERE ucid = %s
                """, (player_ucid,))
                stats = await cursor.fetchone()

                if not stats:
                    return refreshed

                # Get carrier landings if available
                carrier_landings = 0
                try:
                    await cursor.execute("""
                        SELECT COUNT(*) as count FROM traps
                        WHERE player_ucid = %s AND grade IS NOT NULL
                    """, (player_ucid,))
                    row = await cursor.fetchone()
                    if row:
                        carrier_landings = row['count']
                except Exception:
                    pass

                # PvP kills from missionstats table (kills where target is another player)
                pvp_kills = 0
                try:
                    await cursor.execute("""
                        SELECT COUNT(*) as count FROM missionstats
                        WHERE init_id = %s
                          AND target_id IS NOT NULL
                          AND target_id != %s
                          AND event = 'S_EVENT_KILL'
                    """, (player_ucid, player_ucid))
                    row = await cursor.fetchone()
                    if row:
                        pvp_kills = row['count']
                except Exception:
                    pass

                player_stats = {
                    'flight_hours': float(stats.get('total_hours', 0) or 0),
                    'total_kills': int(stats.get('total_kills', 0) or 0),
                    'pvp_kills': pvp_kills,
                    'deaths': int(stats.get('total_deaths', 0) or 0),
                    'takeoffs': int(stats.get('total_takeoffs', 0) or 0),
                    'landings': int(stats.get('total_landings', 0) or 0),
                    'ejections': int(stats.get('total_ejections', 0) or 0),
                    'crashes': int(stats.get('total_crashes', 0) or 0),
                    'carrier_landings': carrier_landings,
                }

                for qual in expiring:
                    if self._check_requirements(player_stats, qual['requirements']):
                        # Player still meets requirements - refresh the expiration
                        new_expires = datetime.now(timezone.utc) + timedelta(days=qual['valid_days'])
                        try:
                            await conn.execute("""
                                UPDATE logbook_pilot_qualifications
                                SET expires_at = %s
                                WHERE player_ucid = %s AND qualification_id = %s
                            """, (new_expires, player_ucid, qual['qualification_id']))
                            refreshed.append(qual['name'])
                            self.log.info(f"Refreshed qualification '{qual['name']}' for player {player_ucid}")
                        except Exception as e:
                            self.log.error(f"Failed to refresh qualification: {e}")

        return refreshed

    async def process_qualification_lifecycle(self, player_ucid: str) -> tuple[list[str], list[str], list[str]]:
        """
        Process the full qualification lifecycle for a player:
        1. Revoke expired qualifications
        2. Refresh expiring qualifications if requirements still met
        3. Grant new qualifications if requirements met

        Returns tuple of (revoked, refreshed, granted) qualification name lists.
        """
        # Order matters: revoke first, then try to re-grant or refresh
        revoked = await self.revoke_expired_qualifications(player_ucid)
        refreshed = await self.refresh_renewable_qualifications(player_ucid)
        granted = await self.auto_grant_qualifications(player_ucid)

        return revoked, refreshed, granted

    @event(name="onMissionEnd")
    async def on_mission_end(self, server: Server, data: dict) -> None:
        """Check for auto-grant qualifications when mission ends."""
        config = self.get_config(server)
        if not config.get('auto_qualifications', True):
            return

        # Get all players who participated in this mission
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get players who have statistics records for recent activity
                await cursor.execute("""
                    SELECT DISTINCT player_ucid
                    FROM statistics
                    WHERE hop_off >= NOW() - INTERVAL '4 hours'
                """)
                players = await cursor.fetchall()

        for player_row in players:
            await self.process_qualification_lifecycle(player_row['player_ucid'])

    @event(name="onPlayerConnect")
    async def on_player_connect(self, server: Server, data: dict) -> None:
        """Check for auto-grant qualifications when player connects."""
        config = self.get_config(server)
        if not config.get('auto_qualifications', True):
            return

        ucid = data.get('ucid')
        if not ucid:
            return

        revoked, refreshed, granted = await self.process_qualification_lifecycle(ucid)

        # Notify player of any changes
        player: Player = server.get_player(ucid=ucid)
        if player:
            messages = []
            if revoked:
                messages.append(_("Qualification(s) expired: {}").format(", ".join(revoked)))
            if refreshed:
                messages.append(_("Qualification(s) renewed: {}").format(", ".join(refreshed)))
            if granted:
                messages.append(_("Congratulations! You've earned qualification(s): {}").format(", ".join(granted)))

            for msg in messages:
                await player.sendChatMessage(msg)
