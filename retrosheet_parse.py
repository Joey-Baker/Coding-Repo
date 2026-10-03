"""
Pure-Python parser for Retrosheet event files (.EVA / .EVN / .EVE).

Turns raw event files into four tidy tables:

    games        one row per game, from the `info` records
    lineups      starters and substitutions, from `start` / `sub`
    plays        one row per play record - the main table
    earned_runs  from the `data,er,...` records

Why a hand-written parser instead of Chadwick's cwevent? Chadwick needs a C
compiler or a platform binary. This module needs nothing but the standard
library, which makes it far easier to run on Windows.

WHAT IS EXACT vs APPROXIMATE
----------------------------
Exact (read straight out of the file, no interpretation):
    inning, half, batter, balls, strikes, pitch sequence, raw event string,
    pitcher of record (tracked through start/sub records)

Approximate (derived by pattern-matching Retrosheet's event grammar):
    event_type, runs_on_play, outs_on_play, is_at_bat

The approximate fields cover the common cases well and are fine for splits
and rate stats. They are NOT a full replay of game state - Retrosheet's
notation has many edge cases. If you need exact base-out state or official
scoring reconstruction, run the files through Chadwick's cwevent instead and
load that CSV. The raw event string is preserved on every row so you can
always re-derive anything yourself.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Event classification
#
# Order matters: the first pattern that matches the start of the event string
# wins, so longer/more specific codes are listed before their prefixes.
# ---------------------------------------------------------------------------

_EVENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("no_play",           re.compile(r"^NP")),
    ("intent_walk",       re.compile(r"^I(W)?(?![0-9])")),
    ("walk",              re.compile(r"^W(?![0-9])")),
    ("strikeout",         re.compile(r"^K")),
    ("hit_by_pitch",      re.compile(r"^HP")),
    ("home_run",          re.compile(r"^(HR|H(?![PR]))")),
    ("ground_rule_double", re.compile(r"^DGR")),
    ("double",            re.compile(r"^D(?![IG])")),
    ("triple",            re.compile(r"^T(?![Hh])")),
    ("single",            re.compile(r"^S(?![BH])")),
    ("fielders_choice",   re.compile(r"^FC")),
    ("foul_fly_error",    re.compile(r"^FLE")),
    ("force_out",         re.compile(r"^FO")),
    ("error",             re.compile(r"^E[0-9]")),
    ("catcher_interf",    re.compile(r"^C(?![SA-Z])")),
    ("stolen_base",       re.compile(r"^SB")),
    ("caught_stealing",   re.compile(r"^CS")),
    ("pickoff_cs",        re.compile(r"^POCS")),
    ("pickoff",           re.compile(r"^PO(?!CS)")),
    ("wild_pitch",        re.compile(r"^WP")),
    ("passed_ball",       re.compile(r"^PB")),
    ("balk",              re.compile(r"^BK")),
    ("defensive_indiff",  re.compile(r"^DI")),
    ("other_advance",     re.compile(r"^OA")),
    ("field_out",         re.compile(r"^[0-9]")),
]

# Events that are baserunning / administrative rather than a batter outcome.
_NON_BATTER_EVENTS = {
    "no_play", "stolen_base", "caught_stealing", "pickoff", "pickoff_cs",
    "wild_pitch", "passed_ball", "balk", "defensive_indiff", "other_advance",
}

# Batter reached or was retired, and it counts as an official at-bat.
_AT_BAT_EVENTS = {
    "strikeout", "home_run", "double", "ground_rule_double", "triple",
    "single", "field_out", "force_out", "fielders_choice", "error",
    "foul_fly_error",
}

_HIT_EVENTS = {"single", "double", "ground_rule_double", "triple", "home_run"}

# Runner advancing to home, e.g. ".2-H" or ".3-H(UR)". A dash means safe;
# "2XH" means thrown out at the plate and must not count.
_SCORE_RE = re.compile(r"[123B]-H")
# Runner retired somewhere, e.g. "1X2".
_RUNNER_OUT_RE = re.compile(r"[123B]X[123H]")


def classify_event(event: str) -> str:
    """Map a raw Retrosheet event string to a coarse event_type."""
    core = event.split("/")[0].split(".")[0].strip()
    for name, pattern in _EVENT_RULES:
        if pattern.match(core):
            return name
    return "unknown"


def split_event(event: str) -> tuple[str, str]:
    """Split 'S8/L.2-H;1-3' into ('S8/L', '2-H;1-3')."""
    if "." in event:
        head, _, tail = event.partition(".")
        return head, tail
    return event, ""


def derive_runs(event: str, event_type: str) -> int:
    """Best-effort count of runs scored on the play."""
    _, advance = split_event(event)
    runs = len(_SCORE_RE.findall(advance))
    if event_type == "home_run":
        runs += 1  # the batter's own run is not written in the advance string
    return runs


def derive_outs(event: str, event_type: str) -> int:
    """Best-effort count of outs recorded on the play."""
    head, advance = split_event(event)
    outs = 0

    if event_type in {"strikeout", "field_out", "force_out"}:
        outs += 1
    elif event_type in {"caught_stealing", "pickoff", "pickoff_cs"}:
        # An error on the throw means the runner was safe after all.
        if not re.search(r"\(\d*E\d\)", head):
            outs += 1

    # Double / triple play modifiers override the single-out assumption.
    if re.search(r"/[A-Z]*DP", head):
        outs = max(outs, 2)
    if re.search(r"/[A-Z]*TP", head):
        outs = max(outs, 3)

    outs += len(_RUNNER_OUT_RE.findall(advance))
    return min(outs, 3)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# info keys worth promoting to columns on the games table
_GAME_INFO_KEYS = [
    "visteam", "hometeam", "site", "date", "number", "starttime", "daynight",
    "usedh", "gametype", "temp", "winddir", "windspeed", "fieldcond",
    "precip", "sky", "timeofgame", "attendance", "wp", "lp", "save",
    "howscored", "pitches",
]


@dataclass
class ParseResult:
    games: list[dict] = field(default_factory=list)
    lineups: list[dict] = field(default_factory=list)
    plays: list[dict] = field(default_factory=list)
    earned_runs: list[dict] = field(default_factory=list)

    def extend(self, other: "ParseResult") -> None:
        self.games.extend(other.games)
        self.lineups.extend(other.lineups)
        self.plays.extend(other.plays)
        self.earned_runs.extend(other.earned_runs)

    def counts(self) -> dict[str, int]:
        return {
            "games": len(self.games),
            "lineups": len(self.lineups),
            "plays": len(self.plays),
            "earned_runs": len(self.earned_runs),
        }


def _iter_records(path: Path) -> Iterator[list[str]]:
    """Yield each line as CSV fields. Retrosheet quotes player names."""
    with path.open("r", encoding="latin-1", newline="") as fh:
        for row in csv.reader(fh):
            if row and row[0]:
                yield row


def parse_file(path: Path, season: int | None = None) -> ParseResult:
    """Parse one Retrosheet event file into tidy rows."""
    result = ParseResult()

    game_id: str | None = None
    info: dict[str, str] = {}
    lineups: list[dict] = []
    plays: list[dict] = []
    ers: list[dict] = []
    # current pitcher for each side: 0 = visiting team, 1 = home team
    current_pitcher: dict[int, str | None] = {0: None, 1: None}
    play_seq = 0
    sub_seq = 0

    def flush() -> None:
        """Emit the game we have accumulated, if any."""
        if game_id is None:
            return
        row = {"game_id": game_id, "season": season, "source_file": path.name}
        for key in _GAME_INFO_KEYS:
            row[key] = info.get(key)
        # Regular season files carry no `info,gametype` record at all, which
        # would leave NULL here - and `WHERE gametype <> 'regular'` silently
        # matches nothing against NULL. Name it explicitly instead.
        if not row.get("gametype"):
            row["gametype"] = "regular"
        result.games.append(row)
        result.lineups.extend(lineups)
        result.plays.extend(plays)
        result.earned_runs.extend(ers)

    for rec in _iter_records(path):
        kind = rec[0]

        if kind == "id":
            flush()
            game_id = rec[1] if len(rec) > 1 else None
            info, lineups, plays, ers = {}, [], [], []
            current_pitcher = {0: None, 1: None}
            play_seq = 0
            sub_seq = 0

        elif kind == "info" and len(rec) >= 3:
            info[rec[1]] = rec[2]

        elif kind in ("start", "sub") and len(rec) >= 6:
            try:
                side = int(rec[3])
                order = int(rec[4])
                position = int(rec[5])
            except ValueError:
                continue
            sub_seq += 1
            lineups.append({
                "game_id": game_id,
                "season": season,
                "player_id": rec[1],
                "player_name": rec[2],
                "side": side,
                "batting_order": order,
                "field_position": position,
                "is_starter": kind == "start",
                "seq": sub_seq,
            })
            if position == 1:
                current_pitcher[side] = rec[1]

        elif kind == "play" and len(rec) >= 7:
            try:
                inning = int(rec[1])
                side = int(rec[2])
            except ValueError:
                continue
            count = rec[4] or ""
            balls = int(count[0]) if len(count) > 0 and count[0].isdigit() else None
            strikes = int(count[1]) if len(count) > 1 and count[1].isdigit() else None
            event = rec[6]
            event_type = classify_event(event)
            pitch_seq = rec[5] or ""

            play_seq += 1
            plays.append({
                "game_id": game_id,
                "season": season,
                "play_seq": play_seq,
                "inning": inning,
                # side is the batting team: 0 = visitor (top), 1 = home (bottom)
                "half": "top" if side == 0 else "bottom",
                "batting_side": side,
                "batting_team": info.get("visteam") if side == 0 else info.get("hometeam"),
                "fielding_team": info.get("hometeam") if side == 0 else info.get("visteam"),
                "batter_id": rec[3],
                "pitcher_id": current_pitcher[1 - side],
                "balls": balls,
                "strikes": strikes,
                # Not named "count": COUNT is a SQL function, so a column of
                # that name breaks `SELECT count FROM ...` and trips up any
                # tool writing queries against this table.
                "balls_strikes": count,
                "pitch_seq": pitch_seq,
                "n_pitches": sum(1 for c in pitch_seq if c not in ".*123>+"),
                "event_raw": event,
                "event_type": event_type,
                "is_no_play": event_type == "no_play",
                "is_batter_event": event_type not in _NON_BATTER_EVENTS,
                "is_at_bat": event_type in _AT_BAT_EVENTS,
                "is_hit": event_type in _HIT_EVENTS,
                "runs_on_play": derive_runs(event, event_type),
                "outs_on_play": derive_outs(event, event_type),
                "two_strikes": strikes == 2 if strikes is not None else None,
            })

        elif kind == "data" and len(rec) >= 4 and rec[1] == "er":
            try:
                value = int(rec[3])
            except ValueError:
                continue
            ers.append({
                "game_id": game_id,
                "season": season,
                "pitcher_id": rec[2],
                "earned_runs": value,
            })

    flush()
    return result


def parse_season(season_dir: Path, season: int | None = None,
                 pattern: str = "*.EV*") -> ParseResult:
    """Parse every event file in a directory (one season's worth)."""
    total = ParseResult()
    files = sorted(season_dir.glob(pattern))
    if not files:
        raise FileNotFoundError(f"no event files matching {pattern} in {season_dir}")
    for path in files:
        total.extend(parse_file(path, season=season))
    return total
