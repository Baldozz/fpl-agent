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
    chips: dict[str, list[int]] = field(default_factory=dict)  # chip -> GWs used
    # Squad-strength scoring (filled by attach_power): a forward-looking rating
    # of how good the SQUAD is right now, independent of accumulated points.
    power: float = 0.0             # Σ projected pts over XI (+ bench depth bonus)
    xi_form: float = 0.0           # Σ current form over the XI
    power_rank: int = 0            # 1 = strongest squad in the league

    @property
    def movement(self) -> str:
        if not self.last_rank or self.last_rank == self.rank:
            return "="
        return "▲" if self.rank < self.last_rank else "▼"

    @property
    def power_delta(self) -> int:
        """How much better/worse the squad rates than the league position.

        Positive = the squad is stronger than the table suggests (rising);
        negative = over-performing the squad's underlying quality.
        """
        if not self.power_rank:
            return 0
        return self.rank - self.power_rank


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


# FPL chip ids -> short column labels. Each is available once per half-season
# (GW1-19, GW20-38), so a manager can legitimately show two gameweeks per chip.
CHIP_LABELS = {"wildcard": "WC", "freehit": "FH", "bboost": "BB", "3xc": "TC"}


def fetch_chips(entry_id: int) -> dict[str, list[int]]:
    """Which chips a manager has already played, as {chip: [gameweeks]}.

    From the public ``entry/{id}/history/`` endpoint. Empty dict on failure so a
    404/blip costs one manager's chips, not the whole league page.
    """
    try:
        data = _get(f"{BASE}/entry/{entry_id}/history/")
    except Exception:
        return {}
    used: dict[str, list[int]] = {}
    for c in data.get("chips", []):
        used.setdefault(c["name"], []).append(c["event"])
    return {k: sorted(v) for k, v in used.items()}


def fetch_league(league_id: int, gw: int, bootstrap: dict,
                 with_teams: bool = True, max_teams: int = 30) -> League:
    """Standings plus (optionally) each rival's live team for the gameweek."""
    name, rows = fetch_standings(league_id)
    if with_teams:
        from concurrent.futures import ThreadPoolExecutor

        def one(row):
            try:
                row.team = fetch_live_team(row.entry_id, gw, bootstrap)
            except Exception:
                row.team = None
            row.chips = fetch_chips(row.entry_id)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(one, rows[:max_teams]))
    return League(league_id=league_id, name=name, gw=gw, rows=rows)


# Bench players rarely play but add depth/cover — count them at a discount.
BENCH_DEPTH_WEIGHT = 0.15


def attach_power(league: League, players: dict) -> None:
    """Rate every rival's SQUAD by projected points + current form.

    ``players`` is the scored ``dict[element_id -> Player]`` from
    ``agent.prepare_players`` — its ``projected`` already blends form, fixture
    difficulty, team strength and availability, so summing it over a manager's
    starting XI gives a forward-looking "how strong is this squad right now"
    number that is independent of the points they have banked so far. A small
    weighted contribution from the bench rewards squad depth.

    Sets ``power``, ``xi_form`` and ``power_rank`` on each row that has a team.
    """
    scored = [r for r in league.rows if r.team]
    for row in scored:
        xi_proj = sum(players[p.element].projected
                      for p in row.team.xi if p.element in players)
        bench_proj = sum(players[p.element].projected
                         for p in row.team.bench if p.element in players)
        row.power = xi_proj + BENCH_DEPTH_WEIGHT * bench_proj
        row.xi_form = sum(players[p.element].form
                          for p in row.team.xi if p.element in players)
    for i, row in enumerate(sorted(scored, key=lambda r: -r.power), start=1):
        row.power_rank = i


# Ownership bands used to tag recommendations. "Template" = most rivals already
# have him (owning him protects your rank, selling him exposes it); a
# "differential" is a pick few rivals have (gains you places when he hauls).
TEMPLATE_OWN = 0.40
DIFFERENTIAL_OWN = 0.10


@dataclass
class Ownership:
    """How often each player appears across rivals' 15-man squads."""
    pct: dict[int, float]          # element id -> fraction of rival squads (0..1)
    n_squads: int                  # rival squads actually read
    n_members: int                 # rivals in the standings we fetched
    gw: int                        # picks are as of this GW's deadline
    league_name: str = ""
    truncated: bool = False        # standings/teams capped before the full league

    @property
    def partial(self) -> bool:
        return self.truncated or self.n_squads < self.n_members

    def of(self, pid: int) -> float:
        return self.pct.get(pid, 0.0)

    def tag(self, pid: int) -> str:
        o = self.of(pid)
        if o >= TEMPLATE_OWN:
            return "template"
        if o <= DIFFERENTIAL_OWN:
            return "differential"
        return ""

    @property
    def caveat(self) -> str:
        """Plain-English note on how complete / current this data is."""
        s = (f"Varsical ownership from {self.n_squads} of {self.n_members} rival "
             f"squads, as entered at the GW{self.gw} deadline — rivals' transfers "
             f"since then aren't public.")
        if self.partial:
            s += " Incomplete: some squads couldn't be read or the league was capped."
        return s


def league_ownership(league: League, exclude_entry: int | None = None,
                     max_teams: int = 30) -> Ownership | None:
    """Ownership fraction per player across rival squads (XI + bench).

    ``exclude_entry`` (your own team) is left out so the numbers describe the
    opposition. None when no rival squad could be read."""
    rivals = [r for r in league.rows if r.entry_id != exclude_entry]
    teams = [r.team for r in rivals if r.team]
    if not teams:
        return None
    count: dict[int, int] = {}
    for t in teams:
        for p in t.xi + t.bench:
            count[p.element] = count.get(p.element, 0) + 1
    return Ownership(
        pct={pid: c / len(teams) for pid, c in count.items()},
        n_squads=len(teams), n_members=len(rivals), gw=league.gw,
        league_name=league.name,
        truncated=len(league.rows) > max_teams or len(league.rows) >= 60)
