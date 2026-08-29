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
POS_ABBR = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


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


def _kit_url(team_code: int, pos: int) -> str:
    suffix = "_1" if pos == 1 else ""
    return ("https://fantasy.premierleague.com/dist/img/shirts/standard/"
            f"shirt_{team_code}{suffix}-66.png")


# FPL-style "Points" pitch: kit image, dark name bar, points bar underneath.
LIVE_CSS = """
.gwbar{display:flex;align-items:center;gap:10px;margin:14px 0 4px;flex-wrap:wrap}
.gwsel{font:inherit;font-weight:700;padding:8px 12px;border-radius:10px;
  border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.fpitch{border-radius:16px;padding:18px 8px;
  background:repeating-linear-gradient(0deg,#0f9d58,#0f9d58 44px,#0c8a4d 44px,#0c8a4d 88px);
  box-shadow:inset 0 0 0 3px rgba(255,255,255,.12);display:flex;flex-direction:column;gap:12px}
.frow{display:flex;justify-content:center;gap:clamp(6px,2.4vw,26px);flex-wrap:wrap}
.fp{width:76px;text-align:center;position:relative}
.kitwrap{position:relative;height:50px;display:flex;align-items:center;justify-content:center}
.kitwrap img{height:50px;width:auto}
.kitwrap.nokit{border-radius:8px 8px 12px 12px;height:44px;width:44px;margin:3px auto 0}
.kitwrap.nokit[data-pos="1"]{background:linear-gradient(#ffd54a,#f2a900)}
.kitwrap.nokit[data-pos="2"]{background:linear-gradient(#63d2ff,#2aa9e0)}
.kitwrap.nokit[data-pos="3"]{background:linear-gradient(#fff,#e9efe9)}
.kitwrap.nokit[data-pos="4"]{background:linear-gradient(#ff7aa8,#e90052)}
.fp .nm{background:#2d0a31;color:#fff;font-size:11px;font-weight:700;
  border-radius:5px 5px 0 0;padding:3px 4px;margin-top:4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.fp .pt{background:#fff;color:#2d0a31;font-weight:800;font-size:13px;
  border-radius:0 0 5px 5px;padding:2px;font-variant-numeric:tabular-nums}
.fp .mins{font-size:10px;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);margin-top:2px}
.fp.cap .nm{background:var(--magenta)}
.fp .band{position:absolute;top:-6px;right:6px;width:20px;height:20px;border-radius:50%;
  background:var(--magenta);color:#fff;font-size:11px;font-weight:800;display:grid;
  place-items:center;z-index:2;box-shadow:0 1px 4px rgba(0,0,0,.4)}
.fp .band.v{background:#7a2c8f}
.benchbar{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 8px;display:flex;justify-content:center;gap:clamp(6px,3vw,26px);flex-wrap:wrap}
.benchbar .fp .mins{color:var(--muted);text-shadow:none}
.capx{color:#fff}
"""


def _live_card(p, captain_mult: int = 2) -> str:
    band = ('<span class="band">C</span>' if p.is_captain else
            '<span class="band v">V</span>' if p.is_vice else "")
    net = p.points * (p.multiplier or 1)
    capx = f'<span class="capx"> ×{p.multiplier}</span>' if p.multiplier > 1 else ""
    dim = "" if p.started_fixture else ' style="opacity:.5"'
    kit = _kit_url(p.team_code, p.pos)
    return f"""
      <div class="fp{' cap' if p.is_captain else ''}"{dim}>
        <div class="kitwrap" data-pos="{p.pos}">{band}<img src="{kit}"
          alt="{_esc(p.team_name)}" loading="lazy"
          onerror="this.remove();this.parentNode.classList.add('nokit')"></div>
        <div class="nm">{_esc(p.name)}</div>
        <div class="pt">{net}{capx}</div>
        <div class="mins">{p.minutes}&#39;</div>
      </div>"""


