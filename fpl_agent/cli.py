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

from . import agent, api, grok, league, live, model, news
from .html_report import (render_dashboard_html, render_html, render_league_html,
                          render_live_html, render_site)
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


def _prepare_players(args, boot, gw, season_started):
    """Thin wrapper over agent.prepare_players using CLI args."""
    return agent.prepare_players(
        boot, gw, season_started, horizon=args.horizon,
        use_grok=not args.no_grok, use_news=not args.no_news,
        use_cache=not args.no_cache)


def _run_site(args, boot, cur, nxt, gw, season_started) -> int:
    """One unified page: Dashboard + My Team (live, GW switcher) + League tabs."""
    team_id = live.resolve_team_id(args.team_id)
    if not team_id:
        print("No team id for --site (set FPL_TEAM_ID / ~/.fpl-mcp).")
        return 1
    players, all_headlines, _, _ = _prepare_players(args, boot, gw, season_started)
    current_gw = (cur or nxt)["id"]
    deadline = nxt["deadline_time"]

    available = sorted({e["id"] for e in boot["events"]
                        if e.get("finished") or e.get("is_current")} | {current_gw})
    live_by_gw = {}
    for g in available:
        try:
            live_by_gw[g] = live.fetch_live_team(team_id, g, boot)
        except Exception as e:
            print(f"[site] GW{g} live skipped: {e}")

    league_id = args.league_id or _resolve_league_id()
    d = agent.build_digest(team_id, boot, players, current_gw, gw, deadline,
                           league_id, args.free_transfers)
    lg = None
    if league_id:
        try:
            lg = league.fetch_league(league_id, current_gw, boot, with_teams=True)
        except Exception as e:
            print(f"[site] league skipped: {e}")
    if lg is None:
        lg = league.League(league_id=league_id or 0, name="League", gw=current_gw)

    headlines = (news.relevant_headlines(
        all_headlines, {p.name for p in d.current},
        {t["name"] for t in boot["teams"]}) if all_headlines else [])
    page = render_site(d, live_by_gw, lg, headlines, available, current_gw, deadline)
    print(f"Unified site: GW{gw} plan (C {d.captain.name if d.captain else '—'}), "
          f"{len(available)} live GW(s), league '{lg.name}' ({len(lg.rows)})")
    if args.html or args.save:
        DOCS.mkdir(exist_ok=True)
        (DOCS / "index.html").write_text(page)
        print(f"[written] {DOCS/'index.html'} (unified site)")
    return 0


def _run_dashboard(args, boot, cur, nxt, gw, season_started) -> int:
    """Previous-GW tracker + upcoming recommendation + transfer plan page."""
    team_id = live.resolve_team_id(args.team_id)
    if not team_id:
        print("No team id for --dashboard (set FPL_TEAM_ID / ~/.fpl-mcp).")
        return 1
    players, all_headlines, _, _ = _prepare_players(args, boot, gw, season_started)
    league_id = args.league_id or _resolve_league_id()
    current_gw = (cur or nxt)["id"]
    d = agent.build_digest(team_id, boot, players, current_gw, gw,
                           nxt["deadline_time"], league_id, args.free_transfers)
    headlines = []
    if all_headlines:
        headlines = news.relevant_headlines(
            all_headlines, {p.name for p in d.current},
            {t["name"] for t in boot["teams"]})
    print(f"GW{gw} plan — (C) {d.captain.name if d.captain else '—'}; "
          f"{len(d.moves)} transfer(s), {len(d.flagged)} flagged")
    if args.html or args.save:
        DOCS.mkdir(exist_ok=True)
        (DOCS / "index.html").write_text(
            render_dashboard_html(d, headlines))
        print(f"[written] {DOCS/'index.html'} (dashboard)")
    return 0


