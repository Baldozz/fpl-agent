"""Expected-points model.

Turns raw FPL data into a single ``projected`` score per player for the upcoming
gameweek(s). Designed to degrade gracefully at the season opener (when live
``form`` is 0 for everyone) by leaning on last-season output and FPL's own
``ep_next`` projection, then to lean more on live form as the season develops.
"""
from __future__ import annotations

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
    fdr: float = 3.0     # avg fixture difficulty over horizon (lower = easier)
    n_fix: int = 1       # number of fixtures in horizon (DGW awareness)
    team_strength: float = 3.0   # own-team quality 1..5 (CS / attack prior)
    opp_strength: float = 3.0    # avg opponent quality 1..5 over the horizon
    start_prob: float = 1.0      # P(starts) after news/overrides/minutes model
    override_reason: str = ""    # why start_prob was set by human/Grok
    projected: float = 0.0
    reasons: list[str] = field(default_factory=list)

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
