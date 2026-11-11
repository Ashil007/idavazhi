"""'Buy me a bus ticket' through Razorpay.

1. The page asks for a ticket of N rupees: order() creates a Razorpay order
   for that amount with the owner's secret key (it never leaves the server).
2. The page opens Razorpay Checkout with that order; the rider pays by UPI
   (Google Pay, PhonePe...), card or net banking.
3. Razorpay hands the page a payment id and a signature; verify() checks the
   signature with the secret key, so a fake "paid" can't be recorded, and
   saves the ticket in data/feedback.db beside messages and ratings. The owner
   gets a Telegram message, and sees it in Vazhi Messages (tools/inbox.py).

The keys come from data/vazhi.env: VAZHI_RAZORPAY_KEY_ID and
VAZHI_RAZORPAY_KEY_SECRET (test keys start rzp_test_, live keys rzp_live_).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.error
import urllib.request

from . import feedback, limits, settings

API = "https://api.razorpay.com/v1/orders"
MIN_RUPEES, MAX_RUPEES = 1, 100000
TIMEOUT_S = 15


class TicketError(Exception):
    """Something the rider is told; the text is for them."""


def keys():
    key_id = settings.get("VAZHI_RAZORPAY_KEY_ID")
    secret = settings.get("VAZHI_RAZORPAY_KEY_SECRET")
    if not re.fullmatch(r"rzp_(test|live)_[A-Za-z0-9]{6,40}", key_id or "") or not secret:
        return None, None
    return key_id, secret


def enabled() -> bool:
    return keys()[0] is not None


def _create_order(key_id, secret, body):
    """POST to Razorpay's Orders API; returns the order (a dict)."""
    auth = base64.b64encode(("%s:%s" % (key_id, secret)).encode()).decode()
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return json.loads(res.read().decode("utf-8"))


def order(body: dict, client: str = "?") -> dict:
    """A Razorpay order for a ticket of body["amount"] rupees."""
    key_id, secret = keys()
    if not key_id:
        raise TicketError("Payments are not switched on yet. Thank you for wanting to help!")
    try:
        limits.hit("ticket", client)
    except limits.Limited:
        raise TicketError("Too many tries for now. Please try again in a while.") from None
    amount = body.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or not MIN_RUPEES <= amount <= MAX_RUPEES:
        raise TicketError("Pick an amount from ₹%d to ₹%d." % (MIN_RUPEES, MAX_RUPEES))
    try:
        made = _create_order(key_id, secret, {
            "amount": amount * 100, "currency": "INR",
            "receipt": "vazhi-%d" % int(time.time() * 1000),
            "notes": {"for": "Vazhi bus ticket"}})
    except urllib.error.HTTPError as exc:
        print("razorpay order refused: HTTP %d" % exc.code, flush=True)   # no key in the log
        raise TicketError("Payments are not working right now. Please try again later.") from None
    except Exception:  # noqa: BLE001 - offline, timeout
        raise TicketError("Couldn't reach the payment service. Please try again.") from None
    if not str(made.get("id", "")).startswith("order_"):
        raise TicketError("Payments are not working right now. Please try again later.")
    db = _connect()                       # the amount, kept here: the page can't change it
    try:
        db.execute("INSERT OR REPLACE INTO ticket_orders (order_id, amount, created) VALUES (?,?,?)",
                   (made["id"], amount, time.time()))
        db.commit()
    finally:
        db.close()
    return {"order_id": made["id"], "amount": amount * 100, "currency": "INR", "key": key_id}


def _signed(order_id, payment_id, secret) -> str:
    return hmac.new(secret.encode("utf-8"), ("%s|%s" % (order_id, payment_id)).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _connect():
    db = feedback._connect()
    db.execute("""CREATE TABLE IF NOT EXISTS tickets (
        payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount INTEGER NOT NULL,
        created REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ticket_orders (
        order_id TEXT PRIMARY KEY, amount INTEGER NOT NULL, created REAL NOT NULL)""")
    return db


def verify(body: dict) -> dict:
    """Check Razorpay's signature for a finished payment and save the ticket."""
    key_id, secret = keys()
    if not key_id:
        raise TicketError("Payments are not switched on.")
    order_id = str(body.get("razorpay_order_id") or "")
    payment_id = str(body.get("razorpay_payment_id") or "")
    signature = str(body.get("razorpay_signature") or "")
    if not re.fullmatch(r"order_\w{6,40}", order_id) or not re.fullmatch(r"pay_\w{6,40}", payment_id):
        raise TicketError("That payment couldn't be checked.")
    if not hmac.compare_digest(_signed(order_id, payment_id, secret), signature):
        raise TicketError("That payment couldn't be checked.")
    db = _connect()
    try:
        row = db.execute("SELECT amount FROM ticket_orders WHERE order_id = ?", (order_id,)).fetchone()
        rupees = row[0] if row else 0
        db.execute("INSERT OR IGNORE INTO tickets (payment_id, order_id, amount, created) VALUES (?,?,?,?)",
                   (payment_id, order_id, rupees, time.time()))
        db.commit()
    finally:
        db.close()
    print("%s bus ticket paid (%s)" % (time.strftime("%Y-%m-%d %H:%M:%S"), payment_id), flush=True)
    threading.Thread(target=send_waiting, daemon=True).start()
    return {"ok": True}


def send_waiting() -> int:
    """Tell the owner on Telegram about new tickets."""
    token = settings.get("VAZHI_TELEGRAM_TOKEN")
    chat = settings.get("VAZHI_TELEGRAM_CHAT")
    if not token or not chat:
        return 0
    sent = 0
    db = _connect()
    try:
        for pid, amount, created in db.execute(
                "SELECT payment_id, amount, created FROM tickets WHERE sent = 0 ORDER BY created").fetchall():
            when = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created))
            text = "Someone bought you a bus ticket on Vazhi%s!\n\n%s\n%s" % (
                (" (₹%d)" % amount) if amount else "", pid, when)
            if not feedback._telegram(token, chat, text):
                break
            db.execute("UPDATE tickets SET sent = 1 WHERE payment_id = ?", (pid,))
            db.commit()
            sent += 1
    finally:
        db.close()
    return sent
