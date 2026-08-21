"""Free WhatsApp reminder before each gameweek deadline.

Uses **CallMeBot** (https://www.callmebot.com/blog/free-api-whatsapp-messages/),
a free service with no account: you register your number once by sending the bot
a WhatsApp message, and it replies with an API key. After that, a plain HTTPS GET
sends yourself a message.

One-time setup
--------------
1. Add the CallMeBot number **+34 644 51 95 23** to your phone contacts.
2. Send it this exact WhatsApp message: ``I allow callmebot to send me messages``
3. You'll get a reply with your personal ``apikey``.
4. Export two environment variables (or set them as GitHub Actions secrets
   ``WHATSAPP_PHONE`` and ``WHATSAPP_APIKEY``):

       export WHATSAPP_PHONE="+447700900123"   # your number, with country code
       export WHATSAPP_APIKEY="123456"

Then ``python -m fpl_agent.notify`` sends a reminder. With no env vars set it
does nothing (so it's safe to leave wired into CI).

The message links to your published team page and only fires when the next
deadline is within ``REMIND_WITHIN_HOURS`` (default 30h), so scheduling it a
couple of times a week won't spam you.
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timezone

import requests

from . import api

REMIND_WITHIN_HOURS = float(os.environ.get("FPL_REMIND_WITHIN_HOURS", "30"))
# Where your always-updated team page lives (GitHub Pages by default).
PAGE_URL = os.environ.get(
    "FPL_PAGE_URL", "https://baldozz.github.io/fpl-agent/")


def build_message() -> tuple[str, float] | None:
    """Return (message, hours_to_deadline) for the next gameweek, or None."""
    boot = api.bootstrap()
    _, nxt = api.current_and_next_event(boot)
    if nxt is None:
        return None
    dl = datetime.fromisoformat(nxt["deadline_time"].replace("Z", "+00:00"))
    hours = (dl - datetime.now(timezone.utc)).total_seconds() / 3600
    when = dl.strftime("%a %d %b %H:%M UTC")
    msg = (f"⚽ FPL reminder — Gameweek {nxt['id']} deadline {when} "
           f"(~{hours:.0f}h). Your recommended XI, captain & bench: {PAGE_URL}")
    return msg, hours


def send_whatsapp(message: str) -> bool:
    phone = os.environ.get("WHATSAPP_PHONE", "").strip()
    apikey = os.environ.get("WHATSAPP_APIKEY", "").strip()
    if not phone or not apikey:
        print("[notify] WHATSAPP_PHONE / WHATSAPP_APIKEY not set — skipping. "
              "See fpl_agent/notify.py for one-time CallMeBot setup.")
        return False
    url = ("https://api.callmebot.com/whatsapp.php?"
           + urllib.parse.urlencode(
               {"phone": phone, "text": message, "apikey": apikey}))
    resp = requests.get(url, timeout=30)
    ok = resp.status_code == 200
    print(f"[notify] CallMeBot status={resp.status_code}: {resp.text[:160]}")
    return ok


def main() -> int:
    built = build_message()
    if built is None:
        print("[notify] No upcoming gameweek.")
        return 0
    message, hours = built
    if hours < 0:
        print("[notify] Deadline already passed — not sending.")
        return 0
    if hours > REMIND_WITHIN_HOURS:
        print(f"[notify] Deadline is {hours:.0f}h away (> "
              f"{REMIND_WITHIN_HOURS:.0f}h window) — not sending yet.")
        return 0
    send_whatsapp(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
