"""Where is my train, from riders on it (transitnav/livetrains.py)."""

import unittest
from unittest import mock

from transitnav import limits, livetrains

T0 = 1_800_000_000.0
ON_ROUTE = (9.9816, 76.2999)     # Ernakulam


def rep(token, lat, lon, number="16302"):
    return {"number": number, "token": token, "lat": lat, "lon": lon}


class LiveTrainsTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        livetrains.reset()
        # A known route box, without needing the timetable download.
        self.box = mock.patch.object(livetrains, "_box", lambda n: (8.0, 11.5, 75.5, 77.5) if n == "16302" else None)
        self.box.start()

    def tearDown(self):
        self.box.stop()

    def test_not_seen_until_someone_shares(self):
        self.assertEqual(livetrains.live("16302", now=T0), {"seen": False})

    def test_riders_together_are_averaged(self):
        self.assertTrue(livetrains.report(rep("a" * 24, 9.9816, 76.2999), now=T0))
        self.assertTrue(livetrains.report(rep("b" * 24, 9.9830, 76.3005), now=T0 + 20))
        got = livetrains.live("16302", now=T0 + 60)
        self.assertTrue(got["seen"])
        self.assertEqual(got["riders"], 2)
        self.assertEqual(got["age_s"], 40)
        self.assertAlmostEqual(got["lat"], 9.9823, places=3)

    def test_far_or_old_reports_are_left_out(self):
        livetrains.report(rep("a" * 24, *ON_ROUTE), now=T0)
        livetrains.report(rep("b" * 24, 10.5276, 76.2144), now=T0 + 30)   # Thrissur: another run
        got = livetrains.live("16302", now=T0 + 60)
        self.assertEqual(got["riders"], 1)                               # only the newest group
        self.assertAlmostEqual(got["lat"], 10.5276, places=3)
        self.assertEqual(livetrains.live("16302", now=T0 + 30 + 16 * 60), {"seen": False})

    def test_off_route_and_impossible_jumps_are_dropped(self):
        self.assertFalse(livetrains.report(rep("a" * 24, 28.61, 77.21), now=T0))       # Delhi
        self.assertTrue(livetrains.report(rep("a" * 24, *ON_ROUTE), now=T0))
        self.assertFalse(livetrains.report(rep("a" * 24, 10.5276, 76.2144), now=T0 + 60))  # 60 km in a minute
        self.assertEqual(livetrains.live("16302", now=T0 + 61)["lat"], round(ON_ROUTE[0], 5))

    def test_bad_reports_are_refused(self):
        for bad in (rep("a" * 24, *ON_ROUTE, number="abc"), rep("short", *ON_ROUTE),
                    rep("a" * 24, 51.5, -0.1), {"number": "16302", "token": "a" * 24}):
            with self.assertRaises(livetrains.LiveError):
                livetrains.report(bad, now=T0)
        with self.assertRaises(livetrains.LiveError):
            livetrains.live("<script>")

    def test_memory_is_forgotten(self):
        livetrains.report(rep("a" * 24, *ON_ROUTE), now=T0)
        livetrains.report(rep("c" * 24, *ON_ROUTE, number="12345"), now=T0 + livetrains.REPORT_TTL + 5)
        self.assertNotIn("16302", livetrains._reports)


if __name__ == "__main__":
    unittest.main()
