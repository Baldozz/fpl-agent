"""Fetch the manager's ACTUAL team and LIVE gameweek scores.

Distinct from the pre-deadline recommendation (model projections): this reads
the real squad you entered on FPL and the points each player is scoring in the
current gameweek, so the page can show your live scoreboard rather than a
forecast. Entry picks and live scores are public once the gameweek has started —
no auth needed here.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-agent)"}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass
class LivePick:
    name: str
    team_name: str
    team_code: int
    pos: int
    cost: int
    slot: int            # 1..15 (1..11 = starting XI, 12..15 = bench order)
    multiplier: int      # 0 bench, 1 normal, 2 captain, 3 triple-captain
    is_captain: bool
    is_vice: bool
    points: int          # live GW points (before multiplier)
    minutes: int
    started_fixture: bool  # has the player's team match kicked off / finished
    element: int = 0       # FPL element id (to join against scored projections)

    @property
    def cost_m(self) -> float:
        return self.cost / 10.0

    @property
    def pos_name(self) -> str:
        return POS[self.pos]

    @property
    def net_points(self) -> int:
        return self.points * (self.multiplier or 1) if self.multiplier else self.points


@dataclass
class LiveTeam:
    team_id: int
    gw: int
    manager_name: str
    entry_name: str
    total_points: int          # official GW points (captain applied, XI only)
    bench_points: int
    overall_rank: int | None
    gw_rank: int | None
    active_chip: str | None
    xi: list[LivePick] = field(default_factory=list)
    bench: list[LivePick] = field(default_factory=list)
    captain: LivePick | None = None
    vice: LivePick | None = None


def resolve_team_id(explicit: int | None = None) -> int | None:
    """Team id from --flag, env FPL_TEAM_ID, or ~/.fpl-mcp/config.json."""
    if explicit:
        return explicit
    env = os.environ.get("FPL_TEAM_ID")
    if env and env.isdigit():
        return int(env)
    cfg = os.path.expanduser("~/.fpl-mcp/config.json")
    if os.path.exists(cfg):
        try:
            import json
            tid = json.load(open(cfg)).get("team_id")
            if tid and str(tid).isdigit():
                return int(tid)
        except Exception:
            pass
    return None


_SESSION = requests.Session()           # keep-alive across the ~70 calls
_MEMO: dict[str, object] = {}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_DISK = Path(__file__).resolve().parent.parent / "data" / "live"


def _get(url: str):
    r = _SESSION.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def _get_shared(url: str, immutable: bool = False):
    """``_get`` memoised for this process (one download even when many threads
    ask at once). ``immutable`` data (finished gameweeks) is also kept on disk."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(url, threading.Lock())
    with lock:
        if url in _MEMO:
            return _MEMO[url]
        disk = _DISK / (re.sub(r"[^a-z0-9]+", "_", url.split("/api/")[-1]) + "json")
        if immutable and disk.exists():
            data = json.loads(disk.read_text())
        else:
            data = _get(url)
            if immutable:
                _DISK.mkdir(parents=True, exist_ok=True)
                disk.write_text(json.dumps(data))
        _MEMO[url] = data
        return data


def _gw_final(bootstrap: dict, gw: int) -> bool:
    """True once a gameweek's points are finalised (safe to cache forever)."""
    ev = next((e for e in bootstrap.get("events", []) if e["id"] == gw), {})
    return bool(ev.get("finished") and ev.get("data_checked"))


@dataclass
class GWHistory:
    gw: int
    points: int
    bench_points: int
    overall_rank: int | None
    gw_rank: int | None
    transfers: int
    transfer_cost: int
    captain: str = ""
    chip: str | None = None


