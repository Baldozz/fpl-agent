"""Chip calendar: when is each chip worth the most?

Values every remaining chip in every gameweek it can still be played, from the
same per-gameweek projections (``Player.gw_proj``) and squad optimiser the
transfer advice uses, so it reacts to YOUR squad rather than the fixture
calendar alone:

  Free Hit       best one-week squad for GW g (your budget, all squad rules)
                 minus your squad's best XI in g — it reverts afterwards, so
                 only that week counts. Blanks/doubles show up on their own.
  Wildcard       best squad for g..g+4 minus your squad over the same weeks.
  Bench Boost    your bench's projected points in g.
  Triple Captain your best MID/FWD captain's projected points in g (the 3rd ×).

All XIs include the captain (MID/FWD, the user's rule) and use the best legal
formation that week. Only one chip may be played per gameweek, so weeks are
assigned greedily by value. Values assume your current 15 with no other
transfers. Advisory only: nothing here plays a chip.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .model import CAPTAIN_POSITIONS, Player
from .optimizer import DEFAULT_FORMATION, optimize_ilp

CHIPS = ("freehit", "wildcard", "bboost", "3xc")
LABEL = {"freehit": "Free Hit", "wildcard": "Wildcard",
         "bboost": "Bench Boost", "3xc": "Triple Captain"}
# Gameweeks scanned from the next deadline (clamped to the chip's half).
SCAN_GWS = 8
WILDCARD_WINDOW = 5
# Per-GW projections needed: every scanned week plus a wildcard window after it.
PROJECTION_GWS = SCAN_GWS + WILDCARD_WINDOW - 1
# A week "stands out" when it beats the next-best week by this much (points)
# or this fraction of its value, whichever is larger; otherwise the advice is
# "no clear week yet" — projections this far out don't justify more precision.
CLEAR_MARGIN = 2.0
CLEAR_FRACTION = 0.15
# First-half chips expire at this deadline; warn when this few GWs remain.
FIRST_HALF_END = 19
EXPIRY_WARN_GWS = 4
SOLVE_WORKERS = 4            # parallel optimiser runs per chip
_FORMATIONS = [(d, m, 10 - d - m) for d in range(3, 6) for m in range(2, 6)
               if 1 <= 10 - d - m <= 3]


@dataclass
class ChipPlan:
    chip: str
    available: bool
    used: list[int] = field(default_factory=list)
    values: dict[int, float] = field(default_factory=dict)   # gw -> points gained
    best_gw: int | None = None          # this chip's single best week
    runner_up_gw: int | None = None
    planned_gw: int | None = None       # after one-chip-per-week assignment
    clear: bool = False                 # best week stands out from the rest
    expires_gw: int | None = None       # last GW this chip can be played
    rivals_used: int | None = None      # Varsical rivals who played it this half
    rivals_total: int | None = None
    start_gw: int = 0                   # the upcoming GW the scan starts from

    @property
    def label(self) -> str:
        return LABEL[self.chip]

    @property
    def best_value(self) -> float:
        return self.values.get(self.best_gw, 0.0) if self.best_gw else 0.0

    @property
    def gws_left(self) -> int | None:
        """Gameweeks left to play it, counting the upcoming one."""
        return None if self.expires_gw is None else self.expires_gw - self.start_gw + 1


def chip_open(upcoming_gw: int, used: list[int]) -> bool:
    """One of each chip per half-season (GW1-19, GW20-38). Open if unused this
    half, or if it's the one activated for the upcoming GW."""
    half = 1 if upcoming_gw <= FIRST_HALF_END else 2
    return all((1 if g <= FIRST_HALF_END else 2) != half or g == upcoming_gw
               for g in used)


def best_xi(squad: list[Player], pts: dict[int, float]) -> tuple[list[Player], float]:
    """Best legal XI for one gameweek's points, any formation, captain doubled
    (MID/FWD only). Returns (xi, points incl. captain)."""
    by_pos = {pos: sorted((p for p in squad if p.pos == pos),
                          key=lambda p: pts.get(p.id, 0.0), reverse=True)
              for pos in (1, 2, 3, 4)}
    best, best_pts = [], -1.0
    for d, m, f in _FORMATIONS:
        if len(by_pos[2]) < d or len(by_pos[3]) < m or len(by_pos[4]) < f \
                or not by_pos[1]:
            continue
        xi = by_pos[1][:1] + by_pos[2][:d] + by_pos[3][:m] + by_pos[4][:f]
        tot = sum(pts.get(p.id, 0.0) for p in xi)
        if tot > best_pts:
            best, best_pts = xi, tot
    return best, best_pts + _captain_pts(best, pts)


def _captain_pts(xi: list[Player], pts: dict[int, float]) -> float:
    pool = [p for p in xi if p.pos in CAPTAIN_POSITIONS] or xi
    return max((pts.get(p.id, 0.0) for p in pool), default=0.0)


