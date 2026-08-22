"""Render the gameweek recommendation as a polished, standalone HTML page.

The same template powers two outputs:
- ``docs/index.html`` (+ ``docs/GW<n>.html`` archive) for GitHub Pages, which
  updates every week when the agent runs.
- an optional body-only fragment for embedding elsewhere.

No external assets: all CSS is inlined and the layout is a "team sheet on a
pitch". Theme-aware (light/dark) via prefers-color-scheme + data-theme.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from .model import Player
from .news import Headline
from .optimizer import Squad

POS_ROWS = [(1, "Goalkeeper"), (2, "Defenders"), (3, "Midfielders"),
            (4, "Forwards")]


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _formation(xi: list[Player]) -> str:
    c = {2: 0, 3: 0, 4: 0}
    for p in xi:
        if p.pos in c:
            c[p.pos] += 1
    return f"{c[2]}-{c[3]}-{c[4]}"


def _card(p: Player, *, captain=False, vice=False, bench_idx: int | None = None,
          reason_notes=True) -> str:
    badge = ""
    if captain:
        badge = '<span class="arm c" title="Captain (2x points)">C</span>'
    elif vice:
        badge = '<span class="arm v" title="Vice-captain">V</span>'
    elif bench_idx is not None:
        badge = f'<span class="arm b" title="Bench order">{bench_idx}</span>'
    flag = ""
    if p.news:
        flag = f'<span class="flag" title="{_esc(p.news)}">!</span>'
    notes = ""
    if reason_notes and p.reasons:
        chips = "".join(
            f'<span class="chip">{_esc(r)}</span>' for r in p.reasons)
        notes = f'<div class="chips">{chips}</div>'
    return f"""
      <div class="player{' cap' if captain else ''}">
        <div class="shirt" data-pos="{p.pos}">{badge}{flag}</div>
        <div class="pname">{_esc(p.name)}</div>
        <div class="pmeta"><span>{_esc(p.team_name)}</span>
          <span class="proj">{p.projected:.1f}</span></div>
        <div class="pprice">£{p.cost_m:.1f}m · {p.value:.1f}/£m</div>
        {notes}
      </div>"""


def _pitch(squad: Squad) -> str:
    rows = []
    for pos, _ in POS_ROWS:
        line = [p for p in squad.xi if p.pos == pos]
        line.sort(key=lambda p: p.projected, reverse=True)
        cards = "".join(
            _card(p, captain=p.id == squad.captain.id,
                  vice=p.id == squad.vice.id) for p in line)
        rows.append(f'<div class="line">{cards}</div>')
    return f'<div class="pitch">{"".join(rows)}</div>'


def _bench(squad: Squad) -> str:
    cards = "".join(_card(p, bench_idx=i, reason_notes=False)
                    for i, p in enumerate(squad.bench, 1))
    return f'<div class="bench-row">{cards}</div>'


def _news(headlines: list[Headline]) -> str:
    if not headlines:
        return ('<p class="muted">No squad-specific headlines from the free '
                'feeds right now. Check back closer to kickoff.</p>')
    items = []
    for h in headlines[:10]:
        if h.link:
            title = (f'<a href="{_esc(h.link)}" target="_blank" '
                     f'rel="noopener">{_esc(h.title)}</a>')
        else:
            title = f'<span>{_esc(h.title)}</span>'
        items.append(f'<li>{title}'
                     f'<span class="src">{_esc(h.source)}</span></li>')
    return f'<ul class="news">{"".join(items)}</ul>'


CSS = """
:root{
  --bg:#f4f7f4; --panel:#ffffff; --ink:#0b1411; --muted:#5c6b63;
  --line:#e2e8e3; --pitch1:#0f9d58; --pitch2:#0c8a4d; --green:#00b06a;
  --magenta:#e90052; --amber:#f2a900;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0b1411; --panel:#111d18; --ink:#eaf2ee; --muted:#8fa79b;
    --line:#1e2e28; --pitch1:#0c6e3f; --pitch2:#0a5e36; }
}
:root[data-theme="light"]{ --bg:#f4f7f4; --panel:#ffffff; --ink:#0b1411;
  --muted:#5c6b63; --line:#e2e8e3; --pitch1:#0f9d58; --pitch2:#0c8a4d; }
:root[data-theme="dark"]{ --bg:#0b1411; --panel:#111d18; --ink:#eaf2ee;
  --muted:#8fa79b; --line:#1e2e28; --pitch1:#0c6e3f; --pitch2:#0a5e36; }

*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;padding:24px 20px 64px}
a{color:var(--green)}
.muted{color:var(--muted)}
.tnum{font-variant-numeric:tabular-nums}

header.hero{display:flex;flex-wrap:wrap;align-items:flex-end;gap:18px;
  justify-content:space-between;padding:22px 24px;border-radius:16px;
  background:linear-gradient(135deg,var(--pitch1),var(--pitch2));color:#fff;
  box-shadow:0 10px 30px rgba(0,0,0,.18)}
.hero .gw{font-size:12px;letter-spacing:.22em;text-transform:uppercase;
  opacity:.85;font-weight:700}
.hero h1{margin:.15em 0 0;font-size:clamp(26px,5vw,40px);font-weight:800;
  letter-spacing:-.01em;text-wrap:balance}
.hero .sub{opacity:.9;font-size:14px;margin-top:4px}
.countdown{text-align:right}
.countdown .big{font-size:clamp(22px,4vw,32px);font-weight:800;letter-spacing:.02em}
.countdown .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.18em;opacity:.85}

