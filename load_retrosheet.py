"""
Load Retrosheet play-by-play into SQL Server.

    python load_retrosheet.py --seasons 2015
    python load_retrosheet.py --seasons 2014 2015 --postseason
    python load_retrosheet.py --seasons 2015 --source github

Creates / fills four tables:

    rs_games         one row per game
    rs_lineups       starters and substitutions
    rs_plays         one row per play - the main table
    rs_earned_runs   earned runs charged per pitcher per game

Re-running a season deletes that season's rows first, so it is safe to run
again after a fix without producing duplicates.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests
import sqlalchemy as sa

import db
from retrosheet_parse import ParseResult, parse_file

CACHE = Path(__file__).parent / "data" / "retrosheet"
MIRROR = "https://github.com/chadwickbureau/retrosheet.git"

# Regular season files are per-home-team; postseason files are per-series.
REGULAR_GLOBS = ("*.EVA", "*.EVN")
POSTSEASON_GLOB = "*.EVE"
# The All-Star game shares the .EVE extension but is not postseason baseball.
EXCLUDE = {"AS"}


def fetch_from_retrosheet(season: int) -> Path:
    """Download <season>eve.zip and <season>post.zip from retrosheet.org."""
    target = CACHE / str(season)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.glob("*.EV*")):
        print(f"  {season}: already cached at {target}")
        return target

    for suffix in ("eve", "post"):
        url = f"https://www.retrosheet.org/events/{season}{suffix}.zip"
        print(f"  downloading {url}")
        resp = requests.get(url, timeout=120)
        if resp.status_code == 404:
            print(f"    not available (404), skipping")
            continue
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(target)
    return target


def fetch_from_github(season: int) -> Path:
    """
    Sparse-clone the Chadwick Bureau mirror of Retrosheet.

    Slower on first use than the zips, but it is one repo for every season and
    `git pull` picks up upstream corrections later.
    """
    repo = CACHE / "_mirror"
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        print(f"  cloning {MIRROR} (metadata only, this takes a minute)")
        subprocess.run(
            ["git", "clone", "--depth", "1", "--no-checkout",
             "--filter=blob:none", MIRROR, str(repo)],
            check=True,
        )
        subprocess.run(["git", "sparse-checkout", "init", "--cone"],
                       cwd=repo, check=True)

    subprocess.run(["git", "sparse-checkout", "add", f"seasons/{season}"],
                   cwd=repo, check=True)
    subprocess.run(["git", "checkout"], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    season_dir = repo / "seasons" / str(season)
    if not season_dir.exists():
        raise FileNotFoundError(f"mirror has no seasons/{season}")
    return season_dir


def parse(season_dir: Path, season: int, postseason: bool) -> ParseResult:
    result = ParseResult()
    files: list[Path] = []
    for pattern in REGULAR_GLOBS:
        files += sorted(season_dir.glob(pattern))
    if postseason:
        for path in sorted(season_dir.glob(POSTSEASON_GLOB)):
            tag = path.stem.replace(str(season), "")
            if tag not in EXCLUDE:
                files.append(path)

    if not files:
        raise FileNotFoundError(f"no event files found in {season_dir}")

    for path in files:
        result.extend(parse_file(path, season=season))
    print(f"  parsed {len(files)} files -> {result.counts()}")
    return result


def clear_season(engine, table: str, season: int) -> None:
    """Remove an existing season so a re-run doesn't duplicate rows."""
    inspector = sa.inspect(engine)
    if not inspector.has_table(table):
        return
    with engine.begin() as conn:
        deleted = conn.execute(
            sa.text(f"DELETE FROM [{table}] WHERE season = :s"), {"s": season}
        ).rowcount
    if deleted:
        print(f"  {table}: cleared {deleted:,} existing {season} rows")


INDEXES = {
    "rs_games": [("season",), ("hometeam",), ("gametype",)],
    "rs_plays": [("season",), ("game_id",), ("batter_id",), ("pitcher_id",),
                 ("event_type",), ("strikes",)],
    "rs_lineups": [("season",), ("game_id",), ("player_id",)],
    "rs_earned_runs": [("season",), ("pitcher_id",)],
}


def build_indexes(engine) -> None:
    print("Building indexes")
    inspector = sa.inspect(engine)
    with engine.begin() as conn:
        for table, columns in INDEXES.items():
            if not inspector.has_table(table):
                continue
            for cols in columns:
                name = f"ix_{table}_{'_'.join(cols)}"
                cols_sql = ", ".join(f"[{c}]" for c in cols)
                conn.execute(sa.text(
                    f"IF NOT EXISTS (SELECT 1 FROM sys.indexes "
                    f"WHERE name = '{name}' AND object_id = OBJECT_ID('{table}')) "
                    f"CREATE INDEX [{name}] ON [{table}] ({cols_sql})"
                ))
    print("  done")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", required=True)
    ap.add_argument("--postseason", action="store_true",
                    help="also load postseason series files")
    ap.add_argument("--source", choices=("retrosheet", "github"),
                    default="retrosheet")
    ap.add_argument("--database", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and parse, print a summary, write nothing")
    args = ap.parse_args()

    if not args.dry_run:
        db.describe_target(args.database)
        engine = db.get_engine(args.database, create=True)
    else:
        engine = None

    fetch = fetch_from_github if args.source == "github" else fetch_from_retrosheet

    for season in args.seasons:
        print(f"\n=== {season} ===")
        season_dir = fetch(season)
        result = parse(season_dir, season, args.postseason)

        frames = {
            "rs_games": pd.DataFrame(result.games),
            "rs_lineups": pd.DataFrame(result.lineups),
            "rs_plays": pd.DataFrame(result.plays),
            "rs_earned_runs": pd.DataFrame(result.earned_runs),
        }

        if args.dry_run:
            for name, frame in frames.items():
                print(f"  [dry-run] {name}: {len(frame):,} rows, "
                      f"{len(frame.columns)} cols")
            continue

        for name, frame in frames.items():
            clear_season(engine, name, season)
            exists = sa.inspect(engine).has_table(name)
            db.write_table(frame, name, engine,
                           if_exists="append" if exists else "replace")

    if not args.dry_run:
        build_indexes(engine)
        print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
