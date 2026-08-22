"""Weekly digest orchestration: tie together the previous-GW tracker, the
upcoming-GW recommendation, and transfer suggestions for the team you own.

Consumed by both the dashboard page (`html_report.render_dashboard_html`) and the
WhatsApp alerts (`notify`), so the recommendation shown and the recommendation
sent are always identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import api
from . import grok as grok_mod
from . import league as league_mod
from . import live
from . import model as model_mod
from . import news as news_mod
from .model import Player
from .overrides import load_overrides, merge
from .transfers import Move, flagged_players, opportunities, suggest_transfers


def prepare_players(boot: dict, gw: int, season_started: bool, horizon: int = 3,
                    use_grok: bool = True, use_news: bool = True,
                    use_cache: bool = True):
    """Build + score every player for the upcoming GW (RSS/overrides/Grok signals
    layered). Shared by the CLI and the alerts so recommendations are identical.
    Returns (players_dict, all_headlines, grok_bullets, grok_used)."""
    fixtures = api.fixtures(use_cache=use_cache)
    players = model_mod.build_players(boot)
    strength = model_mod.team_strength_map(boot)
    model_mod.attach_fixtures(players, fixtures, gw, horizon, strength)

    all_headlines = news_mod.fetch_headlines() if use_news else []
    rss_sig = (news_mod.parse_start_signals(
        all_headlines, {p.name for p in players.values()}) if all_headlines else {})
    signals = merge(rss_sig, load_overrides())
    grok_used = False
    grok_bullets: list[dict] = []
    if use_grok and grok_mod.available():
        gsig, grok_bullets = grok_mod.analyse([t["name"] for t in boot["teams"]], gw)
        if gsig:
            signals = merge(signals, gsig)
            grok_used = True
    model_mod.apply_start_signals(players, signals)
    games_played = sum(1 for e in boot["events"] if e.get("finished"))
    model_mod.score_players(players, season_started, games_played)
    return players, all_headlines, grok_bullets, grok_used


@dataclass
class Digest:
    team_id: int
    entry_name: str
    manager: str
    upcoming_gw: int
    deadline: str
    history: list[live.GWHistory] = field(default_factory=list)
    overall_rank: int | None = None
    league_name: str = ""
    league_rank: int | None = None
    current: list[Player] = field(default_factory=list)   # your 15, scored
    captain: Player | None = None
    vice: Player | None = None
    moves: list[Move] = field(default_factory=list)
    flagged: list[Player] = field(default_factory=list)
    opportunities: list[Player] = field(default_factory=list)
    bank: int = 0                                          # tenths of £m

    @property
    def last_gw(self) -> live.GWHistory | None:
        return self.history[-1] if self.history else None


def build_digest(team_id: int, bootstrap: dict, players: dict[int, Player],
                 current_gw: int, upcoming_gw: int, deadline: str,
                 league_id: int | None = None, free_transfers: int = 1) -> Digest:
    """``players`` must already be scored for the UPCOMING gameweek."""
    entry = live._get(f"{live.BASE}/entry/{team_id}/")
    history = live.fetch_history(team_id, bootstrap)
    squad_ids, bank = live.fetch_squad_ids(team_id, current_gw)
    current = [players[i] for i in squad_ids if i in players]
    current_set = set(squad_ids)

    # Captain from outfield players only (never a goalkeeper).
    outfield = sorted((p for p in current if p.pos != 1),
                      key=lambda p: p.projected, reverse=True)
    captain = outfield[0] if outfield else None
    vice = outfield[1] if len(outfield) > 1 else None

    moves = suggest_transfers(current, list(players.values()), bank,
                              free_transfers=free_transfers)
    flagged = flagged_players(current)
    opps = opportunities(current_set, list(players.values()))

    league_name, league_rank = "", None
    if league_id:
        try:
            league_name, rows = league_mod.fetch_standings(league_id)
            me = next((r for r in rows if r.entry_id == team_id), None)
            league_rank = me.rank if me else None
        except Exception:
            pass

    return Digest(
        team_id=team_id,
        entry_name=entry.get("name", ""),
        manager=f"{entry.get('player_first_name','')} "
                f"{entry.get('player_last_name','')}".strip(),
        upcoming_gw=upcoming_gw, deadline=deadline,
        history=history,
        overall_rank=(history[-1].overall_rank if history else None),
        league_name=league_name, league_rank=league_rank,
        current=current, captain=captain, vice=vice,
        moves=moves, flagged=flagged, opportunities=opps, bank=bank,
    )


def whatsapp_summary(d: Digest) -> str:
    """One compact message for WhatsApp (captain + transfers + flags)."""
    parts = [f"⚽ GW{d.upcoming_gw} plan — deadline {d.deadline[:16].replace('T',' ')}"]
    if d.captain:
        parts.append(f"(C) {d.captain.name}"
                     + (f", (VC) {d.vice.name}" if d.vice else ""))
    if d.flagged:
        parts.append("⚠️ Out/doubt: " + ", ".join(p.name for p in d.flagged[:4]))
    if d.moves:
        for m in d.moves:
            parts.append(f"↔️ {m.out.name} → {m.in_.name} (+{m.gain:.1f})")
    else:
        parts.append("No transfer needed — hold.")
    return " | ".join(parts)
