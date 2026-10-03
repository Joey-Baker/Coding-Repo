"""
Load the Lahman Baseball Database into SQL Server.

    python load_lahman.py
    python load_lahman.py --tables teams_core batting pitching fielding
    python load_lahman.py --dry-run

Season-level batting, pitching, fielding, standings, postseason and awards
for every year from 1871 through the latest release (currently 2025), plus
Negro Leagues data. This is the backbone table set - it is what makes
BaseRuns and league-context comparisons computable.

Tables land as lahman_<name>, e.g. lahman_teams_core, lahman_batting.

Note: Lahman is season-level only. It has no play-by-play (use Retrosheet)
and no pitch tracking (use Statcast). It also stops at the last completed
season, so it will not contain the current year.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
import sqlalchemy as sa

import db

# Every table pybaseball's lahman module exposes. The heavy ones first so a
# failure surfaces early rather than after twenty small successes.
TABLES = [
    "teams_core", "batting", "pitching", "fielding", "appearances", "people",
    "batting_post", "pitching_post", "fielding_post", "series_post",
    "fielding_of", "fielding_of_split", "managers", "managers_half",
    "teams_franchises", "teams_half", "all_star_full", "hall_of_fame",
    "awards_players", "awards_managers", "awards_share_players",
    "awards_share_managers", "salaries", "schools", "college_playing",
    "parks", "home_games",
]


def fetch(name: str) -> pd.DataFrame | None:
    """Call the matching pybaseball.lahman function."""
    import pybaseball.lahman as lahman

    fn = getattr(lahman, name, None)
    if fn is None:
        print(f"  {name}: not available in this pybaseball version, skipped")
        return None
    try:
        frame = fn()
    except Exception as exc:                       # noqa: BLE001
        print(f"  {name}: FAILED ({type(exc).__name__}: {exc})")
        return None
    if frame is None or len(frame) == 0:
        print(f"  {name}: empty, skipped")
        return None
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", nargs="+", default=None,
                    help="subset of tables to load (default: all)")
    ap.add_argument("--database", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wanted = args.tables or TABLES
    unknown = [t for t in wanted if t not in TABLES]
    if unknown:
        print(f"Unknown table(s): {unknown}\nKnown: {', '.join(TABLES)}")
        return 2

    if not args.dry_run:
        db.describe_target(args.database)
        engine = db.get_engine(args.database, create=True)

    print(f"\nLoading {len(wanted)} Lahman tables")
    print("(first call downloads the full archive - expect a pause)\n")

    loaded = skipped = 0
    for name in wanted:
        frame = fetch(name)
        if frame is None:
            skipped += 1
            continue

        if args.dry_run:
            years = ""
            for col in ("yearID", "yearid", "year"):
                if col in frame.columns:
                    years = f"  years {frame[col].min()}-{frame[col].max()}"
                    break
            print(f"  [dry-run] lahman_{name}: {len(frame):,} rows, "
                  f"{len(frame.columns)} cols{years}")
            loaded += 1
            continue

        db.write_table(frame, f"lahman_{name}", engine, if_exists="replace")
        loaded += 1

    print(f"\n{loaded} tables loaded, {skipped} skipped.")

    if not args.dry_run:
        print("\nBuilding indexes")
        inspector = sa.inspect(engine)
        with engine.begin() as conn:
            for name in wanted:
                table = f"lahman_{name}"
                if not inspector.has_table(table):
                    continue
                existing = {c["name"] for c in inspector.get_columns(table)}
                for col in ("yearID", "teamID", "playerID", "lgID"):
                    if col not in existing:
                        continue
                    index = f"ix_{table}_{col}"
                    conn.execute(sa.text(
                        f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
                        f"WHERE name = '{index}' AND object_id = OBJECT_ID('{table}')) "
                        f"CREATE INDEX [{index}] ON [{table}] ([{col}])"
                    ))
        print("  done")

    return 0


if __name__ == "__main__":
    sys.exit(main())
