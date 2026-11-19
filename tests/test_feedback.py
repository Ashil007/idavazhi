"""Riders' messages to the maker, and the "Buy me a bus ticket" settings."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transitnav import feedback, limits, settings  # noqa: E402


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "feedback.db")
        self.patches = [mock.patch.object(feedback, "DB_PATH", self.db),
                        mock.patch.object(feedback.threading, "Thread")]   # no background sends
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def rows(self):
        db = feedback._connect()          # makes the table if nothing was saved yet
        try:
            return db.execute("SELECT text, contact, sent FROM feedback").fetchall()
        finally:
            db.close()                    # Windows can't delete an open database file

    def test_saves_a_message(self):
        feedback.add({"text": "  The 7:40 bus is gone  ", "contact": "Anu\n9999"}, "1.2.3.4")
        self.assertEqual(self.rows(), [("The 7:40 bus is gone", "Anu 9999", 0)])

    def test_refuses_empty_and_long(self):
        with self.assertRaises(feedback.FeedbackError):
            feedback.add({"text": "  a "}, "1.2.3.4")
        with self.assertRaises(feedback.FeedbackError):
            feedback.add({"text": "x" * (feedback.MAX_TEXT + 1)}, "1.2.3.4")
        self.assertEqual(self.rows(), [])

    def test_wrong_fare_report(self):
        feedback.fare({"mode": "bus", "paid": "30", "kind": "Fast Passenger", "shown": "₹27",
                       "km": "21.4", "from": "Kuthiyathodu", "to": "Kundanoor",
                       "note": "conductor said 30"}, "1.2.3.4")
        text = self.rows()[0][0]
        for part in ("Wrong fare reported: Bus", "Kuthiyathodu -> Kundanoor (21.4 km)",
                     "Bus type: Fast Passenger", "Idavazhi showed: ₹27", "Rider paid: Rs 30",
                     "conductor said 30"):
            self.assertIn(part, text)
        for bad in ({"mode": "plane", "paid": 30}, {"mode": "bus", "paid": "lots"},
                    {"mode": "bus", "paid": 0}, {"mode": "bus", "paid": 99999}):
            with self.assertRaises(feedback.FeedbackError, msg=str(bad)):
                feedback.fare(bad, "1.2.3.4")

    def test_trip_rating_needs_no_words(self):
        feedback.add({"rating": 4, "page": "trip"}, "1.2.3.4")
        feedback.add({"rating": 5, "text": "Woke me right on time"}, "1.2.3.4")
        for bad in (0, 6, "5", True, 4.5):
            with self.assertRaises(feedback.FeedbackError):
                feedback.add({"rating": bad}, "1.2.3.4")
        self.assertEqual([r[0] for r in self.rows()], ["", "Woke me right on time"])
        sent = []
        env = {"VAZHI_TELEGRAM_TOKEN": "t", "VAZHI_TELEGRAM_CHAT": "42"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(feedback, "_telegram", lambda tok, chat, text: sent.append(text) or True):
            feedback.send_waiting()
        self.assertIn("\u2605\u2605\u2605\u2605\u2606 4/5", sent[0])
        self.assertIn("Woke me right on time", sent[1])

    def test_rate_limited(self):
        allowed = limits.LIMITS["feedback"][0]
        for i in range(allowed):
            feedback.add({"text": "message %d" % i}, "5.6.7.8")
        with self.assertRaises(feedback.FeedbackError):
            feedback.add({"text": "one too many"}, "5.6.7.8")

    def test_sends_waiting_messages_once(self):
        feedback.add({"text": "first"}, "1.1.1.1")
        feedback.add({"text": "second"}, "1.1.1.1")
        sent = []
        env = {"VAZHI_TELEGRAM_TOKEN": "t", "VAZHI_TELEGRAM_CHAT": "42"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(feedback, "_telegram", lambda tok, chat, text: sent.append(text) or True):
            self.assertEqual(feedback.send_waiting(), 2)
            self.assertEqual(feedback.send_waiting(), 0)
        self.assertEqual(len(sent), 2)
        self.assertIn("first", sent[0])
        self.assertEqual([r[2] for r in self.rows()], [1, 1])

    def test_keeps_messages_when_telegram_fails(self):
        feedback.add({"text": "hello"}, "1.1.1.1")
        env = {"VAZHI_TELEGRAM_TOKEN": "t", "VAZHI_TELEGRAM_CHAT": "42"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(feedback, "_telegram", lambda *a: False):
            self.assertEqual(feedback.send_waiting(), 0)
        self.assertEqual(self.rows()[0][2], 0)

    def test_not_set_up_sends_nothing(self):
        feedback.add({"text": "hello"}, "1.1.1.1")
        with mock.patch.object(settings, "PATH", os.path.join(self.tmp.name, "none.env")), \
                mock.patch.dict(os.environ, {"VAZHI_TELEGRAM_TOKEN": "", "VAZHI_TELEGRAM_CHAT": ""}):
            self.assertEqual(feedback.send_waiting(), 0)

    def test_support_settings_are_checked(self):
        env_file = os.path.join(self.tmp.name, "vazhi.env")
        with open(env_file, "w") as fh:
            fh.write("# test\nVAZHI_UPI_ID=ashil@okaxis\nVAZHI_PAY_LINK=http://not-https.example\n")
        with mock.patch.object(settings, "PATH", env_file), \
                mock.patch.dict(os.environ, {"VAZHI_UPI_ID": "", "VAZHI_PAY_LINK": ""}):
            s = feedback.support()
        self.assertEqual(s["upi"], "ashil@okaxis")
        self.assertEqual(s["link"], "")                 # only https links are offered
        with mock.patch.dict(os.environ, {"VAZHI_UPI_ID": "bad id&am=9999"}):
            self.assertEqual(feedback.support()["upi"], "")


if __name__ == "__main__":
    unittest.main()
