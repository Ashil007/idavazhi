"""Riders' messages to the maker ("Tell me what you think" in About), and
the stars they give a trip in the pop-up at the end of it.

Every message is saved first, in data/feedback.db (riders' data: never in
git, backed up nightly on the cloud server), then sent to the owner's
Telegram through a bot, so it pops up on their phone and laptop. A message
that can't be sent (no internet, Telegram down, bot not set up yet) stays
marked unsent and goes with the next one.

The bot's token and chat come from settings (data/vazhi.env); nothing secret
is in the code. Messages are sent as plain text, so nothing a rider types can
format or link anything in Telegram.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

from . import limits, settings

DB_PATH = os.path.join(settings.ROOT, "data", "feedback.db")
MAX_TEXT = 1000
MAX_CONTACT = 120
SEND_TIMEOUT_S = 10

_lock = threading.Lock()
_sending = threading.Lock()


class FeedbackError(Exception):
    """A message that can't be accepted; the text is for the rider."""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("PRAGMA journal_mode = WAL;")
    db.execute("PRAGMA busy_timeout = 5000;")
    db.execute("PRAGMA synchronous = NORMAL;")
    db.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY, text TEXT NOT NULL, contact TEXT NOT NULL,
        page TEXT NOT NULL, created REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0)""")
    cols = {row[1] for row in db.execute("PRAGMA table_info(feedback)")}
    if "rating" not in cols:                  # databases from before trip ratings
        db.execute("ALTER TABLE feedback ADD COLUMN rating INTEGER")
    return db


def _clean(value, limit):
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or ""))   # keep \n and \t
    return re.sub(r"[ \t]+", " ", value).strip()[:limit]


def add(body: dict, client: str = "?") -> int:
    """Save a rider's message; returns its id. Sending happens in the background."""
    try:
        limits.hit("feedback", client)
    except limits.Limited:
        raise FeedbackError("Thanks - that's a lot of messages for now. "
                            "Please try again in an hour.") from None
    text = _clean(body.get("text"), MAX_TEXT + 1)
    rating = body.get("rating")
    if rating is not None:
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise FeedbackError("Pick 1 to 5 stars.")
    elif len(text) < 3:                       # a message needs words; stars alone are enough
        raise FeedbackError("Write a few words first.")
    if len(text) > MAX_TEXT:
        raise FeedbackError("Please keep it under %d letters." % MAX_TEXT)
    contact = _clean(body.get("contact"), MAX_CONTACT).replace("\n", " ")
    page = _clean(body.get("page"), 40).replace("\n", " ")
    with _lock:
        db = _connect()
        try:
            cur = db.execute("INSERT INTO feedback (text, contact, page, created, rating)"
                             " VALUES (?,?,?,?,?)", (text, contact, page, time.time(), rating))
            db.commit()
            new_id = cur.lastrowid
        finally:
            db.close()
    print("%s feedback #%d saved (%d letters)" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                  new_id, len(text)), flush=True)
    threading.Thread(target=send_waiting, daemon=True).start()
    return new_id


FARE_MODES = {"bus": "Bus", "metro": "Metro", "road": "Auto or taxi", "train": "Train"}


def fare(body: dict, client: str = "?") -> int:
    """A rider says the fare Idavazhi showed for a ride is wrong ("Wrong fare?"
    under a ride): what was shown, what they paid. Saved and sent like any
    message; the maker checks it against the official fare rules by hand -
    nothing here changes a fare by itself."""
    mode = FARE_MODES.get(str(body.get("mode") or ""))
    if not mode:
        raise FeedbackError("Which ride is this about?")
    try:
        paid = float(body.get("paid"))
    except (TypeError, ValueError):
        raise FeedbackError("How much did you pay? Write it in rupees.") from None
    if not 1 <= paid <= 10000:
        raise FeedbackError("How much did you pay? Write it in rupees.")
    try:
        km = float(body.get("km") or 0)
    except (TypeError, ValueError):
        km = 0.0
    lines = ["Wrong fare reported: %s" % mode]
    route = "%s -> %s" % (_clean(body.get("from"), 80) or "?", _clean(body.get("to"), 80) or "?")
    lines.append(route.replace("\n", " ") + (" (%.1f km)" % km if 0 < km < 2000 else ""))
    kind = _clean(body.get("kind"), 60).replace("\n", " ")
    if kind:
        lines.append("Bus type: " + kind)
    lines.append("Idavazhi showed: " + (_clean(body.get("shown"), 60).replace("\n", " ") or "no fare"))
    lines.append("Rider paid: Rs %s" % ("%g" % paid))
    note = _clean(body.get("note"), 300)
    if note:
        lines.append("\n" + note)
    return add({"text": "\n".join(lines), "page": "wrong fare"}, client)


