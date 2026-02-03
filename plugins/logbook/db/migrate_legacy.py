#!/usr/bin/env python3
"""
Migrate data from legacy JSW logbook (mayfly.db) and SLMod stats (combinedStats.json)
to DCSServerBot's logbook plugin.

This script:
1. Imports squadrons from mayfly.db
2. Imports pilots with their squadron memberships, ranks, and service
3. Imports pilot awards with grant dates
4. Imports pilot qualifications with issue/expiry dates
5. Imports historical flight hours from combinedStats.json

For hours: If a pilot has X hours in combinedStats and Y hours in DCSServerBot statistics,
the historical hours are calculated as (X - Y) to avoid double-counting.

Usage:
    python migrate_legacy.py \\
        --mayfly /path/to/mayfly.db \\
        --stats /path/to/combinedStats.json \\
        --target "postgresql://user:pass@host:port/dbname"

Options:
    --dry-run   Show what would be migrated without making changes
"""
import argparse
import json
import sqlite3
import sys

from datetime import datetime, timezone
from decimal import Decimal

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    print("Error: psycopg not installed. Run: pip install psycopg[binary]")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mayfly', required=True, help='Path to mayfly.db (legacy logbook SQLite)')
    parser.add_argument('--stats', required=True, help='Path to combinedStats.json (SLMod stats)')
    parser.add_argument('--target', required=True, help='PostgreSQL connection URL for DCSServerBot')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated without making changes')
    return parser.parse_args()


def load_mayfly(path: str) -> dict:
    """Load all data from mayfly.db"""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    data = {
        'squadrons': [],
        'pilots': [],
        'squadron_pilots': [],
        'pilot_awards': [],
        'pilot_qualifications': [],
        'awards': {},
        'qualifications': {},
    }

    # Load squadrons
    cur.execute("SELECT * FROM Squadrons")
    data['squadrons'] = [dict(row) for row in cur.fetchall()]

    # Load pilots (active)
    cur.execute("SELECT * FROM Pilots")
    data['pilots'] = [dict(row) for row in cur.fetchall()]

    # Load squadron membership
    cur.execute("SELECT * FROM Squadron_Pilots")
    data['squadron_pilots'] = [dict(row) for row in cur.fetchall()]

    # Load pilot awards
    cur.execute("SELECT * FROM Pilot_Awards WHERE pilot_id IS NOT NULL AND pilot_id != ''")
    data['pilot_awards'] = [dict(row) for row in cur.fetchall()]

    # Load pilot qualifications
    cur.execute("SELECT * FROM Pilot_Qualifications WHERE pilot_id IS NOT NULL AND pilot_id != ''")
    data['pilot_qualifications'] = [dict(row) for row in cur.fetchall()]

    # Load award definitions (for name lookup)
    cur.execute("SELECT * FROM Awards")
    for row in cur.fetchall():
        data['awards'][row['award_id']] = dict(row)

    # Load qualification definitions (for name lookup)
    cur.execute("SELECT * FROM Qualifications")
    for row in cur.fetchall():
        data['qualifications'][row['qualification_id']] = dict(row)

    conn.close()
    return data


def load_combined_stats(path: str) -> dict:
    """Load combinedStats.json and calculate total hours per pilot"""
    with open(path, 'r') as f:
        raw_stats = json.load(f)

    pilot_hours = {}
    for ucid, stats in raw_stats.items():
        if 'times' not in stats:
            continue

        total_seconds = 0
        aircraft_hours = {}

        for aircraft, times in stats['times'].items():
            if 'total' in times:
                seconds = times['total']
                total_seconds += seconds
                hours = seconds / 3600.0
                aircraft_hours[aircraft] = round(hours, 2)

        if total_seconds > 0:
            pilot_hours[ucid] = {
                'total_hours': round(total_seconds / 3600.0, 2),
                'aircraft_hours': aircraft_hours,
                'last_join': stats.get('lastJoin'),
                'join_date': stats.get('joinDate'),
            }

    return pilot_hours


