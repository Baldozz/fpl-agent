"""Manual / Grok-supplied per-player overrides.

The FPL API is authoritative for prices and its own injury field, but it does
NOT know things the human eye (or X/Twitter via Grok) does: an unannounced
knock, a player back late from a deep World Cup run who'll be rotated, or a new
signing who is nailed on. ``overrides.json`` captures that knowledge and this
module loads it and merges any live Grok signals on top.
"""
from __future__ import annotations

import json
from pathlib import Path

OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "overrides.json"


def _load() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(OVERRIDES_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_overrides() -> dict[str, dict]:
    """Return {web_name: {"start_prob": float, "reason": str}}."""
    return _load().get("players", {})


def load_must_include() -> list[str]:
    """Names of players to force into the squad (e.g. a template premium)."""
    return list(_load().get("must_include", []))


def load_keeper_order() -> dict[str, list[str]]:
    """Club keeper depth the API can't express, as {"MCI": ["Donnarumma",
    "Trafford"]} (team short name -> web_names, #1 first). Hand-edited."""
    return {k: list(v) for k, v in (_load().get("keeper_order", {}) or {}).items()}


def load_chips_used() -> dict[str, list[int]]:
    """Chips you've played that the public API can't see yet (e.g. a wildcard
    activated before the deadline), as {"wildcard": [7]}. Hand-edited."""
    raw = _load().get("chips_used", {}) or {}
    return {k: [int(g) for g in v] for k, v in raw.items() if isinstance(v, list)}


def merge(base: dict[str, dict], extra: dict[str, dict]) -> dict[str, dict]:
    """Merge Grok signals (``extra``) over file overrides (``base``).

    Grok is fresher, so it wins on conflict, but a hand-edited file entry with
    ``"pin": true`` is never overwritten.
    """
    out = dict(base)
    for name, sig in extra.items():
        if base.get(name, {}).get("pin"):
            continue
        out[name] = sig
    return out