def fetch_history(team_id: int, bootstrap: dict) -> list[GWHistory]:
    """Per-gameweek season history for the previous-GW tracker."""
    data = _get(f"{BASE}/entry/{team_id}/history/")
    chip_by_gw = {c["event"]: c["name"] for c in data.get("chips", [])}
    name = {e["id"]: e["web_name"] for e in bootstrap["elements"]}
    rows: list[GWHistory] = []
    for r in data.get("current", []):
        gw = r["event"]
        cap = ""
        try:
            picks = _get_shared(f"{BASE}/entry/{team_id}/event/{gw}/picks/",
                                _gw_final(bootstrap, gw))
            cid = next((p["element"] for p in picks["picks"] if p["is_captain"]), None)
            cap = name.get(cid, "")
        except Exception:
            pass
        rows.append(GWHistory(
            gw=gw, points=r["points"], bench_points=r["points_on_bench"],
            overall_rank=r.get("overall_rank"), gw_rank=r.get("rank"),
            transfers=r["event_transfers"], transfer_cost=r["event_transfers_cost"],
            captain=cap, chip=chip_by_gw.get(gw),
        ))
    return rows


LAST_MY_TEAM: dict | None = None   # last authenticated my-team payload
SQUAD_SOURCE = ""                  # "live" | "snapshot <iso>" | "picks"
_SNAPSHOT = Path(__file__).resolve().parent.parent / "state" / "my_team.json"


def _save_snapshot(team_id: int, current_gw: int, mine: dict) -> None:
    """Persist the authenticated squad so builds WITHOUT credentials (the
    GitHub Action) can still show the real team instead of last-deadline picks.

    Only ids + transfer counters — no tokens, and nothing the public league page
    doesn't already expose. Tagged with ``current_gw`` because a snapshot is only
    valid inside the transfer window it was taken in: once the next deadline
    passes, the public picks ARE the truth and the snapshot is discarded.
    """
    try:
        _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT.write_text(json.dumps({
            "team_id": int(team_id),
            "current_gw": int(current_gw),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "picks": [{"element": p["element"]} for p in mine.get("picks", [])],
            "transfers": mine.get("transfers", {}),
        }, indent=1))
    except OSError as e:
        print(f"[my-team] snapshot not written: {e}")


def _load_snapshot(team_id: int, current_gw: int) -> dict | None:
    """The saved squad, if it belongs to this team AND this transfer window."""
    try:
        snap = json.loads(_SNAPSHOT.read_text())
    except (OSError, ValueError):
        return None
    if int(snap.get("team_id", 0)) != int(team_id):
        return None
    if int(snap.get("current_gw", -1)) != int(current_gw):
        return None          # stale window: a deadline has passed since
    return snap


def _encrypted_store_readable() -> bool:
    """True only if fpl_mcp's ENCRYPTED credential store decrypts.

    Its key is derived from machine identifiers (MAC/hostname/user), so the
    store can silently become unreadable when those change. fpl_mcp then falls
    back to the plaintext ``~/.fpl-mcp/config.json`` token, which is typically a
    long-since-rotated one. PingOne rotates refresh tokens on every use and can
    revoke the whole token family if a consumed one is replayed — so never
    exchange a credential that didn't come from the encrypted store.
    """
    try:
        import logging
        from fpl_mcp.fpl.credential_manager import CredentialManager
        logging.getLogger("fpl_mcp").setLevel(logging.CRITICAL)
        cm = CredentialManager()
        cm._decrypt_data(cm._encrypted_file.read_bytes())
        return True
    except Exception:
        return False


def fetch_my_team(team_id: int) -> dict | None:
    """Authenticated ``my-team`` payload — the squad as it stands NOW, including
    transfers made since the last deadline. Reuses the fantasy-pl-mcp credential
    store (~/.fpl-mcp, refresh token rotated + persisted by its auth manager).
    Returns None when fpl_mcp isn't installed or auth is unusable (e.g. in CI),
    in which case callers fall back to the public last-deadline picks."""
    global LAST_MY_TEAM
    if not _encrypted_store_readable():
        print("[my-team] skipped: fpl_mcp's encrypted credentials can't be read "
              "(re-run the MCP update_fpl_credentials/setup). Using last-deadline "
              "picks — pending transfers won't show.")
        return None
    try:
        import asyncio
        import logging
        from fpl_mcp.fpl.auth_manager import FPLAuthManager
        logging.getLogger("fpl_mcp").setLevel(logging.ERROR)
        auth = FPLAuthManager()
        if not auth.team_id or int(auth.team_id) != int(team_id):
            return None
        LAST_MY_TEAM = asyncio.run(
            auth.make_authed_request(f"{BASE}/my-team/{team_id}/"))
        return LAST_MY_TEAM
    except Exception as e:
        print(f"[my-team] authenticated fetch unavailable ({e}); "
              "using last-deadline picks")
        return None