DASH_CSS = """
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.card h3{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.move{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:700}
.out{background:rgba(233,0,82,.14);color:var(--magenta)}
.in{background:rgba(0,176,106,.16);color:var(--green)}
.gain{margin-left:auto;font-weight:800;color:var(--green);font-variant-numeric:tabular-nums}
.flag-list span{display:inline-block;background:rgba(242,169,0,.16);color:#8a6a00;
  padding:3px 9px;border-radius:20px;font-size:12px;margin:2px 4px 2px 0}
.hold{color:var(--green);font-weight:700}
.me{background:rgba(0,176,106,.10)}
.mv-up{color:var(--green)} .mv-dn{color:var(--magenta)} .mv-eq{color:var(--muted)}
.btnrow{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 4px}
.btn{display:inline-block;padding:8px 14px;border-radius:10px;border:1px solid var(--line);
  text-decoration:none;color:var(--ink);font-weight:600;font-size:13px}
"""


LEAGUE_CSS = """
/* Insight strip */
.insight{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0 4px}
@media(max-width:760px){.insight{grid-template-columns:1fr}}
.ins{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;position:relative;overflow:hidden}
.ins::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;
  background:linear-gradient(var(--green),var(--pitch2))}
.ins .ik{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:800}
.ins .iv{font-size:19px;font-weight:800;margin:3px 0 1px;letter-spacing:-.01em}
.ins .im{font-size:12px;color:var(--muted)}

/* League table */
.ltable{width:100%;border-collapse:collapse}
.ltable thead th{position:sticky;top:0;background:var(--panel);z-index:1}
.lrow{cursor:default;transition:background .12s}
.lrow.expandable{cursor:pointer}
.lrow.expandable:hover{background:rgba(0,176,106,.07)}
.lrow td{border-bottom:1px solid var(--line);padding:9px 8px;vertical-align:middle}
.lrow.me{background:rgba(0,176,106,.12)}
.lrow.me td:first-child{box-shadow:inset 3px 0 0 var(--green)}
.mgr{display:flex;align-items:center;gap:8px}
.caret{color:var(--muted);font-size:11px;transition:transform .15s;display:inline-block;opacity:0}
.expandable .caret{opacity:.7}
.lrow.open .caret{transform:rotate(90deg);color:var(--green)}

/* Power bar */
.pwrap{display:flex;align-items:center;gap:8px;min-width:150px}
.pbar{flex:1;height:8px;border-radius:6px;background:var(--line);overflow:hidden;min-width:60px}
.pbar span{display:block;height:100%;border-radius:6px;
  background:linear-gradient(90deg,var(--pitch2),var(--green))}
.pval{font-weight:800;font-size:13px;min-width:24px;text-align:right}
.prank{font-size:10px;font-weight:800;color:var(--muted);background:var(--line);
  border-radius:20px;padding:1px 7px}

/* Expanded rival squad */
tr.detail{display:none}
tr.detail.open{display:table-row}
tr.detail>td{padding:0 8px 12px !important;border:none !important}
.rgrid{display:grid;grid-template-columns:2fr 1fr;gap:14px;
  background:var(--bg);border-radius:12px;padding:12px 14px;margin-top:2px}
@media(max-width:640px){.rgrid{grid-template-columns:1fr}}
.rsum{display:flex;gap:16px;padding:8px 12px;margin-top:2px;font-size:12px;color:var(--muted);
  background:var(--bg);border-radius:10px 10px 0 0;border-bottom:1px dashed var(--line)}
.rsum b{color:var(--green);font-size:15px;margin-left:3px}
.rgrid{border-radius:0 0 12px 12px}
.rhd{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);
  font-weight:800;margin-bottom:6px}
.rpl{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:13px;
  border-bottom:1px solid var(--line)}
.rpl.cptn{font-weight:800}
.rpos{font-size:9px;font-weight:800;color:#fff;border-radius:5px;padding:2px 5px;min-width:30px;text-align:center}
.rpos.p1{background:#f2a900}.rpos.p2{background:#2aa9e0}
.rpos.p3{background:#6b7a72}.rpos.p4{background:var(--magenta)}
.rname{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mini{font-style:normal;font-size:9px;font-weight:800;color:#fff;border-radius:50%;
  padding:1px 5px;margin-left:5px}
.mini.c{background:var(--magenta)}.mini.v{background:#7a2c8f}
.rpts{font-weight:800;font-variant-numeric:tabular-nums;min-width:20px;text-align:right}
.rpts::after{content:" pt";font-size:9px;color:var(--muted);font-weight:600}
.pform{font-size:12px;color:var(--amber);font-weight:800;font-variant-numeric:tabular-nums;
  min-width:30px;text-align:right}
.rsum-note{color:var(--muted);font-size:11px;margin-left:auto}
@media(max-width:560px){.rsum-note{display:none}}
@media(max-width:520px){.hide-sm{display:none}}
"""