def epoch_to_datetime(epoch: int) -> datetime:
    """Convert Unix epoch to datetime"""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def migrate_squadrons(mayfly_data: dict, target_conn, dry_run: bool) -> dict:
    """Migrate squadrons and return mapping of old ID to new ID"""
    print("\n=== Migrating Squadrons ===")
    squadron_map = {}

    for sq in mayfly_data['squadrons']:
        name = sq['squadron_id']  # In mayfly, squadron_id is the name like "801 NAS"
        abbreviation = name.split()[0] if ' ' in name else name  # e.g., "801" from "801 NAS"
        description = sq.get('squadron_motto')
        co_ucid = sq.get('squadron_commanding_officer')
        # Derive service from squadron name patterns (RN squadrons are numbered, RAF use letters)
        service = 'RN' if name[0].isdigit() else None

        print(f"  Squadron: {name} (CO: {co_ucid or 'None'})")

        if not dry_run:
            with target_conn.cursor() as cur:
                # CO must exist in players table for foreign key - set to NULL if not
                if co_ucid:
                    cur.execute("SELECT 1 FROM players WHERE ucid = %s", (co_ucid,))
                    if not cur.fetchone():
                        print(f"    WARNING: CO {co_ucid} not in players table, setting to NULL")
                        co_ucid = None

                # Insert into shared squadrons table
                cur.execute("""
                    INSERT INTO squadrons (name, description, co_ucid, locked)
                    VALUES (%s, %s, %s, FALSE)
                    ON CONFLICT (name) DO UPDATE SET
                        description = COALESCE(EXCLUDED.description, squadrons.description),
                        co_ucid = COALESCE(EXCLUDED.co_ucid, squadrons.co_ucid)
                    RETURNING id
                """, (name, description, co_ucid))
                result = cur.fetchone()
                if result:
                    squadron_map[name] = result['id']
                else:
                    # Get existing ID if upsert didn't return
                    cur.execute("SELECT id FROM squadrons WHERE name = %s", (name,))
                    result = cur.fetchone()
                    squadron_map[name] = result['id']

                # Insert logbook-specific metadata (abbreviation, service)
                if abbreviation or service:
                    cur.execute("""
                        INSERT INTO logbook_squadron_metadata (squadron_id, abbreviation, service)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (squadron_id) DO UPDATE SET
                            abbreviation = COALESCE(EXCLUDED.abbreviation, logbook_squadron_metadata.abbreviation),
                            service = COALESCE(EXCLUDED.service, logbook_squadron_metadata.service)
                    """, (squadron_map[name], abbreviation, service))
        else:
            squadron_map[name] = f"<new-id-for-{name}>"

    print(f"  Total: {len(mayfly_data['squadrons'])} squadrons")
    return squadron_map


def migrate_pilots(mayfly_data: dict, target_conn, squadron_map: dict, dry_run: bool):
    """Migrate pilots and their squadron memberships"""
    print("\n=== Migrating Pilots ===")

    # Build pilot -> squadron mapping
    pilot_squadrons = {}
    for sp in mayfly_data['squadron_pilots']:
        pilot_id = sp['pilot_id']
        squadron_id = sp['squadron_id']
        if pilot_id not in pilot_squadrons:
            pilot_squadrons[pilot_id] = []
        pilot_squadrons[pilot_id].append(squadron_id)

    # Ensure pilots exist in players table and add squadron membership
    for pilot in mayfly_data['pilots']:
        ucid = pilot['pilot_id']
        name = pilot['pilot_name']
        rank = pilot['pilot_rank']
        service = pilot['pilot_service']  # RN, Army, RAF

        squadrons = pilot_squadrons.get(ucid, [])
        print(f"  Pilot: {name} ({rank}, {service}) - Squadrons: {squadrons}")

        if not dry_run:
            with target_conn.cursor() as cur:
                # Validate UCID format (must be hex string)
                if not ucid or not all(c in '0123456789abcdefABCDEF' for c in ucid):
                    print(f"    WARNING: Invalid UCID '{ucid}', skipping pilot")
                    continue

                # Ensure pilot exists in players table
                cur.execute("""
                    INSERT INTO players (ucid, name)
                    VALUES (%s, %s)
                    ON CONFLICT (ucid) DO UPDATE SET name = EXCLUDED.name
                """, (ucid, name))

                # Add pilot service and rank to logbook_pilots table
                cur.execute("""
                    INSERT INTO logbook_pilots (player_ucid, service, rank)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (player_ucid) DO UPDATE SET
                        service = EXCLUDED.service,
                        rank = EXCLUDED.rank
                """, (ucid, service, rank))

                # Add squadron memberships to shared table
                for sq_name in squadrons:
                    if sq_name in squadron_map:
                        sq_id = squadron_map[sq_name]
                        cur.execute("""
                            INSERT INTO squadron_members (squadron_id, player_ucid)
                            VALUES (%s, %s)
                            ON CONFLICT (squadron_id, player_ucid) DO NOTHING
                        """, (sq_id, ucid))

    print(f"  Total: {len(mayfly_data['pilots'])} pilots")


