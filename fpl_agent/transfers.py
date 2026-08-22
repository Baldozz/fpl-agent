"""Transfer recommender: work from the team you ACTUALLY own.

Given your current 15 and the model's projections for the upcoming gameweek,
suggest the best single (or double) transfers within budget and the max-3-per-club
rule, and surface two things the agent should act on between gameweeks:
  - owned players who are now injured / doubtful / benched (priority sells)
  - stand-out targets you don't own (opportunities: form + easy fixtures)

Uses ``now_cost`` as the selling-price approximation (public data). Exact selling
price and free-transfer count come from the authenticated my-team endpoint / the
FPL site — treat these as strong suggestions you confirm before committing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Player

MAX_PER_CLUB = 3


@dataclass
class Move:
    out: Player
    in_: Player
    gain: float          # projected points gain (next GW)
    reason: str

    @property
    def cost_delta(self) -> float:
        return (self.in_.cost - self.out.cost) / 10.0


def flagged_players(current: list[Player]) -> list[Player]:
    """Owned players who are injured / suspended / doubtful / likely benched."""
    out = []
    for p in current:
        if p.status != "a" or (p.chance is not None and p.chance < 75) \
                or p.start_prob < 0.5 or p.news:
            out.append(p)
    return sorted(out, key=lambda p: p.start_prob)


def opportunities(current_ids: set[int], players: list[Player],
                  top_n: int = 6) -> list[Player]:
    """Best-projected players you don't own (upcoming-GW targets)."""
    pool = [p for p in players if p.id not in current_ids and p.projected > 0]
    return sorted(pool, key=lambda p: p.projected, reverse=True)[:top_n]


def _club_counts(squad: list[Player]) -> dict[int, int]:
    c: dict[int, int] = {}
    for p in squad:
        c[p.team] = c.get(p.team, 0) + 1
    return c


def _best_replacement(out: Player, squad: list[Player], players: list[Player],
                      bank: int) -> Player | None:
    owned = {p.id for p in squad}
    clubs = _club_counts(squad)
    budget = out.cost + bank                      # tenths
    best, best_gain = None, 0.01
    for cand in players:
        if cand.id in owned or cand.pos != out.pos or cand.projected <= 0:
            continue
        if cand.cost > budget:
            continue
        # club limit after the swap (removing `out`, adding `cand`)
        cnt = clubs.get(cand.team, 0) - (1 if cand.team == out.team else 0)
        if cnt >= MAX_PER_CLUB:
            continue
        gain = cand.projected - out.projected
        if gain > best_gain:
            best, best_gain = cand, gain
    return best


def suggest_transfers(current: list[Player], players: list[Player],
                      bank: int, free_transfers: int = 1,
                      max_moves: int = 2) -> list[Move]:
    """Greedy best transfers. Prioritises replacing flagged players, then the
    biggest projection upgrade. ``bank`` and costs are in tenths of £m."""
    squad = list(current)
    moves: list[Move] = []
    budget = bank
    # Suggest up to your free transfers (capped at max_moves) — avoids proposing
    # points-hit moves you didn't ask for.
    limit = min(max(free_transfers, 1), max_moves)
    flagged_ids = {p.id for p in flagged_players(current)}

    for _ in range(limit):
        best_move = None
        # Consider replacing flagged players first, then everyone.
        candidates_out = sorted(
            squad, key=lambda p: (p.id not in flagged_ids, p.projected))
        for out in candidates_out:
            repl = _best_replacement(out, squad, players, budget)
            if not repl:
                continue
            gain = repl.projected - out.projected
            urgent = out.id in flagged_ids
            # require a meaningful gain unless the out-player is flagged
            if not urgent and gain < 0.8:
                continue
            if best_move is None or gain > best_move.gain:
                why = ("replaces flagged " + (out.news or "rotation/injury risk")
                       if urgent else f"+{gain:.1f} pts upgrade")
                best_move = Move(out=out, in_=repl, gain=gain, reason=why[:120])
        if not best_move:
            break
        # apply
        squad = [p for p in squad if p.id != best_move.out.id] + [best_move.in_]
        budget += best_move.out.cost - best_move.in_.cost
        flagged_ids.discard(best_move.out.id)
        moves.append(best_move)
    return moves
