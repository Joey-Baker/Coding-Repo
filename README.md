# MLB loaders → local SQL Server

Three loaders that pull baseball data into the SQL Server instance on your PC,
so the `mssql` MCP server can query it conversationally.

| Script | Source | Grain | Coverage |
|---|---|---|---|
| `load_lahman.py` | Lahman via pybaseball | season totals | 1871–2025 |
| `load_retrosheet.py` | Retrosheet event files | one row per play | 1908–2025 |
| `load_statcast.py` | Baseball Savant via pybaseball | one row per pitch | 2015– |

## Setup

```powershell
pip install -r requirements.txt
```

You also need **ODBC Driver 18 for SQL Server** — a small standalone installer
from Microsoft, no restart needed. The scripts check for it and tell you if
it's missing. Get it at
<https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>.

**Two logins, deliberately.** The loaders write, so they connect with Windows
authentication (your own account) and no password sits in any file. The MCP
server reads, and connects as `mcp_reader` with only `db_datareader`. That
separation is what stops a chatty AI from ever writing to your data.

Everything defaults to `localhost,1433` and a database called `MLB`, which the
scripts create if it doesn't exist. Override with environment variables if
needed: `MSSQL_SERVER`, `MSSQL_PORT`, `MSSQL_DATABASE`, `MSSQL_DRIVER`, or
`MSSQL_USER` + `MSSQL_PASSWORD` to use a SQL login instead of Windows auth.

A new `MLB` database keeps this reference data away from your existing
`Season2015` / `Season2026` / `KCC` work.

## Running

Every script takes `--dry-run`, which fetches and parses but writes nothing.
Start there — it's the cheap way to confirm a source is reachable.

```powershell
# Season-level backbone. First run downloads the whole archive.
python load_lahman.py

# Play-by-play, regular season plus the playoff run
python load_retrosheet.py --seasons 2015 --postseason

# Several seasons at once, for year-over-year work
python load_retrosheet.py --seasons 2013 2014 2015 2016 --postseason

# Pitch-level tracking. Slow (~700k rows/season) and Savant rate-limits.
python load_statcast.py --season 2015
```

Re-running a season is safe: Retrosheet and Statcast delete that season or
date range before re-inserting, so you won't get duplicates after a fix.

`load_retrosheet.py --source github` pulls from the Chadwick Bureau mirror
instead of retrosheet.org. Slower on first use (one clone covers every
season) but `git pull` picks up upstream corrections later.

## Tables you get

**Retrosheet** — `rs_games`, `rs_lineups`, `rs_plays`, `rs_earned_runs`

`rs_plays` is the one that matters. One row per play, with `inning`, `half`,
`batter_id`, `pitcher_id`, `balls`, `strikes`, `pitch_seq`, `event_raw`,
`event_type`, `runs_on_play`, `outs_on_play`, and convenience flags
(`is_at_bat`, `is_hit`, `two_strikes`).

Indexed on season, game, batter, pitcher, event type and strikes.

**Lahman** — `lahman_teams_core`, `lahman_batting`, `lahman_pitching`,
`lahman_fielding`, and about twenty more, indexed on `yearID` / `teamID` /
`playerID`.

**Statcast** — `statcast_pitches`, indexed on date, pitcher, batter, game.

### Exact vs approximate

Read straight from the files, no interpretation — trust these completely:
inning, half, batter, **balls and strikes**, pitch sequence, raw event string,
and pitcher of record (tracked through `start`/`sub` records).

Derived by pattern-matching Retrosheet's event notation — good for splits and
rate stats, not a full replay of game state: `event_type`, `runs_on_play`,
`outs_on_play`, `is_at_bat`.

The raw event string is on every row, so you can always re-derive anything.
If you ever need exact base-out state, run the files through Chadwick's
`cwevent` and load that CSV instead.

## Validation

The parser was checked against known 2015 facts before shipping:

| Check | Result |
|---|---|
| Regular season games | 2,429 — exact |
| Royals runs allowed | 641 — exact |
| Royals runs scored | 723 vs actual 724 |
| League runs | 20,677 vs actual 20,647 (0.15% high) |
| Royals team K% | 15.78% — MLB's lowest, as expected |
| Highest team K% | Cubs 24.26% — correct |
| World Series games | 5 — correct |
| Pitcher appearances | Davis 69, Herrera 72, Volquez 34 — all exact |
| Unclassified events | 0 of 217,410 |

Derived run totals run ~0.15% high on edge cases in the advancement notation.
Fine for rate work; don't quote it as an official figure.

## Pointing the MCP server at this

Once `MLB` is populated, edit `claude_desktop_config.json` so `DB_DATABASE`
is `MLB`, grant the reader login access, and restart Claude Desktop:

```sql
USE MLB;
CREATE USER mcp_reader FOR LOGIN mcp_reader;
ALTER ROLE db_datareader ADD MEMBER mcp_reader;
```

## Starter queries

Two-strike hitting, Royals against the league — the contact hypothesis:

```sql
SELECT batting_team,
       COUNT(*)                        AS two_strike_ab,
       SUM(CASE WHEN is_hit = 1 THEN 1 ELSE 0 END) AS hits,
       CAST(SUM(CASE WHEN is_hit = 1 THEN 1.0 ELSE 0 END) / COUNT(*) AS DECIMAL(4,3)) AS avg
FROM rs_plays
WHERE season = 2015 AND is_at_bat = 1 AND strikes = 2
GROUP BY batting_team
ORDER BY avg DESC;
```

Bullpen workload by inning — how far the Royals really compressed a game:

```sql
SELECT p.pitcher_id, l.player_name,
       COUNT(DISTINCT p.game_id) AS games,
       MIN(p.inning) AS earliest, MAX(p.inning) AS latest,
       SUM(p.runs_on_play) AS runs_allowed
FROM rs_plays p
LEFT JOIN (SELECT DISTINCT player_id, player_name FROM rs_lineups) l
       ON l.player_id = p.pitcher_id
WHERE p.season = 2015 AND p.fielding_team = 'KCA'
GROUP BY p.pitcher_id, l.player_name
HAVING COUNT(DISTINCT p.game_id) >= 20
ORDER BY games DESC;
```

The playoff comebacks, inning by inning:

```sql
SELECT game_id, inning, half, batting_team, SUM(runs_on_play) AS runs
FROM rs_plays
WHERE season = 2015 AND game_id IN (SELECT game_id FROM rs_games
                                    WHERE gametype <> 'regular')
GROUP BY game_id, inning, half, batting_team
HAVING SUM(runs_on_play) >= 3
ORDER BY game_id, inning;
```

## What this can't answer

DRS and UZR are proprietary metrics from Baseball Info Solutions and
FanGraphs. They can't be derived from any of this — download the leaderboards
from FanGraphs separately. Since defense is likely the largest part of the
Royals explanation, that gap matters.

Statcast's tracking-based fielding metrics don't fill it either; they matured
after 2015.

And the honest caveat: the 2015 playoff run was 16 games. These tables can
tell you whether the regular-season skills were real — large samples, properly
answerable. They cannot tell you why a 16-game stretch went the way it did.
Test the skill claims here; treat the run itself as something to characterize,
not solve.