.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-weight:700}
.stat .v{font-size:22px;font-weight:800;margin-top:2px}
@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}}

.captain{display:flex;align-items:center;gap:18px;margin:18px 0;padding:18px 20px;
  border-radius:14px;background:var(--panel);
  border:1px solid var(--line);border-left:5px solid var(--magenta)}
.captain .arm{position:static;width:38px;height:38px;font-size:16px}
.captain .txt .role{font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--magenta);font-weight:800}
.captain .txt .who{font-size:22px;font-weight:800}
.captain .vice{margin-left:auto;text-align:right;color:var(--muted);font-size:14px}
.captain .vice b{color:var(--ink)}

h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:.16em;
  color:var(--muted);font-weight:800;margin:30px 0 12px}

.pitch{border-radius:16px;padding:22px 10px;
  background:
    repeating-linear-gradient(0deg,var(--pitch1),var(--pitch1) 46px,var(--pitch2) 46px,var(--pitch2) 92px);
  border:1px solid rgba(0,0,0,.15);box-shadow:inset 0 0 0 3px rgba(255,255,255,.12);
  display:flex;flex-direction:column;gap:14px}
.line{display:flex;justify-content:center;gap:clamp(8px,3vw,34px);flex-wrap:wrap}
.player{width:92px;text-align:center;color:#fff}
.player .shirt{position:relative;height:44px;width:44px;margin:0 auto 6px;
  border-radius:9px 9px 12px 12px;
  background:linear-gradient(#ffffff,#e9efe9);
  box-shadow:0 3px 8px rgba(0,0,0,.28)}
.shirt[data-pos="1"]{background:linear-gradient(#ffd54a,#f2a900)}
.shirt[data-pos="2"]{background:linear-gradient(#63d2ff,#2aa9e0)}
.shirt[data-pos="3"]{background:linear-gradient(#ffffff,#e9efe9)}
.shirt[data-pos="4"]{background:linear-gradient(#ff7aa8,#e90052)}
.player.cap .shirt{outline:2px solid #fff;outline-offset:2px}
.arm{position:absolute;top:-8px;right:-8px;width:22px;height:22px;border-radius:50%;
  display:grid;place-items:center;font-size:11px;font-weight:800;color:#fff;
  background:#333;box-shadow:0 1px 4px rgba(0,0,0,.4)}
.arm.c{background:var(--magenta)}
.arm.v{background:#7a2c8f}
.arm.b{background:#2a3b34}
.flag{position:absolute;bottom:-6px;left:-6px;width:18px;height:18px;border-radius:50%;
  background:var(--amber);color:#3a2a00;display:grid;place-items:center;
  font-weight:900;font-size:12px}
.pname{font-weight:800;font-size:13px;line-height:1.15;text-shadow:0 1px 3px rgba(0,0,0,.5)}
.pmeta{display:flex;justify-content:center;gap:6px;font-size:11px;
  text-shadow:0 1px 3px rgba(0,0,0,.5)}
.pmeta .proj{font-weight:800;font-variant-numeric:tabular-nums}
.pprice{font-size:11px;opacity:.9;font-variant-numeric:tabular-nums;
  text-shadow:0 1px 3px rgba(0,0,0,.5)}
.chips{display:flex;flex-wrap:wrap;gap:3px;justify-content:center;margin-top:4px}
.chip{font-size:9px;background:rgba(0,0,0,.35);padding:1px 6px;border-radius:20px;
  text-transform:uppercase;letter-spacing:.04em}

.bench-row{display:flex;gap:12px;flex-wrap:wrap;justify-content:center;
  background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
.bench-row .player{color:var(--ink)}
.bench-row .pname,.bench-row .pmeta,.bench-row .pprice{text-shadow:none}

ul.news{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:2px}
ul.news li{padding:10px 12px;border-bottom:1px solid var(--line);display:flex;
  flex-wrap:wrap;align-items:baseline;gap:8px;justify-content:space-between}
ul.news a{font-weight:600;text-decoration:none}
ul.news a:hover{text-decoration:underline}
ul.news .src{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}

.method{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:16px 20px;color:var(--muted);font-size:14px}
.method b{color:var(--ink)}
footer{margin-top:34px;color:var(--muted);font-size:12px;text-align:center}
footer .disc{margin-top:8px}
"""

JS = """
(function(){
  var el=document.getElementById('cd');
  if(!el)return;
  var target=new Date(el.getAttribute('data-deadline')).getTime();
  function tick(){
    var d=target-Date.now();
    if(d<=0){el.textContent='DEADLINE PASSED';return;}
    var h=Math.floor(d/3.6e6),m=Math.floor(d%3.6e6/6e4),
        days=Math.floor(h/24);
    el.textContent = days>0 ? days+'d '+(h%24)+'h '+m+'m' : h+'h '+m+'m';
  }
  tick();setInterval(tick,30000);
})();
"""


def _body(squad: Squad, gw: int, deadline: str, season_started: bool,
          headlines: list[Headline]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        dl_h = dl.strftime("%a %d %b, %H:%M UTC")
    except Exception:
        dl_h = deadline
    mode = ("Live form + fixtures" if season_started
            else "Season opener — last-season output + FPL ep_next + fixtures")
    bank = (1000 - squad.total_cost) / 10
    return f"""
  <div class="wrap">
    <header class="hero">
      <div>
        <div class="gw">Gameweek {gw} · Recommendation</div>
        <h1>Your Team Sheet</h1>
        <div class="sub">Generated {now} · Deadline {dl_h}</div>
      </div>
      <div class="countdown">
        <div class="big tnum" id="cd" data-deadline="{_esc(deadline)}">—</div>
        <div class="lbl">until deadline</div>
      </div>
    </header>

    <section class="stats">
      <div class="stat"><div class="k">Formation</div>
        <div class="v tnum">{_formation(squad.xi)}</div></div>
      <div class="stat"><div class="k">Squad cost</div>
        <div class="v tnum">£{squad.total_cost/10:.1f}m</div></div>
      <div class="stat"><div class="k">In the bank</div>
        <div class="v tnum">£{bank:.1f}m</div></div>
      <div class="stat"><div class="k">Proj. XI (C×2)</div>
        <div class="v tnum">{squad.xi_projected:.1f}</div></div>
    </section>

    <div class="captain">
      <span class="arm c">C</span>
      <div class="txt">
        <div class="role">Captain · doubles his points</div>
        <div class="who">{_esc(squad.captain.name)}
          <span class="muted tnum" style="font-size:15px">
          {_esc(squad.captain.team_name)} · {squad.captain.projected:.1f} proj</span></div>
      </div>
      <div class="vice">Vice-captain<br><b>{_esc(squad.vice.name)}</b>
        · {squad.vice.projected:.1f}</div>
    </div>

    <h2 class="sec">Starting XI — {_formation(squad.xi)}</h2>
    {_pitch(squad)}

    <h2 class="sec">Bench — substitution order</h2>
    {_bench(squad)}

    <h2 class="sec">Team news — free RSS feeds</h2>
    {_news(headlines)}

    <h2 class="sec">How this XI was chosen</h2>
    <div class="method">
      Squad picked by <b>integer linear programming</b> to maximise projected
      starting-XI points within the £100.0m budget, the 2/5/5/3 quota and the
      max-3-per-club rule. Each projection blends FPL's own <b>ep_next</b>,
      last-season points-per-game and live form, then scales by <b>fixture
      difficulty</b> and <b>injury/availability</b>. Model mode: <b>{mode}</b>.
    </div>

    <footer>
      <div>Built by the FPL Agent · free FPL API + free football RSS ·
        <a href="https://github.com/Baldozz/fpl-agent">source on GitHub</a></div>
      <div class="disc">⚠️ Advisory only — enter the team yourself on the FPL
        site before the deadline. Not affiliated with the Premier League or FPL.</div>
    </footer>
  </div>"""


def _live_card(p, captain_mult: int) -> str:
    badge = ""
    if p.is_captain:
        badge = f'<span class="arm c" title="Captain">C</span>'
    elif p.is_vice:
        badge = '<span class="arm v" title="Vice-captain">V</span>'
    net = p.points * (p.multiplier or 1)
    # dim players whose match hasn't started; highlight the live score
    cls = "player" + (" cap" if p.is_captain else "")
    dim = "" if p.started_fixture else ' style="opacity:.55"'
    capx = f' <span class="capx">×{p.multiplier}</span>' if p.multiplier > 1 else ""
    return f"""
      <div class="{cls}"{dim}>
        <div class="shirt" data-pos="{p.pos}">{badge}</div>
        <div class="pname">{_esc(p.name)}</div>
        <div class="pmeta"><span>{_esc(p.team_name)}</span>
          <span class="proj">{net}{capx}</span></div>
        <div class="pprice">{p.minutes}'</div>
      </div>"""


def render_live_html(team, gw: int, deadline: str,
                     headlines: list[Headline], full_document: bool = True) -> str:
    """Render the manager's ACTUAL team with LIVE gameweek scores."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def line(pos):
        cards = "".join(_live_card(p, team.captain.multiplier if team.captain else 2)
                        for p in sorted([q for q in team.xi if q.pos == pos],
                                        key=lambda z: -z.net_points))
        return f'<div class="line">{cards}</div>'
    pitch = f'<div class="pitch">{"".join(line(pos) for pos,_ in POS_ROWS)}</div>'
    bench = "".join(_live_card(p, 1) for p in team.bench)
    played = sum(1 for p in team.xi if p.started_fixture)
    chip = f' · chip: {team.active_chip}' if team.active_chip else ""
    rank = f"{team.overall_rank:,}" if team.overall_rank else "—"
    content = f"""
    <header class="hero">
      <div>
        <div class="gw">Gameweek {gw} · Live</div>
        <h1>{_esc(team.entry_name or 'My Team')}</h1>
        <div class="sub">{_esc(team.manager_name)} · updated {now}{chip}</div>
      </div>
      <div class="countdown">
        <div class="big tnum">{team.total_points}</div>
        <div class="lbl">GW{gw} points</div>
      </div>
    </header>

    <section class="stats">
      <div class="stat"><div class="k">GW points</div>
        <div class="v tnum">{team.total_points}</div></div>
      <div class="stat"><div class="k">On bench</div>
        <div class="v tnum">{team.bench_points}</div></div>
      <div class="stat"><div class="k">Overall rank</div>
        <div class="v tnum">{rank}</div></div>
      <div class="stat"><div class="k">XI played</div>
        <div class="v tnum">{played}/11</div></div>
    </section>

    <div class="captain">
      <span class="arm c">C</span>
      <div class="txt">
        <div class="role">Captain (×{team.captain.multiplier if team.captain else 2})</div>
        <div class="who">{_esc(team.captain.name) if team.captain else '—'}
          <span class="muted tnum" style="font-size:15px">
          {(team.captain.net_points if team.captain else 0)} pts live</span></div>
      </div>
      <div class="vice">Vice<br><b>{_esc(team.vice.name) if team.vice else '—'}</b></div>
    </div>

    <h2 class="sec">Starting XI — live points</h2>
    {pitch}
    <h2 class="sec">Bench</h2>
    <div class="bench-row">{bench}</div>
"""
    if headlines:
        content += ('<h2 class="sec">Team news — live from X &amp; RSS</h2>'
                    + _news(headlines))
    content += """
    <footer>
      <div>Live from the FPL API · <a
        href="https://github.com/Baldozz/fpl-agent">source on GitHub</a></div>
      <div class="disc">Live scores update as matches are played; provisional
        until the gameweek is finalised (bonus points, auto-subs).</div>
    </footer>"""
    css_extra = ".capx{color:var(--magenta);font-weight:800}"
    body = f'<div class="wrap">{content}</div>'
    if not full_document:
        return f"<style>{CSS}{css_extra}</style>{body}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Live — GW{gw} · {_esc(team.entry_name or 'My Team')}</title>
<style>{CSS}{css_extra}</style>
</head>
<body>
{body}
</body>
</html>"""


def render_html(squad: Squad, gw: int, deadline: str, season_started: bool,
                headlines: list[Headline], full_document: bool = True) -> str:
    body = _body(squad, gw, deadline, season_started, headlines)
    if not full_document:
        return f"<style>{CSS}</style>{body}<script>{JS}</script>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Agent — GW{gw} Team Sheet</title>
<style>{CSS}</style>
</head>
<body>
{body}
<script>{JS}</script>
</body>
</html>"""
