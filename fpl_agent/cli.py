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

from . import api, grok, model, news
from .html_report import render_html
from .optimizer import build_squad
from .overrides import load_overrides, merge
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

    # Start-probability signals: file overrides first, then live Grok (X) on top.
    signals = load_overrides()
    grok_used = False
    grok_bullets: list[dict] = []
    if not args.no_grok and grok.available():
        team_names = [t["name"] for t in boot["teams"]]
        gsig, grok_bullets = grok.analyse(team_names, gw)
        if gsig:
            signals = merge(signals, gsig)
            grok_used = True
    model.apply_start_signals(players, signals)

    model.score_players(players, season_started)

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
    squad = build_squad(list(players.values()), formation)

    headlines: list[news.Headline] = []
    if not args.no_news:
        names = {p.name for p in squad.squad}
        team_full = {t["name"] for t in boot["teams"]}
        headlines = news.relevant_headlines(
            news.fetch_headlines(), names, team_full)
    for b in grok_bullets:
        title = b["title"] + (f" — {b['detail']}" if b.get("detail") else "")
        headlines.insert(0, news.Headline(
            source="Grok (X/Twitter)", title=title, link="", summary=""))

    sources = [
        "Fantasy Premier League public API "
        "(bootstrap-static, fixtures) — https://fantasy.premierleague.com/api/",
        "Free RSS: BBC Sport, The Guardian, Sky Sports (team news / injuries)",
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
