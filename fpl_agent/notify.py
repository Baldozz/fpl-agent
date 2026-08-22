"""Free WhatsApp alerts (CallMeBot): pre-deadline plan + between-GW injury watch.

Modes (``python -m fpl_agent.notify --mode ...``):
  deadline  — ~2h before the deadline, send the recommended captain + transfer
              plan for the upcoming gameweek. A 1h-wide window means an hourly
              cron fires it exactly once, no state needed.
  monitor   — between gameweeks, watch YOUR players; if a new injury / doubt /
              benching appears, send a transfer suggestion. Deduped via a small
              committed state file so you're not spammed.

One-time setup (CallMeBot, free, no account) — see the WhatsApp section of the
README. Set env / GitHub secrets WHATSAPP_PHONE and WHATSAPP_APIKEY; without them
this no-ops safely. Team/league ids come from FPL_TEAM_ID / FPL_LEAGUE_ID /
~/.fpl-mcp/config.json.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import agent, api, live
from .agent import whatsapp_summary

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "alerts.json"
# Public dashboard URL appended to alerts. Override with FPL_PAGE_URL; set to
# "" if the repo/Pages ever go private again (Free-plan Pages needs a public repo).
PAGE_URL = os.environ.get("FPL_PAGE_URL", "https://baldozz.github.io/fpl-agent/")
WINDOW_LO = float(os.environ.get("FPL_DEADLINE_WINDOW_LO", "1.0"))   # hours
WINDOW_HI = float(os.environ.get("FPL_DEADLINE_WINDOW_HI", "2.0"))   # hours


def send_whatsapp(message: str) -> bool:
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    apikey = os.environ.get("WHATSAPP_APIKEY", "").strip()
    if not phone or not apikey:
        print("[notify] WHATSAPP_PHONE / WHATSAPP_APIKEY not set — would send:\n"
              f"        {message}")
        return False
    url = ("https://api.callmebot.com/whatsapp.php?"
           + urllib.parse.urlencode({"phone": phone, "text": message,
                                     "apikey": apikey}))
    r = requests.get(url, timeout=30)
    print(f"[notify] CallMeBot status={r.status_code}: {r.text[:120]}")
    return r.status_code == 200


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _build(use_grok: bool):
    boot = api.bootstrap()
    cur, nxt = api.current_and_next_event(boot)
    if nxt is None:
        return None, None
    gw = nxt["id"]
    season_started = any(e.get("finished") for e in boot["events"])
    players, *_ = agent.prepare_players(boot, gw, season_started, use_grok=use_grok)
    tid = live.resolve_team_id()
    if not tid:
        print("[notify] no team id configured.")
        return None, None
    lid = os.environ.get("FPL_LEAGUE_ID")
    lid = int(lid) if lid and lid.isdigit() else None
    d = agent.build_digest(tid, boot, players, (cur or nxt)["id"], gw,
                           nxt["deadline_time"], lid)
    return d, nxt


def _hours_to(deadline: str) -> float:
    dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    return (dl - datetime.now(timezone.utc)).total_seconds() / 3600.0


def deadline_mode() -> int:
    # Cheap check first: only build the (Grok-powered) digest when we're actually
    # inside the alert window, so hourly crons don't call Grok every run.
    boot = api.bootstrap()
    _, nxt = api.current_and_next_event(boot)
    if nxt is None:
        return 0
    hours = _hours_to(nxt["deadline_time"])
    if not (WINDOW_LO <= hours <= WINDOW_HI):
        print(f"[notify] deadline {hours:.1f}h away (outside "
              f"{WINDOW_LO}-{WINDOW_HI}h window) — not sending.")
        return 0
    d, nxt = _build(use_grok=True)
    if d is None:
        return 0
    msg = whatsapp_summary(d) + (f" | Full plan: {PAGE_URL}" if PAGE_URL else "")
    send_whatsapp(msg)
    return 0


def monitor_mode() -> int:
    d, nxt = _build(use_grok=False)
    if d is None:
        return 0
    state = _load_state()
    if state.get("gw") != d.upcoming_gw:            # new GW -> reset dedupe
        state = {"gw": d.upcoming_gw, "flagged": []}
    already = set(state.get("flagged", []))
    newly = [p for p in d.flagged if p.name not in already]
    if not newly:
        print("[notify] no new injuries/doubts in your squad.")
        return 0
    names = ", ".join(f"{p.name} ({p.news or 'doubt'})" for p in newly)
    msg = (f"⚠️ FPL alert — {names}. {whatsapp_summary(d)}"
           + (f" | {PAGE_URL}" if PAGE_URL else ""))
    send_whatsapp(msg)
    state["flagged"] = [p.name for p in d.flagged]
    _save_state(state)
    return 0


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(prog="fpl_agent.notify")
    ap.add_argument("--mode", choices=["deadline", "monitor"], default="deadline")
    args = ap.parse_args(argv)
    return deadline_mode() if args.mode == "deadline" else monitor_mode()


if __name__ == "__main__":
    raise SystemExit(main())
