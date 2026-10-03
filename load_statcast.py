"""
Load Statcast pitch-level data into SQL Server.

    python load_statcast.py --season 2015
    python load_statcast.py --season 2015 --start 2015-10-01 --end 2015-11-02
    python load_statcast.py --season 2015 --dry-run

Pitch-by-pitch tracking data from Baseball Savant: velocity, spin, release
point, launch speed and angle, expected outcomes, hit coordinates. 2015 is
Statcast's first full season, which is convenient for you - the batted-ball
detail exists for exactly the year in question, though the tracking-based
FIELDING metrics (outs above average and friends) came later and are thin
or absent for 2015.

Data lands in statcast_pitches, appended month by month so a failure part
way through doesn't lose the earlier months. Re-running a date range deletes
those dates first.

A full season is roughly 700k pitches and takes a while to download. Savant
rate-limits, so be patient rather than re-running in parallel.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import pandas as pd
import sqlalchemy as sa

import db

TABLE = "statcast_pitches"

# Regular season plus playoffs, generously bounded.
SEASON_START = (3, 15)
SEASON_END = (11, 15)

# Columns Savant sometimes returns as all-null objects; they confuse type
# inference and carry nothing.
DROP_ALWAYS = ["pitcher.1", "fielder_2.1"]


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """Split a date span into calendar-month chunks."""
    chunks = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        chunk_end = min(end, nxt - timedelta(days=1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.drop(columns=[c for c in DROP_ALWAYS if c in frame.columns])
    # Drop columns that are entirely null - nothing to store, and they break
    # NVARCHAR sizing.
    empty = [c for c in frame.columns if frame[c].isna().all()]
    if empty:
        frame = frame.drop(columns=empty)
    return frame


def clear_range(engine, start: date, end: date) -> None:
    inspector = sa.inspect(engine)
    if not inspector.has_table(TABLE):
        return
    with engine.begin() as conn:
        deleted = conn.execute(
            sa.text(f"DELETE FROM [{TABLE}] "
                    f"WHERE game_date >= :a AND game_date <= :b"),
            {"a": start.isoformat(), "b": end.isoformat()},
        ).rowcount
    if deleted:
        print(f"    cleared {deleted:,} existing rows in range")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (default: season start)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: season end)")
    ap.add_argument("--database", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="download one month only, print the shape, write nothing")
    args = ap.parse_args()

    start = date.fromisoformat(args.start) if args.start \
        else date(args.season, *SEASON_START)
    end = date.fromisoformat(args.end) if args.end \
        else date(args.season, *SEASON_END)

    from pybaseball import statcast

    if not args.dry_run:
        db.describe_target(args.database)
        engine = db.get_engine(args.database, create=True)

    chunks = month_ranges(start, end)
    print(f"\nStatcast {start} to {end} in {len(chunks)} chunks")

    total = 0
    for i, (a, b) in enumerate(chunks, 1):
        print(f"\n[{i}/{len(chunks)}] {a} .. {b}")
        try:
            frame = statcast(start_dt=a.isoformat(), end_dt=b.isoformat(),
                             verbose=False)
        except Exception as exc:                   # noqa: BLE001
            print(f"    FAILED ({type(exc).__name__}: {exc})")
            print("    continuing with the next chunk")
            continue

        if frame is None or len(frame) == 0:
            print("    no pitches in range")
            continue

        frame = clean(frame)
        frame["season"] = args.season
        print(f"    {len(frame):,} pitches, {len(frame.columns)} columns")

        if args.dry_run:
            print("    [dry-run] sample columns:",
                  ", ".join(list(frame.columns)[:14]))
            return 0

        clear_range(engine, a, b)
        exists = sa.inspect(engine).has_table(TABLE)
        db.write_table(frame, TABLE, engine,
                       if_exists="append" if exists else "replace")
        total += len(frame)

    if not args.dry_run and total:
        print(f"\n{total:,} pitches loaded. Building indexes")
        with engine.begin() as conn:
            for col in ("game_date", "pitcher", "batter", "game_pk", "season"):
                index = f"ix_{TABLE}_{col}"
                conn.execute(sa.text(
                    f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
                    f"WHERE name = '{index}' AND object_id = OBJECT_ID('{TABLE}')) "
                    f"CREATE INDEX [{index}] ON [{TABLE}] ([{col}])"
                ))
        print("  done")

    return 0


if __name__ == "__main__":
    sys.exit(main())
