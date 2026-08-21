"""Command-line entry point.

    python -m fpl_agent                 # build squad for the next deadline
    python -m fpl_agent --horizon 3     # weight fixtures over 3 gameweeks
    python -m fpl_agent --no-news       # skip RSS fetch (offline / faster)
    python -m fpl_agent --no-cache      # force fresh API pull
    python -m fpl_agent --save          # also write reports/GW<n>.md
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import api, model, news
from .optimizer import build_squad
from .report import render

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fpl_agent")
    ap.add_argument("--horizon", type=int, default=1,
                    help="gameweeks of fixtures to weight (default 1)")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--save", action="store_true",
                    help="write reports/GW<n>.md")
    args = ap.parse_args(argv)

    boot = api.bootstrap(use_cache=not args.no_cache)
    cur, nxt = api.current_and_next_event(boot)
    if nxt is None:
        print("No upcoming gameweek found (season may be over).")
        return 1
    gw = nxt["id"]
    season_started = cur is not None and cur.get("finished") is not None \
        and any(e.get("finished") for e in boot["events"])

    fixtures = api.fixtures(use_cache=not args.no_cache)
    players = model.build_players(boot)
    model.attach_fixtures(players, fixtures, gw, args.horizon)
    model.score_players(players, season_started)

    squad = build_squad(list(players.values()))

    headlines: list[news.Headline] = []
    if not args.no_news:
        names = {p.name for p in squad.squad}
        headlines = news.relevant_headlines(news.fetch_headlines(), names)

    sources = [
        "Fantasy Premier League public API "
        "(bootstrap-static, fixtures) — https://fantasy.premierleague.com/api/",
        "Free RSS: BBC Sport, The Guardian, Sky Sports (team news / injuries)",
    ]
    md = render(squad, gw, nxt["deadline_time"], season_started,
                headlines, sources)
    print(md)

    if args.save:
        REPORTS.mkdir(exist_ok=True)
        out = REPORTS / f"GW{gw:02d}.md"
        out.write_text(md)
        print(f"\n[written] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
