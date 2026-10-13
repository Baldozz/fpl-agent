"""Expected-points model.

Turns raw FPL data into a single ``projected`` score per player for the upcoming
gameweek(s). Designed to degrade gracefully at the season opener (when live
``form`` is 0 for everyone) by leaning on last-season output and FPL's own
``ep_next`` projection, then to lean more on live form as the season develops.
"""
from __future__ import annotations

import math

import re
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
    starts: int
    chance: int | None   # chance_of_playing_next_round (0..100 or None)
    xgi90: float = 0.0   # expected goal involvements per 90 (last season)
    xg90: float = 0.0    # expected goals per 90 (this season)
    xa90: float = 0.0    # expected assists per 90
    xgc90: float = 0.0   # team expected goals conceded per 90 while on pitch
    dc90: float = 0.0    # defensive contributions (CBIT/CBIRT) per 90
    saves90: float = 0.0
    bonus: int = 0
    pen_order: int | None = None  # 1 = first-choice penalty taker
    fdr: float = 3.0     # avg fixture difficulty over horizon (lower = easier)
    n_fix: int = 1       # number of fixtures in horizon (DGW awareness)
    team_strength: float = 3.0   # own-team quality 1..5 (CS / attack prior)
    opp_strength: float = 3.0    # avg opponent quality 1..5 over the horizon
    start_prob: float = 1.0      # P(starts) after news/overrides/minutes model
    override_reason: str = ""    # why start_prob was set by human/Grok
    projected: float = 0.0
    reasons: list[str] = field(default_factory=list)
    # Per-match points BEFORE fixture difficulty / availability (set by
    # score_players) — lets attach_lookahead re-price each upcoming fixture.
    base_pm: float = 0.0
    play: float = 1.0            # min(availability, start_prob) for next GW
    # Multi-GW look-ahead (attach_lookahead): Σ projected points over the next
    # N gameweeks, and the fixture run as [(gw, "ARS (H)", fdr), ...].
    gk_rank: int = 0             # keepers: 1 = club's first choice, 2 = backup
    horizon_proj: float = 0.0
    fixture_run: list[tuple[int, str, int]] = field(default_factory=list)
    # Projected points per gameweek (0 in a blank, two fixtures in a double),
    # possibly further out than the look-ahead — feeds chip timing.
    gw_proj: dict[int, float] = field(default_factory=dict)

    @property
    def cost_m(self) -> float:
        return self.cost / 10.0

    @property
    def pos_name(self) -> str:
        return POS_NAME[self.pos]

    @property
    def value(self) -> float:
        """Projected points per £1.0m — the points-per-million efficiency."""
        return round(self.projected / self.cost_m, 2) if self.cost else 0.0


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


def team_strength_map(bootstrap: dict) -> dict[int, float]:
    """Per-team quality on a ~1..5 scale from strength_overall_home/away.

    The detailed attack/defence strengths are 0 until the season is under way,
    but the overall home/away figures are populated from the off, so we average
    them as a proxy for how good the player's own team is (clean-sheet and
    attacking potential), independent of the specific opponent (that's FDR).
    """
    out: dict[int, float] = {}
    for t in bootstrap["teams"]:
        h = t.get("strength_overall_home") or 0
        a = t.get("strength_overall_away") or 0
        vals = [v for v in (h, a) if v]
        out[t["id"]] = sum(vals) / len(vals) if vals else 3.0
    return out


def build_players(bootstrap: dict) -> dict[int, Player]:
    team_name = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    strength = team_strength_map(bootstrap)
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
            starts=int(e.get("starts") or 0),
            xgi90=float(e.get("expected_goal_involvements_per_90") or 0),
            xg90=float(e.get("expected_goals_per_90") or 0),
            xa90=float(e.get("expected_assists_per_90") or 0),
            xgc90=float(e.get("expected_goals_conceded_per_90") or 0),
            dc90=float(e.get("defensive_contribution_per_90") or 0),
            saves90=float(e.get("saves_per_90") or 0),
            bonus=int(e.get("bonus") or 0),
            pen_order=e.get("penalties_order"),
            chance=e.get("chance_of_playing_next_round"),
            team_strength=strength.get(e["team"], 3.0),
        )
    return players


