"""The owner's private settings: things that must never be in git.

Read from environment variables, or else from data/vazhi.env (one KEY=VALUE
per line, # for comments). data/ is never committed, and the file is read
again whenever it changes, so a new setting needs no restart.

    VAZHI_TELEGRAM_TOKEN   the feedback bot's token, from @BotFather
    VAZHI_TELEGRAM_CHAT    the chat the bot sends feedback to
                           (tools/telegram_setup.py finds it)
    VAZHI_UPI_ID           UPI ID for "Buy me a bus ticket", e.g. name@okaxis
    VAZHI_UPI_NAME         the name the UPI app shows, e.g. Ashil J Thomas
    VAZHI_PAY_LINK         optional: a Razorpay (or other) payment page link
    VAZHI_INBOX_TOKEN      password for /api/inbox (tools/inbox.py --setup makes it)
    VAZHI_INBOX_URL        on the owner's computer: the server Vazhi Messages reads
"""

from __future__ import annotations

import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "vazhi.env")

_lock = threading.Lock()
_cache = {"mtime": None, "values": {}}


def _file() -> dict:
    try:
        mtime = os.path.getmtime(PATH)
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != mtime:
            values = {}
            with open(PATH, encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    values[key.strip()] = value.strip().strip('"').strip("'")
            _cache.update(mtime=mtime, values=values)
        return _cache["values"]


def get(key: str, default: str = "") -> str:
    value = os.environ.get(key, "").strip()
    return value or _file().get(key, default)


def put(key: str, value: str) -> None:
    """Set one key in data/vazhi.env, keeping every other line."""
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    lines, found = [], False
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8-sig") as fh:
            for line in fh.read().splitlines():
                if line.split("=", 1)[0].strip() == key:
                    line, found = "%s=%s" % (key, value), True
                lines.append(line)
    if not found:
        lines.append("%s=%s" % (key, value))
    with open(PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
