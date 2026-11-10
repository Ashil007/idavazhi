"""Planner pieces that have gone wrong before, and the rate limiter.

    python -m unittest discover tests
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transitnav import limits, planner  # noqa: E402
from transitnav.geo import path_length  # noqa: E402


class BusLineTest(unittest.TestCase):
    def test_bus_line_does_not_run_past_the_stop_and_back(self):
        # North along a road, past the alighting stop by 1 km, then back to it:
        # what the street router draws when it turns around further on.
        board = {"lat": 9.700, "lon": 76.300}
        alight = {"lat": 9.720, "lon": 76.300}
        line = [[9.700 + i * 0.001, 76.300] for i in range(30)]     # up to 9.729
        line += [[9.729 - i * 0.001, 76.3001] for i in range(1, 10)]  # back to 9.720
        cut, kept = planner._no_overshoot(line, board, alight)
        self.assertLess(path_length(cut), path_length(line) * 0.75)
        self.assertLess(kept, 0.75)
        end = cut[-1]
        self.assertAlmostEqual(end[0], alight["lat"], places=3)

    def test_straight_bus_line_is_left_alone(self):
        board = {"lat": 9.700, "lon": 76.300}
        alight = {"lat": 9.720, "lon": 76.300}
        line = [[9.700 + i * 0.001, 76.300] for i in range(21)]
        cut, kept = planner._no_overshoot(line, board, alight)
        self.assertEqual(kept, 1.0)
        self.assertEqual(cut, line)


class TrainLineTest(unittest.TestCase):
    def test_without_track_data_the_line_stays_straight(self):
        saved = planner._track_graph
        planner._track_graph = lambda tiles, tt: (None, set())
        try:
            pts = [[9.6, 76.3], [9.5, 76.33], [9.4, 76.35]]
            self.assertEqual(planner._on_track(pts, None), pts)
        finally:
            planner._track_graph = saved

    def test_cross_country_ride_is_not_traced(self):
        # Kerala to Delhi spans far more tiles than a plan should build.
        calls = []
        saved = planner._track_graph
        planner._track_graph = lambda tiles, tt: calls.append(tiles) or (None, set())
        try:
            pts = [[8.5, 76.9], [15.0, 74.0], [22.0, 75.0], [28.6, 77.2]]
            self.assertEqual(planner._on_track(pts, None), pts)
            self.assertEqual(calls, [])
        finally:
            planner._track_graph = saved


class LimitsTest(unittest.TestCase):
    def setUp(self):
        limits.reset()
        self.saved = dict(limits.LIMITS)
        limits.LIMITS["plan"] = (2, 60)

    def tearDown(self):
        limits.LIMITS.clear()
        limits.LIMITS.update(self.saved)
        limits.reset()

    def test_limit_per_visitor(self):
        limits.hit("plan", "a")
        limits.hit("plan", "a")
        with self.assertRaises(limits.Limited) as ctx:
            limits.hit("plan", "a")
        self.assertGreaterEqual(ctx.exception.retry_after, 1)
        limits.hit("plan", "b")       # someone else is unaffected

    def test_old_visitors_are_forgotten(self):
        limits.hit("plan", "gone")
        limits._sweep(time.monotonic() + 3600)
        self.assertNotIn(("plan", "gone"), limits._hits)


if __name__ == "__main__":
    unittest.main()
