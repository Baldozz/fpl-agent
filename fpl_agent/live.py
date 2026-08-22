"""Fetch the manager's ACTUAL team and LIVE gameweek scores.

Distinct from the pre-deadline recommendation (model projections): this reads
the real squad you entered on FPL and the points each player is scoring in the
current gameweek, so the page can show your live scoreboard rather than a
forecast. Entry picks and live scores are public once the gameweek has started —
no auth needed here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import requests

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-agent)"}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass
class LivePick:
    name: str
    team_name: str
    pos: int
    cost: int
    slot: int            # 1..15 (1..11 = starting XI, 12..15 = bench order)
    multiplier: int      # 0 bench, 1 normal, 2 captain, 3 triple-captain
    is_captain: bool
    is_vice: bool
    points: int          # live GW points (before multiplier)
    minutes: int
    started_fixture: bool  # has the player's team match kicked off / finished

    @property
    def cost_m(self) -> float:
        return self.cost / 10.0

    @property
    def pos_name(self) -> str:
        return POS[self.pos]

    @property
    def net_points(self) -> int:
        return self.points * (self.multiplier or 1) if self.multiplier else self.points


@dataclass
class LiveTeam:
    team_id: int
    gw: int
    manager_name: str
    entry_name: str
    total_points: int          # official GW points (captain applied, XI only)
    bench_points: int
    overall_rank: int | None
    gw_rank: int | None
    active_chip: str | None
    xi: list[LivePick] = field(default_factory=list)
    bench: list[LivePick] = field(default_factory=list)
    captain: LivePick | None = None
    vice: LivePick | None = None


def resolve_team_id(explicit: int | None = None) -> int | None:
    """Team id from --flag, env FPL_TEAM_ID, or ~/.fpl-mcp/config.json."""
    if explicit:
        return explicit
    env = os.environ.get("FPL_TEAM_ID")
    if env and env.isdigit():
        return int(env)
    cfg = os.path.expanduser("~/.fpl-mcp/config.json")
    if os.path.exists(cfg):
        try:
            import json
            tid = json.load(open(cfg)).get("team_id")
            if tid and str(tid).isdigit():
                return int(tid)
        except Exception:
            pass
    return None


def _get(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_live_team(team_id: int, gw: int, bootstrap: dict) -> LiveTeam:
    elements = {e["id"]: e for e in bootstrap["elements"]}
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    picks = _get(f"{BASE}/entry/{team_id}/event/{gw}/picks/")
    live = _get(f"{BASE}/event/{gw}/live/")
    entry = _get(f"{BASE}/entry/{team_id}/")

    live_pts = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
    live_min = {e["id"]: e["stats"]["minutes"] for e in live["elements"]}
    # A player's fixture is "started" if the live feed shows any minutes or the
    # element has been provisionally scored; minutes>0 is the simplest signal.
    hist = picks.get("entry_history", {}) or {}

    def make(p: dict) -> LivePick:
        el = elements[p["element"]]
        pid = p["element"]
        return LivePick(
            name=el["web_name"], team_name=team_short[el["team"]],
            pos=el["element_type"], cost=el["now_cost"],
            slot=p["position"], multiplier=p["multiplier"],
            is_captain=p["is_captain"], is_vice=p["is_vice_captain"],
            points=live_pts.get(pid, 0), minutes=live_min.get(pid, 0),
            started_fixture=live_min.get(pid, 0) > 0,
        )

    all_picks = [make(p) for p in picks["picks"]]
    xi = [p for p in all_picks if p.slot <= 11]
    bench = [p for p in all_picks if p.slot > 11]
    captain = next((p for p in all_picks if p.is_captain), None)
    vice = next((p for p in all_picks if p.is_vice), None)

    return LiveTeam(
        team_id=team_id, gw=gw,
        manager_name=f"{entry.get('player_first_name','')} "
                     f"{entry.get('player_last_name','')}".strip(),
        entry_name=entry.get("name", ""),
        total_points=hist.get("points", 0),
        bench_points=hist.get("points_on_bench", 0),
        overall_rank=hist.get("overall_rank"),
        gw_rank=hist.get("rank"),
        active_chip=picks.get("active_chip"),
        xi=xi, bench=bench, captain=captain, vice=vice,
    )
