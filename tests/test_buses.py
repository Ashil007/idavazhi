"""Riders say whose buses stop at a stop - private, KSRTC or both - and up to
where each kind goes (stopnames.buses), and the planner uses it."""

import os
import tempfile
import unittest
from unittest import mock

from transitnav import config, limits, planner, stopnames
from transitnav.timings import TimingError

KEY = "node/5757830538"
STOP = {"id": KEY, "lat": 10.03, "lon": 76.319, "name": "Unnichira", "kind": "stop",
        "operator": None, "network": None}


def device(i):
    return "rider%016d" % i


class BusesTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(stopnames, "DB_PATH", os.path.join(self.tmp.name, "stops.db"))
        self.p.start()
        stopnames._invalidate()
        self.r = mock.patch.object(stopnames, "_rate", lambda kind, client: None)
        self.r.start()

    def tearDown(self):
        self.r.stop()
        self.p.stop()
        stopnames._invalidate()
        self.tmp.cleanup()

    def say(self, i, kind, private_to="", ksrtc_to="", key=KEY, fast=""):
        return stopnames.buses({"key": key, "kind": kind, "private_to": private_to,
                                "ksrtc_to": ksrtc_to, "device": device(i), "fast": fast})

    def applied(self):
        stopnames._invalidate()
        return stopnames.apply([dict(STOP)])[0]

    def test_kind_used_once_enough_riders_agree(self):
        for i in range(stopnames.BUSES_CONFIRM - 1):
            r = self.say(i, "both", "Kaloor", "Alappuzha")
        self.assertNotIn("kind", r)
        self.assertIsNone(self.applied().get("buses"))
        r = self.say(99, "both", "kaloor ", "Alappuzha, Cherthala")
        self.assertEqual(r["kind"], "both")
        self.assertEqual(r["votes"]["both"], stopnames.BUSES_CONFIRM)
        stop = self.applied()
        self.assertEqual(stop["buses"], "both")
        self.assertEqual(stop["private_to"], ["Kaloor"])
        self.assertEqual(stop["ksrtc_to"], ["Alappuzha"])      # Cherthala: one rider only
        self.assertEqual(planner._stop_operators(stop, config.MODES["bus"]), {"ksrtc", "private"})

    def test_riders_outweigh_the_curated_list(self):
        cfg = config.MODES["bus"]
        self.assertEqual(planner._stop_operators(dict(STOP), cfg), {"private"})   # config
        for i in range(stopnames.BUSES_CONFIRM):
            self.say(i, "both")
        self.assertTrue(planner._serves(self.applied(), "ksrtc", cfg))

    def test_a_split_vote_is_not_used(self):
        n = stopnames.BUSES_CONFIRM
        for i in range(n):
            self.say(i, "private")
        for i in range(n, 2 * n):
            r = self.say(i, "ksrtc")
        self.assertNotIn("kind", r)

    def test_places_only_for_the_kinds_that_stop(self):
        for i in range(stopnames.BUSES_CONFIRM):
            self.say(i, "private", "Kaloor and Kakkanad", "Alappuzha")   # KSRTC part ignored
        stop = self.applied()
        self.assertEqual(stop["private_to"], ["Kaloor", "Kakkanad"])
        self.assertIsNone(stop["ksrtc_to"])

    def test_one_rider_counts_once_and_can_take_it_back(self):
        self.say(1, "private")
        self.assertEqual(self.say(1, "ksrtc")["votes"], {"private": 0, "ksrtc": 1, "both": 0})
        self.assertEqual(self.say(1, None)["votes"]["ksrtc"], 0)

    def test_bad_reports(self):
        for bad in ({"key": "nope", "kind": "private"}, {"key": KEY, "kind": "tempo"},
                    {"key": KEY, "kind": "private", "private_to": "see http://x.example"},
                    {"key": KEY, "kind": "private", "private_to": "a1,b2,c3,d4,e5,f6,g7"}):
            with self.assertRaises(TimingError, msg=str(bad)):
                stopnames.buses(dict(bad, device=device(1)))
        with self.assertRaises(TimingError):
            stopnames.buses({"key": KEY, "kind": "private"})       # no rider id

    def test_fast_buses_once_enough_riders_agree(self):
        n = stopnames.FAST_CONFIRM
        for i in range(n - 1):
            self.say(i, "ksrtc", fast="yes")
        self.assertIsNone(self.applied().get("fast"))
        self.say(50, "ksrtc", fast="no")
        r = self.say(51, "ksrtc", fast="yes")
        self.assertEqual(r["fast_votes"], {"yes": n, "no": 1})
        self.assertEqual(r["fast"], "yes")
        stop = self.applied()
        self.assertEqual(stop["fast"], "yes")
        self.assertIsNone(stop["buses"])        # whose buses: not enough yet

    def test_riders_fast_answer_decides_a_big_stop(self):
        cfg = config.MODES["bus"]
        self.assertTrue(planner._fast_stop(dict(STOP, fast="yes"), 1, cfg))
        self.assertFalse(planner._fast_stop(dict(STOP, fast="no"), 3, cfg))   # a junction, but no
        self.assertTrue(planner._fast_stop(dict(STOP), 3, cfg))
        with mock.patch.object(planner.places, "is_hub", lambda *a: False):
            self.assertFalse(planner._fast_stop(dict(STOP), 1, cfg))

    def test_not_sure_counts_for_neither(self):
        for i in range(5):
            r = self.say(i, "both")
        self.assertEqual(r["fast_votes"], {"yes": 0, "no": 0})
        self.assertNotIn("fast", r)
        with self.assertRaises(TimingError):
            self.say(9, "both", fast="maybe")

    def test_an_older_database_gets_the_column(self):
        import sqlite3
        stopnames._invalidate()
        db = sqlite3.connect(stopnames.DB_PATH)
        db.execute("CREATE TABLE bus_votes (key TEXT NOT NULL, device TEXT NOT NULL,"
                   " kind TEXT NOT NULL, private_to TEXT NOT NULL, ksrtc_to TEXT NOT NULL,"
                   " at REAL NOT NULL, PRIMARY KEY (key, device))")
        db.execute("INSERT INTO bus_votes VALUES (?, 'old', 'private', '', '', 0)", (KEY,))
        db.commit()
        db.close()
        r = self.say(1, "private", fast="no")
        self.assertEqual(r["votes"]["private"], 2)
        self.assertEqual(r["fast_votes"]["no"], 1)


if __name__ == "__main__":
    unittest.main()
