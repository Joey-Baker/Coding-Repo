"""
Load FanGraphs team fielding — including Defensive Runs Saved — into SQL Server.

    python load_fangraphs_fielding.py --seasons 2015
    python load_fangraphs_fielding.py --seasons 2015 2016 2017 2018 2019 2021 2022 2023 2024 2025
    python load_fangraphs_fielding.py --seasons 2015 --dry-run

WHY THIS EXISTS
---------------
DRS and UZR are proprietary metrics from Baseball Info Solutions and FanGraphs.
They cannot be derived from play-by-play at any effort level, because they depend
on batted-ball location and difficulty charting that Retrosheet does not carry.
This is the one piece of the 2015 Royals question the event files cannot answer.

pybaseball scrapes the FanGraphs leaderboard, so this has to run somewhere that
can reach fangraphs.com — your machine, not a sandbox.

WHAT YOU GET vs WHAT WE COMPUTED
--------------------------------
Defensive Efficiency (DER), which we can compute from Retrosheet, is the share of
balls in play a defense turns into outs. It credits the entire run-prevention
system at once: a staff that allows soft contact looks like a good defense.

DRS asks a narrower question — given where and how hard this ball was hit, how
many plays did these fielders make relative to what an average fielder makes on
that ball? That isolates the glovework, which is exactly the claim about the
2015 Royals that DER cannot test.

Lands in fg_team_fielding, one row per team per season.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
import sqlalchemy as sa

import db

TABLE = "fg_team_fielding"

# The columns worth keeping if present. FanGraphs renames things periodically,
# so anything missing is skipped rather than treated as an error.
PREFERRED = [
    "Season", "Team", "G", "Inn", "PO", "A", "E", "FE", "TE", "DP",
    "FP", "DRS", "UZR", "UZR/150", "Def", "OAA", "RngR", "ErrR", "ARM", "DPR",
]


def fetch(seasons: list[int]) -> pd.DataFrame:
    from pybaseball import team_fielding

    frames = []
    for year in seasons:
        print(f"  fetching {year} ...", end=" ", flush=True)
        try:
            frame = team_fielding(year)
        except Exception as exc:                        # noqa: BLE001
            print(f"FAILED ({type(exc).__name__}: {exc})")
            continue
        if frame is None or len(frame) == 0:
            print("empty")
            continue
        if "Season" not in frame.columns:
            frame.insert(0, "Season", year)
        frames.append(frame)
        print(f"{len(frame)} teams, {len(frame.columns)} columns")

    if not frames:
        raise SystemExit("nothing fetched — check your connection to fangraphs.com")
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", required=True)
    ap.add_argument("--database", default=None)
    ap.add_argument("--all-columns", action="store_true",
                    help="keep every column FanGraphs returns, not just the useful ones")
    ap.add_argument("--csv", default=None, help="also write a CSV copy here")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Fetching FanGraphs team fielding for {len(args.seasons)} season(s)")
    frame = fetch(args.seasons)

    if not args.all_columns:
        keep = [c for c in PREFERRED if c in frame.columns]
        missing = [c for c in PREFERRED if c not in frame.columns]
        if missing:
            print(f"\n  not returned by FanGraphs this time: {', '.join(missing)}")
        print(f"  keeping {len(keep)} of {len(frame.columns)} columns "
              f"(use --all-columns to keep everything)")
        frame = frame[keep]

    if "DRS" not in frame.columns:
        print("\n  WARNING: no DRS column came back. FanGraphs may have renamed it —")
        print("  re-run with --all-columns and look at what you actually got.")

    print(f"\n{len(frame)} rows, {len(frame.columns)} columns")
    if "DRS" in frame.columns and "Team" in frame.columns:
        top = frame.sort_values("DRS", ascending=False).head(5)
        cols = [c for c in ("Season", "Team", "DRS", "UZR") if c in top.columns]
        print("\nBest defenses by DRS in this pull:")
        print(top[cols].to_string(index=False))

    if args.csv:
        frame.to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")

    if args.dry_run:
        print("\n[dry-run] nothing written to the database")
        return 0

    db.describe_target(args.database)
    engine = db.get_engine(args.database, create=True)
    db.write_table(frame, TABLE, engine, if_exists="replace")

    with engine.begin() as conn:
        for col in ("Season", "Team"):
            if col not in frame.columns:
                continue
            index = f"ix_{TABLE}_{col}"
            conn.execute(sa.text(
                f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
                f"WHERE name = '{index}' AND object_id = OBJECT_ID('{TABLE}')) "
                f"CREATE INDEX [{index}] ON [{TABLE}] ([{col}])"
            ))
    print("indexes built. Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
