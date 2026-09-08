"""Weekly FPL intel from free expert-site summary pages.

Two community sites publish a free, human-readable "Gameweek N tips" hub each
week. This module fetches those pages (plain ``requests`` — no login, no API
key, no browser) and distils them into:

* a short readable summary per source, and
* a ranked list of **clubs the experts are flagging to target** this gameweek,

so the agent can surface "what the experts are saying" alongside its own
projections. It is advisory context only — the optimizer still decides.

Sources (free summary pages):
  * Fantasy Football Hub  — the "Ultimate Guide" (stable slug).
  * Fantasy Football Scout — the "Gameweek N tips" hub (slug embeds the GW).

Everything degrades gracefully: if a page is unreachable or its layout changes,
that source is skipped and the rest of the agent is unaffected (same contract as
``news`` / ``grok``).
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

_CACHE = Path(__file__).resolve().parent.parent / "data"
_CACHE_TTL = 3600  # 1 hour, matching api.py
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
_TIMEOUT = 25

# All 20 PL clubs + common promoted sides, mapping display name -> aliases used
# in prose. Kept broad so club detection survives naming quirks in the data set.
_CLUB_ALIASES: dict[str, list[str]] = {
    "Arsenal": ["arsenal"],
    "Aston Villa": ["aston villa", "villa"],
    "Bournemouth": ["bournemouth"],
    "Brentford": ["brentford"],
    "Brighton": ["brighton"],
    "Chelsea": ["chelsea"],
    "Coventry City": ["coventry"],
    "Crystal Palace": ["crystal palace", "palace"],
    "Everton": ["everton"],
    "Fulham": ["fulham"],
    "Hull City": ["hull"],
    "Ipswich Town": ["ipswich"],
    "Leeds": ["leeds"],
    "Liverpool": ["liverpool"],
    "Man City": ["man city", "manchester city"],
    "Man Utd": ["man utd", "man united", "manchester united"],
    "Newcastle": ["newcastle"],
    "Nott'm Forest": ["nottingham forest", "nott'm forest", "forest"],
    "Sunderland": ["sunderland"],
    "Tottenham": ["tottenham", "spurs"],
}

# Words that signal a club/player is being recommended (positive) vs avoided.
_POS = ("target", "targeting", "best", "buy", "buying", "own", "must", "favourable",
        "favorable", "captain", "triple captain", "haul", "watch", "essential",
        "explosive", "scramble", "premium", "in-form", "differential")
_NEG = ("avoid", "sell", "selling", "bench", "injury", "injured", "doubt",
        "rotation", "suspended", "ban", "blank")

_HEADLINE_URL = "https://www.fantasyfootballhub.co.uk/fantasy-premier-league-ultimate-guide-fpl-tips"


def _scout_url(gw: int) -> str:
    return (f"https://www.fantasyfootballscout.co.uk/fpl-gameweek-{gw}-tips-"
            f"best-players-predicted-line-ups-team-news-more-{gw}")


def _sources(gw: int) -> list[tuple[str, str]]:
    return [
        ("Fantasy Football Hub", _HEADLINE_URL),
        ("Fantasy Football Scout", _scout_url(gw)),
    ]


@dataclass
class SourceIntel:
    name: str
    url: str
    ok: bool
    summary: str = ""            # short readable snippet
    clubs: dict[str, float] = field(default_factory=dict)  # club -> sentiment score


@dataclass
class WeeklyIntel:
    gw: int
    sources: list[SourceIntel]
    target_clubs: list[tuple[str, float]]  # ranked, positive-sentiment clubs

    @property
    def ok(self) -> bool:
        return any(s.ok for s in self.sources)


def _fetch(url: str, *, use_cache: bool = True) -> str | None:
    """Return page HTML (cached ~1h on disk). None on any failure."""
    _CACHE.mkdir(exist_ok=True)
    key = _CACHE / ("experts_" + re.sub(r"[^a-z0-9]+", "_", url.lower())[-80:] + ".html")
    if use_cache and key.exists() and (time.time() - key.stat().st_mtime) < _CACHE_TTL:
        return key.read_text(encoding="utf-8", errors="ignore")
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        if r.status_code != 200 or not r.text:
            return None
        key.write_text(r.text, encoding="utf-8", errors="ignore")
        return r.text
    except Exception:
        return None


def _visible_text(page_html: str) -> str:
    """Strip scripts/styles/tags -> collapsed visible text."""
    txt = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page_html)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


# Phrases that mark the start of the real article body (past the nav chrome).
_BODY_ANCHORS = (
    "Looking for the best FPL tips",
    "This Ultimate Guide",
    "From Scout Picks",
    "there'll be a scramble",
    "the team to target",
)


def _summary(text: str, gw: int, limit: int = 700) -> str:
    """Grab a readable snippet from the article body (skipping nav chrome)."""
    start = 0
    for anchor in _BODY_ANCHORS:
        i = text.find(anchor)
        if i != -1:
            start = i
            break
    else:
        m = re.search(rf"Gameweek {gw}\b", text)
        # Skip the first hit (breadcrumb/nav); prefer a later, prose mention.
        if m:
            nxt = text.find(f"Gameweek {gw}", m.end())
            start = max(0, (nxt if nxt != -1 else m.start()) - 40)
    snippet = text[start:start + limit].strip()
    cut = snippet.rfind(". ")
    if cut > limit * 0.5:
        snippet = snippet[:cut + 1]
    # Reject nav/paywall boilerplate: real prose names a club or a game concept.
    low = snippet.lower()
    signal = any(a in low for al in _CLUB_ALIASES.values() for a in al) or any(
        w in low for w in ("fixture", "captain", "transfer", "haul", "clean sheet",
                            "assets", "deadline", "points"))
    junk = ("for members only" in low or "become a" in low
            or low.count(" tools ") >= 2)
    if not signal or junk:
        return ""
    return snippet


def _club_sentiment(text: str) -> dict[str, float]:
    """Score each club by positive/negative keyword proximity in the prose."""
    low = text.lower()
    scores: dict[str, float] = {}
    for club, aliases in _CLUB_ALIASES.items():
        score = 0.0
        for alias in aliases:
            for m in re.finditer(re.escape(alias), low):
                window = low[max(0, m.start() - 60): m.end() + 60]
                score += sum(1 for w in _POS if w in window)
                score -= 0.5 * sum(1 for w in _NEG if w in window)
        if score:
            scores[club] = round(score, 1)
    return scores


def weekly_intel(gw: int, *, use_cache: bool = True) -> WeeklyIntel:
    """Fetch + distil this gameweek's expert intel. Never raises."""
    sources: list[SourceIntel] = []
    combined: dict[str, float] = {}
    for name, url in _sources(gw):
        page = _fetch(url, use_cache=use_cache)
        if not page:
            sources.append(SourceIntel(name, url, ok=False))
            continue
        text = _visible_text(page)
        clubs = _club_sentiment(text)
        sources.append(SourceIntel(name, url, ok=True,
                                   summary=_summary(text, gw), clubs=clubs))
        for club, s in clubs.items():
            combined[club] = combined.get(club, 0.0) + s
    ranked = sorted(((c, s) for c, s in combined.items() if s > 0),
                    key=lambda kv: -kv[1])
    return WeeklyIntel(gw=gw, sources=sources, target_clubs=ranked)


def to_dict(intel: WeeklyIntel) -> dict:
    return {
        "gw": intel.gw,
        "target_clubs": intel.target_clubs,
        "sources": [
            {"name": s.name, "url": s.url, "ok": s.ok,
             "summary": s.summary, "clubs": s.clubs}
            for s in intel.sources
        ],
    }


def _main(argv: list[str] | None = None) -> int:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    gw = int(args[0]) if args else 4
    intel = weekly_intel(gw)
    print(json.dumps(to_dict(intel), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