def _match_players(players: dict[int, Player], name: str) -> list[Player]:
    """Map a signal name (e.g. Grok's 'Bukayo Saka') to FPL players.

    FPL uses short web_names ('Saka', 'B.Fernandes'). We try, in order: exact
    web_name, surname (last token) equals web_name, and web_name contained in
    the signal name (or vice versa). Ambiguous multi-hits are all updated.
    """
    n = name.lower().strip()
    if not n:
        return []
    exact = [p for p in players.values() if p.name.lower() == n]
    if exact:
        return exact
    surname = n.split()[-1]
    by_surname = [p for p in players.values() if p.name.lower() == surname]
    if by_surname:
        return by_surname
    # web_name is a fragment of the full name, e.g. 'B.Fernandes' vs
    # 'Bruno Fernandes': compare on alphabetic surname tokens.
    def surtok(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower().split()[-1] if s else "")
    st = surtok(name)
    frag = [p for p in players.values()
            if st and (surtok(p.name) == st or st in p.name.lower())]
    return frag


def apply_start_signals(players: dict[int, Player],
                        signals: dict[str, dict]) -> None:
    """Apply human/Grok start-probability overrides with flexible name matching."""
    for name, sig in signals.items():
        sp = sig.get("start_prob")
        if sp is None:
            continue
        for p in _match_players(players, name):
            p.start_prob = max(0.0, min(1.0, float(sp)))
            p.override_reason = sig.get("reason", "")


def attach_fixtures(players: dict[int, Player], fixtures: list[dict],
                    start_event: int, horizon: int,
                    strength: dict[int, float] | None = None) -> None:
    """Attach average FDR and opponent strength over the next ``horizon`` GWs.

    ``strength`` maps team id -> overall quality (~1..5), used to record each
    player's average OPPONENT strength so the model can favour the stronger side
    of a fixture (top team vs bottom team).
    """
    strength = strength or {}
    events = set(range(start_event, start_event + horizon))
    by_team: dict[int, list[float]] = {}
    opp_by_team: dict[int, list[float]] = {}
    for fx in fixtures:
        ev = fx.get("event")
        if ev is None or ev not in events:
            continue
        h, a = fx["team_h"], fx["team_a"]
        by_team.setdefault(h, []).append(fx["team_h_difficulty"])
        by_team.setdefault(a, []).append(fx["team_a_difficulty"])
        # opponent strength: home team's opponent is the away team, and vice versa
        opp_by_team.setdefault(h, []).append(strength.get(a, 3.0))
        opp_by_team.setdefault(a, []).append(strength.get(h, 3.0))
    for p in players.values():
        diffs = by_team.get(p.team, [])
        p.n_fix = len(diffs)
        p.fdr = sum(diffs) / len(diffs) if diffs else 5.0  # no fixture -> penalise
        opps = opp_by_team.get(p.team, [])
        p.opp_strength = sum(opps) / len(opps) if opps else 3.0


FIT_PRIOR = 0.80  # early-season default P(starts) for an available player


def _minutes_security(minutes: int, starts: int, games_played: int,
                      status: str, chance: int | None) -> float:
    """Estimate P(starts) — the 'is he nailed?' prior.

    The FPL ``minutes``/``starts`` fields hold the CURRENT season only, which is
    ~0 in the opening weeks, so a minutes model is unreliable until a few games
    are played. Early season we therefore assume an available player is a likely
    starter (``FIT_PRIOR``) and let injury news / Grok / overrides demote him;
    once ~4+ games are in, we switch to actual minutes/starts per game.
    """
    if games_played < 4:
        if status == "a" and (chance is None or chance >= 75):
            return FIT_PRIOR
        return 0.5  # flagged/doubtful; availability multiplier handles the rest
    exp_min = games_played * 90
    m = min(minutes, exp_min) / exp_min
    s = min(starts, games_played) / games_played
    return round(0.30 + 0.70 * (0.6 * m + 0.4 * s), 3)


