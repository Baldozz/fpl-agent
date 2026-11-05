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
  { "model", "input": [{role,content}...], "tools": [{"type":"x_search"}],
    "max_turns", "parallel_tool_calls": false }
The final text is the ``output`` item whose ``type == "message"``.
"""
from __future__ import annotations

import json
import os
import re

import requests

API_URL = "https://api.x.ai/v1/responses"
# Pinned, non-reasoning: the job is "search X, extract JSON", not deep thought.
# NB legacy names (grok-4-latest, grok-4-fast...) silently alias to whatever xAI
# picks (grok-4.3 as of 2026-11), so name an exact model.
MODEL = os.environ.get("XAI_MODEL", "grok-4.20-0309-non-reasoning")
# ~80% of each call's bill is the server-side SEARCHES (~$0.02 each), not
# tokens: one call measured 9 searches = $0.19 of $0.24. So search X only (web
# results mostly echo it) and cap the agent's search rounds.
TOOLS = [{"type": "x_search"}]
MAX_TURNS = int(os.environ.get("GROK_MAX_TURNS", "2"))

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


def _call(prompt: str, use_tools: bool = True, max_tokens: int = 4000,
          gw: int | None = None) -> str:
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
        payload["tools"] = TOOLS
        payload["max_turns"] = MAX_TURNS          # caps search ROUNDS...
        payload["parallel_tool_calls"] = False    # ...and one search per round
    try:
        r = requests.post(API_URL, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }, json=payload, timeout=180)
    except requests.RequestException as e:
        # The agentic search keeps running (and billing) server-side after we
        # give up, so count it against the budget at a typical call's price.
        _log_cost({}, gw=gw, error=f"transport: {type(e).__name__}",
                  usd=ASSUMED_FAILED_CALL_USD)
        raise GrokUnavailable(f"transport error: {e}") from e
    if r.status_code != 200:
        _log_cost({}, gw=gw, error=f"HTTP {r.status_code}", usd=0.0)
        raise GrokUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    _log_cost(data.get("usage") or {}, gw=gw)
    return _extract_message(data)


_ROOT = os.path.dirname(os.path.dirname(__file__))
_COST_LOG = os.path.join(_ROOT, "data", "grok_costs.jsonl")

# ── Spend guard ──────────────────────────────────────────────────────────────
# Hard ceilings checked BEFORE every paid call, from the cost log. When hit, the
# last cached answer is served (however old) and no request is made. Override
# via env (.env) if you deliberately want more.
MONTHLY_BUDGET_USD = float(os.environ.get("GROK_MONTHLY_BUDGET_USD", "10"))
MAX_CALLS_PER_GW = int(os.environ.get("GROK_MAX_CALLS_PER_GW", "12"))
ASSUMED_FAILED_CALL_USD = 0.15     # a timed-out call is likely still billed
# After a failure, don't retry for this long (the site rebuilds every 30 min,
# and an un-cached failure used to re-fire on every rebuild).
FAIL_BACKOFF = 2 * 60 * 60
FAIL_BACKOFF_BILLING = 24 * 60 * 60  # 401/402/403/429: credits / limit / key


def _log_cost(usage: dict, gw: int | None = None, error: str | None = None,
              usd: float | None = None) -> None:
    """Print + append each call's bill (xAI reports it in 1e-10 USD ticks)."""
    import time
    if usd is None:
        usd = (usage.get("cost_in_usd_ticks") or 0) / 1e10
    if error:
        print(f"[grok] call FAILED ({error}) — counted as ${usd:.3f}")
    else:
        print(f"[grok] call cost ${usd:.3f} ({usage.get('input_tokens', 0)} in / "
              f"{usage.get('output_tokens', 0)} out tokens, "
              f"{usage.get('num_server_side_tools_used', 0)} searches)")
    row = {"t": time.strftime("%Y-%m-%d %H:%M"), "usd": round(usd, 4), "gw": gw,
           **{k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                        "num_server_side_tools_used")}}
    if error:
        row["error"] = error
    try:
        with open(_COST_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _cost_rows() -> list[dict]:
    try:
        with open(_COST_LOG) as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def spend_summary(gw: int | None = None) -> dict:
    """{'month_usd', 'gw_calls'} from the cost log (this calendar month)."""
    import time
    month = time.strftime("%Y-%m")
    rows = _cost_rows()
    return {
        "month_usd": sum(r.get("usd") or 0 for r in rows
                         if str(r.get("t", "")).startswith(month)),
        "gw_calls": sum(1 for r in rows if gw is not None and r.get("gw") == gw
                        and r.get("usd")),
    }


def budget_block(gw: int) -> str | None:
    """Reason a new paid call is NOT allowed right now, else None."""
    if os.environ.get("GITHUB_ACTIONS"):
        # CI can't write the cost log or keep the cache, so it never spends;
        # it reuses the answer the local scheduled build commits to state/.
        return "CI never makes paid calls"
    s = spend_summary(gw)
    if s["month_usd"] >= MONTHLY_BUDGET_USD:
        return (f"monthly budget reached (${s['month_usd']:.2f} of "
                f"${MONTHLY_BUDGET_USD:.2f}, GROK_MONTHLY_BUDGET_USD)")
    if s["gw_calls"] >= MAX_CALLS_PER_GW:
        return (f"GW{gw} call cap reached ({s['gw_calls']}/{MAX_CALLS_PER_GW}, "
                "GROK_MAX_CALLS_PER_GW)")
    fail = _read_json(_FAIL_PATH)
    if fail:
        import time
        wait = fail.get("until", 0) - time.time()
        if wait > 0:
            return (f"backing off {int(wait / 60)} min after a failed call "
                    f"({fail.get('reason', '')[:60]})")
    return None


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


# Grok calls are slow (~15-20s) and each is a paid agentic X/web search, so
# they only happen when team news matters: from CALL_WINDOW before the next
# deadline. Outside it the agent is FROZEN — no calls; the last answer for that
# GW (if any) is served, else it runs on overrides + RSS. Inside the window a
# cached answer is reused for 12 h, then 4 h on the last day (press
# conferences, late knocks). With the site rebuilding every 30 min and quiet
# nights this is ~9-10 calls per GW (~$1.15), under MAX_CALLS_PER_GW.
CALL_WINDOW = 72 * 60 * 60           # no calls earlier than this before deadline
CACHE_TTL = 4 * 60 * 60              # within NEAR_DEADLINE of the deadline
CACHE_TTL_MID = 12 * 60 * 60         # NEAR_DEADLINE .. CALL_WINDOW
NEAR_DEADLINE = 24 * 60 * 60
QUIET_HOURS = range(0, 7)            # local hours: serve the cache, don't call
# The answer is committed (state/ is pushed by scripts/auto_update.sh) so CI
# builds reuse it instead of paying again; the fetch time lives INSIDE the file
# because a git checkout resets mtimes.
_CACHE_DIR = os.path.join(_ROOT, "state")
_LEGACY_CACHE_DIR = os.path.join(_ROOT, "data")
_FAIL_PATH = os.path.join(_ROOT, "data", "grok_fail.json")


def _read_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_cache(gw: int) -> tuple[float, dict, list] | None:
    """(fetched_epoch, signals, headlines) for this GW, or None."""
    obj = _read_json(os.path.join(_CACHE_DIR, f"grok_gw{gw}.json"))
    if isinstance(obj, dict) and "fetched" in obj:
        return obj["fetched"], obj.get("signals", {}), obj.get("headlines", [])
    legacy = os.path.join(_LEGACY_CACHE_DIR, f"grok_gw{gw}.json")
    obj = _read_json(legacy)
    if isinstance(obj, list) and len(obj) == 2:
        return os.path.getmtime(legacy), obj[0], obj[1]
    return None


def _save_cache(gw: int, sig: dict, heads: list) -> None:
    import time
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(os.path.join(_CACHE_DIR, f"grok_gw{gw}.json"), "w") as f:
            json.dump({"gw": gw, "fetched": time.time(), "signals": sig,
                       "headlines": heads}, f, indent=1)
    except OSError:
        pass


def _note_failure(reason: str) -> None:
    import time
    billing = any(f"HTTP {c}" in reason for c in (401, 402, 403, 429))
    try:
        with open(_FAIL_PATH, "w") as f:
            json.dump({"reason": reason[:200], "until": time.time() + (
                FAIL_BACKOFF_BILLING if billing else FAIL_BACKOFF)}, f)
    except OSError:
        pass


def _clear_failure() -> None:
    try:
        os.remove(_FAIL_PATH)
    except OSError:
        pass


def cache_ttl(deadline: str | None) -> int | None:
    """Seconds a cached answer stays fresh given the next deadline (ISO), or
    None when we're FROZEN (more than CALL_WINDOW before it: no new calls)."""
    if not deadline:
        return CACHE_TTL
    from datetime import datetime, timezone
    try:
        dl = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError:
        return CACHE_TTL
    left = (dl - datetime.now(timezone.utc)).total_seconds()
    if left > CALL_WINDOW:
        return None
    return CACHE_TTL if left <= NEAR_DEADLINE else CACHE_TTL_MID


def analyse(team_names: list[str], gw: int,
            deadline: str | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Cached wrapper around ``_analyse`` (keyed by gameweek). Frozen (no
    calls) until CALL_WINDOW before the deadline; then fresh for 12 h, 4 h on
    the last day; overnight any cached answer is served regardless of age."""
    import time
    cached = _load_cache(gw)

    def serve(why: str):
        fetched, sig, heads = cached
        print(f"[grok] {len(sig)} start-prob signals, {len(heads)} headlines "
              f"(cached {int((time.time() - fetched) / 60)} min ago{why})")
        return sig, heads

    ttl = cache_ttl(deadline)
    if ttl is None:
        hrs = CALL_WINDOW // 3600
        if cached:
            return serve(f"; frozen until {hrs} h before the deadline")
        print(f"[grok] frozen: no calls until {hrs} h before the GW{gw} "
              "deadline — using overrides + RSS")
        return {}, []
    if cached and (time.time() - cached[0] < ttl
                   or time.localtime().tm_hour in QUIET_HOURS):
        return serve("")
    blocked = budget_block(gw)
    if blocked:
        print(f"[grok] no new call: {blocked}")
        return serve("; stale") if cached else ({}, [])
    sig, heads = _analyse(team_names, gw)
    if sig or heads:  # never cache a failure
        _clear_failure()
        _save_cache(gw, sig, heads)
        return sig, heads
    return serve("; last call failed") if cached else ({}, [])


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
        raw = _call(prompt, gw=gw)
        obj = _extract_json(raw)
    except (GrokUnavailable, ValueError, KeyError) as e:
        print(f"[grok] unavailable, falling back to overrides + RSS: {e}")
        _note_failure(str(e))
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
