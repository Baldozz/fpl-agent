"""Monitor a private classic league: standings + every rival's live team.

Public endpoints (no auth): the league standings are paginated; each manager's
gameweek picks are read the same way as your own. Used to render a league page
so you can see where you stand and what your rivals are fielding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from .live import LivePick, LiveTeam, fetch_live_team

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-agent)"}


@dataclass
class LeagueRow:
    rank: int
    last_rank: int
    manager: str
    entry_name: str
    entry_id: int
    total: int
    gw_points: int
    team: LiveTeam | None = None   # filled if we fetch each rival's squad

    @property
    def movement(self) -> str:
        if not self.last_rank or self.last_rank == self.rank:
            return "="
        return "▲" if self.rank < self.last_rank else "▼"


@dataclass
class League:
    league_id: int
    name: str
    gw: int
    rows: list[LeagueRow] = field(default_factory=list)


def _get(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_standings(league_id: int, max_members: int = 60) -> tuple[str, list[LeagueRow]]:
    name = ""
    rows: list[LeagueRow] = []
    page = 1
    while len(rows) < max_members:
        data = _get(f"{BASE}/leagues-classic/{league_id}/standings/?page_standings={page}")
        name = data["league"]["name"]
        results = data["standings"]["results"]
        for r in results:
            rows.append(LeagueRow(
                rank=r["rank"], last_rank=r.get("last_rank", 0),
                manager=r["player_name"], entry_name=r["entry_name"],
                entry_id=r["entry"], total=r["total"], gw_points=r["event_total"],
            ))
        if not data["standings"].get("has_next") or not results:
            break
        page += 1
    return name, rows[:max_members]


def fetch_league(league_id: int, gw: int, bootstrap: dict,
                 with_teams: bool = True, max_teams: int = 30) -> League:
    """Standings plus (optionally) each rival's live team for the gameweek."""
    name, rows = fetch_standings(league_id)
    if with_teams:
        for row in rows[:max_teams]:
            try:
                row.team = fetch_live_team(row.entry_id, gw, bootstrap)
            except Exception:
                row.team = None
    return League(league_id=league_id, name=name, gw=gw, rows=rows)
