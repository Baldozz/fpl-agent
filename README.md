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
  - **Availability**: injuries / suspensions / "% chance of playing"
- **Optimises the squad** with integer linear programming (PuLP/CBC), so the
  chosen XI is provably the highest-projected legal team for the budget. Falls
  back to a greedy heuristic if no solver is installed.
- **Recommends captain (2×), vice-captain and bench order.**
- **Pulls free football news** (BBC Sport, The Guardian, Sky Sports RSS) and
  surfaces headlines relevant to your squad or general injury/team-news.
- **Publishes a report** per gameweek to `reports/GW<n>.md`, committed to GitHub
  so there's a running history of every recommendation.

## 📄 Live team page

Every run can render a standalone HTML page — your starting XI laid out on a
pitch, captain/vice, bench and team news — to `docs/index.html`. When GitHub
Pages is enabled (Settings → Pages → Deploy from branch → `main` / `/docs`) it
publishes at:

**https://baldozz.github.io/fpl-agent/**

The weekly GitHub Action regenerates it before each deadline, so the page always
shows the current gameweek's recommendation.

```bash
python3 -m fpl_agent --html        # writes docs/index.html + docs/GW<n>.html
```

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

## Data sources (all free, no API keys)

| Source | Used for |
|--------|----------|
| `fantasy.premierleague.com/api/bootstrap-static/` | players, prices, form, availability |
| `fantasy.premierleague.com/api/fixtures/` | fixtures & fixture difficulty (FDR) |
| BBC Sport / Guardian / Sky Sports RSS | injuries, team news, predicted line-ups |

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
  model.py       # per-player expected-points model
  optimizer.py   # ILP squad selection + XI / captain / bench
  news.py        # free RSS feeds -> injury / team-news signals
  report.py      # Markdown report renderer
  cli.py         # `python -m fpl_agent` entry point
reports/         # generated per-gameweek recommendations (committed)
data/            # cached API payloads (gitignored)
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