def migrate_awards(mayfly_data: dict, target_conn, dry_run: bool):
    """Migrate pilot awards"""
    print("\n=== Migrating Pilot Awards ===")

    # Get award name -> id mapping from target database
    award_id_map = {}
    missing_awards = set()
    if not dry_run:
        with target_conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, name FROM logbook_awards")
            for row in cur.fetchall():
                award_id_map[row['name']] = row['id']

    migrated = 0
    skipped = 0

    for pa in mayfly_data['pilot_awards']:
        pilot_ucid = pa['pilot_id']
        old_award_id = pa['award_id']
        date_issued = epoch_to_datetime(pa.get('date_issued'))

        # Get award name from old system
        old_award = mayfly_data['awards'].get(old_award_id)
        if not old_award:
            skipped += 1
            continue

        award_name = old_award['award_name']

        if not dry_run:
            new_award_id = award_id_map.get(award_name)
            if not new_award_id:
                if award_name not in missing_awards:
                    print(f"  WARNING: Award '{award_name}' not found in target database")
                    missing_awards.add(award_name)
                skipped += 1
                continue

            with target_conn.cursor() as cur:
                try:
                    # Ensure pilot exists in players table first
                    cur.execute("""
                        INSERT INTO players (ucid, name)
                        VALUES (%s, %s)
                        ON CONFLICT (ucid) DO NOTHING
                    """, (pilot_ucid, f"Player_{pilot_ucid[:8]}"))

                    cur.execute("""
                        INSERT INTO logbook_pilot_awards (player_ucid, award_id, granted_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (player_ucid, award_id, granted_at) DO NOTHING
                    """, (pilot_ucid, new_award_id, date_issued or datetime.now(timezone.utc)))
                    migrated += 1
                except Exception as e:
                    print(f"  ERROR migrating award {award_name} for {pilot_ucid}: {e}")
                    skipped += 1
        else:
            print(f"  Award: {award_name} -> {pilot_ucid} (issued: {date_issued})")
            migrated += 1

    print(f"  Total: {migrated} awards migrated, {skipped} skipped")


def migrate_qualifications(mayfly_data: dict, target_conn, dry_run: bool):
    """Migrate pilot qualifications"""
    print("\n=== Migrating Pilot Qualifications ===")

    # Get qualification name -> id mapping from target database
    qual_id_map = {}
    missing_quals = set()
    if not dry_run:
        with target_conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, name FROM logbook_qualifications")
            for row in cur.fetchall():
                qual_id_map[row['name']] = row['id']

    migrated = 0
    skipped = 0

    for pq in mayfly_data['pilot_qualifications']:
        pilot_ucid = pq['pilot_id']
        old_qual_id = pq['qualification_id']
        date_issued = epoch_to_datetime(pq.get('date_issued'))
        date_expires = epoch_to_datetime(pq.get('date_expires'))

        # Get qualification name from old system
        old_qual = mayfly_data['qualifications'].get(old_qual_id)
        if not old_qual:
            skipped += 1
            continue

        qual_name = old_qual['qualification_name']

        if not dry_run:
            new_qual_id = qual_id_map.get(qual_name)
            if not new_qual_id:
                if qual_name not in missing_quals:
                    print(f"  WARNING: Qualification '{qual_name}' not found in target database")
                    missing_quals.add(qual_name)
                skipped += 1
                continue

            with target_conn.cursor() as cur:
                try:
                    # Ensure pilot exists in players table first
                    cur.execute("""
                        INSERT INTO players (ucid, name)
                        VALUES (%s, %s)
                        ON CONFLICT (ucid) DO NOTHING
                    """, (pilot_ucid, f"Player_{pilot_ucid[:8]}"))

                    cur.execute("""
                        INSERT INTO logbook_pilot_qualifications (player_ucid, qualification_id, granted_at, expires_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (player_ucid, qualification_id) DO UPDATE SET
                            granted_at = EXCLUDED.granted_at,
                            expires_at = EXCLUDED.expires_at
                    """, (pilot_ucid, new_qual_id, date_issued or datetime.now(timezone.utc), date_expires))
                    migrated += 1
                except Exception as e:
                    print(f"  ERROR migrating qualification {qual_name} for {pilot_ucid}: {e}")
                    skipped += 1
        else:
            print(f"  Qualification: {qual_name} -> {pilot_ucid} (issued: {date_issued}, expires: {date_expires})")
            migrated += 1

    print(f"  Total: {migrated} qualifications migrated, {skipped} skipped")