def _team_multiplier(pos: int, strength: float) -> float:
    """Own-team quality prior. Weighted more for GK/DEF (clean sheets)."""
    w = 0.10 if pos in (1, 2) else 0.07
    return 1.0 + (strength - 3.0) * w


# How hard to favour the stronger side of a fixture (top team vs bottom team).
# Applied to the strength GAP between a player's team and its opponent. Raise for
# a more decisive top-team lean, lower/zero to rely on FDR alone.
MISMATCH_WEIGHT = 0.10


def _mismatch_multiplier(team_strength: float, opp_strength: float) -> float:
    """Boost players from the stronger team in a lopsided fixture, penalise the
    weaker team's players. Gap is clamped to [-3, 3] on the 1..5 strength scale."""
    gap = max(-3.0, min(3.0, team_strength - opp_strength))
    return 1.0 + gap * MISMATCH_WEIGHT


# FPL scoring by position (1 GK, 2 DEF, 3 MID, 4 FWD).
_GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
_CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
_DC_THRESHOLD = {2: 10, 3: 12, 4: 12}   # defensive contributions for +2
# Weight of the xG-based estimate vs points-based form once the season is going.
UNDERLYING_WEIGHT = 0.55
_MIN_MINUTES_FOR_XG = 180

# The armband DOUBLES a score, so it should chase the fat right tail, not the
# mean: 2x a defender's likely 6 is worth less than the chance of 2x a striker's
# 15. This weights a player's attacking upside (the only route to a haul) on top
# of his projection when ranking captain candidates. Raise for a more
# haul-hunting armband, 0 to caption purely on expected points.
CAPTAIN_CEILING_WEIGHT = 0.8


def attacking_upside(p: Player) -> float:
    """Expected points per 90 from GOALS AND ASSISTS only — the haul-producing
    part of a score. Clean sheets, defensive contributions and appearance points
    are excluded: they cap out low and rarely double into a big captain week."""
    per90 = p.xg90 * _GOAL_PTS[p.pos] + p.xa90 * 3
    if p.pen_order == 1 and p.pos != 1:
        per90 += 0.08 * _GOAL_PTS[p.pos]
    return per90 * min(p.start_prob, 1.0)


# The armband and the vice go to a MIDFIELDER or FORWARD — the user's standing
# rule. A defender's good week is a clean sheet plus bonus; doubling that wastes
# the multiplier, and the vice only ever plays when the captain blanks, which is
# exactly when you want someone who can still haul.
CAPTAIN_POSITIONS = (3, 4)          # 3 = MID, 4 = FWD
_INELIGIBLE = -1000.0               # ranks below every eligible player


def captain_score(p: Player) -> float:
    """Ranking key for the armband: projection + weighted attacking ceiling.

    GKs and defenders are pushed below every midfielder/forward rather than
    excluded outright, so a squad with no fit attacker still yields a captain
    (and the two of them still rank sensibly against each other).

    Even among eligible players the ceiling term matters: without it a cheap
    defender with a cushy fixture outranked a premium striker with a hard one
    (Gabriel 7.9 vs Haaland 7.4 in GW6) — true on average, wrong for a 2x.
    """
    score = p.projected + CAPTAIN_CEILING_WEIGHT * attacking_upside(p)
    if p.pos not in CAPTAIN_POSITIONS:
        return _INELIGIBLE + score
    return score


def _captain_if_plays(p: Player) -> float:
    """``captain_score`` assuming he starts: projection and upside with the
    start/availability discount taken back out."""
    play = p.play if p.play > 0 else 0.0
    if play == 0:
        return 0.0
    upside = attacking_upside(p) / p.start_prob if p.start_prob > 0 else 0.0
    return p.projected / play + CAPTAIN_CEILING_WEIGHT * upside


# A fit player who doesn't start often still comes off the bench: the armband
# then doubles a cameo score and the vice never activates. Share of a fit
# player's non-starts assumed to be cameos, and what a cameo scores.
CAMEO_SHARE = 0.5
CAMEO_POINTS = 1.5
# Injury rule (user's): rest/rotation doubts use the expected-value logic
# below, but an injury-flagged player whose chance of playing is at or under
# this % never takes the armband — it passes to the next-best player rather
# than gambling on the vice. FPL publishes 75 / 50 / 25 / 0.
INJURY_ARMBAND_MAX_CHANCE = 50


