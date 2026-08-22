# ⚽ FPL Agent

An autonomous **Fantasy Premier League** advisor. It pulls live data from the
**free, public FPL API**, builds a rules-legal 15-man squad that maximises
projected points, picks your **captain, vice-captain and bench order**, cross-
references **free football news (RSS)** for injuries and team news, and writes a
Markdown report for every gameweek into [`reports/`](reports/).

> ⚠️ **Advisory tool only.** It does *not* log into your FPL account or submit a
> team for you. It produces a recommendation that **you enter yourself** on the
> FPL site before the deadline. Not affiliated with the Premier League or FPL.

---

## What it does

- **Reads the competition rules** and encodes them as hard constraints:
  - 15 players: **2 GK, 5 DEF, 5 MID, 3 FWD**
  - **£100.0m** budget
  - **Max 3 players per club**
  - Valid starting XI (1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD)
- **Projects points** for every player by blending:
  - FPL's own `ep_next` expected-points figure
  - Last-season **points-per-game** and total points
  - **Live form** (weighted in progressively once the season is under way)
  - **Fixture difficulty** over a configurable horizon (double-gameweek aware)
  - **Own-team strength** — an Arsenal defender keeps more clean sheets than a
    promoted-side defender, so team quality boosts CS (GK/DEF) and attack (MID/FWD)
  - **Fixture mismatch** — the strength *gap* between the two teams in a fixture,
    so in a top-vs-bottom game the stronger side's players are decisively favoured
    (tunable via `MISMATCH_WEIGHT`; stacks with FDR for a ~3× top/bottom swing)
  - **Probability of actually starting** — a last-season minutes/starts model,
    because the game is about picking players who *play*. This is overridden by
    injury news and by human/Grok signals (see below).
