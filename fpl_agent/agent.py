"""Weekly digest orchestration: tie together the previous-GW tracker, the
upcoming-GW recommendation, and transfer suggestions for the team you own.

Consumed by both the dashboard page (`html_report.render_dashboard_html`) and the
WhatsApp alerts (`notify`), so the recommendation shown and the recommendation
sent are always identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import api
from . import chips as chips_mod
from . import grok as grok_mod
from . import league as league_mod
from . import live
from . import model as model_mod
from . import news as news_mod
from . import strategy as strategy_mod
from .model import Player
from .overrides import (load_chips_used, load_keeper_order, load_must_include,
                        load_overrides, merge)
from .transfers import (Move, TransferOption, WildcardDraft, best_plan,
                        flagged_players, keeper_issue,
                        transfer_options, wildcard_draft)


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
        deadline = next((e["deadline_time"] for e in boot["events"]
                         if e["id"] == gw), None)
        gsig, grok_bullets = grok_mod.analyse([t["name"] for t in boot["teams"]],
                                              gw, deadline)
        if gsig:
            signals = merge(signals, gsig)
            grok_used = True
    model_mod.apply_start_signals(players, signals)
    games_played = sum(1 for e in boot["events"] if e.get("finished"))
    model_mod.score_players(players, season_started, games_played)
    model_mod.rank_keepers(players, load_keeper_order())
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
    done: list[tuple[Player, Player]] = field(default_factory=list)  # (out, in) made this GW
    free_transfers: int = 1
    flagged: list[Player] = field(default_factory=list)
    bank: int = 0                                          # tenths of £m
    squad_source: str = ""     # "live" | "snapshot <iso>" | "picks"
    # Look-ahead advice (all judged over ``lookahead_gws``).
    lookahead_gws: list[int] = field(default_factory=list)
    options: list[TransferOption] = field(default_factory=list)
    wildcard: WildcardDraft | None = None
    wildcard_used: list[int] = field(default_factory=list)   # GWs WC was played
    chip_plan: list[chips_mod.ChipPlan] = field(default_factory=list)
    dgw_gws: list[int] = field(default_factory=list)   # doubles in the chip scan
    bgw_gws: list[int] = field(default_factory=list)   # blanks in the chip scan
    prices_exact: bool = False   # selling prices from my-team (else now_cost)
    ownership: league_mod.Ownership | None = None
    keeper_issue: str = ""       # non-empty when your GK pair breaks the rule

    @property
    def lookahead_label(self) -> str:
        g = self.lookahead_gws
        if not g:
            return f"GW{self.upcoming_gw}"
        return f"GW{g[0]}–GW{g[-1]} ({len(g)} GWs)" if len(g) > 1 else f"GW{g[0]}"

    @property
    def wildcard_available(self) -> bool:
        return chips_mod.chip_open(self.upcoming_gw, self.wildcard_used)

    @property
    def wildcard_active(self) -> bool:
        """Wildcard activated for the upcoming deadline: the draft IS the plan."""
        return self.upcoming_gw in self.wildcard_used

    @property
    def last_gw(self) -> live.GWHistory | None:
        return self.history[-1] if self.history else None


def _done_transfers(team_id: int, current_gw: int, squad_ids: list[int],
                    players: dict[int, Player]) -> list[tuple[Player, Player]]:
    """Transfers made since the last deadline: diff the pending squad against
    the last-deadline picks (FPL's public transfer log only updates after the
    deadline). Outs and ins are paired by position."""
    try:
        old = [p["element"] for p in live._get_shared(
            f"{live.BASE}/entry/{team_id}/event/{current_gw}/picks/")["picks"]]
    except Exception:
        return []
    outs = [players[i] for i in old if i not in squad_ids and i in players]
    ins = [players[i] for i in squad_ids if i not in old and i in players]
    pairs = []
    for o in outs:
        i = next((x for x in ins if x.pos == o.pos), None)
        if i:
            ins.remove(i)
            pairs.append((o, i))
    return pairs


def _must_include_ids(players: dict[int, Player]) -> set[int]:
    """overrides.json ``must_include`` names -> ids (same rule as the CLI)."""
    ids = set()
    for nm in load_must_include():
        m = model_mod._match_players(players, nm)
        if m:
            ids.add(max(m, key=lambda p: p.projected).id)
    return ids


def build_digest(team_id: int, bootstrap: dict, players: dict[int, Player],
                 current_gw: int, upcoming_gw: int, deadline: str,
                 league_id: int | None = None, free_transfers: int = 1,
                 league: league_mod.League | None = None,
                 lookahead: int = model_mod.LOOKAHEAD) -> Digest:
    """``players`` must already be scored for the UPCOMING gameweek.

    ``league`` (already fetched with rival teams) avoids a second fetch; when
    absent but ``league_id`` is set, the league is fetched here so every surface
    gets the same ownership context."""
    entry = live._get(f"{live.BASE}/entry/{team_id}/")
    history = live.fetch_history(team_id, bootstrap)
    squad_ids, bank = live.fetch_squad_ids(team_id, current_gw)
    # Real free-transfer count from the authenticated view, when we have it.
    t = (live.LAST_MY_TEAM or {}).get("transfers") or {}
    if t.get("limit") is not None:
        free_transfers = max(0, t["limit"] - t.get("made", 0))
    done = _done_transfers(team_id, current_gw, squad_ids, players) \
        if t.get("made") else []
    current = [players[i] for i in squad_ids if i in players]

    # Captain from outfield players only (never a goalkeeper), ranked by
    # projection + attacking ceiling — the armband wants hauls, not averages.
    outfield = sorted((p for p in current if p.pos != 1),
                      key=model_mod.captain_score, reverse=True)
    captain = outfield[0] if outfield else None
    vice = outfield[1] if len(outfield) > 1 else None

    # Look-ahead: re-price every player's next N fixtures one by one (and keep
    # per-GW points further out for chip timing).
    gws = model_mod.attach_lookahead(
        players, api.fixtures(), upcoming_gw, lookahead,
        model_mod.team_strength_map(bootstrap),
        {t["id"]: t["short_name"] for t in bootstrap["teams"]},
        per_gw_horizon=chips_mod.PROJECTION_GWS)

    league_name, league_rank, ownership = "", None, None
    if league is None and league_id:
        try:
            league = league_mod.fetch_league(league_id, current_gw, bootstrap,
                                             with_teams=True)
        except Exception as e:
            print(f"[digest] league skipped: {e}")
    if league is not None and league.rows:
        league_name = league.name
        me = next((r for r in league.rows if r.entry_id == team_id), None)
        league_rank = me.rank if me else None
        ownership = league_mod.league_ownership(league, exclude_entry=team_id)

    selling = live.selling_prices()
    pool = list(players.values())
    options = transfer_options(current, pool, bank, free_transfers,
                               selling, ownership)
    moves = best_plan(options)
    flagged = flagged_players(current)
    # Chip history from every source we have: public chip log (played),
    # authenticated my-team (played + activated for this deadline), and the
    # hand-edited overrides.json ``chips_used``.
    sources = [league_mod.fetch_chips(team_id), live.chips_played(upcoming_gw),
               load_chips_used()]
    chips_used = {c: sorted({g for src in sources for g in src.get(c, [])})
                  for c in chips_mod.CHIPS}
    wc_used = chips_used["wildcard"]
    must = _must_include_ids(players)
    wc = None
    if chips_mod.chip_open(upcoming_gw, wc_used):
        try:
            wc = wildcard_draft(current, pool, bank, selling, must_include=must)
        except Exception as e:
            print(f"[digest] wildcard draft skipped: {e}")
    rival_chips, n_rivals = ({}, None)
    if league is not None and league.rows:
        rival_chips, n_rivals = chips_mod.rival_chip_counts(league, team_id,
                                                            upcoming_gw)
    dgw, bgw = strategy_mod.detect_dgw_bgw(api.fixtures(), upcoming_gw,
                                           chips_mod.SCAN_GWS)
    try:
        chip_plan = chips_mod.chip_calendar(
            current, pool, bank, upcoming_gw, chips_used, selling, must,
            rival_chips, n_rivals)
    except Exception as e:
        print(f"[digest] chip calendar skipped: {e}")
        chip_plan = []

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
        moves=moves, flagged=flagged, bank=bank,
        done=done, free_transfers=free_transfers,
        squad_source=live.SQUAD_SOURCE,
        lookahead_gws=gws, options=options, wildcard=wc, wildcard_used=wc_used,
        chip_plan=chip_plan, dgw_gws=dgw, bgw_gws=bgw,
        prices_exact=bool(selling) and all(p.id in selling for p in current),
        ownership=ownership, keeper_issue=keeper_issue(current),
    )


def whatsapp_summary(d: Digest) -> str:
    """One compact message for WhatsApp (captain + transfers + flags)."""
    parts = [f"⚽ GW{d.upcoming_gw} plan — deadline {d.deadline[:16].replace('T',' ')}"]
    if d.captain:
        parts.append(f"(C) {d.captain.name}"
                     + (f", (VC) {d.vice.name}" if d.vice else ""))
    if d.flagged:
        parts.append("⚠️ Out/doubt: " + ", ".join(p.name for p in d.flagged[:4]))
    for o, i in d.done:
        parts.append(f"✅ Done: {o.name} → {i.name}")
    n = len(d.lookahead_gws) or 1
    if d.moves:
        for m in d.moves:
            parts.append(f"↔️ {m.out.name} → {m.in_.name} (+{m.gain:.1f} xPts/{n}GW)")
    elif not d.done:
        parts.append("No transfer needed — hold.")
    plan = [(m.out.id, m.in_.id) for m in d.moves]
    alts = [o for o in d.options
            if [(m.out.id, m.in_.id) for m in o.moves] != plan][:2]
    if d.moves and alts:
        parts.append("Alt: " + "; ".join(
            " + ".join(f"{m.out.name}→{m.in_.name}" for m in o.moves)
            + f" (+{o.net:.1f}{', -%d hit' % o.hit if o.hit else ''})" for o in alts))
    for c in d.chip_plan:
        if c.available and c.planned_gw == d.upcoming_gw and c.clear:
            parts.append(f"🃏 {c.label}: this GW is its best week (+{c.best_value:.0f})")
        elif c.available and c.gws_left is not None \
                and c.gws_left <= chips_mod.EXPIRY_WARN_GWS:
            parts.append(f"⏳ {c.label} expires after GW{c.expires_gw}")
    wc = d.wildcard
    if wc and d.wildcard_active:
        parts.append(f"🃏 Wildcard ACTIVE — draft +{wc.gain:.0f} xPts/{n}GW on page")
    elif wc and d.wildcard_available and wc.gain >= WILDCARD_FLAG_GAIN:
        parts.append(f"🃏 Wildcard draft +{wc.gain:.0f} xPts/{n}GW "
                     f"({len(wc.ins)} changes) — see page")
    return " | ".join(parts)


# Mention the wildcard draft in alerts only when a rebuild beats the current
# XI by this much over the look-ahead (it's a once-per-half chip).
WILDCARD_FLAG_GAIN = 15.0
