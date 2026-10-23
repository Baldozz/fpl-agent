"""xAI Grok integration — Premier League insight sourced from live X/Twitter.

Most FPL-relevant news (predicted line-ups, late fitness tests, rotation calls,
"he trained today") breaks on X before anywhere else. Grok's **Responses API**
exposes a server-side ``x_search`` tool that reads live X posts, so we ask Grok
to return structured start-probability signals plus a few headlines for the
upcoming gameweek, grounded in the last few days of X.

The API key is read from the ``XAI_API_KEY`` environment variable and is NEVER
committed. If the key is missing / has no credits, or the API errors, every
function degrades to a no-op so the agent keeps working on the FPL API + RSS +
the hand-editable ``overrides.json``.

Endpoint: POST https://api.x.ai/v1/responses
  { "model", "input": [{role,content}...], "tools": [{"type":"x_search"},
    {"type":"web_search"}] }
The final text is the ``output`` item whose ``type == "message"``.
"""
from __future__ import annotations

import json
import os
import re

import requests

API_URL = "https://api.x.ai/v1/responses"
MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")

SYSTEM = (
    "You are a Premier League Fantasy (FPL) analyst. Ground every answer in the "
    "most recent team news you can find on X/Twitter (beat reporters, official "
    "club accounts, reliable ITKs) and the web. Be precise, current, and output "
    "ONLY what is asked — no preamble.")


class GrokUnavailable(Exception):
    """Raised when Grok can't be used (no key, no credits, transport error)."""


def _key() -> str | None:
    k = os.environ.get("XAI_API_KEY", "").strip()
    return k or None


def available() -> bool:
    return _key() is not None


def _call(prompt: str, use_tools: bool = True, max_tokens: int = 4000) -> str:
    key = _key()
    if not key:
        raise GrokUnavailable("XAI_API_KEY not set")
    payload: dict = {
        "model": MODEL,
        "input": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_output_tokens": max_tokens,
    }
    if use_tools:
        payload["tools"] = [{"type": "x_search"}, {"type": "web_search"}]
    try:
        r = requests.post(API_URL, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }, json=payload, timeout=180)
    except requests.RequestException as e:
        raise GrokUnavailable(f"transport error: {e}") from e
    if r.status_code != 200:
        raise GrokUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    _log_cost(data.get("usage") or {})
    return _extract_message(data)


_COST_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "data", "grok_costs.jsonl")


def _log_cost(usage: dict) -> None:
    """Print + append each call's bill (xAI reports it in 1e-10 USD ticks)."""
    import time
    usd = (usage.get("cost_in_usd_ticks") or 0) / 1e10
    print(f"[grok] call cost ${usd:.3f} ({usage.get('input_tokens', 0)} in / "
          f"{usage.get('output_tokens', 0)} out tokens, "
          f"{usage.get('num_server_side_tools_used', 0)} searches)")
    try:
        with open(_COST_LOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"),
                                "usd": round(usd, 4), **{k: usage.get(k) for k in (
                                    "input_tokens", "output_tokens",
                                    "num_server_side_tools_used")}}) + "\n")
    except OSError:
        pass


def _extract_message(data: dict) -> str:
    """Concatenate text from the final assistant 'message' item(s)."""
    parts: list[str] = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                t = c.get("text")
                if t:
                    parts.append(t)
    if not parts:
        raise GrokUnavailable("no message text in Grok response")
    return "\n".join(parts)


def _extract_json(text: str):
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    m = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON found in Grok response")
    return json.loads(m.group(0))


