"""Buy me a bus ticket through Razorpay: orders, signature checks, saving."""

import hashlib
import hmac
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transitnav import feedback, limits, tickets  # noqa: E402

KEYS = {"VAZHI_RAZORPAY_KEY_ID": "rzp_test_abc123XYZ", "VAZHI_RAZORPAY_KEY_SECRET": "s3cret"}


def signed(order_id, payment_id, secret="s3cret"):
    return hmac.new(secret.encode(), ("%s|%s" % (order_id, payment_id)).encode(), hashlib.sha256).hexdigest()


class TicketTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.made = []
        self.patches = [
            mock.patch.object(feedback, "DB_PATH", os.path.join(self.tmp.name, "f.db")),
            mock.patch.object(tickets.threading, "Thread"),
            mock.patch.dict(os.environ, KEYS),
            mock.patch.object(tickets, "_create_order",
                              lambda k, s, body: self.made.append(body) or {"id": "order_Test%04d" % len(self.made)}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def rows(self):
        db = tickets._connect()
        try:
            return db.execute("SELECT payment_id, order_id, amount FROM tickets").fetchall()
        finally:
            db.close()

    def test_order_in_paise(self):
        o = tickets.order({"amount": 30}, "1.2.3.4")
        self.assertEqual(o, {"order_id": "order_Test0001", "amount": 3000, "currency": "INR",
                             "key": "rzp_test_abc123XYZ"})
        self.assertEqual(self.made[0]["amount"], 3000)
        self.assertEqual(self.made[0]["currency"], "INR")

    def test_bad_amounts(self):
        for bad in (0, -5, 100001, "10", 10.5, True, None):
            with self.assertRaises(tickets.TicketError):
                tickets.order({"amount": bad}, "1.2.3.4")
        self.assertEqual(self.made, [])

    def test_not_switched_on(self):
        with mock.patch.dict(os.environ, {"VAZHI_RAZORPAY_KEY_ID": "", "VAZHI_RAZORPAY_KEY_SECRET": ""}), \
                mock.patch.object(tickets.settings, "PATH", os.path.join(self.tmp.name, "none.env")):
            self.assertFalse(tickets.enabled())
            with self.assertRaises(tickets.TicketError):
                tickets.order({"amount": 10}, "1.2.3.4")

    def test_paid_ticket_saved_with_its_own_amount(self):
        o = tickets.order({"amount": 50}, "1.2.3.4")
        tickets.verify({"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_Abc123",
                        "razorpay_signature": signed(o["order_id"], "pay_Abc123"), "amount": 999999})
        tickets.verify({"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_Abc123",
                        "razorpay_signature": signed(o["order_id"], "pay_Abc123")})   # sent twice: kept once
        self.assertEqual(self.rows(), [("pay_Abc123", o["order_id"], 50)])

    def test_forged_payment_refused(self):
        o = tickets.order({"amount": 10}, "1.2.3.4")
        for sig in ("", "0" * 64, signed(o["order_id"], "pay_Abc123", secret="wrong"),
                    signed(o["order_id"], "pay_Other1")):
            with self.assertRaises(tickets.TicketError):
                tickets.verify({"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_Abc123",
                                "razorpay_signature": sig})
        self.assertEqual(self.rows(), [])

    def test_owner_told_once(self):
        o = tickets.order({"amount": 10}, "1.2.3.4")
        tickets.verify({"razorpay_order_id": o["order_id"], "razorpay_payment_id": "pay_Abc123",
                        "razorpay_signature": signed(o["order_id"], "pay_Abc123")})
        sent = []
        with mock.patch.dict(os.environ, {"VAZHI_TELEGRAM_TOKEN": "t", "VAZHI_TELEGRAM_CHAT": "42"}), \
                mock.patch.object(feedback, "_telegram", lambda tok, chat, text: sent.append(text) or True):
            self.assertEqual(tickets.send_waiting(), 1)
            self.assertEqual(tickets.send_waiting(), 0)
        self.assertIn("₹10", sent[0])

    def test_rate_limited(self):
        for _ in range(limits.LIMITS["ticket"][0]):
            tickets.order({"amount": 10}, "9.9.9.9")
        with self.assertRaises(tickets.TicketError):
            tickets.order({"amount": 10}, "9.9.9.9")


if __name__ == "__main__":
    unittest.main()