def fetch_squad_ids(team_id: int, gw: int) -> tuple[list[int], int]:
    """Return (element ids of the current 15, bank in tenths).

    Precedence: authenticated my-team (pending transfers included) > snapshot
    from a previous authenticated run in THIS transfer window > public
    last-deadline picks. The middle rung is what keeps the published page
    correct when the build has no credentials (CI).
    """
    global LAST_MY_TEAM, SQUAD_SOURCE
    mine = fetch_my_team(team_id)
    if mine and mine.get("picks"):
        ids = [p["element"] for p in mine["picks"]]
        bank = (mine.get("transfers") or {}).get("bank", 0)
        _save_snapshot(team_id, gw, mine)
        SQUAD_SOURCE = "live"
        print(f"[my-team] live squad incl. pending transfers "
              f"(bank £{bank/10:.1f}m)")
        return ids, bank
    snap = _load_snapshot(team_id, gw)
    if snap and snap.get("picks"):
        LAST_MY_TEAM = {"picks": snap["picks"],
                        "transfers": snap.get("transfers", {})}
        SQUAD_SOURCE = f"snapshot {snap['fetched_at']}"
        bank = (snap.get("transfers") or {}).get("bank", 0)
        print(f"[my-team] using snapshot from {snap['fetched_at']} "
              f"(bank £{bank/10:.1f}m) — no credentials in this build")
        return [p["element"] for p in snap["picks"]], bank
    SQUAD_SOURCE = "picks"
    picks = _get(f"{BASE}/entry/{team_id}/event/{gw}/picks/")
    ids = [p["element"] for p in picks["picks"]]
    bank = (picks.get("entry_history", {}) or {}).get("bank", 0)
    return ids, bank


def fetch_live_team(team_id: int, gw: int, bootstrap: dict) -> LiveTeam:
    elements = {e["id"]: e for e in bootstrap["elements"]}
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    final = _gw_final(bootstrap, gw)
    picks = _get_shared(f"{BASE}/entry/{team_id}/event/{gw}/picks/", final)
    live = _get_shared(f"{BASE}/event/{gw}/live/", final)
    entry = _get_shared(f"{BASE}/entry/{team_id}/")

    live_pts = {e["id"]: e["stats"]["total_points"] for e in live["elements"]}
    live_min = {e["id"]: e["stats"]["minutes"] for e in live["elements"]}
    # A player's fixture is "started" if the live feed shows any minutes or the
    # element has been provisionally scored; minutes>0 is the simplest signal.
    hist = picks.get("entry_history", {}) or {}

    def make(p: dict) -> LivePick:
        el = elements[p["element"]]
        pid = p["element"]
        return LivePick(
            name=el["web_name"], team_name=team_short[el["team"]],
            team_code=el.get("team_code", 0),
            pos=el["element_type"], cost=el["now_cost"],
            slot=p["position"], multiplier=p["multiplier"],
            is_captain=p["is_captain"], is_vice=p["is_vice_captain"],
            points=live_pts.get(pid, 0), minutes=live_min.get(pid, 0),
            started_fixture=live_min.get(pid, 0) > 0,
            element=pid,
        )

    all_picks = [make(p) for p in picks["picks"]]
    xi = [p for p in all_picks if p.slot <= 11]
    bench = [p for p in all_picks if p.slot > 11]
    captain = next((p for p in all_picks if p.is_captain), None)
    vice = next((p for p in all_picks if p.is_vice), None)

    return LiveTeam(
        team_id=team_id, gw=gw,
        manager_name=f"{entry.get('player_first_name','')} "
                     f"{entry.get('player_last_name','')}".strip(),
        entry_name=entry.get("name", ""),
        total_points=hist.get("points", 0),
        bench_points=hist.get("points_on_bench", 0),
        overall_rank=hist.get("overall_rank"),
        gw_rank=hist.get("rank"),
        active_chip=picks.get("active_chip"),
        xi=xi, bench=bench, captain=captain, vice=vice,
    )