def _message(row) -> str:
    _id, text, contact, page, created, rating = row
    when = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created))
    where = (" - from " + page) if page else ""
    if rating:
        stars = "\u2605" * rating + "\u2606" * (5 - rating)
        return ("Trip rated on Idavazhi (#%d): %s %d/5%s%s\n%s%s"
                % (_id, stars, rating, "\n\n" + text if text else "",
                   "\n\n- " + contact if contact else "", when, where))
    return ("New message on Idavazhi (#%d)\n\n%s\n\n- %s\n%s%s"
            % (_id, text, contact or "no name given", when, where))


def _telegram(token: str, chat: str, text: str) -> bool:
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps({"chat_id": chat, "text": text,
                         "disable_web_page_preview": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as res:
            return bool(json.loads(res.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001 - offline, Telegram down, wrong token: try later
        return False


def send_waiting(limit: int = 20) -> int:
    """Send unsent messages to Telegram, oldest first; returns how many went."""
    token = settings.get("VAZHI_TELEGRAM_TOKEN")
    chat = settings.get("VAZHI_TELEGRAM_CHAT")
    if not token or not chat:
        return 0                                  # not set up yet: they wait in the database
    if not _sending.acquire(blocking=False):
        return 0                                  # another thread is already sending
    sent = 0
    try:
        db = _connect()
        try:
            rows = db.execute("SELECT id, text, contact, page, created, rating FROM feedback"
                              " WHERE sent = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()
            for row in rows:
                if not _telegram(token, chat, _message(row)):
                    break
                db.execute("UPDATE feedback SET sent = 1 WHERE id = ?", (row[0],))
                db.commit()
                sent += 1
        finally:
            db.close()
    finally:
        _sending.release()
    return sent


INBOX_MAX = 2000


def inbox() -> dict:
    """Everything the Idavazhi Messages app shows (tools/inbox.py), newest first:
    messages and trip ratings, and the bus tickets bought through Razorpay.
    Only ever answered to the owner's password (server.py, /api/inbox)."""
    if not os.path.isfile(DB_PATH):
        return {"messages": [], "tickets": []}
    with _lock:
        db = _connect()
        try:
            messages = [dict(zip(("id", "text", "contact", "page", "created", "sent", "rating"), r))
                        for r in db.execute("SELECT id, text, contact, page, created, sent, rating"
                                            " FROM feedback ORDER BY created DESC LIMIT ?", (INBOX_MAX,))]
            tickets = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'tickets'").fetchone():
                tickets = [dict(zip(("payment_id", "amount", "created", "sent"), r))
                           for r in db.execute("SELECT payment_id, amount, created, sent FROM tickets"
                                               " ORDER BY created DESC LIMIT ?", (INBOX_MAX,))]
        finally:
            db.close()
    return {"messages": messages, "tickets": tickets}


def support() -> dict:
    """What "Buy me a bus ticket" can offer: Razorpay, a UPI ID, a payment link."""
    upi = settings.get("VAZHI_UPI_ID")
    if upi and not re.fullmatch(r"[\w.\-]{2,256}@[A-Za-z][\w]{1,64}", upi):
        upi = ""
    link = settings.get("VAZHI_PAY_LINK")
    if link and not link.startswith("https://"):
        link = ""
    from . import tickets                 # here, not at the top: tickets imports feedback
    return {"upi": upi, "name": settings.get("VAZHI_UPI_NAME") or "Idavazhi", "link": link,
            "razorpay": tickets.enabled()}
