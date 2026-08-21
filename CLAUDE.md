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
| `grok.py` | xAI **Responses API** (`/v1/responses`) with server-side `x_search` → live-X start-probability signals + headlines. Optional; needs `XAI_API_KEY` + credits. NB: the old chat-completions `search_parameters` live-search is deprecated (410); `_extract_message` reads the `output[]` item whose `type=="message"`. |
| `overrides.py` + `overrides.json` | Human/Grok start-probability overrides the FPL API lacks. |
| `report.py` / `html_report.py` | Markdown report / standalone HTML page (GitHub Pages). |
| `notify.py` | Free WhatsApp deadline reminder via CallMeBot. |
| `cli.py` / `__main__.py` | `python -m fpl_agent` entry point. |

## The optimisation philosophy (important)

You score the **starting XI (11), not the squad (15)** each week. The ILP
objective (`optimize_ilp`) is: XI projected points, plus an attacking-ceiling
bonus for started forwards (`ATTACK_CEILING`), plus a strong reward for bench
players who will actually PLAY (`cover = max(0, start_prob-0.5)`), minus a tiny
bench-cost term.

Why this shape (all from the user's FPL strategy):
- **Bench must be cheap AND playing.** Cheap *playing* cover exists for
  defenders (£4.0-4.5m starters at weaker clubs) and the reserve keeper (your #1
  is nailed so the bench GK never plays — £4.0m is correct). But a £4.5m
  midfielder/forward never starts — dead cover. So rewarding real cover pushes
  the bench toward cheap playing defenders/mids.
- That in turn favours an **attacking XI** (more forwards started = more
  goal-scorers), since you don't want to carry your 3 required forwards as
  non-playing bench fodder. `ATTACK_CEILING` (default 1.0) tips genuine near-ties
  to a 3-forward shape (4-3-3 / 3-4-3); raise it for more attack, set 0 for pure
  mean-points.
- **Points-per-million**: freed money upgrades the XI (`Player.value`).

**Critical wiring:** `optimize_ilp` returns BOTH the squad and the XI (from the
`s` starter variables). `build_squad` must use that XI — do NOT re-pick the XI
by raw points afterwards, or the cover/attacking logic (and thus the formation)
is silently discarded. `_pick_xi` is only for the greedy fallback.

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
