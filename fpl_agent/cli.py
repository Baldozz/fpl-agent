"""Command-line entry point.

    python -m fpl_agent                 # build squad for the next deadline
    python -m fpl_agent --horizon 3     # weight fixtures over 3 gameweeks
    python -m fpl_agent --no-news       # skip RSS fetch (offline / faster)
    python -m fpl_agent --no-grok       # skip the xAI Grok (X) team-news query
    python -m fpl_agent --formation 4-3-3   # pin a shape (default 3-4-3; 'free' = auto)
    python -m fpl_agent --no-cache      # force fresh API pull
    python -m fpl_agent --save          # also write reports/GW<n>.md
    python -m fpl_agent --html          # also write docs/index.html (Pages)

Start-probability signals come from overrides.json (+ live Grok when the
XAI_API_KEY has credits). The XAI_API_KEY is read from the environment or a
gitignored .env file and is never committed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from . import api, grok, live, model, news
from .html_report import render_html, render_live_html
from .optimizer import build_squad
from .overrides import load_must_include, load_overrides, merge
from .report import render

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a gitignored .env into the environment."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _run_live(args, boot, cur, nxt) -> int:
    """Render the user's actual entered team + live gameweek scores."""
    team_id = live.resolve_team_id(args.team_id)
    if not team_id:
        print("No team id. Pass --team-id N, set FPL_TEAM_ID, or configure "
              "~/.fpl-mcp/config.json.")
        return 1
    gw = (cur or nxt)["id"]
    team = live.fetch_live_team(team_id, gw, boot)

    headlines: list[news.Headline] = []
    if not args.no_news:
        names = {p.name for p in team.xi + team.bench}
        team_full = {t["name"] for t in boot["teams"]}
        headlines = news.relevant_headlines(
            news.fetch_headlines(), names, team_full)

    page = render_live_html(team, gw, (cur or nxt)["deadline_time"], headlines)
    print(f"GW{gw} live: {team.entry_name} — {team.total_points} pts "
          f"(C: {team.captain.name if team.captain else '—'}), "
          f"rank {team.overall_rank:,}" if team.overall_rank else
          f"GW{gw} live: {team.total_points} pts")
    if args.html or args.save:
        DOCS.mkdir(exist_ok=True)
        (DOCS / "index.html").write_text(page)
        (DOCS / f"GW{gw:02d}-live.html").write_text(page)
        print(f"[written] {DOCS/'index.html'} (live)")
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(prog="fpl_agent")
    ap.add_argument("--horizon", type=int, default=1,
                    help="gameweeks of fixtures to weight (default 1)")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--save", action="store_true",
                    help="write reports/GW<n>.md")
    ap.add_argument("--html", action="store_true",
                    help="write docs/index.html + docs/GW<n>.html (GitHub Pages)")
    ap.add_argument("--no-grok", action="store_true",
                    help="skip the xAI Grok (X/Twitter) team-news query")
    ap.add_argument("--formation", default="3-4-3",
                    help="starting shape DEF-MID-FWD (default 3-4-3), "
                         "or 'free' to let the optimiser choose")
    ap.add_argument("--include", default="",
                    help="comma-separated player names to force into the squad "
                         "(added to must_include in overrides.json)")
    ap.add_argument("--live", action="store_true",
                    help="show YOUR actual entered team + live gameweek scores "
                         "(not the recommendation)")
    ap.add_argument("--team-id", type=int, default=None,
                    help="FPL team id for --live (else env FPL_TEAM_ID / "
                         "~/.fpl-mcp/config.json)")
    args = ap.parse_args(argv)

    boot = api.bootstrap(use_cache=not args.no_cache)
    cur, nxt = api.current_and_next_event(boot)
    if nxt is None:
        print("No upcoming gameweek found (season may be over).")
        return 1
    gw = nxt["id"]
    season_started = cur is not None and cur.get("finished") is not None \
        and any(e.get("finished") for e in boot["events"])

    if args.live:
        return _run_live(args, boot, cur, nxt)

    fixtures = api.fixtures(use_cache=not args.no_cache)
    players = model.build_players(boot)
    strength = model.team_strength_map(boot)
    model.attach_fixtures(players, fixtures, gw, args.horizon, strength)

    # Fetch RSS once; used both to drive selection (parse_start_signals) and for
    # display (relevant_headlines) later.
    all_headlines: list[news.Headline] = []
    if not args.no_news:
        all_headlines = news.fetch_headlines()

    # Start-probability signals, layered lowest -> highest confidence:
    #   RSS (heuristic, negative-only) < file overrides < Grok (X).
    # merge(base, extra): extra wins unless the base entry is pinned, so a pinned
    # manual override always wins.
    rss_sig = news.parse_start_signals(
        all_headlines, {p.name for p in players.values()}) if all_headlines else {}
    signals = merge(rss_sig, load_overrides())      # file overrides beat RSS
    grok_used = False
    grok_bullets: list[dict] = []
    if not args.no_grok and grok.available():
        team_names = [t["name"] for t in boot["teams"]]
        gsig, grok_bullets = grok.analyse(team_names, gw)
        if gsig:
            signals = merge(signals, gsig)          # Grok beats RSS + unpinned file
            grok_used = True
    model.apply_start_signals(players, signals)
    rss_used = sum(1 for s in signals.values() if s.get("source") == "rss")
    if rss_used:
        print(f"[rss] {rss_used} start-prob signals from RSS headlines")

    games_played = sum(1 for e in boot["events"] if e.get("finished"))
    model.score_players(players, season_started, games_played)

    if args.formation.strip().lower() == "free":
        formation = None
    else:
        try:
            d, m, f = (int(x) for x in args.formation.split("-"))
            formation = (d, m, f)
            if d + m + f != 10 or not (3 <= d <= 5 and 2 <= m <= 5 and 1 <= f <= 3):
                raise ValueError
        except ValueError:
            print(f"Invalid --formation '{args.formation}'; using 3-4-3.")
            formation = (3, 4, 3)

    # Force-include players: from overrides.json must_include + --include flag.
    want_names = load_must_include() + \
        [n.strip() for n in args.include.split(",") if n.strip()]
    must_ids: set[int] = set()
    for nm in want_names:
        matches = model._match_players(players, nm)
        if matches:
            must_ids.add(max(matches, key=lambda p: p.projected).id)
        else:
            print(f"[include] no player matched '{nm}' — skipping")
    if want_names:
        print(f"[include] forcing into squad: "
              f"{', '.join(sorted({players[i].name for i in must_ids}))}")

    squad = build_squad(list(players.values()), formation, must_ids)

    headlines: list[news.Headline] = []
    if all_headlines:
        names = {p.name for p in squad.squad}
        team_full = {t["name"] for t in boot["teams"]}
        headlines = news.relevant_headlines(all_headlines, names, team_full)
    for b in grok_bullets:
        title = b["title"] + (f" — {b['detail']}" if b.get("detail") else "")
        headlines.insert(0, news.Headline(
            source="Grok (X/Twitter)", title=title, link="", summary=""))

    sources = [
        "Fantasy Premier League public API "
        "(bootstrap-static, fixtures) — https://fantasy.premierleague.com/api/",
        "Free RSS: BBC Sport, The Guardian (team news / injuries — parsed into "
        "start-probability signals as well as shown)",
        ("xAI Grok live search over X/Twitter (predicted line-ups, rotation, "
         "injuries)" + ("" if grok_used else " — NOT USED this run: "
                        "no XAI_API_KEY/credits, using overrides + RSS")),
        "Manual overrides file (overrides.json) — human/Grok start-prob signals",
    ]
    md = render(squad, gw, nxt["deadline_time"], season_started,
                headlines, sources)
    print(md)

    if args.save:
        REPORTS.mkdir(exist_ok=True)
        out = REPORTS / f"GW{gw:02d}.md"
        out.write_text(md)
        print(f"\n[written] {out}")

    if args.html:
        DOCS.mkdir(exist_ok=True)
        page = render_html(squad, gw, nxt["deadline_time"], season_started,
                           headlines)
        (DOCS / "index.html").write_text(page)
        (DOCS / f"GW{gw:02d}.html").write_text(page)
        print(f"[written] {DOCS/'index.html'} and {DOCS/f'GW{gw:02d}.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
