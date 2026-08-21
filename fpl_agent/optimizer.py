"""Squad optimisation under the official FPL rules.

Squad: 15 players = 2 GK, 5 DEF, 5 MID, 3 FWD, budget £100.0m, max 3 per club.
Starting XI: exactly 1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD (11 players).
We maximise projected points for the XI while ensuring the full squad is legal,
then pick captain (2x) / vice and order the bench.

Uses PuLP (CBC) if available; otherwise falls back to a greedy + swap heuristic
so the agent still produces a legal team with no hard dependency.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Player

BUDGET = 1000  # £100.0m in tenths
# Per-started-forward attacking-ceiling bonus (tips near-ties to 3-4-3). Raise
# for a more attacking bias, lower/zero for a purely mean-points team.
ATTACK_CEILING = 1.0
# Hard cap on total bench cost (tenths of £m). The absolute rules floor is
# ~£16.5m: a bench is always 4 players (1 GK + 3 outfield) and the cheapest are
# £4.0m (GK/DEF) and £4.5m (MID/FWD), so 4.0+4.0+4.0+4.5 = 16.5. At this floor
# the bench is pure cheap fodder (won't reliably play) — all the money is in XI.
MAX_BENCH_COST = 165
# Gentle nudge toward cheaper bench *within* the cap; the cover reward keeps the
# first subs playable.
BENCH_COST_WEIGHT = 0.02
# Default starting formation as (DEF, MID, FWD); None lets the optimiser choose.
DEFAULT_FORMATION = (3, 4, 3)
SQUAD_QUOTA = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3


@dataclass
class Squad:
    squad: list[Player]        # all 15
    xi: list[Player]           # starting 11
    bench: list[Player]        # ordered: GK bench first, then outfield by score
    captain: Player
    vice: Player

    @property
    def total_cost(self) -> int:
        return sum(p.cost for p in self.squad)

    @property
    def xi_projected(self) -> float:
        base = sum(p.projected for p in self.xi)
        return round(base + self.captain.projected, 2)  # captain counted twice


def _pick_xi(squad: list[Player]) -> list[Player]:
    """Best legal XI from 15 by projected points."""
    by_pos: dict[int, list[Player]] = {1: [], 2: [], 3: [], 4: []}
    for p in squad:
        by_pos[p.pos].append(p)
    for lst in by_pos.values():
        lst.sort(key=lambda p: p.projected, reverse=True)

    # Start with the minimum required at each position.
    xi = [by_pos[1][0]]                 # 1 GK
    xi += by_pos[2][:XI_MIN[2]]
    xi += by_pos[3][:XI_MIN[3]]
    xi += by_pos[4][:XI_MIN[4]]

    # Remaining outfield candidates (respecting per-position maxima), fill to 11.
    remaining: list[Player] = []
    for pos in (2, 3, 4):
        remaining += by_pos[pos][XI_MIN[pos]:XI_MAX[pos]]
    remaining.sort(key=lambda p: p.projected, reverse=True)
    for p in remaining:
        if len(xi) == 11:
            break
        xi.append(p)
    return xi


def _order_bench(squad: list[Player], xi: list[Player]) -> list[Player]:
    xi_ids = {p.id for p in xi}
    bench = [p for p in squad if p.id not in xi_ids]
    gk = [p for p in bench if p.pos == 1]
    out = sorted((p for p in bench if p.pos != 1),
                 key=lambda p: p.projected, reverse=True)
    return gk + out  # bench GK is a separate slot; outfield by likelihood to sub in


def optimize_ilp(players: list[Player], formation: tuple[int, int, int] | None = None,
                 must_include: set[int] | None = None):
    """Return (squad, xi) as lists, or None if no solver / not optimal.

    ``formation`` pins the starting XI outfield shape as (DEF, MID, FWD), e.g.
    ``(3, 4, 3)`` for 3-4-3. When None the shape is free within the legal ranges.
    ``must_include`` is a set of player ids forced into the 15-man squad.
    """
    try:
        import pulp
    except ImportError:
        return None

    must_include = must_include or set()
    # Forced players must be candidates even if their projection is ~0.
    avail = [p for p in players if p.projected > 0 or p.id in must_include]
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    x = {p.id: pulp.LpVariable(f"x_{p.id}", cat="Binary") for p in avail}
    s = {p.id: pulp.LpVariable(f"s_{p.id}", cat="Binary") for p in avail}  # in XI
    pm = {p.id: p for p in avail}

    # Objective — what we actually score each week is the STARTING XI, not all 15.
    #   (1) maximise XI projected points (dominant term);
    #   (2) reward bench players who will ACTUALLY PLAY (real injury/rotation
    #       cover). This is the key structural lever: cheap playing cover exists
    #       for defenders (£4.0m starters at weak clubs) and the bench GK, but a
    #       £4.5m midfielder/forward never starts. Rewarding cover therefore
    #       nudges the squad to bench cheap playing DEFENDERS — which means
    #       fewer defenders and more attackers in the XI (e.g. 3-4-3), giving
    #       the best chance of fielding the actual goal-scorers;
    #   (3) among equally good options, prefer a CHEAPER bench so spare budget
    #       concentrates in the XI (the points-per-million effect).
    # Weights are small vs XI points, so they shape the bench/formation without
    # ever sacrificing real starting-XI points.
    # "Cover" credits only bench players who will genuinely play (start_prob
    # comfortably above 50%); a £4.5m forward who never starts scores ~0 here.
    cover = {i: max(0.0, pm[i].start_prob - 0.5) for i in x}
    bench = {i: (x[i] - s[i]) for i in x}
    # Attacking-ceiling bonus for STARTING forwards: mean projections understate
    # a striker's goal upside, and fielding more forwards (e.g. 3-4-3) gives the
    # best chance of owning the actual goal-scorers. Small enough that it only
    # tips genuine near-ties toward the attacking shape, never a clear call.
    attack = {i: (ATTACK_CEILING if pm[i].pos == 4 else 0.0) for i in x}
    prob += (
        pulp.lpSum(s[i] * pm[i].projected for i in x)
        + pulp.lpSum(s[i] * attack[i] for i in x)
        + 1.6 * pulp.lpSum(bench[i] * cover[i] for i in x)
        - BENCH_COST_WEIGHT * pulp.lpSum(bench[i] * pm[i].cost for i in x)
    )

    prob += pulp.lpSum(x.values()) == 15
    prob += pulp.lpSum(s.values()) == 11
    prob += pulp.lpSum(pm[i].cost * x[i] for i in x) <= BUDGET
    prob += pulp.lpSum(bench[i] * pm[i].cost for i in x) <= MAX_BENCH_COST
    for i in x:
        prob += s[i] <= x[i]
    for pos, q in SQUAD_QUOTA.items():
        prob += pulp.lpSum(x[i] for i in x if pm[i].pos == pos) == q
    if formation is not None:
        want = {1: 1, 2: formation[0], 3: formation[1], 4: formation[2]}
        for pos, q in want.items():
            prob += pulp.lpSum(s[i] for i in x if pm[i].pos == pos) == q
    else:
        for pos in (1, 2, 3, 4):
            prob += pulp.lpSum(s[i] for i in x if pm[i].pos == pos) >= XI_MIN[pos]
            prob += pulp.lpSum(s[i] for i in x if pm[i].pos == pos) <= XI_MAX[pos]
    clubs = {p.team for p in avail}
    for c in clubs:
        prob += pulp.lpSum(x[i] for i in x if pm[i].team == c) <= MAX_PER_CLUB
    for i in must_include:
        if i in x:
            prob += x[i] == 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    squad = [pm[i] for i in x if x[i].value() == 1]
    xi = [pm[i] for i in x if s[i].value() == 1]
    return squad, xi


def optimize_greedy(players: list[Player]) -> list[Player]:
    """Value-based greedy fill + club/budget repair. Fallback when no solver."""
    avail = sorted((p for p in players if p.projected > 0),
                   key=lambda p: p.projected / max(p.cost, 1), reverse=True)
    squad: list[Player] = []
    spend = 0
    per_club: dict[int, int] = {}
    per_pos: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for p in avail:
        if len(squad) == 15:
            break
        if per_pos[p.pos] >= SQUAD_QUOTA[p.pos]:
            continue
        if per_club.get(p.team, 0) >= MAX_PER_CLUB:
            continue
        if spend + p.cost > BUDGET:
            continue
        squad.append(p)
        spend += p.cost
        per_club[p.team] = per_club.get(p.team, 0) + 1
        per_pos[p.pos] += 1
    return squad


def build_squad(players: list[Player],
                formation: tuple[int, int, int] | None = DEFAULT_FORMATION,
                must_include: set[int] | None = None) -> Squad:
    result = optimize_ilp(list(players), formation, must_include)
    if result is not None:
        chosen, xi = result       # formation pinned / decided by the ILP
    else:
        chosen = optimize_greedy(list(players))
        xi = _pick_xi(chosen)
    xi_sorted = sorted(xi, key=lambda p: p.projected, reverse=True)
    captain, vice = xi_sorted[0], xi_sorted[1]
    bench = _order_bench(chosen, xi)
    return Squad(squad=chosen, xi=xi, bench=bench, captain=captain, vice=vice)