def _injury_flagged(p: Player) -> bool:
    return p.status != "a" or (p.chance is not None and p.chance < 100)


def _serious_injury(p: Player) -> bool:
    """Injury/suspension flag with a low chance of playing (not a rest doubt)."""
    if not _injury_flagged(p):
        return False
    chance = p.chance if p.chance is not None else round(p.play * 100)
    return chance <= INJURY_ARMBAND_MAX_CHANCE


def _no_play(p: Player) -> float:
    """P(0 minutes) — when the armband passes to the vice. Injured / flagged
    players are simply absent; a fit-but-rotated one may still get a cameo."""
    miss = 1.0 - p.play
    return miss if _injury_flagged(p) else miss * (1 - CAMEO_SHARE)


def pick_captain(pool: list[Player]) -> tuple[Player | None, Player | None]:
    """Captain + vice that maximise the expected armband score.

    The armband passes to the vice only when the captain plays 0 minutes, so a
    doubt over a captain costs far less than his whole score. Value of (C, V) =
        P(start C)·score(C) + P(cameo C)·CAMEO_POINTS + P(0 min C)·P(V)·score(V)
    with score = projection + attacking ceiling if he starts. For nailed
    players this is exactly the ``captain_score`` ranking; it stops a small
    rotation doubt (e.g. Grok's "possible rest" 0.9) flipping the armband to a
    weaker player the vice already covers, while a real doubt still moves it.
    A seriously injured player (``INJURY_ARMBAND_MAX_CHANCE``) is never
    captain — the armband goes to the next best; he can still be vice.
    MID/FWD only (``CAPTAIN_POSITIONS``); others only if fewer than two
    attackers are available.
    """
    # Someone certain not to play "scores" exactly his vice's points — a tie
    # with captaining the vice directly, never better. Leave him out.
    playing = [p for p in pool if p.play > 0] or list(pool)
    elig = [p for p in playing if p.pos in CAPTAIN_POSITIONS]
    if len(elig) < 2:
        rest = sorted((p for p in playing if p not in elig), key=captain_score,
                      reverse=True)
        elig += rest[:2 - len(elig)]
    if not elig:
        return None, None
    if len(elig) == 1:
        return elig[0], None
    full = {p.id: _captain_if_plays(p) for p in elig}
    best, best_key = (elig[0], elig[1]), (float("-inf"), float("-inf"))
    captains = [p for p in elig if not _serious_injury(p)] or elig
    for c in captains:
        for v in elig:
            if v is c:
                continue
            absent = _no_play(c)
            cameo = 1.0 - c.play - absent
            val = (c.play * full[c.id] + cameo * CAMEO_POINTS
                   + absent * v.play * full[v.id])
            # Ties: prefer the captain's own expected score, then the vice's —
            # with a nailed captain every vice values the same, but the best
            # one should still cover.
            key = (round(val, 6), c.play * full[c.id], v.play * full[v.id])
            if key > best_key:
                best, best_key = (c, v), key
    return best


def _underlying_points(p: Player) -> float:
    """Expected points per full match from underlying stats (xG, xA, xGC,
    defensive contributions, saves, bonus rate, penalty duty) — what the player's
    output *should* be worth, stripping out finishing luck and fortunate hauls."""
    pts = 2.0                                             # 60+ min appearance
    pts += p.xg90 * _GOAL_PTS[p.pos] + p.xa90 * 3
    if p.pen_order == 1 and p.pos != 1:
        pts += 0.08 * _GOAL_PTS[p.pos]                    # ~pen/game not yet in xG
    if p.pos in (1, 2, 3):
        pts += math.exp(-p.xgc90) * _CS_PTS[p.pos]        # P(clean sheet) ~ Poisson
    if p.pos in (1, 2):
        pts -= p.xgc90 / 2                                # -1 per 2 conceded
    if p.pos == 1:
        pts += p.saves90 / 3                              # +1 per 3 saves
    thr = _DC_THRESHOLD.get(p.pos)
    if thr:
        pts += 2 * max(0.0, min(1.0, (p.dc90 - 0.5 * thr) / thr))
    games = max(p.starts, 1)
    pts += 0.6 * p.bonus / games                          # regressed bonus rate
    return pts


