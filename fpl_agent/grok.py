"""xAI Grok integration — Premier League insight sourced from X/Twitter.

Most breaking FPL-relevant news (predicted line-ups, late fitness tests,
rotation calls, "he trained today") surfaces on X before anywhere else. Grok has
live access to X, so we ask it — with Live Search enabled over X — to return
structured start-probability signals and a few headlines for the upcoming
gameweek.

The API key is read from the ``XAI_API_KEY`` environment variable and is NEVER
committed to the repo. If the key is missing or the team has no credits, every
function degrades to a no-op so the rest of the agent keeps working on the free
FPL API + RSS feeds.
"""
from __future__ import annotations

import json
import os
import re

import requests

API_URL = "https://api.x.ai/v1/chat/completions"
MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")


class GrokUnavailable(Exception):
    """Raised when Grok can't be used (no key, no credits, transport error)."""


def _key() -> str | None:
    k = os.environ.get("XAI_API_KEY", "").strip()
    return k or None


def available() -> bool:
    return _key() is not None


def _call(prompt: str, use_live_search: bool = True,
          max_tokens: int = 2000) -> str:
    key = _key()
    if not key:
        raise GrokUnavailable("XAI_API_KEY not set")
    payload: dict = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content":
                "You are a Premier League Fantasy (FPL) analyst. Base answers on "
                "the most recent team news from X/Twitter (beat reporters, "
                "official club accounts, reliable ITKs). Be precise and only "
                "output what is asked."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if use_live_search:
        # xAI Live Search — restrict to X so we get Twitter-sourced insight.
        # NOTE: xAI deprecated this parameter (HTTP 410) in favour of the Agent
        # Tools API. We still send it (in case it's re-enabled / for newer keys)
        # and transparently retry without it on a 410 so the call still works
        # off Grok's own knowledge. For true live-X grounding, upgrade this to
        # the Agent Tools API once the key has credits — see module docstring.
        payload["search_parameters"] = {
            "mode": "auto",
            "sources": [{"type": "x"}, {"type": "news"}],
            "max_search_results": 20,
        }

    def _post(body: dict):
        try:
            return requests.post(API_URL, headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }, json=body, timeout=90)
        except requests.RequestException as e:
            raise GrokUnavailable(f"transport error: {e}") from e

    r = _post(payload)
    if r.status_code == 410 and "search_parameters" in payload:
        # Live search retired — fall back to a plain completion.
        payload.pop("search_parameters", None)
        r = _post(payload)
    if r.status_code != 200:
        # Common cases: 403 permission-denied (no credits), 401 (bad key).
        raise GrokUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model response."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    m = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON found in Grok response")
    return json.loads(m.group(0))


def player_signals(team_names: list[str], gw: int) -> dict[str, dict]:
    """Ask Grok for start-probability signals for the upcoming gameweek.

    Returns {web_name: {"start_prob": float, "reason": str}}. Empty on failure.
    """
    prompt = (
        f"Premier League Gameweek {gw} is about to start. Using the latest team "
        f"news from X/Twitter, list players who are OUT, DOUBTFUL, a ROTATION "
        f"RISK (e.g. back late from World Cup duty, heavily rotated, or a "
        f"cup/travel situation), or newly NAILED-ON starters. Focus on "
        f"fantasy-relevant players across these clubs: {', '.join(team_names)}. "
        "Return ONLY a JSON array, each item: "
        '{"name": "<surname as on FPL>", "start_prob": <0..1>, '
        '"reason": "<short, cite the gist>"}. '
        "start_prob: 0=won't play, 0.3=major doubt, 0.5=50/50 rotation, "
        "0.8=likely starts, 1=nailed. Keep to at most 40 of the most relevant."
    )
    try:
        raw = _call(prompt)
        arr = _extract_json(raw)
    except (GrokUnavailable, ValueError, KeyError) as e:
        print(f"[grok] player signals unavailable: {e}")
        return {}
    out: dict[str, dict] = {}
    for item in arr if isinstance(arr, list) else []:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            sp = float(item.get("start_prob"))
        except (TypeError, ValueError):
            continue
        out[name] = {"start_prob": max(0.0, min(1.0, sp)),
                     "reason": str(item.get("reason", ""))[:160],
                     "source": "grok/x"}
    print(f"[grok] received {len(out)} player signals")
    return out


def headlines(gw: int, limit: int = 8) -> list[dict]:
    """A few X-sourced headline bullets for the report. Empty on failure."""
    prompt = (
        f"Give the {limit} most important Premier League team-news items for "
        f"Gameweek {gw} from X/Twitter in the last 48h (injuries, predicted "
        "line-ups, rotation). Return ONLY a JSON array of "
        '{"title": "<one line>", "detail": "<=140 chars"}.')
    try:
        raw = _call(prompt)
        arr = _extract_json(raw)
    except (GrokUnavailable, ValueError, KeyError) as e:
        print(f"[grok] headlines unavailable: {e}")
        return []
    items = []
    for it in (arr if isinstance(arr, list) else [])[:limit]:
        t = str(it.get("title", "")).strip()
        if t:
            items.append({"title": t, "detail": str(it.get("detail", ""))[:160]})
    return items
