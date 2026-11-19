"""Riders' reports of bus stops where no bus runs (transitnav/stopnames.py),
and the stops the planner skips (planner._keep_stops)."""

import os
import tempfile
import unittest
from unittest import mock

from transitnav import config, limits, planner, stopnames

KEY = "node/123456"


def device(i):
    return "rider%016d" % i


class NoBusTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(stopnames, "DB_PATH", os.path.join(self.tmp.name, "stops.db"))
        self.p.start()
        stopnames._invalidate()
        # Many riders from one test client would hit the rate limit.
        self.r = mock.patch.object(stopnames, "_rate", lambda kind, client: None)
        self.r.start()

    def tearDown(self):
        self.r.stop()
        self.p.stop()
        stopnames._invalidate()
        self.tmp.cleanup()

    def say(self, i, no_bus=True, key=KEY):
        return stopnames.no_bus({"key": key, "no_bus": no_bus, "device": device(i)})

    def test_used_once_enough_riders_agree(self):
        for i in range(stopnames.NO_BUS_CONFIRM - 1):
            r = self.say(i)
        self.assertFalse(r["used"])
        self.assertNotIn(KEY, stopnames.no_bus_keys())
        r = self.say(99)
        self.assertTrue(r["used"])
        self.assertEqual(r["no_bus"], stopnames.NO_BUS_CONFIRM)
        stopnames._invalidate()
        self.assertIn(KEY, stopnames.no_bus_keys())

    def test_one_rider_counts_once_and_can_take_it_back(self):
        self.say(1)
        self.assertEqual(self.say(1)["no_bus"], 1)
        self.assertEqual(self.say(1, None)["no_bus"], 0)

    def test_riders_who_say_buses_stop_outweigh(self):
        n = stopnames.NO_BUS_CONFIRM
        for i in range(n):
            self.say(i)
        for i in range(n, 2 * n):
            r = self.say(i, False)
        self.assertFalse(r["used"])

    def test_bad_reports_are_refused(self):
        for bad in ({"key": "lat,lon", "no_bus": True, "device": device(1)},
                    {"key": KEY, "no_bus": "yes", "device": device(1)},
                    {"key": KEY, "no_bus": True, "device": "short"}):
            with self.assertRaises(stopnames.TimingError, msg=str(bad)):
                stopnames.no_bus(bad)

    def test_planner_skips_listed_and_confirmed_stops(self):
        cfg = config.MODES["bus"]
        for i in range(stopnames.NO_BUS_CONFIRM):
            self.say(i)
        stopnames._invalidate()
        stops = [{"id": "node/11505252377", "name": "Nalukulangara"},   # on the config list
                 {"id": KEY, "name": "Riders' report"},
                 {"id": "node/1", "name": "Turavur"}]
        self.assertEqual([s["id"] for s in planner._keep_stops(stops, cfg)], ["node/1"])
        # Trains are not affected by bus reports.
        self.assertEqual(len(planner._keep_stops(stops[1:], config.MODES["train"])), 2)


if __name__ == "__main__":
    unittest.main()
