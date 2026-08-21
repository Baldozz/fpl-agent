"""Expected-points model.

Turns raw FPL data into a single ``projected`` score per player for the upcoming
gameweek(s). Designed to degrade gracefully at the season opener (when live
``form`` is 0 for everyone) by leaning on last-season output and FPL's own
``ep_next`` projection, then to lean more on live form as the season develops.
"""
from __future__ import annotations

from dataclasses import dataclass, field

POS_NAME = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# Availability status -> multiplier applied to projected points.
STATUS_MULT = {
    "a": 1.00,   # available
    "d": 0.75,   # doubtful (refined by chance_of_playing below)
    "i": 0.0,    # injured
    "s": 0.0,    # suspended
    "u": 0.0,    # unavailable (left club etc.)
    "n": 0.0,    # not in squad
}


@dataclass
class Player:
    id: int
    name: str
    team: int
    team_name: str
    pos: int            # element_type 1..4
    cost: int           # now_cost in tenths of a million (e.g. 60 == £6.0m)
    status: str
    news: str
    selected_by: float
    ep_next: float
    ppg: float
    form: float
    total_points: int
    minutes: int
    chance: int | None   # chance_of_playing_next_round (0..100 or None)
    fdr: float = 3.0     # avg fixture difficulty over horizon (lower = easier)
    n_fix: int = 1       # number of fixtures in horizon (DGW awareness)
    projected: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def cost_m(self) -> float:
        return self.cost / 10.0

    @property
    def pos_name(self) -> str:
        return POS_NAME[self.pos]


def _availability_mult(status: str, chance: int | None) -> float:
    if chance is not None:
        # An explicit percentage overrides the coarse status bucket.
        return chance / 100.0
    return STATUS_MULT.get(status, 1.0)


def _fdr_multiplier(fdr: float) -> float:
    """Map fixture difficulty (1 easy .. 5 hard) to a scoring multiplier.

    FDR 2 -> ~1.15, FDR 3 -> ~1.0, FDR 4 -> ~0.85, FDR 5 -> ~0.7.
    """
    return 1.0 + (3.0 - fdr) * 0.15


def build_players(bootstrap: dict) -> dict[int, Player]:
    team_name = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    players: dict[int, Player] = {}
    for e in bootstrap["elements"]:
        players[e["id"]] = Player(
            id=e["id"],
            name=e["web_name"],
            team=e["team"],
            team_name=team_name[e["team"]],
            pos=e["element_type"],
            cost=e["now_cost"],
            status=e["status"],
            news=e["news"] or "",
            selected_by=float(e["selected_by_percent"]),
            ep_next=float(e["ep_next"] or 0),
            ppg=float(e["points_per_game"] or 0),
            form=float(e["form"] or 0),
            total_points=int(e["total_points"]),
            minutes=int(e["minutes"]),
            chance=e.get("chance_of_playing_next_round"),
        )
    return players


def attach_fixtures(players: dict[int, Player], fixtures: list[dict],
                    start_event: int, horizon: int) -> None:
    """Attach average FDR over the next ``horizon`` gameweeks to each player."""
    events = set(range(start_event, start_event + horizon))
    by_team: dict[int, list[float]] = {}
    for fx in fixtures:
        ev = fx.get("event")
        if ev is None or ev not in events:
            continue
        by_team.setdefault(fx["team_h"], []).append(fx["team_h_difficulty"])
        by_team.setdefault(fx["team_a"], []).append(fx["team_a_difficulty"])
    for p in players.values():
        diffs = by_team.get(p.team, [])
        p.n_fix = len(diffs)
        p.fdr = sum(diffs) / len(diffs) if diffs else 5.0  # no fixture -> penalise


def score_players(players: dict[int, Player], season_started: bool) -> None:
    """Compute ``projected`` points for each player for the horizon."""
    for p in players.values():
        # Base per-match expectation. At the opener there is no live form, so we
        # trust FPL's ep_next and last-season points-per-game. Once the season is
        # under way, live form gets progressively more weight.
        if season_started and p.form > 0:
            base = 0.45 * p.ep_next + 0.30 * p.form + 0.25 * p.ppg
        else:
            base = 0.60 * p.ep_next + 0.40 * p.ppg

        fdr_mult = _fdr_multiplier(p.fdr)
        avail = _availability_mult(p.status, p.chance)

        # Scale by number of fixtures in the horizon (double gameweek aware),
        # but keep single-GW as the unit so scores stay interpretable.
        per_match = base * fdr_mult * avail
        p.projected = round(per_match * max(p.n_fix, 1), 2)

        p.reasons = []
        if avail == 0:
            p.reasons.append("unavailable")
        elif avail < 1:
            p.reasons.append(f"doubt {int(avail*100)}%")
        if p.fdr <= 2.3:
            p.reasons.append("easy fixture")
        elif p.fdr >= 4.0:
            p.reasons.append("hard fixture")
        if p.n_fix >= 2:
            p.reasons.append(f"{p.n_fix} fixtures")
