"""
Fix for logbook hours display bug.
Run from the DCSServerBot directory:

    python fix_logbook_view.py

This updates the pilot_logbook_stats database view to correctly
sum historical + DCS hours instead of taking the larger value.
"""
import sys
import re

def get_db_url():
    """Read database URL from config/nodes.yaml"""
    try:
        with open('config/nodes.yaml', 'r') as f:
            content = f.read()
        match = re.search(r'url:\s*(postgres\S+)', content)
        if not match:
            print("ERROR: No database URL found in config/nodes.yaml")
            sys.exit(1)
        url = match.group(1)
        # psycopg requires postgresql:// not postgres://
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        return url
    except FileNotFoundError:
        print("ERROR: config/nodes.yaml not found. Run this from the DCSServerBot directory.")
        sys.exit(1)

def main():
    try:
        import psycopg
    except ImportError:
        print("ERROR: psycopg not installed. Run: pip install psycopg[binary]")
        sys.exit(1)

    # Allow --target override for testing
    if '--target' in sys.argv:
        idx = sys.argv.index('--target')
        db_url = sys.argv[idx + 1]
    else:
        db_url = get_db_url()
    print("Connecting to database...")

    conn = psycopg.connect(db_url)
    conn.autocommit = True

    # Check current view definition
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('pilot_logbook_stats', true)")
        view_def = cur.fetchone()[0]

    if 'greatest' in view_def.lower():
        print("Current view uses GREATEST (bug). Applying fix...")
    else:
        print("View already uses addition. No fix needed.")
        conn.close()
        return

    # Apply the fix
    sql = """
    CREATE OR REPLACE VIEW pilot_logbook_stats AS
    SELECT
        p.ucid,
        p.name,
        p.discord_id,
        COALESCE(h.total_hours, 0) +
            ROUND(COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'utc') - s.hop_on))) / 3600.0, 0)::DECIMAL, 2)
        AS total_hours,
        COALESCE(SUM(s.kills), 0) AS total_kills,
        COALESCE(SUM(s.deaths), 0) AS total_deaths,
        COALESCE(SUM(s.takeoffs), 0) AS total_takeoffs,
        COALESCE(SUM(s.landings), 0) AS total_landings,
        COALESCE(SUM(s.ejections), 0) AS total_ejections,
        COALESCE(SUM(s.crashes), 0) AS total_crashes,
        h.aircraft_hours AS historical_aircraft_hours
    FROM players p
    LEFT JOIN statistics s ON p.ucid = s.player_ucid
    LEFT JOIN (
        SELECT player_ucid, SUM(total_hours) AS total_hours,
               jsonb_object_agg(COALESCE(imported_from, 'unknown'), aircraft_hours) AS aircraft_hours
        FROM logbook_historical_hours
        GROUP BY player_ucid
    ) h ON p.ucid = h.player_ucid
    GROUP BY p.ucid, p.name, p.discord_id, h.total_hours, h.aircraft_hours;
    """

    with conn.cursor() as cur:
        cur.execute(sql)

    # Verify
    with conn.cursor() as cur:
        cur.execute("SELECT pg_get_viewdef('pilot_logbook_stats', true)")
        new_def = cur.fetchone()[0]

    if 'greatest' not in new_def.lower():
        print("Fix applied successfully!")
    else:
        print("ERROR: Fix did not apply correctly.")
        conn.close()
        sys.exit(1)

    # Show sample results
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pls.name, pls.total_hours,
                   COALESCE(h.total_hours, 0) as historical,
                   pls.total_hours - COALESCE(h.total_hours, 0) as dcs_hours
            FROM pilot_logbook_stats pls
            LEFT JOIN (
                SELECT player_ucid, SUM(total_hours) as total_hours
                FROM logbook_historical_hours GROUP BY player_ucid
            ) h ON pls.ucid = h.player_ucid
            WHERE COALESCE(h.total_hours, 0) > 0
            ORDER BY pls.total_hours DESC
            LIMIT 10
        """)
        rows = cur.fetchall()

    print()
    print("Top 10 pilots with historical hours:")
    print(f"  {'Name':<25} {'Total':>10} {'Historical':>12} {'DCS Bot':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*12} {'-'*10}")
    for name, total, hist, dcs in rows:
        print(f"  {name:<25} {float(total):>10.1f} {float(hist):>12.1f} {float(dcs):>10.1f}")

    conn.close()
    print()
    print("Done. Run /logbook pilot to verify hours are correct.")

if __name__ == '__main__':
    main()
