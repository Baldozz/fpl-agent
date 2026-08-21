"""Render a gameweek report as Markdown."""
from __future__ import annotations

from datetime import datetime, timezone

from .model import Player
from .news import Headline
from .optimizer import Squad

FORMATION_ORDER = {1: 0, 2: 1, 3: 2, 4: 3}


def _fmt_player(p: Player, tag: str = "") -> str:
    reasons = f" _({', '.join(p.reasons)})_" if p.reasons else ""
    news = f" ⚠️ {p.news}" if p.news else ""
    star = f" {tag}" if tag else ""
    return (f"| {p.pos_name} | **{p.name}**{star} | {p.team_name} | "
            f"£{p.cost_m:.1f}m | {p.projected:.2f} | {p.value:.2f} |"
            f"{reasons}{news}".rstrip())


def _xi_by_pos(xi: list[Player]) -> list[Player]:
    return sorted(xi, key=lambda p: (FORMATION_ORDER[p.pos], -p.projected))


def _formation(xi: list[Player]) -> str:
    counts = {2: 0, 3: 0, 4: 0}
    for p in xi:
        if p.pos in counts:
            counts[p.pos] += 1
    return f"{counts[2]}-{counts[3]}-{counts[4]}"


def render(squad: Squad, gw: int, deadline: str, season_started: bool,
           headlines: list[Headline], sources: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# FPL Agent — Gameweek {gw} Recommendation")
    lines.append("")
    lines.append(f"- **Generated:** {now}")
    lines.append(f"- **GW{gw} deadline:** {deadline}")
    lines.append(f"- **Formation:** {_formation(squad.xi)}")
    lines.append(f"- **Squad cost:** £{squad.total_cost/10:.1f}m / £100.0m "
                 f"(bank £{(1000-squad.total_cost)/10:.1f}m)")
    lines.append(f"- **Projected XI points (capt x2):** "
                 f"{squad.xi_projected:.1f}")
    mode = "live form + fixtures" if season_started else \
        "season opener (last-season output + FPL ep_next + fixtures)"
    lines.append(f"- **Model mode:** {mode}")
    lines.append("")

    lines.append(f"## ⭐ Captain: {squad.captain.name} "
                 f"({squad.captain.team_name}) — {squad.captain.projected:.2f} pts")
    lines.append(f"**Vice-captain:** {squad.vice.name} "
                 f"({squad.vice.team_name}) — {squad.vice.projected:.2f} pts")
    lines.append("")

    lines.append("## Starting XI")
    lines.append("")
    lines.append("| Pos | Player | Team | Price | Proj | Pts/£m | Notes |")
    lines.append("|-----|--------|------|-------|------|--------|-------|")
    for p in _xi_by_pos(squad.xi):
        tag = "(C)" if p.id == squad.captain.id else \
              "(V)" if p.id == squad.vice.id else ""
        lines.append(_fmt_player(p, tag))
    lines.append("")

    lines.append("## Bench (in substitution order)")
    lines.append("")
    lines.append("| Pos | Player | Team | Price | Proj | Pts/£m | Notes |")
    lines.append("|-----|--------|------|-------|------|--------|-------|")
    for i, p in enumerate(squad.bench, 1):
        lines.append(_fmt_player(p, f"[{i}]"))
    lines.append("")

    lines.append("## How this XI was chosen")
    lines.append("")
    lines.append("- Integer linear programming maximises the projected points of "
                 "the **starting XI** (not all 15) within £100.0m, the 2/5/5/3 "
                 "quota and max-3-per-club — then **minimises bench cost** so "
                 "spare budget is concentrated in the XI (the points-per-million "
                 "effect: an overpriced pick is swapped for a cheaper equal one "
                 "and the saving upgrades the XI).")
    lines.append("- Each projection blends FPL's `ep_next`, last-season "
                 "points-per-game and live form, then scales by **fixture "
                 "difficulty**, **own-team strength** (clean-sheet / attack "
                 "prior) and **probability of actually starting** — a "
                 "minutes/starts model, overridden by injury news and "
                 "human/Grok (X) signals in `overrides.json`.")
    lines.append("- Bench = cheapest viable cover that can still come on; "
                 "`Pts/£m` shows each pick's value.")
    lines.append("- Captain = highest projected starter; vice = second highest.")
    lines.append("")

    if headlines:
        lines.append("## Relevant team news (free RSS sources)")
        lines.append("")
        for h in headlines[:12]:
            title = f"[{h.title}]({h.link})" if h.link else h.title
            lines.append(f"- {title} — _{h.source}_")
        lines.append("")

    lines.append("## Data sources")
    lines.append("")
    for s in sources:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("> ⚠️ Recommendations only. Enter the team yourself on the FPL "
                 "site before the deadline. Not affiliated with the Premier "
                 "League or FPL.")
    lines.append("")
    return "\n".join(lines)