def _pts(players: list[Player], gw: int) -> dict[int, float]:
    return {p.id: p.gw_proj.get(gw, 0.0) for p in players}


def _value_freehit(current, players, gw, budget) -> float | None:
    pts = _pts(players, gw)
    res = optimize_ilp(players, None, None, points=pts, budget=budget)
    if res is None:
        return None
    return best_xi(res[0], pts)[1] - best_xi(current, pts)[1]


def _value_wildcard(current, players, gw, budget, must_include, horizon_end) -> float | None:
    window = [g for g in range(gw, gw + WILDCARD_WINDOW) if g <= horizon_end]
    per_gw = {g: _pts(players, g) for g in window}
    total = {p.id: sum(per_gw[g][p.id] for g in window) for p in players}
    res = optimize_ilp(players, DEFAULT_FORMATION, must_include, points=total,
                       budget=budget)
    if res is None:
        return None
    return sum(best_xi(res[0], per_gw[g])[1] - best_xi(current, per_gw[g])[1]
               for g in window)


def _value_bboost(current, gw) -> float:
    pts = _pts(current, gw)
    xi, _ = best_xi(current, pts)
    ids = {p.id for p in xi}
    return sum(pts[p.id] for p in current if p.id not in ids)


def _value_tc(current, gw) -> float:
    pts = _pts(current, gw)
    return _captain_pts(best_xi(current, pts)[0], pts)


def chip_calendar(current: list[Player], players: list[Player], bank: int,
                  upcoming_gw: int, chips_used: dict[str, list[int]],
                  selling: dict[int, int] | None = None,
                  must_include: set[int] | None = None,
                  rival_chips: dict[str, int] | None = None,
                  n_rivals: int | None = None) -> list[ChipPlan]:
    """Per-chip value by gameweek, best week, and a one-chip-per-week plan."""
    selling = selling or {}
    budget = bank + sum(selling.get(p.id, p.cost) for p in current)
    half_end = FIRST_HALF_END if upcoming_gw <= FIRST_HALF_END else 38
    last = min(upcoming_gw + SCAN_GWS - 1, half_end)
    scan = list(range(upcoming_gw, last + 1))
    horizon_end = max((g for p in current for g in p.gw_proj), default=last)

    plans: list[ChipPlan] = []
    for chip in CHIPS:
        used = sorted(chips_used.get(chip, []))
        plan = ChipPlan(chip=chip, available=chip_open(upcoming_gw, used),
                        used=used, expires_gw=half_end,
                        rivals_used=(rival_chips or {}).get(chip),
                        rivals_total=n_rivals, start_gw=upcoming_gw)
        if plan.available:
            # An activated chip is locked to the upcoming GW.
            weeks = [upcoming_gw] if upcoming_gw in used else scan

            def value(gw, chip=chip):
                if chip == "freehit":
                    return _value_freehit(current, players, gw, budget)
                if chip == "wildcard":
                    return _value_wildcard(current, players, gw, budget,
                                           must_include, horizon_end)
                if chip == "bboost":
                    return _value_bboost(current, gw)
                return _value_tc(current, gw)
            # Each Free Hit / Wildcard week is an independent solve; CBC runs
            # as a subprocess, so threads overlap them.
            with ThreadPoolExecutor(max_workers=SOLVE_WORKERS) as pool:
                for gw, v in zip(weeks, pool.map(value, weeks)):
                    if v is not None:
                        plan.values[gw] = round(v, 1)
            ranked = sorted(plan.values, key=plan.values.get, reverse=True)
            if ranked:
                plan.best_gw = ranked[0]
                plan.runner_up_gw = ranked[1] if len(ranked) > 1 else None
                top = plan.values[ranked[0]]
                second = plan.values[ranked[1]] if len(ranked) > 1 else 0.0
                plan.clear = top - second >= max(CLEAR_MARGIN, CLEAR_FRACTION * abs(top))
        plans.append(plan)

    # One chip per gameweek: hand out weeks by value, biggest first.
    taken: set[int] = set()
    cands = sorted(((v, p.chip, gw) for p in plans for gw, v in p.values.items()),
                   reverse=True)
    by_chip = {p.chip: p for p in plans}
    for v, chip, gw in cands:
        plan = by_chip[chip]
        if plan.planned_gw is None and gw not in taken:
            plan.planned_gw = gw
            taken.add(gw)
    return plans


def rival_chip_counts(league, exclude_entry: int, upcoming_gw: int) -> tuple[dict[str, int], int]:
    """How many Varsical rivals have already played each chip this half.
    Only rivals whose history was fetched (those with a team) are counted."""
    half = 1 if upcoming_gw <= FIRST_HALF_END else 2
    rivals = [r for r in league.rows if r.entry_id != exclude_entry and r.team]
    counts = {c: sum(1 for r in rivals
                     if any((1 if g <= FIRST_HALF_END else 2) == half
                            for g in r.chips.get(c, [])))
              for c in CHIPS}
    return counts, len(rivals)
