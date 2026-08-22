"""Free, no-auth football news via public RSS feeds.

The FPL API already carries the authoritative injury/availability field
(``player.news``) which the model uses directly. These RSS feeds add colour and
let the agent surface relevant headlines (team news, predicted line-ups,
injuries) alongside its picks. All sources below are free and publicly
available; add or remove feeds in ``FEEDS`` freely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Free public RSS feeds. No API keys required. Kept to Premier-League-focused
# feeds so we don't pull in boxing / F1 / other sports.
FEEDS = {
    "BBC Sport – Premier League":
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
    "The Guardian – Premier League":
        "https://www.theguardian.com/football/premierleague/rss",
    "The Guardian – Football": "https://www.theguardian.com/football/rss",
}

# Negative-only signals for driving selection. Each maps a start probability to
# the phrases that imply it. OUT beats DOUBT when both appear. Phrases are kept
# specific (e.g. "ruled out", not bare "out for") to avoid false matches like
# "10 things to look out for"; parsing uses headline TITLES only for precision.
SIGNAL_LEVELS = [
    (0.0, ("ruled out", "will miss", "set to miss", "expected to miss",
           "misses the", "sidelined", "injury blow", "suspended", "banned",
           "out injured", "long-term absence", "ruled him out",
           "won't play", "will not play", "out of the game")),
    (0.4, ("major doubt", "doubtful", "in doubt", "fitness test",
           "race to be fit", "injury concern", "rated doubtful",
           "could miss", "may miss", "a doubt for")),
]

INJURY_KEYWORDS = (
    "injury", "injured", "doubt", "ruled out", "sidelined", "return",
    "fitness", "knock", "strain", "hamstring", "suspended", "ban", "team news",
    "line-up", "lineup", "starting xi", "predicted",
)


@dataclass
class Headline:
    source: str
    title: str
    link: str
    summary: str = ""


def fetch_headlines(limit_per_feed: int = 15) -> list[Headline]:
    try:
        import feedparser
        import requests
    except ImportError:
        return []
    headers = {"User-Agent": "Mozilla/5.0 (fpl-agent)"}
    out: list[Headline] = []
    for source, url in FEEDS.items():
        try:
            # Fetch via requests (bundles certifi) then parse the bytes, so we
            # don't depend on the system SSL trust store that feedparser's own
            # urllib fetch would use.
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception:
            continue
        for entry in feed.entries[:limit_per_feed]:
            out.append(Headline(
                source=source,
                title=getattr(entry, "title", ""),
                link=getattr(entry, "link", ""),
                summary=getattr(entry, "summary", "")[:280],
            ))
    return out


def parse_start_signals(headlines: list[Headline],
                        player_names: set[str]) -> dict[str, dict]:
    """Turn RSS headlines into NEGATIVE start-probability signals for selection.

    For each headline, if a player's name (>=4 chars, word-boundary) co-occurs
    with an injury/availability phrase, emit ``{name: {start_prob, reason,
    source}}``. Negative-only (never promotes a player) to stay safe against
    noisy headlines; keeps the most severe signal per player. Lowest precedence —
    Grok and manual overrides supersede it (see cli layering).
    """
    names = [n for n in player_names if len(n) >= 4]
    patterns = {n: re.compile(r"\b" + re.escape(n.lower()) + r"\b") for n in names}
    out: dict[str, dict] = {}
    for h in headlines:
        # Titles only — summaries pull in unrelated names/phrases and hurt
        # precision for something that auto-benches players.
        text = h.title.lower()
        level = None
        for prob, phrases in SIGNAL_LEVELS:
            if any(ph in text for ph in phrases):
                level = prob
                break
        if level is None:
            continue
        for n in names:
            if patterns[n].search(text):
                prev = out.get(n)
                if prev is None or level < prev["start_prob"]:
                    out[n] = {"start_prob": level,
                              "reason": h.title[:140], "source": "rss"}
    return out


def relevant_headlines(headlines: list[Headline], names: set[str],
                       teams: set[str] | None = None) -> list[Headline]:
    """Headlines relevant to the squad or the Premier League.

    Requires a squad-player name match, OR a Premier-League team mention paired
    with an injury/team-news keyword. Keyword-only matches are rejected so
    unrelated sports (boxing, F1, rugby) don't leak in from mixed feeds.
    """
    lower_names = {n.lower() for n in names if len(n) > 3}
    lower_teams = {t.lower() for t in (teams or set()) if len(t) > 3}
    hits: list[Headline] = []
    seen: set[str] = set()
    for h in headlines:
        text = f"{h.title} {h.summary}".lower()
        by_name = any(n in text for n in lower_names)
        by_team = any(t in text for t in lower_teams) and \
            any(k in text for k in INJURY_KEYWORDS)
        if (by_name or by_team) and h.title not in seen:
            seen.add(h.title)
            hits.append(h)
    return hits
