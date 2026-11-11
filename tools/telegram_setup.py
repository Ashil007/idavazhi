"""Connect riders' feedback to your Telegram, once.

    python tools/telegram_setup.py

Before you run it:
  1. In Telegram, open @BotFather, send /newbot, and follow the steps (any
     name, e.g. "Vazhi Feedback"; a username ending in "bot").
  2. BotFather replies with a token like 123456789:AA... - keep it secret.

This asks for the token (typed in, not shown), waits while you send your new
bot any message, finds your chat, saves both in data/vazhi.env (never in
git), and sends a test message. After that, every message riders send from
About -> "Tell me what you think" arrives in Telegram on your phone and laptop.
"""

import getpass
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transitnav import settings  # noqa: E402


def call(token, method, payload=None):
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/%s" % (token, method),
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def main():
    token = getpass.getpass("Paste the bot token from BotFather (it won't show), then Enter: ").strip()
    try:
        me = call(token, "getMe")["result"]
    except (urllib.error.URLError, KeyError, ValueError):
        print("That token didn't work. Copy it again from BotFather and retry.")
        return 1
    print("\nFound your bot: @%s" % me["username"])
    input("Now open Telegram, search @%s, tap Start and send it 'hi'.\n"
          "Then come back here and press Enter... " % me["username"])
    updates = call(token, "getUpdates").get("result", [])
    chats = [u["message"]["chat"] for u in updates
             if "message" in u and u["message"]["chat"].get("type") == "private"]
    if not chats:
        print("No message reached the bot yet. Send it 'hi' and run this again.")
        return 1
    chat = chats[-1]
    settings.put("VAZHI_TELEGRAM_TOKEN", token)
    settings.put("VAZHI_TELEGRAM_CHAT", str(chat["id"]))
    call(token, "sendMessage", {"chat_id": chat["id"],
                                "text": "Vazhi is connected. Riders' messages will arrive here."})
    print("Done. Saved in data/vazhi.env, and a test message is on its way to %s."
          % (chat.get("first_name") or "you"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