def score_players(players: dict[int, Player], season_started: bool,
                  games_played: int = 0) -> None:
    """Compute ``projected`` points for each player for the horizon.

    Combines: scoring baseline (ep_next / ppg / form) x fixture difficulty x
    own-team strength x probability of actually playing. That last term is the
    heart of it — the game is about picking players who START.
    """
    for p in players.values():
        # Early season the current-season form/ppg are 1-game noise (and 0 for
        # teams yet to play), so lean on FPL's forward-looking ep_next; once ~4+
        # games are in, blend in live form and points-per-game.
        if games_played >= 4:
            base = 0.45 * p.ep_next + 0.30 * p.form + 0.25 * p.ppg
        else:
            base = 0.80 * p.ep_next + 0.20 * p.ppg
        # Regress towards underlying (xG-based) output once there's a sample, so
        # a lucky haul (e.g. a defender's goal from 0.2 xG) isn't projected on.
        if p.minutes >= _MIN_MINUTES_FOR_XG and games_played >= 2:
            under = _underlying_points(p)
            base = UNDERLYING_WEIGHT * under + (1 - UNDERLYING_WEIGHT) * base

        fdr_mult = _fdr_multiplier(p.fdr)
        team_mult = _team_multiplier(p.pos, p.team_strength)
        mismatch_mult = _mismatch_multiplier(p.team_strength, p.opp_strength)
        avail = _availability_mult(p.status, p.chance)

        # Start probability: use a human/Grok override when present, otherwise
        # derive it from last-season minutes/starts.
        if not p.override_reason:
            p.start_prob = _minutes_security(p.minutes, p.starts, games_played,
                                             p.status, p.chance)
        # Playing chance is capped by BOTH availability (injury/suspension from
        # the API) and start probability (rotation). An injured player (avail 0)
        # or a flagged-out player (start_prob 0) scores nothing.
        play = min(avail, p.start_prob)
        p.base_pm = base * team_mult
        p.play = play

        per_match = base * fdr_mult * team_mult * mismatch_mult * play
        p.projected = round(per_match * max(p.n_fix, 1), 2)

        p.reasons = []
        if avail == 0:
            p.reasons.append("unavailable")
        elif avail < 1:
            p.reasons.append(f"injury doubt {int(avail*100)}%")
        if p.override_reason:
            if p.start_prob == 0:
                p.reasons.append("ruled out")
            elif p.start_prob < 0.7:
                p.reasons.append(f"rotation risk {int(p.start_prob*100)}%")
            else:
                p.reasons.append("nailed")
        elif p.start_prob < 0.6:
            p.reasons.append(f"minutes risk {int(p.start_prob*100)}%")
        if p.fdr <= 2.3:
            p.reasons.append("easy fixture")
        elif p.fdr >= 4.0:
            p.reasons.append("hard fixture")
        gap = p.team_strength - p.opp_strength
        if gap >= 1.5:
            p.reasons.append("favourable matchup")
        elif gap <= -1.5:
            p.reasons.append("tough matchup")
        if p.team_strength >= 4.2:
            p.reasons.append("strong team")
        if p.n_fix >= 2:
            p.reasons.append(f"{p.n_fix} fixtures")


# How many gameweeks transfer and wildcard advice look ahead. One GW is noise
# (a single tough fixture sinks a premium); much beyond ~5 the projections are
# guesses about form and fitness that will have changed.
LOOKAHEAD = 5


def _lookahead_play(p: Player, k: int) -> float:
    """P(plays) for the k-th upcoming GW (k=0 is next). Next GW uses the live
    signal; later GWs let short-term doubts recover — a 'd' 50% flag usually
    clears within a week, while an injury/suspension is only half-credited from
    two GWs out (return dates are unknown). Left-the-club stays at zero."""
    if k == 0 or p.status in ("u", "n"):
        return p.play
    if p.status == "d" or (p.status == "a" and p.play < p.start_prob):
        return p.start_prob
    if p.status in ("i", "s"):
        return 0.5 * p.start_prob if k >= 2 else p.play
    return p.play


