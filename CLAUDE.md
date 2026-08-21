# CLAUDE.md — context for AI agents working on this repo

This file orients Claude Code (or any AI agent) working in this repository.

## What this project is

An **advisory** Fantasy Premier League agent. It ingests free FPL data + free
football-news RSS, projects points, and outputs a rules-legal squad with
captain/bench recommendations as a Markdown report per gameweek. It does **not**
authenticate to a user's FPL account or submit teams — recommendations are
entered manually by the human. Keep it that way unless the user explicitly asks
for account automation (which needs their credentials and carries account risk).

## The competition rules encoded here

- Squad of 15: **2 GK, 5 DEF, 5 MID, 3 FWD**.
- Budget **£100.0m** (`now_cost` is in tenths of a million: `60` = £6.0m).
- **Max 3 players from any one club.**
- Starting XI of 11: exactly 1 GK; 3–5 DEF; 2–5 MID; 1–3 FWD.
- Captain scores **2×**; if the captain doesn't play, the vice-captain does.
- Bench players auto-sub in (in listed order) for starters who don't play.
- These live in `fpl_agent/optimizer.py` as hard constraints — change carefully.

## Architecture

| File | Responsibility |
|------|----------------|
| `api.py` | Fetch `bootstrap-static` and `fixtures` from the public FPL API; 1-hour on-disk cache in `data/`. No auth. |
| `model.py` | `Player` dataclass + `score_players()` — expected points from form/fixtures/team-strength/start-probability. |
| `optimizer.py` | ILP (PuLP/CBC): maximise XI points, minimise bench cost; greedy fallback; XI/captain/bench. |
| `news.py` | Free Premier League RSS feeds → headlines relevant to the squad / injuries. |
| `grok.py` | xAI Grok over X/Twitter → start-probability signals (optional; needs `XAI_API_KEY` + credits). |
| `overrides.py` + `overrides.json` | Human/Grok start-probability overrides the FPL API lacks. |
| `report.py` / `html_report.py` | Markdown report / standalone HTML page (GitHub Pages). |
| `notify.py` | Free WhatsApp deadline reminder via CallMeBot. |
| `cli.py` / `__main__.py` | `python -m fpl_agent` entry point. |

## The optimisation philosophy (important)

You score the **starting XI (11), not the squad (15)** each week. So the ILP:
1. maximises XI projected points (dominant term);
2. **minimises bench cost** — the four bench slots (1 GK + 3 outfield) should be
   as cheap as possible so budget concentrates in the XI. This is the
   points-per-million effect: freed money upgrades the XI, so a £7m pick that
   scores like a £5m pick loses to the £5m pick;
3. keeps bench players playable (small `start_prob` reward) so they're real
   injury/rotation cover.
Weights in `optimize_ilp` are tiny vs XI points, so (2)/(3) only break ties
between XI-optimal squads — never sacrifice XI points. If you change them, keep
that ordering.

## Predicting starts (the crux of FPL)

`Player.start_prob` (0..1) gates a player's projection (an injured/benched
player scores ~0). It comes from, in order of precedence: a human/Grok override
in `overrides.json`, else a last-season minutes/starts model
(`_minutes_security`). Add knowledge the API lacks — unannounced injuries,
World-Cup-return rotation, nailed new signings — to `overrides.json` (or let
Grok populate it).

## Secrets

`XAI_API_KEY` (and any secret) lives in a **gitignored `.env`** or the
environment, and as a GitHub Actions secret for CI. **Never commit keys.**
`_load_dotenv()` in `cli.py` loads `.env` for local runs. Grok degrades to a
no-op (overrides + RSS only) when the key is missing or has no credits, or if
xAI's live-search endpoint is unavailable — keep that graceful fallback.

## The scoring model (where to tune)

`model.score_players()` computes `projected` per player:

- Base = weighted blend of `ep_next`, `points_per_game`, and live `form`
  (form only weighted once `season_started`).
- `_fdr_multiplier(fdr)` scales by fixture difficulty (1 easy … 5 hard).
- `_availability_mult(status, chance)` zeroes injured/suspended players and
  scales "doubtful" ones by the published % chance of playing.
- `attach_fixtures()` sets each player's average FDR and fixture count over the
  chosen `--horizon` (double-gameweek aware via `n_fix`).

To improve results, the highest-leverage changes are: better minutes/rotation
modelling, expected goals/assists (xG/xA) inputs, and set-piece/penalty duties.

## Conventions

- Standard library + `requests`, `pulp`, `feedparser` only. Keep dependencies
  minimal and everything **free / no API key**.
- Prices are integers in tenths — divide by 10 for display (`Player.cost_m`).
- Element types: 1=GK, 2=DEF, 3=MID, 4=FWD.
- Player availability `status`: `a` available, `d` doubtful, `i` injured,
  `s` suspended, `u`/`n` unavailable.

## Running & verifying

```bash
python3 -m pip install -r requirements.txt
python3 -m fpl_agent --save            # writes reports/GW<n>.md
```
A valid run prints a squad costing ≤ £100.0m with 2/5/5/3 and ≤3 per club.
If you change the optimizer, verify those invariants still hold.

## Weekly / automation notes

- Reports are committed to `reports/` so the repo is a running audit trail.
- To automate: schedule `python -m fpl_agent --horizon 3 --save` a day before
  each deadline (cron or GitHub Actions) and commit the new report. The FPL API
  needs no auth, so this runs unattended.
- Deadlines come from the API (`events[].deadline_time`); `current_and_next_event`
  picks the right gameweek automatically.

## Guardrails

- Do not add code that logs into FPL or submits a team without explicit user
  consent and their credentials.
- Keep the "advisory only / enter it yourself" disclaimer in generated reports.
- Don't hardcode a gameweek — always resolve the next deadline from the API.
