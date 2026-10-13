"""Transfer recommender: work from the team you ACTUALLY own.

Given your current 15 and the model's projections, it produces:
  - ``transfer_options``: ranked one-for-one transfer options judged over a multi-gameweek LOOK-AHEAD (``Player.horizon_proj``),
    each with its reason, budget impact and Varsical ownership context;
  - ``wildcard_draft``: a full 15-man rebuild under the squad rules, priced
    against your selling value + bank, with the changes from your current team;
  - ``flagged_players``: owned players who are injured / doubtful / benched.

The top free option is THE plan (``Digest.moves``), so the dashboard card, the
options table and the WhatsApp message always agree.

Selling prices come from the authenticated my-team payload when available;
otherwise ``now_cost`` is used and the budget is labelled an estimate (real sell
prices can be lower after a price rise — FPL keeps half the profit). Advisory
only: nothing here submits a transfer or plays a chip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Player, keepers_ok, pick_captain

MAX_PER_CLUB = 3
HIT_COST = 4                 # points per transfer beyond the free ones
# Minimum look-ahead gain worth a transfer (a flagged out-player only needs >0).
MIN_GAIN = 1.5
# With no free transfer left, an option is listed only if it nets this after -4.
MIN_NET_FOR_HIT = 2.0
# Affordability: each £1m extra spent must earn this many look-ahead points to
# rank level with a cost-neutral move (money in the bank funds later moves).
SPEND_PENALTY = 0.25
SHOWN_OPTIONS = 5            # visible on the page; the rest behind 'show more'
MAX_OPTIONS = 15             # ranked options returned in total
MAX_PER_SALE = 2             # times one player-to-sell may appear


@dataclass
class Move:
    out: Player
    in_: Player
    gain: float          # projected points gain over the look-ahead
    reason: str
    gain_next: float = 0.0   # gain for the next gameweek alone
    sell: int = 0            # selling price of ``out`` (tenths)

    @property
    def cost_delta(self) -> float:
        """£m change in bank (positive = costs money)."""
        return (self.in_.cost - (self.sell or self.out.cost)) / 10.0


@dataclass
class TransferOption:
    moves: list[Move]
    gain: float              # Σ look-ahead gain, before any hit
    gain_next: float
    hit: int                 # points deducted (0 within free transfers)
    bank_after: int          # tenths
    league_swing: float | None = None   # look-ahead gain vs Varsical rivals
    tags: list[str] = field(default_factory=list)

    @property
    def net(self) -> float:
        return self.gain - self.hit

    @property
    def cost_delta(self) -> float:
        return sum(m.cost_delta for m in self.moves)

    @property
    def rank_score(self) -> float:
        return self.net - SPEND_PENALTY * max(0.0, self.cost_delta)


@dataclass
class WildcardDraft:
    squad: list[Player]
    xi: list[Player]
    bench: list[Player]
    captain: Player
    budget: int                  # tenths available (sell value + bank)
    cost: int                    # tenths spent
    xi_points: float             # Σ look-ahead points of the XI
    current_xi_points: float     # same measure for your current squad
    outs: list[Player]
    ins: list[Player]
    prices_exact: bool

    @property
    def gain(self) -> float:
        return self.xi_points - self.current_xi_points

    @property
    def bank_after(self) -> int:
        return self.budget - self.cost


def flagged_players(current: list[Player]) -> list[Player]:
    """Owned players who are injured / suspended / doubtful / likely benched."""
    out = []
    for p in current:
        if p.status != "a" or (p.chance is not None and p.chance < 75) \
                or p.start_prob < 0.5 or p.news:
            out.append(p)
    return sorted(out, key=lambda p: p.start_prob)


def _club_counts(squad: list[Player]) -> dict[int, int]:
    c: dict[int, int] = {}
    for p in squad:
        c[p.team] = c.get(p.team, 0) + 1
    return c


def _clubs_ok(squad: list[Player], outs: list[Player], ins: list[Player]) -> bool:
    c = _club_counts(squad)
    for o in outs:
        c[o.team] -= 1
    for i in ins:
        c[i.team] = c.get(i.team, 0) + 1
        if c[i.team] > MAX_PER_CLUB:
            return False
    return True


def _gk_ok(squad: list[Player], outs: list[Player], ins: list[Player]) -> bool:
    """A move touching a keeper must leave a legal pair (model.keepers_ok)."""
    if not any(p.pos == 1 for p in outs + ins):
        return True
    gone = {o.id for o in outs}
    return keepers_ok([p for p in squad if p.pos == 1 and p.id not in gone]
                      + [i for i in ins if i.pos == 1])


def keeper_issue(current: list[Player]) -> str:
    """Why the current keeper pair breaks the rule ('' if it doesn't)."""
    gks = sorted((p for p in current if p.pos == 1), key=lambda g: g.gk_rank)
    if keepers_ok(gks):
        return ""
    names = " + ".join(f"{g.name} ({g.team_name} #{g.gk_rank})" for g in gks)
    return (f"Keeper rule broken: {names}. The reserve must be a first-choice "
            f"keeper or the #2 at your starter's club.")


def _avg_fdr(p: Player) -> float | None:
    run = p.fixture_run
    return sum(f for _, _, f in run) / len(run) if run else None


def _reason(out: Player, in_: Player, urgent: bool, ownership) -> str:
    bits = []
    if urgent:
        bits.append("replaces flagged " + (out.news or "rotation/injury risk")[:60])
    fo, fi = _avg_fdr(out), _avg_fdr(in_)
    if fo is not None and fi is not None and fo - fi >= 0.5:
        bits.append(f"easier run (FDR {fi:.1f} vs {fo:.1f})")
    if len(in_.fixture_run) > len(out.fixture_run):
        bits.append(f"{len(in_.fixture_run)} fixtures vs {len(out.fixture_run)}")
    if in_.form - out.form >= 1.5:
        bits.append(f"form {in_.form:.1f} vs {out.form:.1f}")
    if not urgent and in_.start_prob - out.start_prob >= 0.2:
        bits.append("more nailed")
    if ownership is not None:
        oi, oo = ownership.of(in_.id), ownership.of(out.id)
        if oi >= 0.40:
            bits.append(f"covers a Varsical template pick ({oi:.0%})")
        elif oi <= 0.10:
            bits.append(f"differential — {oi:.0%} of Varsical own")
        if oo >= 0.40:
            bits.append(f"careful: {oo:.0%} of rivals keep {out.name}")
    return "; ".join(bits) or "higher projection over the look-ahead"


def _make_option(squad, outs, ins, bank, sell, free_transfers, flagged_ids,
                 ownership) -> TransferOption:
    moves = []
    for o, i in zip(outs, ins):
        moves.append(Move(out=o, in_=i, gain=i.horizon_proj - o.horizon_proj,
                          gain_next=i.projected - o.projected,
                          reason=_reason(o, i, o.id in flagged_ids, ownership),
                          sell=sell(o)))
    spent = sum(i.cost for i in ins) - sum(sell(o) for o in outs)
    swing = None
    if ownership is not None:
        swing = sum(i.horizon_proj * (1 - ownership.of(i.id))
                    - o.horizon_proj * (1 - ownership.of(o.id))
                    for o, i in zip(outs, ins))
    tags = []
    if not keepers_ok([p for p in squad if p.pos == 1]) and _gk_ok(squad, outs, ins) \
            and any(p.pos == 1 for p in outs):
        tags.append("fixes keeper rule")
    if any(o.id in flagged_ids for o in outs):
        tags.append("fixes a flag")
    if ownership is not None:
        if any(ownership.tag(i.id) == "differential" for i in ins):
            tags.append("differential")
        if any(ownership.tag(i.id) == "template" for i in ins):
            tags.append("template cover")
    return TransferOption(
        moves=moves, gain=sum(m.gain for m in moves),
        gain_next=sum(m.gain_next for m in moves),
        hit=HIT_COST * max(0, len(moves) - free_transfers),
        bank_after=bank - spent, league_swing=swing, tags=tags)


def transfer_options(current: list[Player], players: list[Player], bank: int,
                     free_transfers: int = 1, selling: dict[int, int] | None = None,
                     ownership=None, limit: int = MAX_OPTIONS) -> list[TransferOption]:
    """Ranked one-for-one transfer options over the look-ahead.

    Ranking = net look-ahead gain (after the -4 hit when no free transfer is
    left) minus a small penalty per £m spent (``SPEND_PENALTY``), so of two
    similar moves the cheaper ranks first. Each player sold appears at most
    ``MAX_PER_SALE`` times. Returns up to ``limit``; the page shows the top
    ``SHOWN_OPTIONS`` and folds the rest behind "show more". With no free
    transfer left every option costs a hit and is listed only if it still nets
    ``MIN_NET_FOR_HIT`` — taking it is the user's call; ``best_plan`` never
    recommends a hit."""
    selling = selling or {}

    def sell(p: Player) -> int:
        return selling.get(p.id, p.cost)

    squad = list(current)
    owned = {p.id for p in squad}
    flagged_ids = {p.id for p in flagged_players(squad)}
    opts: list[TransferOption] = []
    for o in squad:
        for i in players:
            if i.pos != o.pos or i.id in owned or i.horizon_proj <= 0:
                continue
            if i.cost > sell(o) + bank or not _clubs_ok(squad, [o], [i]) \
                    or not _gk_ok(squad, [o], [i]):
                continue
            g = i.horizon_proj - o.horizon_proj
            if g < MIN_GAIN and not (o.id in flagged_ids and g > 0):
                continue
            opt = _make_option(squad, [o], [i], bank, sell, free_transfers,
                               flagged_ids, ownership)
            if opt.hit and opt.net < MIN_NET_FOR_HIT:
                continue
            opts.append(opt)

    opts.sort(key=lambda o: (o.rank_score, o.bank_after), reverse=True)
    out: list[TransferOption] = []
    sales: dict[int, int] = {}
    for o in opts:
        k = o.moves[0].out.id
        if sales.get(k, 0) >= MAX_PER_SALE:
            continue
        sales[k] = sales.get(k, 0) + 1
        out.append(o)
        if len(out) >= limit:
            break
    fix = _keeper_fix(squad, players, bank, sell, free_transfers, flagged_ids,
                      ownership)
    if fix and not any("fixes keeper rule" in o.tags for o in out):
        out = [fix] + out[:limit - 1]
    return out


def _keeper_fix(squad, players, bank, sell, free_transfers, flagged_ids,
                ownership) -> TransferOption | None:
    """When the current keeper pair breaks the rule, the best single GK swap
    that fixes it — offered whatever its points cost (a backup scores ~0)."""
    gks = [p for p in squad if p.pos == 1]
    if keepers_ok(gks):
        return None
    owned = {p.id for p in squad}
    best = None
    for o in gks:
        for i in players:
            if i.pos != 1 or i.id in owned or i.cost > sell(o) + bank:
                continue
            if not _clubs_ok(squad, [o], [i]) or not _gk_ok(squad, [o], [i]):
                continue
            opt = _make_option(squad, [o], [i], bank, sell, free_transfers,
                               flagged_ids, ownership)
            if best is None or (opt.rank_score, opt.bank_after) > \
                    (best.rank_score, best.bank_after):
                best = opt
    if best:
        best.moves[0].reason = "fixes the keeper rule; " + best.moves[0].reason
    return best


def best_plan(options: list[TransferOption]) -> list[Move]:
    """THE recommendation: the top option that needs no points hit."""
    free = next((o for o in options if o.hit == 0), None)
    return list(free.moves) if free else []


def suggest_transfers(current: list[Player], players: list[Player],
                      bank: int, free_transfers: int = 1,
                      selling: dict[int, int] | None = None,
                      ownership=None) -> list[Move]:
    """Back-compat wrapper: the best hit-free plan from ``transfer_options``."""
    return best_plan(transfer_options(current, players, bank, free_transfers,
                                      selling, ownership))


def _xi_points(squad: list[Player], formation: tuple[int, int, int]) -> float:
    """Σ look-ahead points of the best XI in a pinned formation (no captain)."""
    want = {1: 1, 2: formation[0], 3: formation[1], 4: formation[2]}
    tot = 0.0
    for pos, n in want.items():
        tot += sum(sorted((p.horizon_proj for p in squad if p.pos == pos),
                          reverse=True)[:n])
    return round(tot, 1)


def wildcard_draft(current: list[Player], players: list[Player], bank: int,
                   selling: dict[int, int] | None = None,
                   must_include: set[int] | None = None,
                   formation: tuple[int, int, int] | None = None) -> WildcardDraft | None:
    """Best 15 from scratch for the look-ahead, within selling value + bank.

    Reuses the squad ILP (quotas, £-cap, max 3 per club, bench cap, pinned
    formation) with ``horizon_proj`` as the objective. None if no solver."""
    from .optimizer import DEFAULT_FORMATION, _order_bench, optimize_ilp
    formation = formation or DEFAULT_FORMATION
    selling = selling or {}
    budget = bank + sum(selling.get(p.id, p.cost) for p in current)
    pts = {p.id: p.horizon_proj for p in players}
    res = optimize_ilp(players, formation, must_include, points=pts, budget=budget)
    if res is None:
        return None
    squad, xi = res
    cur_ids, new_ids = {p.id for p in current}, {p.id for p in squad}
    order = {1: 0, 2: 1, 3: 2, 4: 3}
    xi_sorted = sorted(xi, key=lambda p: (order[p.pos], -p.horizon_proj))
    captain = pick_captain(xi)[0]
    return WildcardDraft(
        squad=squad, xi=xi_sorted, bench=_order_bench(squad, xi), captain=captain,
        budget=budget, cost=sum(p.cost for p in squad),
        xi_points=_xi_points(squad, formation),
        current_xi_points=_xi_points(current, formation),
        outs=sorted((p for p in current if p.id not in new_ids),
                    key=lambda p: (p.pos, -p.horizon_proj)),
        ins=sorted((p for p in squad if p.id not in cur_ids),
                   key=lambda p: (p.pos, -p.horizon_proj)),
        prices_exact=bool(selling) and all(p.id in selling for p in current))
