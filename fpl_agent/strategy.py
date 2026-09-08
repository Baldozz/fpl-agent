"""Formation (module) and chip-timing advice.

Two advisory helpers that complement the transfer recommender:

* :func:`best_formation` — given the squad's projected points, find the
  highest-scoring **legal** starting XI shape (1 GK; 3-5 DEF; 2-5 MID; 1-3 FWD)
  and compare it to the current shape. Useful when cheap "bench" defenders
  out-project weak forwards in a given week.
* :func:`chip_advice` — a rule-of-thumb on whether to play a chip this week,
  driven by upcoming double/blank gameweeks.

Both are pure functions (no I/O) so they are trivially testable and reusable
from the CLI, the site renderer, or ad-hoc.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Legal starting-XI bounds per position (excluding the single GK).
_BOUNDS = {"DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}


@dataclass
class SquadPlayer:
    name: str
    position: str          # "GK" | "DEF" | "MID" | "FWD"
    projected: float
    team: str = ""
    key: str = ""          # stable id to map back (names can collide)


@dataclass
class FormationRec:
    formation: str                       # e.g. "5-3-2"
    points: float                        # XI projected points
    xi: list[SquadPlayer]
    bench: list[SquadPlayer]
    current_formation: str | None
    current_points: float | None
    # (formation, points, XI players) for each legal shape, best first
    alternatives: list[tuple[str, float, list["SquadPlayer"]]]
    note: str = ""
    current_xi: list["SquadPlayer"] = field(default_factory=list)

    @property
    def gain(self) -> float | None:
        if self.current_points is None:
            return None
        return round(self.points - self.current_points, 1)


def _xi_for(players: list[SquadPlayer], d: int, m: int, f: int):
    """Pick the top-projected legal XI for a given (DEF, MID, FWD) split."""
    byp = {k: sorted((p for p in players if p.position == k),
                     key=lambda p: -p.projected)
           for k in ("GK", "DEF", "MID", "FWD")}
    if not byp["GK"] or len(byp["DEF"]) < d or len(byp["MID"]) < m or len(byp["FWD"]) < f:
        return None
    xi = byp["GK"][:1] + byp["DEF"][:d] + byp["MID"][:m] + byp["FWD"][:f]
    return xi


def _formation_str(players: list[SquadPlayer]) -> str:
    c = {"DEF": 0, "MID": 0, "FWD": 0}
    for p in players:
        if p.position in c:
            c[p.position] += 1
    return f"{c['DEF']}-{c['MID']}-{c['FWD']}"


def best_formation(squad: list[SquadPlayer],
                   current_xi: list[SquadPlayer] | None = None) -> FormationRec:
    """Rank every legal formation by summed projected points; recommend the best."""
    results: list[tuple[float, str, list[SquadPlayer]]] = []
    for d in range(_BOUNDS["DEF"][0], _BOUNDS["DEF"][1] + 1):
        for m in range(_BOUNDS["MID"][0], _BOUNDS["MID"][1] + 1):
            f = 10 - d - m
            if not (_BOUNDS["FWD"][0] <= f <= _BOUNDS["FWD"][1]):
                continue
            xi = _xi_for(squad, d, m, f)
            if xi is None:
                continue
            pts = round(sum(p.projected for p in xi), 1)
            results.append((pts, f"{d}-{m}-{f}", xi))
    if not results:
        return FormationRec("—", 0.0, [], [], None, None, [], "No legal XI found.")
    results.sort(key=lambda r: -r[0])
    top_pts, top_form, top_xi = results[0]
    xi_ids = {id(p) for p in top_xi}
    bench = sorted((p for p in squad if id(p) not in xi_ids),
                   key=lambda p: (_ORDER[p.position], -p.projected))

    cur_form = cur_pts = None
    if current_xi:
        cur_form = _formation_str(current_xi)
        cur_pts = round(sum(p.projected for p in current_xi), 1)

    note = ""
    if cur_form and cur_form != top_form:
        note = (f"Switch from {cur_form} to {top_form} for +"
                f"{round(top_pts - cur_pts, 1)} projected points this week.")
    elif cur_form:
        note = f"Your current {cur_form} is already the highest-projected shape."
    return FormationRec(top_form, top_pts, top_xi, bench, cur_form, cur_pts,
                        [(f, p, xi) for p, f, xi in results[:5]], note,
                        current_xi=list(current_xi) if current_xi else [])


@dataclass
class ChipRec:
    play: bool
    chip: str | None            # "wildcard" | "freehit" | "3xc" | "bboost" | None
    reason: str
    horizon_note: str = ""


_CHIP_LABEL = {"wildcard": "Wildcard", "freehit": "Free Hit",
               "3xc": "Triple Captain", "bboost": "Bench Boost"}


def chip_advice(*, upcoming_gw: int,
                dgw_gws: list[int] | None = None,
                bgw_gws: list[int] | None = None,
                chips_available: set[str] | None = None) -> ChipRec:
    """Rule-of-thumb chip timing.

    Chips are best saved for structural weeks: Triple Captain / Bench Boost for
    a double gameweek, Free Hit for a blank. Absent either, hold.
    """
    dgw_gws = sorted(dgw_gws or [])
    bgw_gws = sorted(bgw_gws or [])
    avail = chips_available if chips_available is not None else {
        "wildcard", "freehit", "3xc", "bboost"}

    # A blank/double landing on THIS gameweek is the actionable case.
    if upcoming_gw in bgw_gws and "freehit" in avail:
        return ChipRec(True, "freehit",
                       f"GW{upcoming_gw} is a blank — Free Hit fields a full XI for one week.")
    if upcoming_gw in dgw_gws:
        if "bboost" in avail:
            return ChipRec(True, "bboost",
                           f"GW{upcoming_gw} is a double — Bench Boost scores all 15.")
        if "3xc" in avail:
            return ChipRec(True, "3xc",
                           f"GW{upcoming_gw} is a double — Triple Captain a nailed double-fixture premium.")

    nxt_dgw = next((g for g in dgw_gws if g > upcoming_gw), None)
    nxt_bgw = next((g for g in bgw_gws if g > upcoming_gw), None)
    if nxt_dgw or nxt_bgw:
        bits = []
        if nxt_dgw:
            bits.append(f"a double is coming in GW{nxt_dgw} (save TC / Bench Boost)")
        if nxt_bgw:
            bits.append(f"a blank is coming in GW{nxt_bgw} (save Free Hit)")
        return ChipRec(False, None, "Hold your chips — " + "; ".join(bits) + ".")
    return ChipRec(False, None,
                   "Hold all chips — no double or blank gameweeks on the horizon. "
                   "Save TC/Bench Boost for a double and Free Hit for a blank.")


def label_chip(name: str | None) -> str:
    return _CHIP_LABEL.get(name or "", "—")


def detect_dgw_bgw(fixtures: list[dict], upcoming_gw: int,
                   horizon: int = 8) -> tuple[list[int], list[int]]:
    """Scan fixtures for double (a team plays 2+) and blank (a team plays 0)
    gameweeks in the [upcoming_gw, upcoming_gw+horizon) window.

    Returns (dgw_gws, bgw_gws). A blank is inferred when an event has fixtures
    but fewer than the usual 10 (i.e. some clubs are missing).
    """
    events = range(upcoming_gw, upcoming_gw + horizon)
    per_event: dict[int, list[dict]] = {}
    for f in fixtures:
        ev = f.get("event")
        if ev in events:
            per_event.setdefault(ev, []).append(f)
    dgw, bgw = [], []
    for ev, fs in per_event.items():
        counts: dict[int, int] = {}
        for f in fs:
            counts[f["team_h"]] = counts.get(f["team_h"], 0) + 1
            counts[f["team_a"]] = counts.get(f["team_a"], 0) + 1
        if any(c >= 2 for c in counts.values()):
            dgw.append(ev)
        # A normal round is 10 fixtures / 20 clubs; fewer clubs playing = blank.
        if fs and len(counts) < 20:
            bgw.append(ev)
    return sorted(dgw), sorted(bgw)