- **Optimises for the XI, not the 15.** Integer linear programming (PuLP/CBC)
  maximises the **starting XI's** projected points. It then shapes the squad the
  way good managers do:
  - **Bench = cheapest cover that will actually play.** A £4.0m reserve keeper
    (your #1 is nailed, so he never plays) and cheap *playing* defenders make
    real cover; a £4.5m forward never starts, so it's dead money.
  - Plays an **attacking 3-4-3 by default** (more forwards = more goal-scorers)
    with a bench of cheap playing defenders/mids. Change it with
    `--formation 4-3-3` (or `free` to let the optimiser choose; `DEFAULT_FORMATION`
    / `ATTACK_CEILING` in `optimizer.py`).
  - **Bench budget is hard-capped** (`MAX_BENCH_COST`, default £16.5m — the
    rules floor) so the bench is the cheapest legal fodder (a 15-man squad always
    has 4 bench players at £4.0-4.5m each, so ~£16.5m is the minimum possible),
    keeping every spare pound in the XI.
  - **Force players in** with `--include "Haaland, Salah"` or the `must_include`
    list in `overrides.json` — the ILP builds the best legal squad around them.
  - **Points-per-million:** an overpriced pick is swapped for a cheaper
    equal-scorer and the saving upgrades the XI (`Pts/£m` shown per player).
  - Greedy fallback if no solver is installed.
- **Recommends captain (2×), vice-captain and bench order.**
- **Pulls free football news** (BBC Sport, The Guardian, Sky Sports RSS) and
  surfaces headlines relevant to your squad or general injury/team-news.
- **Publishes a report** per gameweek to `reports/GW<n>.md`, committed to GitHub
  so there's a running history of every recommendation.

## 🗂️ One unified page (`--site` → `docs/index.html`)

Everything lives on a **single tabbed page** at
**https://baldozz.github.io/fpl-agent/**:

- **📋 Dashboard** — previous-GW **tracker** (points, rank, captain, bench, chips)
  + **upcoming-GW recommendation**: captain, **transfer plan** (out→in with point
  gain), flagged players, and opportunities you don't own.
- **⚽ My Team** — your actual team with **live GW scores** in the FPL layout
  (kit shirts on the pitch) and a **gameweek dropdown** to switch weeks.
- **🏆 Varsical** — league standings (id `1739086`) with every rival's captain,
  formation and points; your row highlighted.

```bash
python3 -m fpl_agent --site --html          # the unified page (default deliverable)
# standalone variants also exist: --dashboard, --live, --league
```

## 🔁 Transfer recommender

The agent works from the team you **actually own** (`fpl_agent/transfers.py` +
`agent.py`): it compares your 15 to the model's projections for the upcoming GW
and suggests the best transfer(s) within budget and the max-3-per-club rule,
prioritising **flagged** players (injury/rotation) and then the biggest upgrade.
Between gameweeks it watches your squad and alerts you when a player is newly
injured or a strong opportunity appears.

## ⏰ Deadline alert & injury watch (WhatsApp)

- **~2h before every deadline** you get a WhatsApp with the recommended captain +
  transfer plan (`notify.py --mode deadline`; an hourly cron fires it once inside
  a 1h window).
- **Between gameweeks**, a new injury/doubt in your squad triggers a transfer
  suggestion (`notify.py --mode monitor`, deduped via `state/alerts.json`).

Both need the free CallMeBot setup (WhatsApp section below) — set `WHATSAPP_PHONE`
+ `WHATSAPP_APIKEY`; without them they no-op safely.

## 📄 Live team page

Every run can render a standalone HTML page — your starting XI laid out on a
pitch, captain/vice, bench and team news — to `docs/index.html`. When GitHub
Pages is enabled (Settings → Pages → Deploy from branch → `main` / `/docs`) it
publishes at:

**https://baldozz.github.io/fpl-agent/**

During the season the page shows your **actual entered team with LIVE gameweek
scores** (real captain, live points, rank), refreshed by the Action on match
days. Before deadlines the agent also produces the *recommendation* for the
upcoming GW (markdown in `reports/`).

```bash
python3 -m fpl_agent --html                 # recommendation page (pre-deadline)
python3 -m fpl_agent --live --html          # YOUR real team + live scores
python3 -m fpl_agent --live --team-id 8799067   # explicit team id
```

The **recommendation** (`--html`) is a forecast/pick; the **live** view
(`--live`) reads your real squad and live points from the FPL API — use that
during a gameweek so the page matches the official FPL site (captain, scores).

## 🔔 WhatsApp deadline reminder (free)

Get a WhatsApp nudge with a link to your team page before every deadline, using
the free **CallMeBot** service (no account, no cost). One-time setup:

1. Add **+34 644 51 95 23** to your contacts.
2. WhatsApp it: `I allow callmebot to send me messages` → you receive an API key.
3. Set env vars (locally) or repo **secrets** `WHATSAPP_PHONE` + `WHATSAPP_APIKEY`
   (for the GitHub Action):

   ```bash
   export WHATSAPP_PHONE="+447700900123"
   export WHATSAPP_APIKEY="123456"
   python3 -m fpl_agent.notify        # sends only if a deadline is <30h away
   ```

The Action already calls this each run; it silently no-ops until the secrets are
set. Prefer a calendar alarm instead? The deadline is in every report and on the
page's live countdown.

## Predicting who actually starts

The hardest, most valuable part of FPL is knowing **who will play** — and the
API doesn't always know (a knock the club hasn't announced, a player back late
from a deep World Cup run who'll be rotated, a nailed-on new signing). Two
mechanisms handle this:

1. **`overrides.json`** — a human-editable file of start-probability signals,
   matched by player name. `start_prob`: `0` = won't play, `0.5` = rotation
   risk / 50-50, `1` = nailed. Example already in the file: Mukiele ruled out,
   Guéhi flagged as a World-Cup-return rotation risk.
2. **Free RSS (BBC/Guardian)** — headline **titles** are parsed for injury/
   availability phrases ("ruled out", "a major doubt") and, when they name one of
   your players, lower that player's start probability so he drops out of the
   squad. Negative-only and title-only for precision; lowest precedence (Grok and
   manual overrides always win).
3. **Grok (xAI) over live X/Twitter** — most team news breaks on X first. With an
   `XAI_API_KEY` that has credits, the agent calls xAI's **Responses API** with
   the server-side **`x_search`** tool, so Grok reads *live* X posts (last few
   days) for predicted line-ups / rotation / injuries and returns
   start-probability signals + headlines, merged on top of the file
   automatically. Set the key via a **gitignored `.env`** or the `XAI_API_KEY`
   env var — it is **never committed**. Without credits the agent falls back to
   `overrides.json` + the free RSS feeds, so it always runs.

```bash
echo 'XAI_API_KEY=xai-...' >> .env      # gitignored; or export XAI_API_KEY=...
python3 -m fpl_agent                     # Grok used automatically when available
python3 -m fpl_agent --no-grok           # force-skip Grok
```

## Live MCP companion (interactive, reads your real team)

The automated agent builds a squad from scratch and publishes the page/reports.
For **interactive, in-chat** analysis — and to work from the team you *actually*
own — this repo is wired to the community
[fantasy-pl-mcp](https://github.com/rishijatia/fantasy-pl-mcp) server
(`.mcp.json`). It gives the assistant 23 live FPL tools: `get_my_current_team`,
`get_manager_transfer_history`, `suggest_captain`, `compare_players`,
`analyze_fixtures`, `get_double_gameweeks`, `get_league_standings`, and more.

Setup:

```bash
pip install fpl-mcp          # already installed
fpl-mcp-config setup        # one-time: paste your FPL refresh token (from the
                            # browser console) — enables your-team tools.
                            # Stored encrypted in ~/.fpl-mcp/, never committed.
```

Then **restart Claude Code** (or approve the `fantasy-pl` server when prompted)
so the tools load. The `.mcp.json` command uses this machine's Python path —
adjust it if you run elsewhere. The token is optional: without it the public
data/analysis tools still work; with it, the assistant can read your live squad,
bank and transfers to give transfer/captain advice grounded in your real team.

This is complementary to the automated agent, not a replacement — the agent still
runs unattended (page, reports, WhatsApp) with no login.

## Data sources (all free)

| Source | Used for |
|--------|----------|
| `fantasy.premierleague.com/api/bootstrap-static/` | players, prices, form, minutes/starts, team strength, availability |
| `fantasy.premierleague.com/api/fixtures/` | fixtures & fixture difficulty (FDR) |
| BBC Sport / Guardian (Premier League) RSS | injuries, team news, predicted line-ups |
| xAI Grok live search over X/Twitter *(optional, needs credits)* | predicted line-ups, rotation, breaking injuries |
| `overrides.json` | human/Grok start-probability signals the API lacks |

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Usage

```bash
# Recommend a team for the next deadline (per-gameweek projections)
python3 -m fpl_agent

# Weight fixtures across the next 3 gameweeks (good for squad-building)
python3 -m fpl_agent --horizon 3

# Save the report to reports/GW<n>.md
python3 -m fpl_agent --save

# Skip the RSS news fetch (faster / offline)
python3 -m fpl_agent --no-news

# Force a fresh pull instead of the 1-hour on-disk cache
python3 -m fpl_agent --no-cache
```

## Weekly workflow

1. A day or two before the deadline, run `python3 -m fpl_agent --horizon 3 --save`.
2. Read `reports/GW<n>.md`: the XI, captain, bench and relevant team news.
3. Enter (or adjust to) the team on the FPL website before the deadline.
4. Commit the report — the repo keeps a history of every week's call.

This can be automated with a scheduler (cron / GitHub Actions) — see
[`CLAUDE.md`](CLAUDE.md) for the automation notes.

## Project layout

```
fpl_agent/
  api.py         # free FPL API client (+ on-disk cache)
  model.py       # per-player expected-points model (form, fixtures, team, minutes)
  optimizer.py   # ILP: maximise XI points, minimise bench cost
  news.py        # free Premier League RSS feeds -> team-news signals
  grok.py        # xAI Grok over X/Twitter -> start-probability signals (optional)
  overrides.py   # load human/Grok start-prob overrides
  report.py      # Markdown report renderer
  html_report.py # standalone HTML 'team sheet on a pitch' (GitHub Pages)
  notify.py      # free WhatsApp deadline reminder (CallMeBot)
  cli.py         # `python -m fpl_agent` entry point
overrides.json   # human/Grok start-probability signals (committed, editable)
reports/         # generated per-gameweek recommendations (committed)
docs/            # generated HTML page served by GitHub Pages (committed)
data/            # cached API payloads (gitignored)
.env             # XAI_API_KEY etc. (gitignored, never committed)
```

## How the scoring works (short version)

For each player, a per-match expectation is built from `ep_next`, last-season
points-per-game, and (once available) live form. That is multiplied by a
**fixture-difficulty factor** (easy fixtures boosted, hard ones penalised) and an
**availability factor** (injured/suspended → 0, doubtful scaled by the published
"% chance of playing"). The ILP then maximises the total projected points of the
starting XI subject to all the FPL rules. See `fpl_agent/model.py` for the exact
weights — they are deliberately easy to tune.

## Limitations & honesty

- Projections are heuristic, not a betting model; treat them as a strong first
  draft you sanity-check against the news section.
- At the **season opener** there is no live form, so picks lean on last-season
  output and FPL's `ep_next`. The model automatically shifts weight to live form
  as gameweeks are played.
- No account automation: submission is manual by design.

## Licence

MIT — see [`LICENSE`](LICENSE).
