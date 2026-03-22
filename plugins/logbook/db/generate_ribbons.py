#!/usr/bin/env python3
"""
Generate ribbon images for all existing awards that don't have one.
Run this after adding the ribbon_image column to populate existing awards.

Usage:
    python generate_ribbons.py "postgresql://user:pass@host:port/dbname"
"""
import sys
import json

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    print("Error: psycopg not installed. Run: pip install psycopg[binary]")
    sys.exit(1)

# Import ribbon generator from parent
sys.path.insert(0, str(__file__).replace('/db/generate_ribbons.py', ''))
try:
    from utils.ribbon import create_ribbon_rack, HAS_IMAGING
except ImportError:
    # Try alternative path
    sys.path.insert(0, str(__file__).replace('\\db\\generate_ribbons.py', ''))
    from utils.ribbon import create_ribbon_rack, HAS_IMAGING

if not HAS_IMAGING:
    print("Error: PIL/Pillow not installed. Cannot generate ribbon images.")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    db_url = sys.argv[1]

    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        # Get all awards without ribbon images
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, ribbon_colors
                FROM logbook_awards
                WHERE ribbon_image IS NULL
            """)
            awards = cur.fetchall()

        if not awards:
            print("All awards already have ribbon images.")
            return

        print(f"Generating ribbons for {len(awards)} awards...")

        for award in awards:
            colors = None
            if award.get('ribbon_colors'):
                try:
                    colors = award['ribbon_colors'] if isinstance(award['ribbon_colors'], list) else json.loads(award['ribbon_colors'])
                except (json.JSONDecodeError, TypeError):
                    pass

            # Generate ribbon
            ribbon_bytes = create_ribbon_rack([(award['name'], colors, 1)], scale=2.0)

            if ribbon_bytes:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE logbook_awards SET ribbon_image = %s WHERE id = %s",
                        (ribbon_bytes, award['id'])
                    )
                print(f"  Generated ribbon for: {award['name']}")
            else:
                print(f"  Failed to generate ribbon for: {award['name']}")

        conn.commit()
        print("Done!")


if __name__ == "__main__":
    main()