def migrate_historical_hours(combined_stats: dict, target_conn, dry_run: bool):
    """Import historical hours from combinedStats.json, adjusting for existing DCSServerBot hours"""
    print("\n=== Migrating Historical Hours ===")

    # Get current hours from DCSServerBot statistics for each pilot
    current_hours = {}
    if not dry_run:
        with target_conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT player_ucid,
                       ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(hop_off, NOW() AT TIME ZONE 'utc') - hop_on))) / 3600.0, 0)::DECIMAL, 2) as total_hours
                FROM statistics
                GROUP BY player_ucid
            """)
            for row in cur.fetchall():
                current_hours[row['player_ucid']] = float(row['total_hours'])

    migrated = 0
    skipped = 0

    for ucid, stats in combined_stats.items():
        legacy_hours = stats['total_hours']
        dcs_hours = current_hours.get(ucid, 0)

        # Calculate historical hours: legacy - current DCS hours
        # This is the baseline that existed before DCSServerBot started tracking
        historical_hours = max(0, legacy_hours - dcs_hours)

        if historical_hours <= 0:
            # No historical hours to import (DCS already has same or more)
            skipped += 1
            continue

        aircraft_hours = stats.get('aircraft_hours', {})

        if not dry_run:
            with target_conn.cursor() as cur:
                # Ensure player exists
                cur.execute("""
                    INSERT INTO players (ucid, name)
                    VALUES (%s, %s)
                    ON CONFLICT (ucid) DO NOTHING
                """, (ucid, f"Player_{ucid[:8]}"))

                # Insert historical hours
                cur.execute("""
                    INSERT INTO logbook_historical_hours (player_ucid, imported_from, total_hours, aircraft_hours)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (player_ucid, imported_from) DO UPDATE SET
                        total_hours = EXCLUDED.total_hours,
                        aircraft_hours = EXCLUDED.aircraft_hours
                """, (ucid, 'legacy_slmod', Decimal(str(historical_hours)), json.dumps(aircraft_hours)))
                migrated += 1
        else:
            print(f"  {ucid}: {legacy_hours:.1f}h legacy - {dcs_hours:.1f}h DCS = {historical_hours:.1f}h historical")
            migrated += 1

    print(f"  Total: {migrated} pilots with historical hours, {skipped} skipped (no additional hours)")


def main():
    args = parse_args()

    print("=" * 60)
    print("DCSServerBot Logbook Migration Tool")
    print("=" * 60)

    if args.dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")

    # Load source data
    print(f"Loading mayfly.db from: {args.mayfly}")
    mayfly_data = load_mayfly(args.mayfly)
    print(f"  - {len(mayfly_data['squadrons'])} squadrons")
    print(f"  - {len(mayfly_data['pilots'])} pilots")
    print(f"  - {len(mayfly_data['pilot_awards'])} pilot awards")
    print(f"  - {len(mayfly_data['pilot_qualifications'])} pilot qualifications")

    print(f"\nLoading combinedStats.json from: {args.stats}")
    combined_stats = load_combined_stats(args.stats)
    print(f"  - {len(combined_stats)} pilots with flight time")

    # Connect to target database
    print(f"\nConnecting to target database...")
    with psycopg.connect(args.target, row_factory=dict_row) as conn:
        conn.autocommit = False

        try:
            # Run migrations
            squadron_map = migrate_squadrons(mayfly_data, conn, args.dry_run)
            migrate_pilots(mayfly_data, conn, squadron_map, args.dry_run)
            migrate_awards(mayfly_data, conn, args.dry_run)
            migrate_qualifications(mayfly_data, conn, args.dry_run)
            migrate_historical_hours(combined_stats, conn, args.dry_run)

            if not args.dry_run:
                conn.commit()
                print("\n" + "=" * 60)
                print("Migration completed successfully!")
                print("=" * 60)
            else:
                conn.rollback()
                print("\n" + "=" * 60)
                print("Dry run completed - no changes made")
                print("=" * 60)

        except Exception as e:
            conn.rollback()
            print(f"\nERROR: Migration failed: {e}")
            raise


if __name__ == "__main__":
    main()