def _parse_prob(v) -> float | None:
    """Accept 0..1 floats, 0..100 numbers, or strings like '40%'/'0.4'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip().replace("%", "").strip()
        try:
            x = float(s)
        except ValueError:
            return None
    if x > 1.5:            # a percentage like 40 or 40%
        x /= 100.0
    return max(0.0, min(1.0, x))


# Grok calls are slow (~15-20s) and cost xAI credits, so reuse a recent answer.
# Independent of --no-cache (that flag is about FPL data freshness). Team news
# mostly breaks in the last day (press conferences, late knocks), so refresh
# most often then and rarely before — the site rebuilds every 30 min, and each
# call is a paid agentic X/web search. The old 6 h / 1 h tiers came to ~48
# calls per gameweek; these come to ~18.
CACHE_TTL = 2 * 60 * 60              # within NEAR_DEADLINE of the deadline
CACHE_TTL_MID = 8 * 60 * 60          # within MID_DEADLINE
CACHE_TTL_FAR = 24 * 60 * 60         # further out
NEAR_DEADLINE = 24 * 60 * 60
MID_DEADLINE = 72 * 60 * 60
QUIET_HOURS = range(0, 7)            # local hours: serve the cache, don't call
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def cache_ttl(deadline: str | None) -> int:
    """Seconds a cached answer stays fresh, given the next deadline (ISO)."""
    if not deadline:
        return CACHE_TTL
    from datetime import datetime, timezone
    try:
        dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError:
        return CACHE_TTL
    left = (dl - datetime.now(timezone.utc)).total_seconds()
    if left <= NEAR_DEADLINE:
        return CACHE_TTL
    return CACHE_TTL_MID if left <= MID_DEADLINE else CACHE_TTL_FAR


def analyse(team_names: list[str], gw: int,
            deadline: str | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Cached wrapper around ``_analyse`` (keyed by gameweek; fresh for
    ``cache_ttl(deadline)``: 24 h >3 days out, 8 h within 3 days, 2 h in the
    last day; overnight any cached answer is served regardless of age)."""
    import time
    path = os.path.join(_CACHE_DIR, f"grok_gw{gw}.json")
    try:
        age = time.time() - os.path.getmtime(path)
        if age < cache_ttl(deadline) or time.localtime().tm_hour in QUIET_HOURS:
            with open(path) as f:
                sig, heads = json.load(f)
            print(f"[grok] {len(sig)} start-prob signals, {len(heads)} headlines "
                  f"(cached {int((time.time()-os.path.getmtime(path))/60)} min ago)")
            return sig, heads
    except (OSError, ValueError):
        pass
    sig, heads = _analyse(team_names, gw)
    if sig or heads:  # never cache a failure
        try:
            with open(path, "w") as f:
                json.dump([sig, heads], f)
        except OSError:
            pass
    return sig, heads


def _analyse(team_names: list[str], gw: int) -> tuple[dict[str, dict], list[dict]]:
    """One combined X-grounded query → (start_prob signals, headline bullets).

    signals: {name: {"start_prob", "reason", "source"}}  (name as Grok gives it)
    headlines: [{"title", "detail"}]
    Returns ({}, []) on any failure.
    """
    prompt = (
        f"Premier League Gameweek {gw} is imminent. Using the LATEST team news "
        f"from X/Twitter (last ~4 days), assess these clubs: "
        f"{', '.join(team_names)}.\n\n"
        "Return ONLY a JSON object with two keys:\n"
        '1. "players": array of players who are OUT, DOUBTFUL, a ROTATION RISK '
        "(e.g. back late from World Cup duty, heavily rotated, cup/travel), or a "
        "newly NAILED-ON starter. Each item: "
        '{"name":"<player surname/common name>", "start_prob":<0..1 decimal>, '
        '"reason":"<short, cite the gist>"}. '
        "start_prob: 0=won't play, 0.3=major doubt, 0.5=50/50 rotation, "
        "0.8=likely, 1=nailed. Up to 40 of the most fantasy-relevant.\n"
        '2. "headlines": array (max 8) of {"title":"<one line>", '
        '"detail":"<=140 chars"} of the most important GW team-news items.\n'
        "Use decimals not percentages for start_prob."
    )
    try:
        raw = _call(prompt)
        obj = _extract_json(raw)
    except (GrokUnavailable, ValueError, KeyError) as e:
        print(f"[grok] unavailable, falling back to overrides + RSS: {e}")
        return {}, []

    signals: dict[str, dict] = {}
    for item in obj.get("players", []) if isinstance(obj, dict) else []:
        name = str(item.get("name", "")).strip()
        sp = _parse_prob(item.get("start_prob"))
        if not name or sp is None:
            continue
        signals[name] = {"start_prob": sp,
                         "reason": str(item.get("reason", ""))[:160],
                         "source": "grok/x"}
    headlines = []
    for it in (obj.get("headlines", []) if isinstance(obj, dict) else [])[:8]:
        t = str(it.get("title", "")).strip()
        if t:
            headlines.append({"title": t,
                              "detail": str(it.get("detail", ""))[:160]})
    print(f"[grok] {len(signals)} start-prob signals, "
          f"{len(headlines)} headlines from live X search")
    return signals, headlines
