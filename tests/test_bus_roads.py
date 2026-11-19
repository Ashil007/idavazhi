"""Bus routes keep to the main roads: the router asks the app's own Valhalla
for a bus path that uses residential lanes only near either end, and keeps
the plain bus route when that is no path or a long detour."""

import unittest
from unittest import mock

from transitnav import router

A, B = (10.0, 76.30), (10.0, 76.38)


def line(km):
    """A straight east-west line about `km` long, from A."""
    return [[A[0], A[1]], [A[0], A[1] + km / 109.6]]


class BusRoadsTest(unittest.TestCase):
    def ask(self, plain_km, kept):
        calls = []

        def fake(a, b, profile, backend="valhalla", base=None, options=None):
            calls.append(options)
            if options is None:
                return line(plain_km), 600
            if kept is None:
                raise router._NoRoute()
            return line(kept), 700
        with mock.patch.object(router, "_valhalla_route", fake):
            return router._local_route(A, B, "bus"), calls

    def test_main_roads_kept_when_not_much_longer(self):
        found, calls = self.ask(7.3, 8.3)
        self.assertEqual(found[1], 700)
        limits = calls[1]["bus"]["hierarchy_limits"]["2"]
        self.assertEqual(limits["max_up_transitions"], 0)
        self.assertEqual(limits["expand_within_distance"], router.BUS_LANE_REACH_M)

    def test_long_detour_keeps_plain_route(self):
        found, _ = self.ask(7.8, 17.4)
        self.assertEqual(found[1], 600)

    def test_no_main_road_path_keeps_plain_route(self):
        found, _ = self.ask(7.8, None)
        self.assertEqual(found[1], 600)

    def test_other_profiles_ask_once_without_limits(self):
        calls = []

        def fake(a, b, profile, backend="valhalla", base=None, options=None):
            calls.append(options)
            return line(2), 900
        with mock.patch.object(router, "_valhalla_route", fake):
            router._local_route(A, B, "foot")
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
