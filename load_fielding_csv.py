"""
Load a FanGraphs team-fielding CSV export into SQL Server.

    python load_fielding_csv.py --csv "~/Downloads/fangraphs-leaderboards.csv"
    python load_fielding_csv.py --csv a.csv b.csv --dry-run
    python load_fielding_csv.py --csv 2015.csv --season 2015

WHY A CSV AND NOT AN API
------------------------
pybaseball's team_fielding() still calls FanGraphs' retired /leaders-legacy.aspx
endpoint, so it fails for everyone regardless of network — and 2.2.7 is the
latest release, so upgrading does not help. Baseball Reference needs one scrape
per team per season and blocks scrapers. The export button is the reliable route.

HOW TO GET THE FILE (one click, all eleven seasons)
--------------------------------------------------
  1. Open FanGraphs -> Leaders -> Major League, and set:
       Stats    = Fielding
       Split    = Team   (the "Team" toggle, so you get 30 rows per season)
       Seasons  = 2015 through 2025, with "split seasons" ON
     Or go straight there:
       https://www.fangraphs.com/leaders/major-league?pos=all&stats=fld&lg=all&type=1&season=2025&season1=2015&ind=1&team=0,ts
  2. Click "Export Data" (top right of the table).
  3. Point this script at the downloaded file.

Column names are normalised on the way in — "UZR/150" becomes UZR_150, spaces
become underscores — because SQL Server will not take the originals.

Lands in fg_team_fielding, one row per team per season.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

import db

TABLE = "fg_team_fielding"

# FanGraphs team codes -> the Retrosheet codes rs_plays uses, so the two
# tables can be joined. Anything not listed passes through unchanged.
FG_TO_RETROSHEET = {
    "LAA": "ANA", "ANA": "ANA", "ARI": "ARI", "ATL": "ATL", "BAL": "BAL",
    "BOS": "BOS", "CHW": "CHA", "CWS": "CHA", "CHC": "CHN", "CIN": "CIN",
    "CLE": "CLE", "COL": "COL", "DET": "DET", "HOU": "HOU", "KCR": "KCA",
    "KC": "KCA", "LAD": "LAN", "MIA": "MIA", "FLA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYY": "NYA", "NYM": "NYN", "OAK": "OAK", "PHI": "PHI",
    "PIT": "PIT", "SDP": "SDN", "SD": "SDN", "SEA": "SEA", "SFG": "SFN",
    "SF": "SFN", "STL": "SLN", "TBR": "TBA", "TB": "TBA", "TEX": "TEX",
    "TOR": "TOR", "WSN": "WAS", "WSH": "WAS",
}


# Baseball Reference names the same things differently. Normalise so downstream
# queries say DRS and Team regardless of which site the export came from.
ALIASES = {
    "Rdrs": "DRS",        # bbref's Defensive Runs Saved
    "Rdrs_yr": "DRS_150",
    "Rtot": "TZ",         # Total Zone, the pre-2003 predecessor
    "Rtot_yr": "TZ_150",
    "Tm": "Team",
    "Year": "Season",
}


def clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """SQL Server will not take 'UZR/150' or 'Def '. Make the names safe."""
    def fix(name: str) -> str:
        n = str(name).strip()
        n = n.replace("/", "_").replace("%", "pct").replace("+", "plus")
        n = re.sub(r"[^\w]", "_", n)
        n = re.sub(r"_+", "_", n).strip("_")
        return n or "col"

    frame = frame.rename(columns=fix)
    renamed = {c: ALIASES[c] for c in frame.columns if c in ALIASES}
    if renamed:
        print("  normalised column names:",
              ", ".join(f"{k} -> {v}" for k, v in renamed.items()))
        frame = frame.rename(columns=renamed)
    return frame.loc[:, ~frame.columns.duplicated()]


def coerce_numeric(frame: pd.DataFrame) -> pd.DataFrame:
    """FanGraphs exports numbers as text with commas; Team stays text."""
    for col in frame.columns:
        if col.lower() in ("team", "name", "season_team"):
            continue
        # not `dtype == object`: pandas 3 gives string columns a `str` dtype,
        # which that test misses, leaving "1,442.0" as text.
        if not pd.api.types.is_numeric_dtype(frame[col]):
            converted = pd.to_numeric(
                frame[col].astype(str).str.replace(",", "", regex=False).str.strip(),
                errors="coerce",
            )
            # only take it if most values actually parsed
            if converted.notna().sum() >= 0.8 * frame[col].notna().sum():
                frame[col] = converted
    return frame


def find_team_column(frame: pd.DataFrame, override: str | None = None) -> str | None:
    """
    Locate the column holding team identity. Exports name it Team, Tm, an empty
    header, or 'Unnamed: 0' depending on the site and how the table was copied,
    so fall back to looking at the values themselves.
    """
    if override:
        if override not in frame.columns:
            raise SystemExit(f"--team-column {override!r} not in file. "
                             f"Columns are: {', '.join(frame.columns)}")
        return override

    for name in ("Team", "Tm", "Franchise", "Club"):
        if name in frame.columns:
            return name

    # No obvious header. Find a text column whose values look like team
    # names or codes: mostly non-numeric, ~30 distinct values.
    best = None
    for col in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[col]):
            continue
        vals = frame[col].astype(str).fillna("").str.strip()
        distinct = vals.nunique()
        if 20 <= distinct <= 40 and vals.str.len().between(2, 30).mean() > 0.9:
            best = col
            break
    if best:
        print(f"  no Team header — using column {best!r}, which looks like team names")
    return best


# Some exports carry full club names instead of codes. Keyed on the last word
# (the nickname) because prefixes vary: "Los Angeles Angels", "LA Angels",
# "Anaheim Angels" and "Los Angeles Angels of Anaheim" are all the same club.
NICKNAME_TO_RETROSHEET = {
    "ANGELS": "ANA", "DIAMONDBACKS": "ARI", "D-BACKS": "ARI", "BRAVES": "ATL",
    "ORIOLES": "BAL", "RED SOX": "BOS", "WHITE SOX": "CHA", "CUBS": "CHN",
    "REDS": "CIN", "INDIANS": "CLE", "GUARDIANS": "CLE", "ROCKIES": "COL",
    "TIGERS": "DET", "ASTROS": "HOU", "ROYALS": "KCA", "DODGERS": "LAN",
    "MARLINS": "MIA", "BREWERS": "MIL", "TWINS": "MIN", "YANKEES": "NYA",
    "METS": "NYN", "ATHLETICS": "OAK", "A'S": "OAK", "PHILLIES": "PHI",
    "PIRATES": "PIT", "PADRES": "SDN", "MARINERS": "SEA", "GIANTS": "SFN",
    "CARDINALS": "SLN", "RAYS": "TBA", "RANGERS": "TEX", "BLUE JAYS": "TOR",
    "NATIONALS": "WAS",
}


def to_retrosheet(value: str) -> str | None:
    """Map a code or a full club name onto a Retrosheet code, or None."""
    s = str(value).strip().upper()
    if s in FG_TO_RETROSHEET:
        return FG_TO_RETROSHEET[s]
    if len(s) == 3 and s in set(FG_TO_RETROSHEET.values()):
        return s                                    # already a Retrosheet code
    # Containment, not endswith: "Los Angeles Angels of Anaheim" has the
    # nickname in the middle. Longest key first so "RED SOX" is tested before
    # any shorter key that happens to sit inside it.
    for nickname in sorted(NICKNAME_TO_RETROSHEET, key=len, reverse=True):
        if nickname in s:
            return NICKNAME_TO_RETROSHEET[nickname]
    return None


# Rows that are league totals or averages rather than a team.
TOTAL_ROW = re.compile(r"^(lg|league|total|average|avg|mlb)\b", re.I)


def add_retrosheet_code(frame: pd.DataFrame, team_col: str | None) -> pd.DataFrame:
    if team_col is None:
        print("\n  COULD NOT FIND A TEAM COLUMN. Here is what the file actually has:")
        print("   columns:", ", ".join(map(str, frame.columns)))
        print("   first row:", frame.iloc[0].to_dict() if len(frame) else "(empty)")
        print("   Re-run with --team-column <name> once you can see which one it is.")
        return frame

    if team_col != "Team":
        frame = frame.rename(columns={team_col: "Team"})

    # .astype(str) does NOT turn a missing value into "nan" on pandas 3's
    # arrow-backed string columns — it stays NA, and NA reaches a regex as a
    # float. fillna after the cast, and match vectorised rather than through
    # a lambda, so there is no per-value type to get wrong.
    names = frame["Team"].astype(str).fillna("").str.strip()
    before = len(frame)
    junk = names.eq("") | names.str.lower().isin({"nan", "none"}) | \
        names.str.match(TOTAL_ROW, na=True)
    frame = frame[~junk].copy()
    names = names[~junk]
    if len(frame) != before:
        print(f"  dropped {before - len(frame)} total/average/blank row(s)")

    mapped = names.map(to_retrosheet)
    frame["rs_team"] = mapped.fillna(names.str.upper())
    unmapped = sorted(set(names[mapped.isna()]))
    if unmapped:
        print(f"  note: no Retrosheet mapping for {unmapped} — passed through as-is")
    else:
        print(f"  mapped all {len(frame)} teams to Retrosheet codes")
    return frame


def load_csv(path: Path, season: int | None, team_column: str | None = None) -> pd.DataFrame:
    frame = pd.read_csv(path)
    print(f"  {path.name}: {len(frame)} rows, {len(frame.columns)} columns")
    frame = clean_columns(frame)
    # Always say what the headers are. Guessing at them one round-trip at a
    # time is what made this file take three tries.
    print("  columns:", ", ".join(map(str, frame.columns)))
    frame = coerce_numeric(frame)
    if "Season" not in frame.columns:
        if season is None:
            raise SystemExit(
                f"{path.name} has no Season column — pass --season, or re-export "
                f"with 'split seasons' turned on so each season is its own row."
            )
        frame.insert(0, "Season", season)
    frame = add_retrosheet_code(frame, find_team_column(frame, team_column))
    # A single blank trailing row turns every integer column into a float for
    # the whole file. Once it is dropped, put them back.
    for col in frame.columns:
        if pd.api.types.is_float_dtype(frame[col]) and frame[col].notna().all() \
                and (frame[col] % 1 == 0).all():
            frame[col] = frame[col].astype("int64")
    return frame


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", nargs="+", required=True)
    ap.add_argument("--season", type=int, default=None,
                    help="season to stamp on a file that has no Season column")
    ap.add_argument("--team-column", default=None,
                    help="name of the column holding team identity, if the "
                         "loader cannot work it out on its own")
    ap.add_argument("--database", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    frames = []
    for raw in args.csv:
        path = Path(raw).expanduser()
        if not path.exists():
            raise SystemExit(f"not found: {path}")
        frames.append(load_csv(path, args.season, args.team_column))
    frame = pd.concat(frames, ignore_index=True)

    print(f"\n{len(frame)} rows total, {len(frame.columns)} columns")
    if "DRS" not in frame.columns:
        print("\n  WARNING: no DRS column. On the FanGraphs table, DRS sits under the")
        print("  'Advanced' or 'Win Values' view — switch views before exporting.")
        print(f"  What you have: {', '.join(list(frame.columns)[:20])}")
    else:
        cols = [c for c in ("Season", "Team", "rs_team", "DRS", "UZR", "Def") if c in frame.columns]
        best = frame.sort_values("DRS", ascending=False).head(5)
        print("\nBest defenses by DRS in this file:")
        print(best[cols].to_string(index=False))

        # `frame.get("rs_team", frame.get("Team"))` looks tidy and is a trap:
        # with neither column present the default collapses to a scalar None
        # and the comparison becomes a bare False, which pandas reads as a
        # column name. Ask for the column by name instead.
        key = "rs_team" if "rs_team" in frame.columns else (
            "Team" if "Team" in frame.columns else None)
        if key is None:
            print("\n  No team column, so these numbers are anonymous — rows cannot be")
            print("  joined to anything. Re-run with --team-column <name> from the list above.")
        else:
            kc = frame[frame[key].astype(str).str.strip().str.upper().isin(
                {"KCA", "KCR", "KC", "KANSAS CITY ROYALS", "KANSAS CITY"})]
            if len(kc):
                print("\nKansas City:")
                print(kc[cols].to_string(index=False))

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    db.describe_target(args.database)
    engine = db.get_engine(args.database, create=True)
    db.write_table(frame, TABLE, engine, if_exists="replace")

    with engine.begin() as conn:
        for col in ("Season", "Team", "rs_team"):
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