LEAGUE_JS = """
document.querySelectorAll('.lrow.expandable').forEach(function(row){
  row.addEventListener('click',function(){
    var d=document.getElementById(row.dataset.team);
    if(!d)return;
    var open=d.classList.toggle('open');
    row.classList.toggle('open',open);
  });
});
"""


def _doc(title: str, body: str, css_extra: str = "", js: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title><style>{CSS}{css_extra}</style></head>
<body><div class="wrap">{body}</div><script>{js}</script></body></html>"""


def render_dashboard_html(d, headlines: list[Headline] | None = None,
                          full_document: bool = True) -> str:
    """Previous-GW tracker + upcoming-GW recommendation + transfer plan."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lg = (f"{d.league_name} #{d.league_rank}" if d.league_rank else "—")
    orank = f"{d.overall_rank:,}" if d.overall_rank else "—"
    last = d.last_gw

    # transfer plan
    if d.moves:
        rows = "".join(
            f'<div class="move"><span class="pill out">OUT {_esc(m.out.name)}</span>'
            f'<span class="pill in">IN {_esc(m.in_.name)}</span>'
            f'<span class="gain">+{m.gain:.1f}</span>'
            f'<div style="flex-basis:100%;font-size:12px;color:var(--muted)">'
            f'{_esc(m.reason)} · net £{m.cost_delta:+.1f}m</div></div>'
            for m in d.moves)
        transfers = rows
    else:
        transfers = '<p class="hold">✓ No transfer needed — hold your team.</p>'

    flagged = ("".join(f"<span>{_esc(p.name)} — {_esc(p.news or 'rotation risk')}</span>"
                       for p in d.flagged) or
               '<span style="background:none;color:var(--muted)">None flagged 👍</span>')
    opps = "".join(
        f'<tr><td>{_esc(p.name)}</td><td>{_esc(p.team_name)}</td>'
        f'<td class="n">£{p.cost_m:.1f}</td><td class="n">{p.projected:.1f}</td></tr>'
        for p in d.opportunities)
    histrows = "".join(
        f'<tr><td class="n">{h.gw}</td><td class="n">{h.points}</td>'
        f'<td>{_esc(h.captain)}</td><td class="n">{h.bench_points}</td>'
        f'<td class="n">{h.transfers}{"(-"+str(h.transfer_cost)+")" if h.transfer_cost else ""}</td>'
        f'<td class="n">{(f"{h.overall_rank:,}" if h.overall_rank else "—")}</td>'
        f'<td>{_esc(h.chip or "")}</td></tr>'
        for h in reversed(d.history))
    cap = (f'<b>{_esc(d.captain.name)}</b> ({_esc(d.captain.team_name)}) '
           f'· {d.captain.projected:.1f} proj' if d.captain else "—")
    vice = f'{_esc(d.vice.name)}' if d.vice else "—"
    news_html = (f'<div class="card"><h3>Team news</h3>{_news(headlines)}</div>'
                 if headlines else "")
    nav = ('<div class="btnrow"><a class="btn" href="./live.html">▶ Live scores</a>'
           '<a class="btn" href="./league.html">🏆 Varsical league</a></div>'
           if full_document else "")

    body = f"""
    <header class="hero">
      <div>
        <div class="gw">Gameweek {d.upcoming_gw} · Plan</div>
        <h1>{_esc(d.entry_name or 'My Team')}</h1>
        <div class="sub">{_esc(d.manager)} · updated {now}</div>
      </div>
      <div class="countdown">
        <div class="big tnum" id="cd" data-deadline="{_esc(d.deadline)}">—</div>
        <div class="lbl">to GW{d.upcoming_gw} deadline</div>
      </div>
    </header>
    <section class="stats">
      <div class="stat"><div class="k">Last GW</div><div class="v tnum">{last.points if last else '—'}</div></div>
      <div class="stat"><div class="k">Overall rank</div><div class="v tnum">{orank}</div></div>
      <div class="stat"><div class="k">Varsical</div><div class="v tnum">{('#'+str(d.league_rank)) if d.league_rank else '—'}</div></div>
      <div class="stat"><div class="k">In the bank</div><div class="v tnum">£{d.bank/10:.1f}m</div></div>
    </section>
    {nav}
    <div class="grid2">
      <div class="card"><h3>⭐ Recommended captain</h3>
        <p style="font-size:18px;margin:.2em 0">{cap}</p>
        <p class="muted">Vice: {vice}</p></div>
      <div class="card"><h3>🔁 Transfer plan (GW{d.upcoming_gw})</h3>{transfers}</div>
    </div>
    <div class="grid2">
      <div class="card"><h3>⚠️ Flagged in your squad</h3><div class="flag-list">{flagged}</div></div>
      <div class="card"><h3>💡 Opportunities (not owned)</h3>
        <table><tr><th>Player</th><th>Team</th><th class="n">£m</th><th class="n">Proj</th></tr>{opps}</table></div>
    </div>
    {news_html}
    <h2 class="sec">📅 Previous gameweeks</h2>
    <div class="card" style="overflow-x:auto">
      <table><tr><th class="n">GW</th><th class="n">Pts</th><th>Captain</th>
      <th class="n">Bench</th><th class="n">Transfers</th><th class="n">OR</th><th>Chip</th></tr>
      {histrows}</table></div>
    <footer><div>Built by the FPL Agent · <a href="https://github.com/Baldozz/fpl-agent">source</a></div>
    <div class="disc">Transfer suggestions use projected points and now-cost as sell price —
    confirm on the FPL site before committing.</div></footer>"""
    if not full_document:
        return body
    return _doc(f"FPL Plan — GW{d.upcoming_gw}", body, DASH_CSS, JS)


MY_ENTRY_ID = 8799067


def _formation_of(team) -> str:
    if not team:
        return "—"
    c = {2: 0, 3: 0, 4: 0}
    for p in team.xi:
        if p.pos in c:
            c[p.pos] += 1
    return f"{c[2]}-{c[3]}-{c[4]}"


def _rival_squad(team, players: dict | None) -> str:
    """Expanded view of one rival's team: XI grouped by position + bench.

    Each player shows live GW points and (when we have projections) the
    forward-looking projected score that feeds the squad-power rating.
    """
    def chip(p, bench=False):
        form = ""
        if players and p.element in players:
            form = (f'<span class="pform tnum">{players[p.element].form:.1f}</span>')
        role = (' cptn' if p.is_captain else ' vcptn' if p.is_vice else '')
        badge = ('<i class="mini c">C</i>' if p.is_captain
                 else '<i class="mini v">V</i>' if p.is_vice else '')
        live = p.points * (p.multiplier or 1) if not bench else p.points
        return (f'<div class="rpl{role}">'
                f'<span class="rpos p{p.pos}">{POS_ABBR[p.pos]}</span>'
                f'<span class="rname">{_esc(p.name)}{badge}</span>'
                f'<span class="rpts tnum">{live}</span>{form}</div>')
    xi = "".join(chip(p) for pos, _ in POS_ROWS
                 for p in sorted([q for q in team.xi if q.pos == pos],
                                 key=lambda z: -z.net_points))
    bench = "".join(chip(p, bench=True) for p in team.bench)
    summary = ""
    if players:
        xi_proj = sum(players[p.element].projected
                      for p in team.xi if p.element in players)
        xi_form = sum(players[p.element].form
                      for p in team.xi if p.element in players)
        summary = (f'<div class="rsum">'
                   f'<span>Squad power <b class="tnum">{xi_proj:.0f}</b></span>'
                   f'<span>XI form <b class="tnum">{xi_form:.0f}</b></span>'
                   f'<span class="rsum-note">numbers after each player = live GW pts · '
                   f'<span style="color:var(--amber)">form</span></span></div>')
    return (f'{summary}<div class="rgrid">'
            f'<div class="rcol"><div class="rhd">Starting XI</div>{xi}</div>'
            f'<div class="rcol"><div class="rhd">Bench</div>{bench}</div></div>')


def render_league_html(league, full_document: bool = True,
                       players: dict | None = None) -> str:
    """Varsical standings + squad-power rating + expandable rival squads."""
    rows_sorted = league.rows
    max_power = max((r.power for r in league.rows), default=0.0) or 1.0
    scored = [r for r in league.rows if r.power_rank]

    # Insight strip: strongest squad, biggest riser, your own standing.
    insight = ""
    if scored:
        strongest = min(scored, key=lambda r: r.power_rank)
        riser = max(scored, key=lambda r: r.power_delta)
        me = next((r for r in scored if r.entry_id == MY_ENTRY_ID), None)
        cards = [
            ('💪 Strongest squad', _esc(strongest.entry_name),
             f'power {strongest.power:.0f} · table #{strongest.rank}'),
        ]
        if riser.power_delta > 0:
            cards.append(('📈 Squad punching above table', _esc(riser.entry_name),
                          f'table #{riser.rank} but squad #{riser.power_rank}'))
        if me:
            verdict = ('squad stronger than rank' if me.power_delta > 0
                       else 'over-performing squad' if me.power_delta < 0
                       else 'squad matches rank')
            cards.append(('🎯 Your squad power',
                          f'#{me.power_rank} of {len(scored)}',
                          f'power {me.power:.0f} · {verdict}'))
        insight = ('<div class="insight">' + "".join(
            f'<div class="ins"><div class="ik">{k}</div>'
            f'<div class="iv">{v}</div><div class="im">{m}</div></div>'
            for k, v, m in cards) + '</div>')

    rows = ""
    for r in rows_sorted:
        cap = r.team.captain.name if (r.team and r.team.captain) else "—"
        chip = (r.team.active_chip if r.team and r.team.active_chip else "")
        me = ' me' if r.entry_id == MY_ENTRY_ID else ""
        mv = {"▲": "mv-up", "▼": "mv-dn", "=": "mv-eq"}[r.movement]
        pw = (r.power / max_power * 100) if r.power else 0
        prank = (f'<span class="prank">#{r.power_rank}</span>' if r.power_rank else "")
        power_cell = (
            f'<div class="pwrap"><div class="pbar"><span style="width:{pw:.0f}%"></span></div>'
            f'<span class="pval tnum">{r.power:.0f}</span>{prank}</div>'
            if r.power else '<span class="muted">—</span>')
        expandable = ' expandable' if r.team else ''
        rows += (
            f'<tr class="lrow{me}{expandable}" data-team="t{r.entry_id}">'
            f'<td class="n">{r.rank}</td>'
            f'<td class="{mv}">{r.movement}</td>'
            f'<td class="mgr"><span class="caret">▸</span>'
            f'<span><b>{_esc(r.entry_name)}</b><br>'
            f'<span class="muted" style="font-size:12px">{_esc(r.manager)}</span></span></td>'
            f'<td>{_esc(cap)}</td><td class="hide-sm">{_esc(_formation_of(r.team))}</td>'
            f'<td class="n">{r.gw_points}</td><td class="n"><b>{r.total}</b></td>'
            f'<td>{power_cell}</td></tr>')
        if r.team:
            rows += (f'<tr class="detail" id="t{r.entry_id}"><td colspan="8">'
                     f'{_rival_squad(r.team, players)}</td></tr>')

    nav = ('<div class="btnrow"><a class="btn" href="./index.html">← Dashboard</a>'
           '<a class="btn" href="./live.html">▶ My live team</a></div>'
           if full_document else "")
    body = f"""
    <header class="hero"><div>
      <div class="gw">Gameweek {league.gw} · League</div>
      <h1>{_esc(league.name)}</h1>
      <div class="sub">{len(league.rows)} managers · live scores + squad-power rating</div>
    </div></header>
    {nav}
    {insight}
    <p class="muted" style="margin:14px 0 6px;font-size:13px">
      <b>Squad Power</b> rates each manager's current 15 by projected points
      (form × fixtures × availability, bench counted at 15%) — a live read on who
      has the strongest team, independent of points banked.
      <b>Tap any row</b> to see that manager's full squad.</p>
    <div class="card" style="overflow-x:auto;padding:6px 8px">
      <table class="ltable"><thead><tr><th class="n">#</th><th></th><th>Team / Manager</th>
      <th>Captain</th><th class="hide-sm">Form.</th><th class="n">GW</th>
      <th class="n">Total</th><th>Squad Power</th></tr></thead><tbody>{rows}</tbody></table></div>
    <footer><div>Live from the FPL API · <a href="https://github.com/Baldozz/fpl-agent">source</a></div>
    <div class="disc">Squad Power is a model projection, not FPL's own score;
      it updates each time the agent runs.</div></footer>"""
    if not full_document:
        return body
    return _doc(f"{league.name} — GW{league.gw}", body, DASH_CSS + LEAGUE_CSS, LEAGUE_JS)


def render_live_html(team, gw: int, deadline: str,
                     headlines: list[Headline], full_document: bool = True,
                     available_gws: list[int] | None = None,
                     embed: bool = False) -> str:
    """Render the manager's ACTUAL team with LIVE gameweek scores (FPL layout).

    ``embed=True`` returns just the inner content (no page chrome / own dropdown)
    for composing into the unified single-page site.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def line(pos):
        cards = "".join(_live_card(p)
                        for p in sorted([q for q in team.xi if q.pos == pos],
                                        key=lambda z: -z.net_points))
        return f'<div class="frow">{cards}</div>'
    pitch = f'<div class="fpitch">{"".join(line(pos) for pos,_ in POS_ROWS)}</div>'
    bench = "".join(_live_card(p) for p in team.bench)
    played = sum(1 for p in team.xi if p.started_fixture)
    chip = f' · chip: {team.active_chip}' if team.active_chip else ""
    rank = f"{team.overall_rank:,}" if team.overall_rank else "—"

    gwsel = ""
    if available_gws and not embed:
        opts = "".join(
            f'<option value="live-gw{g}.html"{" selected" if g == gw else ""}>'
            f'Gameweek {g}</option>' for g in available_gws)
        gwsel = (f'<div class="gwbar"><label for="gw">View gameweek:</label>'
                 f'<select id="gw" class="gwsel" '
                 f'onchange="if(this.value)location.href=this.value">{opts}</select>'
                 f'<a class="btn" href="./index.html">Dashboard</a>'
                 f'<a class="btn" href="./league.html">League</a></div>')

    content = f"""
    <header class="hero">
      <div>
        <div class="gw">Gameweek {gw} · Points</div>
        <h1>{_esc(team.entry_name or 'My Team')}</h1>
        <div class="sub">{_esc(team.manager_name)} · updated {now}{chip}</div>
      </div>
      <div class="countdown">
        <div class="big tnum">{team.total_points}</div>
        <div class="lbl">GW{gw} points</div>
      </div>
    </header>
    {gwsel}

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
    <div class="benchbar">{bench}</div>
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
    if embed:
        return content
    css_extra = LIVE_CSS + DASH_CSS
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


SITE_CSS = """
/* Modern sticky app bar with segmented tab control */
.topbar{position:sticky;top:0;z-index:20;
  background:color-mix(in srgb,var(--bg) 82%,transparent);
  backdrop-filter:saturate(1.4) blur(12px);-webkit-backdrop-filter:saturate(1.4) blur(12px);
  border-bottom:1px solid var(--line)}
.topbar-in{max-width:1040px;margin:0 auto;padding:10px 20px;display:flex;
  align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:9px;font-weight:900;letter-spacing:-.02em;font-size:16px}
.brand .dot{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--green),var(--pitch2));color:#fff;font-size:14px;
  box-shadow:0 3px 8px rgba(0,176,106,.4)}
.tabs{display:flex;gap:3px;margin-left:auto;background:var(--panel);
  border:1px solid var(--line);border-radius:12px;padding:3px}
.tab{padding:8px 16px;font:inherit;font-weight:700;cursor:pointer;border:none;
  background:none;color:var(--muted);border-radius:9px;font-size:14px;transition:all .14s}
.tab:hover{color:var(--ink)}
.tab.active{color:#fff;background:linear-gradient(135deg,var(--green),var(--pitch2));
  box-shadow:0 2px 8px rgba(0,176,106,.35)}
@media(max-width:560px){.tabs{width:100%;margin-left:0}.tab{flex:1;text-align:center;padding:8px 6px}
  .tab .lbl{display:none}}
.pane{display:none} .pane.active{display:block;animation:fade .25s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.gwpane{display:none} .gwpane.active{display:block}
.pane>.hero:first-child{margin-top:4px}
/* Softer, more modern cards + stat tiles across the whole site */
.card{box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -18px rgba(0,0,0,.5)}
.stat{box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -18px rgba(0,0,0,.5);
  transition:transform .12s}
.stat:hover{transform:translateY(-2px)}
header.hero{box-shadow:0 12px 34px -14px rgba(0,0,0,.5)}
"""

SITE_JS = """
function showTab(id){
  document.querySelectorAll('.pane').forEach(function(p){p.classList.toggle('active',p.id===id);});
  document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('active',t.dataset.pane===id);});
  if(history.replaceState)history.replaceState(null,'',location.pathname+'#'+id.replace('tab-',''));
}
document.querySelectorAll('.tab').forEach(function(t){t.onclick=function(){showTab(t.dataset.pane);};});
var gsel=document.getElementById('gwsel');
if(gsel)gsel.onchange=function(){
  document.querySelectorAll('.gwpane').forEach(function(p){p.classList.toggle('active',p.dataset.gw===gsel.value);});
};
(function(){var h=(location.hash||'').slice(1);var id='tab-'+h;
  if(h&&document.getElementById(id))showTab(id);})();
"""


def render_site(d, live_by_gw: dict, league, headlines, available_gws: list[int],
                current_gw: int, deadline: str, players: dict | None = None) -> str:
    """One tabbed page: Dashboard | My Team (live, GW switcher) | League."""
    dash = render_dashboard_html(d, headlines, full_document=False)
    lg = render_league_html(league, full_document=False, players=players)

    gws = sorted(available_gws)
    opts = "".join(f'<option value="{g}"{" selected" if g==current_gw else ""}>'
                   f'Gameweek {g}</option>' for g in gws)
    panes = ""
    for g in gws:
        team = live_by_gw.get(g)
        if not team:
            continue
        inner = render_live_html(team, g, deadline, [], embed=True)
        active = " active" if g == current_gw else ""
        panes += f'<div class="gwpane{active}" data-gw="{g}">{inner}</div>'
    team_tab = (f'<div class="gwbar"><label for="gwsel">View gameweek:</label>'
                f'<select id="gwsel" class="gwsel">{opts}</select></div>{panes}')

    topbar = (
        '<div class="topbar"><div class="topbar-in">'
        '<div class="brand"><span class="dot">⚽</span>'
        f'<span>FPL Agent<span class="muted" style="font-weight:600"> · '
        f'{_esc(d.entry_name or "My Team")}</span></span></div>'
        '<div class="tabs">'
        '<button class="tab active" data-pane="tab-dash">📋<span class="lbl"> Dashboard</span></button>'
        '<button class="tab" data-pane="tab-team">⚽<span class="lbl"> My Team</span></button>'
        '<button class="tab" data-pane="tab-league">🏆<span class="lbl"> Varsical</span></button>'
        '</div></div></div>')
    body = (
        f'{topbar}<div class="wrap">'
        f'<div id="tab-dash" class="pane active">{dash}</div>'
        f'<div id="tab-team" class="pane">{team_tab}</div>'
        f'<div id="tab-league" class="pane">{lg}</div>'
        f'</div>')
    css = CSS + DASH_CSS + LIVE_CSS + LEAGUE_CSS + SITE_CSS
    js = JS + SITE_JS + LEAGUE_JS
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Agent — {_esc(d.entry_name or 'My Team')}</title>
<style>{css}</style></head>
<body>{body}<script>{js}</script></body></html>"""


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