def _run_league(args, boot, cur, nxt) -> int:
    """Varsical league monitor page."""
    league_id = args.league_id or _resolve_league_id()
    if not league_id:
        print("No league id (set FPL_LEAGUE_ID or pass --league-id).")
        return 1
    gw = (cur or nxt)["id"]
    lg = league.fetch_league(league_id, gw, boot, with_teams=True)
    print(f"{lg.name}: {len(lg.rows)} managers, GW{gw}")
    if args.html or args.save:
        DOCS.mkdir(exist_ok=True)
        (DOCS / "league.html").write_text(render_league_html(lg))
        print(f"[written] {DOCS/'league.html'}")
    return 0


def _resolve_league_id() -> int | None:
    v = os.environ.get("FPL_LEAGUE_ID")
    return int(v) if v and v.isdigit() else None


def _run_live(args, boot, cur, nxt) -> int:
    """Render the user's actual entered team + live gameweek scores."""
    team_id = live.resolve_team_id(args.team_id)
    if not team_id:
        print("No team id. Pass --team-id N, set FPL_TEAM_ID, or configure "
              "~/.fpl-mcp/config.json.")
        return 1
    cur_gw = (cur or nxt)["id"]
    # Every gameweek that has a submitted team (finished ones + the current one).
    available = [e["id"] for e in boot["events"]
                 if e.get("finished") or e.get("is_current")]
    if cur_gw not in available:
        available.append(cur_gw)
    available = sorted(set(available))
    deadline = (cur or nxt)["deadline_time"]

    team_full = {t["name"] for t in boot["teams"]}
    rss = news.fetch_headlines() if not args.no_news else []

    pages = 0
    for g in available:
        try:
            team = live.fetch_live_team(team_id, g, boot)
        except Exception as e:
            print(f"[live] GW{g} skipped: {e}")
            continue
        headlines = (news.relevant_headlines(
            rss, {p.name for p in team.xi + team.bench}, team_full)
            if g == cur_gw and rss else [])
        page = render_live_html(team, g, deadline, headlines,
                                available_gws=available)
        if args.html or args.save:
            DOCS.mkdir(exist_ok=True)
            (DOCS / f"live-gw{g}.html").write_text(page)
            if g == cur_gw:
                (DOCS / "live.html").write_text(page)   # default = current GW
        if g == cur_gw:
            print(f"GW{g} live: {team.entry_name} — {team.total_points} pts "
                  f"(C: {team.captain.name if team.captain else '—'})")
        pages += 1
    if args.html or args.save:
        print(f"[written] {pages} live page(s) incl. {DOCS/'live.html'}")
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
                    help="FPL team id (else env FPL_TEAM_ID / ~/.fpl-mcp/config.json)")
    ap.add_argument("--site", action="store_true",
                    help="ONE unified page (Dashboard + My Team + League tabs) "
                         "→ docs/index.html")
    ap.add_argument("--dashboard", action="store_true",
                    help="standalone dashboard page (tracker + recommendation + "
                         "transfers)")
    ap.add_argument("--league", action="store_true",
                    help="Varsical league monitor page (writes docs/league.html)")
    ap.add_argument("--league-id", type=int, default=None,
                    help="classic league id (else env FPL_LEAGUE_ID)")
    ap.add_argument("--free-transfers", type=int, default=1,
                    help="free transfers available (for the transfer plan)")
    args = ap.parse_args(argv)

    boot = api.bootstrap(use_cache=not args.no_cache)
    cur, nxt = api.current_and_next_event(boot)
    if nxt is None:
        print("No upcoming gameweek found (season may be over).")
        return 1
    gw = nxt["id"]
    season_started = cur is not None and cur.get("finished") is not None \
        and any(e.get("finished") for e in boot["events"])

    if args.site:
        return _run_site(args, boot, cur, nxt, gw, season_started)
    if args.live:
        return _run_live(args, boot, cur, nxt)
    if args.league:
        return _run_league(args, boot, cur, nxt)
    if args.dashboard:
        return _run_dashboard(args, boot, cur, nxt, gw, season_started)

    players, all_headlines, grok_bullets, grok_used = _prepare_players(
        args, boot, gw, season_started)

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
