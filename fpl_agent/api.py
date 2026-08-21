"""Client for the public, free Fantasy Premier League JSON API.

No authentication is required for any of these endpoints. Responses are cached
to ``data/`` so repeated runs on the same day don't hammer the FPL servers.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-agent; +https://github.com)"}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _get(url: str) -> Any:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _cache_path(name: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / name


def _load_cached(name: str, max_age_seconds: int) -> Any | None:
    p = _cache_path(name)
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age_seconds:
        return json.loads(p.read_text())
    return None


def _save_cache(name: str, data: Any) -> None:
    _cache_path(name).write_text(json.dumps(data))


def bootstrap(max_age_seconds: int = 3600, use_cache: bool = True) -> dict:
    """Core data dump: players (``elements``), teams, gameweeks (``events``)."""
    if use_cache:
        cached = _load_cached("bootstrap.json", max_age_seconds)
        if cached is not None:
            return cached
    data = _get(f"{BASE}/bootstrap-static/")
    _save_cache("bootstrap.json", data)
    return data


def fixtures(event: int | None = None, max_age_seconds: int = 3600,
             use_cache: bool = True) -> list[dict]:
    """All fixtures, or just those for a single gameweek ``event``."""
    name = f"fixtures_{event or 'all'}.json"
    if use_cache:
        cached = _load_cached(name, max_age_seconds)
        if cached is not None:
            return cached
    url = f"{BASE}/fixtures/"
    if event is not None:
        url += f"?event={event}"
    data = _get(url)
    _save_cache(name, data)
    return data


def element_summary(player_id: int) -> dict:
    """Per-player detail: fixture list + history. Not cached (used sparingly)."""
    return _get(f"{BASE}/element-summary/{player_id}/")


def current_and_next_event(data: dict) -> tuple[dict | None, dict | None]:
    """Return (current, next) gameweek dicts from a bootstrap payload."""
    cur = next((e for e in data["events"] if e.get("is_current")), None)
    nxt = next((e for e in data["events"] if e.get("is_next")), None)
    if nxt is None:
        # Season may be finished / not started; fall back to first unfinished.
        nxt = next((e for e in data["events"] if not e.get("finished")), None)
    return cur, nxt
