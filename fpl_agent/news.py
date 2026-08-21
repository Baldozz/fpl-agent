"""Free, no-auth football news via public RSS feeds.

The FPL API already carries the authoritative injury/availability field
(``player.news``) which the model uses directly. These RSS feeds add colour and
let the agent surface relevant headlines (team news, predicted line-ups,
injuries) alongside its picks. All sources below are free and publicly
available; add or remove feeds in ``FEEDS`` freely.
"""
from __future__ import annotations

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
