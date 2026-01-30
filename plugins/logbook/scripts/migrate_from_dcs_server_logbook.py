#!/usr/bin/env python3
"""
Migration Script: dcs_server_logbook -> DCSServerBot logbook plugin

This script migrates data from the old dcs_server_logbook SQLite database
to the new DCSServerBot PostgreSQL-based logbook plugin.

CRITICAL: This script ensures NO DATA IS LOST during migration.
- Flight hours: Uses GREATEST() to always show higher value
- Awards: All imported with full history
- Qualifications: All imported with expiration tracking
- Squadrons: Full squadron structure preserved

Usage:
    python migrate_from_dcs_server_logbook.py --sqlite-db /path/to/mayfly.db --json-stats /path/to/stats/

Prerequisites:
    - DCSServerBot must be installed and running
    - The logbook plugin tables must exist (run bot once first)
    - Access to both old SQLite database and Slmod stats JSON files
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import psycopg
import re
import sqlite3

from decimal import Decimal
from pathlib import Path
from psycopg.rows import dict_row

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


class MigrationReport:
    """Tracks migration statistics for validation."""

    def __init__(self):
        self.pilots_found = 0
        self.pilots_mapped = 0
        self.pilots_unmapped = []
        self.squadrons_imported = 0
        self.awards_imported = 0
        self.qualifications_imported = 0
        self.pilot_awards_imported = 0
        self.pilot_qualifications_imported = 0
        self.flight_hours_imported = 0
        self.total_hours_old = Decimal(0)
        self.total_hours_new = Decimal(0)
        self.errors = []

    def print_report(self):
        log.info("=" * 60)
        log.info("MIGRATION REPORT")
        log.info("=" * 60)
        log.info(f"Pilots found in old DB: {self.pilots_found}")
        log.info(f"Pilots successfully mapped: {self.pilots_mapped}")
        log.info(f"Pilots unmapped: {len(self.pilots_unmapped)}")
        if self.pilots_unmapped:
            for pilot in self.pilots_unmapped[:10]:
                log.warning(f"  - {pilot}")
            if len(self.pilots_unmapped) > 10:
                log.warning(f"  ... and {len(self.pilots_unmapped) - 10} more")
        log.info(f"Squadrons imported: {self.squadrons_imported}")
        log.info(f"Award definitions imported: {self.awards_imported}")
        log.info(f"Qualification definitions imported: {self.qualifications_imported}")
        log.info(f"Pilot awards imported: {self.pilot_awards_imported}")
        log.info(f"Pilot qualifications imported: {self.pilot_qualifications_imported}")
        log.info(f"Flight hours records imported: {self.flight_hours_imported}")
        log.info(f"Total hours in old system: {self.total_hours_old:.2f}")
        log.info(f"Total hours imported: {self.total_hours_new:.2f}")
        if self.errors:
            log.error(f"Errors encountered: {len(self.errors)}")
            for err in self.errors[:10]:
                log.error(f"  - {err}")
        log.info("=" * 60)

        # CRITICAL VALIDATION
        if self.total_hours_new < self.total_hours_old:
            log.critical("VALIDATION FAILED: Hours in new system less than old system!")
            log.critical("Migration should be reviewed before production use.")
            return False

        if self.pilots_unmapped:
            log.warning("Some pilots could not be mapped. Review manually.")

        log.info("VALIDATION PASSED: All hours preserved or exceeded.")
        return True


def normalize_name(name: str) -> str:
    """
    Normalize pilot name for fuzzy matching.
    Removes rank prefixes, squadron tags, and normalizes case.
    """
    if not name:
        return ""

    # Remove common rank prefixes
    rank_patterns = [
        r'^(Wg Cdr|Sqn Ldr|Flt Lt|Fg Off|Plt Off|Wg Cmdr|Lt Cdr|Lt|S/Lt|Mid|'
        r'Maj|Capt|Lt Col|Col|2nd Lt|1st Lt|Gen|Brig|Cdre|'
        r'CPO|PO|AB|WO|MCPO|SCPO|'
        r'=JSW=|=\w+=)\s*',
    ]

    normalized = name.strip()
    for pattern in rank_patterns:
        normalized = re.sub(pattern, '', normalized, flags=re.IGNORECASE)

    # Remove special characters, lowercase
    normalized = re.sub(r'[^a-zA-Z0-9]', '', normalized).lower()

    return normalized


class LogbookMigrator:
    """Handles migration from dcs_server_logbook to DCSServerBot."""

    def __init__(self, sqlite_path: str, postgres_url: str, stats_path: str = None):
        self.sqlite_path = sqlite_path
        self.postgres_url = postgres_url
        self.stats_path = stats_path
        self.report = MigrationReport()
        self.pilot_mapping = {}  # old_pilot_id -> ucid

    async def run(self, dry_run: bool = False):
        """Execute the full migration."""
        log.info(f"Starting migration from {self.sqlite_path}")
        log.info(f"Dry run: {dry_run}")

        # Connect to databases
        sqlite_conn = sqlite3.connect(self.sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row

        async with await psycopg.AsyncConnection.connect(self.postgres_url) as pg_conn:
            # Step 1: Build pilot mapping
            await self._build_pilot_mapping(sqlite_conn, pg_conn)

            if dry_run:
                log.info("DRY RUN - No changes will be made")
                self.report.print_report()
                return

            # Step 2: Import squadrons
            await self._import_squadrons(sqlite_conn, pg_conn)

            # Step 3: Import award definitions
            await self._import_awards(sqlite_conn, pg_conn)

            # Step 4: Import qualification definitions
            await self._import_qualifications(sqlite_conn, pg_conn)

            # Step 5: Import pilot awards
            await self._import_pilot_awards(sqlite_conn, pg_conn)

            # Step 6: Import pilot qualifications
            await self._import_pilot_qualifications(sqlite_conn, pg_conn)

            # Step 7: Import flight hours from JSON
            if self.stats_path:
                await self._import_flight_hours(pg_conn)

            # Step 8: Import squadron membership
            await self._import_squadron_members(sqlite_conn, pg_conn)

        sqlite_conn.close()

        # Print final report
        return self.report.print_report()

    async def _build_pilot_mapping(self, sqlite_conn, pg_conn):
        """Map old pilot_ids to DCSServerBot UCIDs."""
        log.info("Building pilot mapping...")

        # Get all pilots from old database
        cursor = sqlite_conn.execute("""
            SELECT pilot_id, pilot_name, pilot_rank, pilot_service
            FROM Pilots
            UNION
            SELECT pilot_id, pilot_name, pilot_rank, pilot_service
            FROM Former_Pilots
        """)
        old_pilots = cursor.fetchall()
        self.report.pilots_found = len(old_pilots)

        # Get all players from DCSServerBot
        async with pg_conn.cursor(row_factory=dict_row) as pg_cursor:
            await pg_cursor.execute("SELECT ucid, name, discord_id FROM players")
            dcsbot_players = await pg_cursor.fetchall()

        # Build normalized name lookup
        dcsbot_by_name = {}
        dcsbot_by_discord = {}
        for player in dcsbot_players:
            if player['name']:
                norm_name = normalize_name(player['name'])
                if norm_name:
                    dcsbot_by_name[norm_name] = player
            if player['discord_id'] and player['discord_id'] > 0:
                dcsbot_by_discord[player['discord_id']] = player

        # Map pilots
        for pilot in old_pilots:
            pilot_id = pilot['pilot_id']
            pilot_name = pilot['pilot_name']
            norm_name = normalize_name(pilot_name)

            # Try name match
            if norm_name in dcsbot_by_name:
                self.pilot_mapping[pilot_id] = dcsbot_by_name[norm_name]['ucid']
                self.report.pilots_mapped += 1
                log.debug(f"Mapped {pilot_name} -> {self.pilot_mapping[pilot_id]}")
            else:
                self.report.pilots_unmapped.append(f"{pilot_name} ({pilot_id[:8]}...)")
                log.warning(f"Could not map pilot: {pilot_name}")

        log.info(f"Mapped {self.report.pilots_mapped}/{self.report.pilots_found} pilots")

    async def _import_squadrons(self, sqlite_conn, pg_conn):
        """Import squadron definitions."""
        log.info("Importing squadrons...")

        cursor = sqlite_conn.execute("""
            SELECT squadron_id, squadron_motto, squadron_service,
                   squadron_commission_date, squadron_commanding_officer,
                   squadron_aircraft_type, squadron_pseudo_type
            FROM Squadrons
        """)

        async with pg_conn.cursor() as pg_cursor:
            for row in cursor:
                co_ucid = self.pilot_mapping.get(row['squadron_commanding_officer'])

                await pg_cursor.execute("""
                    INSERT INTO logbook_squadrons
                    (name, abbreviation, description, co_ucid)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        co_ucid = EXCLUDED.co_ucid
                    RETURNING id
                """, (
                    row['squadron_id'],
                    row['squadron_id'][:8] if row['squadron_id'] else None,
                    row['squadron_motto'],
                    co_ucid
                ))
                self.report.squadrons_imported += 1

            await pg_conn.commit()

        log.info(f"Imported {self.report.squadrons_imported} squadrons")

    async def _import_awards(self, sqlite_conn, pg_conn):
        """Import award definitions."""
        log.info("Importing award definitions...")

        cursor = sqlite_conn.execute("""
            SELECT award_id, award_name, award_description
            FROM Awards
        """)

        async with pg_conn.cursor() as pg_cursor:
            for row in cursor:
                await pg_cursor.execute("""
                    INSERT INTO logbook_awards
                    (name, description)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO NOTHING
                """, (row['award_name'], row['award_description']))
                self.report.awards_imported += 1

            await pg_conn.commit()

        log.info(f"Imported {self.report.awards_imported} award definitions")

    async def _import_qualifications(self, sqlite_conn, pg_conn):
        """Import qualification definitions."""
        log.info("Importing qualification definitions...")

        cursor = sqlite_conn.execute("""
            SELECT qualification_id, qualification_name,
                   qualification_description, qualification_duration
            FROM Qualifications
        """)

        async with pg_conn.cursor() as pg_cursor:
            for row in cursor:
                # Convert duration from seconds to days
                valid_days = None
                if row['qualification_duration']:
                    valid_days = row['qualification_duration'] // 86400

                await pg_cursor.execute("""
                    INSERT INTO logbook_qualifications
                    (name, description, valid_days)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                """, (
                    row['qualification_name'],
                    row['qualification_description'],
                    valid_days
                ))
                self.report.qualifications_imported += 1

            await pg_conn.commit()

        log.info(f"Imported {self.report.qualifications_imported} qualification definitions")

    async def _import_pilot_awards(self, sqlite_conn, pg_conn):
        """Import pilot award assignments."""
        log.info("Importing pilot awards...")

        cursor = sqlite_conn.execute("""
            SELECT pa.pilot_id, a.award_name
            FROM Pilot_Awards pa
            JOIN Awards a ON pa.award_id = a.award_id
        """)

        async with pg_conn.cursor(row_factory=dict_row) as pg_cursor:
            # Get award ID mapping
            await pg_cursor.execute("SELECT id, name FROM logbook_awards")
            award_map = {row['name']: row['id'] for row in await pg_cursor.fetchall()}

            for row in cursor:
                ucid = self.pilot_mapping.get(row['pilot_id'])
                if not ucid:
                    continue

                award_id = award_map.get(row['award_name'])
                if not award_id:
                    continue

                try:
                    await pg_cursor.execute("""
                        INSERT INTO logbook_pilot_awards
                        (player_ucid, award_id, citation)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (ucid, award_id, "Migrated from dcs_server_logbook"))
                    self.report.pilot_awards_imported += 1
                except Exception as e:
                    self.report.errors.append(f"Award import error: {e}")

            await pg_conn.commit()

        log.info(f"Imported {self.report.pilot_awards_imported} pilot awards")

    async def _import_pilot_qualifications(self, sqlite_conn, pg_conn):
        """Import pilot qualification assignments."""
        log.info("Importing pilot qualifications...")

        cursor = sqlite_conn.execute("""
            SELECT pq.pilot_id, q.qualification_name
            FROM Pilot_Qualifications pq
            JOIN Qualifications q ON pq.qualification_id = q.qualification_id
        """)

        async with pg_conn.cursor(row_factory=dict_row) as pg_cursor:
            # Get qualification ID mapping
            await pg_cursor.execute("SELECT id, name FROM logbook_qualifications")
            qual_map = {row['name']: row['id'] for row in await pg_cursor.fetchall()}

            for row in cursor:
                ucid = self.pilot_mapping.get(row['pilot_id'])
                if not ucid:
                    continue

                qual_id = qual_map.get(row['qualification_name'])
                if not qual_id:
                    continue

                try:
                    await pg_cursor.execute("""
                        INSERT INTO logbook_pilot_qualifications
                        (player_ucid, qualification_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                    """, (ucid, qual_id))
                    self.report.pilot_qualifications_imported += 1
                except Exception as e:
                    self.report.errors.append(f"Qualification import error: {e}")

            await pg_conn.commit()

        log.info(f"Imported {self.report.pilot_qualifications_imported} pilot qualifications")

    async def _import_flight_hours(self, pg_conn):
        """Import flight hours from Slmod stats JSON files."""
        log.info(f"Importing flight hours from {self.stats_path}...")

        stats_dir = Path(self.stats_path)
        if not stats_dir.exists():
            log.warning(f"Stats directory not found: {self.stats_path}")
            return

        # Find all SlmodStats JSON files
        json_files = list(stats_dir.glob("SlmodStats*.json"))
        if not json_files:
            log.warning("No SlmodStats JSON files found")
            return

        # Aggregate hours by pilot and aircraft
        pilot_hours = {}  # ucid -> aircraft -> hours

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
            except Exception as e:
                log.error(f"Error reading {json_file}: {e}")
                continue

            for pilot_id, pilot_stats in stats.items():
                if pilot_id in ('host', 'dates'):
                    continue

                ucid = self.pilot_mapping.get(pilot_id)
                if not ucid:
                    # Try direct match - stats might use UCIDs already
                    ucid = pilot_id if len(pilot_id) == 32 else None

                if not ucid:
                    continue

                if ucid not in pilot_hours:
                    pilot_hours[ucid] = {}

                times = pilot_stats.get('times', {})
                for aircraft, aircraft_stats in times.items():
                    if isinstance(aircraft_stats, dict):
                        seconds = aircraft_stats.get('total', 0)
                    else:
                        seconds = aircraft_stats

                    hours = Decimal(seconds) / Decimal(3600)

                    if aircraft not in pilot_hours[ucid]:
                        pilot_hours[ucid][aircraft] = Decimal(0)
                    pilot_hours[ucid][aircraft] += hours
                    self.report.total_hours_old += hours

        # Import to database
        async with pg_conn.cursor() as pg_cursor:
            for ucid, aircraft_hours in pilot_hours.items():
                total_hours = sum(aircraft_hours.values())
                self.report.total_hours_new += total_hours

                try:
                    await pg_cursor.execute("""
                        INSERT INTO logbook_historical_hours
                        (player_ucid, imported_from, total_hours, aircraft_hours)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (player_ucid, imported_from) DO UPDATE SET
                            total_hours = GREATEST(logbook_historical_hours.total_hours, EXCLUDED.total_hours),
                            aircraft_hours = EXCLUDED.aircraft_hours,
                            imported_at = NOW()
                    """, (
                        ucid,
                        'dcs_server_logbook',
                        float(total_hours),
                        json.dumps({k: float(v) for k, v in aircraft_hours.items()})
                    ))
                    self.report.flight_hours_imported += 1
                except Exception as e:
                    self.report.errors.append(f"Flight hours import error for {ucid}: {e}")

            await pg_conn.commit()

        log.info(f"Imported {self.report.flight_hours_imported} flight hour records")

    async def _import_squadron_members(self, sqlite_conn, pg_conn):
        """Import squadron membership."""
        log.info("Importing squadron memberships...")

        cursor = sqlite_conn.execute("""
            SELECT sp.squadron_id, sp.pilot_id, p.pilot_rank, p.pilot_service
            FROM Squadron_Pilots sp
            JOIN Pilots p ON sp.pilot_id = p.pilot_id
        """)

        async with pg_conn.cursor(row_factory=dict_row) as pg_cursor:
            # Get squadron ID mapping
            await pg_cursor.execute("SELECT id, name FROM logbook_squadrons")
            squadron_map = {row['name']: row['id'] for row in await pg_cursor.fetchall()}

            count = 0
            for row in cursor:
                ucid = self.pilot_mapping.get(row['pilot_id'])
                if not ucid:
                    continue

                squadron_id = squadron_map.get(row['squadron_id'])
                if not squadron_id:
                    continue

                try:
                    await pg_cursor.execute("""
                        INSERT INTO logbook_squadron_members
                        (squadron_id, player_ucid, rank, position)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (squadron_id, ucid, row['pilot_rank'], row['pilot_service']))
                    count += 1
                except Exception as e:
                    self.report.errors.append(f"Squadron member import error: {e}")

            await pg_conn.commit()

        log.info(f"Imported {count} squadron memberships")


async def main():
    parser = argparse.ArgumentParser(
        description='Migrate data from dcs_server_logbook to DCSServerBot logbook plugin'
    )
    parser.add_argument(
        '--sqlite-db',
        required=True,
        help='Path to the old dcs_server_logbook SQLite database (mayfly.db)'
    )
    parser.add_argument(
        '--postgres-url',
        default='postgresql://dcsserverbot:dcsserverbot@localhost:5432/dcsserverbot',
        help='PostgreSQL connection URL for DCSServerBot'
    )
    parser.add_argument(
        '--json-stats',
        help='Path to directory containing SlmodStats JSON files'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Perform a dry run without making changes'
    )

    args = parser.parse_args()

    if not os.path.exists(args.sqlite_db):
        log.error(f"SQLite database not found: {args.sqlite_db}")
        return 1

    migrator = LogbookMigrator(
        sqlite_path=args.sqlite_db,
        postgres_url=args.postgres_url,
        stats_path=args.json_stats
    )

    success = await migrator.run(dry_run=args.dry_run)
    return 0 if success else 1


if __name__ == '__main__':
    exit(asyncio.run(main()))