def attach_lookahead(players: dict[int, Player], fixtures: list[dict],
                     start_event: int, horizon: int,
                     strength: dict[int, float],
                     team_short: dict[int, str],
                     per_gw_horizon: int = 0) -> list[int]:
    """Set ``horizon_proj`` = Σ projected points over GWs start..start+horizon-1,
    pricing EACH fixture by its own difficulty and opponent (a blank GW adds 0,
    a double adds two). Also fills ``gw_proj`` per gameweek over
    max(horizon, per_gw_horizon) GWs for chip timing. Must run after
    ``score_players`` (needs ``base_pm``). Returns the look-ahead GWs (clamped
    to the season)."""
    span = max(horizon, per_gw_horizon)
    all_gws = [g for g in range(start_event, start_event + span) if g <= 38]
    gws = all_gws[:horizon]
    by_team: dict[int, list[tuple[int, int, int, bool]]] = {}
    for fx in fixtures:
        ev = fx.get("event")
        if ev not in all_gws:
            continue
        h, a = fx["team_h"], fx["team_a"]
        by_team.setdefault(h, []).append((ev, a, fx["team_h_difficulty"], True))
        by_team.setdefault(a, []).append((ev, h, fx["team_a_difficulty"], False))
    for fl in by_team.values():
        fl.sort()
    for p in players.values():
        per_gw = {g: 0.0 for g in all_gws}
        run = []
        for ev, opp, fdr, home in by_team.get(p.team, []):
            mult = (_fdr_multiplier(fdr)
                    * _mismatch_multiplier(p.team_strength, strength.get(opp, 3.0)))
            per_gw[ev] += p.base_pm * mult * _lookahead_play(p, ev - start_event)
            if ev in gws:
                run.append((ev, f"{team_short.get(opp, '?')} ({'H' if home else 'A'})",
                            int(fdr)))
        p.gw_proj = {g: round(v, 2) for g, v in per_gw.items()}
        p.horizon_proj = round(sum(per_gw[g] for g in gws), 2)
        p.fixture_run = run
    return gws


def rank_keepers(players: dict[int, Player],
                 order: dict[str, list[str]] | None = None) -> None:
    """Set ``gk_rank`` per club: 1 = first choice, 2 = the backup who plays if
    he's out, 3+ = never plays. A hand-set ``order`` (team short name -> names)
    wins; otherwise available keepers rank by starts, minutes, then price (with
    no minutes to go on, clubs' #2s are usually priced above their #3s). Injured
    / unavailable keepers rank after every available one."""
    order = order or {}
    by_team: dict[int, list[Player]] = {}
    for p in players.values():
        if p.pos == 1:
            by_team.setdefault(p.team, []).append(p)
    for gks in by_team.values():
        manual = [n.lower() for n in order.get(gks[0].team_name, [])]

        def key(p: Player):
            pinned = manual.index(p.name.lower()) if p.name.lower() in manual \
                else len(manual)
            fit = p.status == "a" or (p.status == "d" and (p.chance or 0) >= 50)
            return (pinned, not fit, -p.starts, -p.minutes, -p.cost)
        for r, p in enumerate(sorted(gks, key=key), start=1):
            p.gk_rank = r


def keepers_ok(gks: list[Player]) -> bool:
    """User rule for the two keepers: both are first choice at their clubs, OR
    one is first choice and the other is THAT club's #2 (so the reserve plays
    when the starter is out). A #3, or a #2 from another club, is dead weight.
    Unranked keepers (gk_rank 0) are not judged."""
    if len(gks) != 2 or any(g.gk_rank == 0 for g in gks):
        return True
    a, b = sorted(gks, key=lambda g: g.gk_rank)
    return a.gk_rank == 1 and (b.gk_rank == 1
                               or (b.gk_rank == 2 and b.team == a.team))
