"""KSRTC or private buses, and long bus trips from bus stands (planner.py)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transitnav import fares, planner  # noqa: E402
from transitnav.config import MODES  # noqa: E402

CFG = MODES["bus"]


def stop(sid, lat, lon, name=None, kind="stop", operator=None):
    return {"id": sid, "lat": lat, "lon": lon, "name": name, "kind": kind,
            "operator": operator, "network": None}


UNNICHIRA = stop("node/5757830538", 10.03, 76.319, "Unnichira")
KSRTC_STOP = stop("node/1", 10.0, 76.3, "Somewhere", operator="KSRTC")
PLAIN = stop("node/2", 10.01, 76.31, "Edappally")


class OperatorTest(unittest.TestCase):
    def test_who_runs_a_stop(self):
        self.assertEqual(planner._stop_operators(UNNICHIRA, CFG), {"private"})
        self.assertEqual(planner._stop_operators(KSRTC_STOP, CFG), {"ksrtc"})
        self.assertIsNone(planner._stop_operators(PLAIN, CFG))
        both = stop("node/3", 10, 76, "X", operator="ksrtc,private")
        self.assertEqual(planner._stop_operators(both, CFG), {"ksrtc", "private"})
        # A roadside stop's name does not count; a stand's does.
        named = stop("node/4", 10, 76, "Private Road Junction")
        self.assertIsNone(planner._stop_operators(named, CFG))
        stand = stop("way/5", 10, 76, "Kaloor Private Bus Station", kind="station")
        self.assertEqual(planner._stop_operators(stand, CFG), {"private"})

    def test_ride_operator(self):
        self.assertEqual(planner._leg_operator(UNNICHIRA, PLAIN, "any", CFG), "private")
        self.assertEqual(planner._leg_operator(UNNICHIRA, PLAIN, "ksrtc", CFG), "private")
        self.assertEqual(planner._leg_operator(PLAIN, PLAIN, "ksrtc", CFG), "ksrtc")
        self.assertIsNone(planner._leg_operator(PLAIN, PLAIN, "any", CFG))
        self.assertTrue(planner._serves(PLAIN, "ksrtc", CFG))
        self.assertFalse(planner._serves(UNNICHIRA, "ksrtc", CFG))
        self.assertTrue(planner._serves(UNNICHIRA, "private", CFG))

    def test_note_when_the_other_kind_runs(self):
        option = {"notes": [], "legs": [{"type": "transit", "mode": "bus", "operator": "private",
                                         "from": UNNICHIRA, "to": PLAIN}]}
        planner._operator_notes(option, "ksrtc")
        self.assertEqual(option["notes"],
                         ["Only private buses stop at Unnichira, so that ride is by private bus."])
        option["notes"] = []
        planner._operator_notes(option, "private")
        self.assertEqual(option["notes"], [])

    def test_stands(self):
        self.assertTrue(planner._is_stand(stop("way/1", 10, 76, "Alappuzha Bus stand",
                                               kind="station"), CFG))
        self.assertTrue(planner._is_stand(stop("rel/1", 10, 76, "Vyttila Mobility Hub",
                                               kind="station"), CFG))
        self.assertTrue(planner._is_stand(stop("way/2", 10, 76, "Cherthala", kind="station",
                                               operator="KSRTC"), CFG))
        # Roadside stops some mappers tag as stations are not stands.
        self.assertFalse(planner._is_stand(stop("node/9", 10, 76, "Njaralloor",
                                                kind="station"), CFG))
        self.assertFalse(planner._is_stand(stop("node/8", 10, 76, "Kaloor bus stand"), CFG))

    def test_long_trip_picks_stands(self):
        origin, dest = (10.0285, 76.3215), (9.4981, 76.3388)
        direct = planner.haversine(origin[0], origin[1], dest[0], dest[1])
        stops = [
            UNNICHIRA,
            stop("rel/17712257", 9.9686, 76.3218, "Vyttila Mobility Hub", kind="station"),
            stop("way/273205558", 9.9942, 76.2919, "Kaloor Private Bus Station", kind="station"),
            stop("node/1472174498", 10.0207, 76.3437, "Bus Terminal, Kakkanad", kind="station"),
            stop("way/133875108", 9.4999, 76.3474, "Alappuzha Bus stand", kind="station",
                 operator="KSRTC"),
            stop("way/649481258", 9.5, 76.3363, "Alappuzha Private Bus stand", kind="station"),
            # Behind the rider, away from Alappuzha: never a start.
            stop("way/7", 10.2, 76.33, "Angamaly KSRTC Bus Stand", kind="station"),
        ]
        near_o, near_d = planner._long_bus_stands(origin, dest, direct, stops, CFG, "any")
        self.assertEqual([s["name"] for s in near_o], ["Vyttila Mobility Hub"])
        self.assertEqual([s["name"] for s in near_d], ["Alappuzha Bus stand"])
        near_o, near_d = planner._long_bus_stands(origin, dest, direct, stops, CFG, "private")
        self.assertEqual([s["name"] for s in near_o], ["Kaloor Private Bus Station"])
        self.assertEqual([s["name"] for s in near_d], ["Alappuzha Private Bus stand"])
        self.assertIsNone(planner._long_bus_stands(origin, dest, direct, stops[:4], CFG, "any"))

    def test_one_place_mapped_twice_is_one_stand(self):
        # Thuravoor to Navaikulam: Vaikom's stand and depot (25 m apart) took
        # two of the three places, and every pair traced started at Vaikom.
        origin, dest = (9.778, 76.312), (8.7811, 76.7865)
        direct = planner.haversine(origin[0], origin[1], dest[0], dest[1])
        stops = [
            stop("way/1", 9.7534, 76.3913, "Vaikom KSRTC Bus Stand", kind="station"),
            stop("way/2", 9.7532, 76.3912, "Vaikom KSRTC Sub Depot", kind="station"),
            stop("way/3", 9.6869, 76.3436, "Cherthala", kind="station", operator="KSRTC"),
            stop("way/4", 8.8180, 76.7480, "Kallambalam KSRTC Bus Stand", kind="station"),
            stop("way/5", 8.8182, 76.7482, "Kallambalam KSRTC Depot", kind="station"),
        ]
        near_o, near_d = planner._long_bus_stands(origin, dest, direct, stops, CFG, "any")
        self.assertEqual(len(near_o), 2)
        self.assertTrue(near_o[0]["name"].startswith("Vaikom KSRTC"))
        self.assertEqual(near_o[1]["name"], "Cherthala")
        self.assertEqual(len(near_d), 1)

    def test_every_stand_is_traced(self):
        def s(sid, reach):
            return dict(stop(sid, 10 + reach / 1000, 76, sid), distance_m=0.0, reach_s=reach)
        starts = [s("vaikom", 3660), s("vaikom2", 3661), s("cherthala", 4200)]
        dests = [s("d1", 600), s("d2", 900), s("d3", 1000)]
        pairs = planner._usable_pairs(starts, dests)
        # The four quickest to reach all start at Vaikom...
        self.assertEqual({a["id"] for a, _ in pairs[:4]}, {"vaikom", "vaikom2"})
        picked = planner._spread_pairs(pairs, 4)
        # ...but every start gets traced, Cherthala too.
        self.assertEqual(len(picked), 4)
        self.assertEqual({a["id"] for a, _ in picked}, {"vaikom", "vaikom2", "cherthala"})
        self.assertIs(picked[0], pairs[0])
        self.assertEqual(planner._spread_pairs(pairs[:2], 4), pairs[:2])
        self.assertEqual(len(planner._spread_pairs(pairs, 20)), len(pairs))

    def test_private_fares(self):
        every = fares.bus_fare(20000)
        private = fares.bus_fare(20000, "private")
        self.assertEqual([t["name"] for t in private["types"]], list(fares.PRIVATE_TYPES))
        self.assertEqual((private["min"], private["max"]), (every["min"], every["max"]))
        self.assertGreater(len(every["types"]), len(private["types"]))

    def test_unknown_operator_refused(self):
        with self.assertRaises(planner.PlanError):
            planner.plan((10.0, 76.3), (9.5, 76.3), "bus", operator="tempo")


class KsrtcMainRoadsTest(unittest.TestCase):
    """A KSRTC ride starts and ends on a main road (NH / SH), at a stand, or
    at a stop riders or the map say KSRTC calls at."""

    def test_which_stops_ksrtc_calls_at(self):
        lane = stop("node/10", 10.02, 76.31, "Club Junction")
        self.assertFalse(planner._ksrtc_stop(lane, "secondary", CFG))
        self.assertTrue(planner._ksrtc_stop(lane, "trunk", CFG))
        self.assertTrue(planner._ksrtc_stop(lane, "primary", CFG))
        self.assertTrue(planner._ksrtc_stop(KSRTC_STOP, "residential", CFG))   # the map says
        self.assertFalse(planner._ksrtc_stop(UNNICHIRA, "trunk", CFG))         # private only
        said = dict(lane, buses="both")                                        # riders say
        self.assertTrue(planner._ksrtc_stop(said, "residential", CFG))

    def test_points_skip_lane_stops(self):
        lane = stop("node/11", 10.030, 76.320, "Thykkavu")
        nh = stop("node/12", 10.033, 76.313, "Koonamthai")
        far_nh = stop("node/13", 9.80, 76.32, "NCC Junction")
        roads = {"node/11": "secondary", "node/12": "trunk", "node/13": "trunk"}
        stops = [lane, nh, far_nh]
        with mock.patch.object(planner.router, "road_classes",
                               lambda pts: [roads[s["id"]] for s in stops
                                            for p in pts if (s["lat"], s["lon"]) == p]),                 mock.patch.object(planner.router, "main_roads_meeting", lambda pts: [1] * len(pts)):
            o, d = planner._ksrtc_points((10.0285, 76.3215), (9.79, 76.32), 26000, stops, CFG)
        self.assertEqual([s["name"] for s in o], ["Koonamthai"])
        self.assertEqual([s["name"] for s in d], ["NCC Junction"])

    def test_long_any_bus_rides_start_on_main_roads(self):
        lane = stop("node/14", 10.02, 76.31, "Club Junction")
        self.assertFalse(planner._main_road_stop(lane, "secondary", CFG))
        self.assertFalse(planner._main_road_stop(UNNICHIRA, "secondary", CFG))
        self.assertTrue(planner._main_road_stop(lane, "trunk", CFG))
        self.assertTrue(planner._main_road_stop(KSRTC_STOP, "residential", CFG))
        stand = stop("node/15", 10.0, 76.3, "Aluva KSRTC Bus Stand", kind="station")
        self.assertTrue(planner._main_road_stop(stand, None, CFG))

    def test_a_big_junction_beats_a_nearer_small_stop(self):
        small = stop("node/16", 10.0335, 76.3132, "Koonamthai")          # NH 544 only
        big = stop("node/17", 10.0242, 76.3085, "Edappally Junction")    # NH 66 and NH 544
        far = stop("node/18", 9.80, 76.32, "NCC Junction")
        meet = {"node/16": 1, "node/17": 3, "node/18": 1}
        stops = [small, big, far]

        def per_stop(table):
            return lambda pts: [table[s["id"]] for p in pts for s in stops
                                if (s["lat"], s["lon"]) == p]
        with mock.patch.object(planner.router, "road_classes", per_stop(dict.fromkeys(meet, "trunk"))),                 mock.patch.object(planner.router, "main_roads_meeting", per_stop(meet)),                 mock.patch.object(planner.places, "is_hub", lambda *a: False):
            o, _ = planner._ksrtc_points((10.0285, 76.3215), (9.79, 76.32), 26000, stops, CFG)
        self.assertEqual(o[0]["name"], "Edappally Junction")

    def test_reach_takes_the_quicker_of_walking_and_a_bus(self):
        near = planner._reach_s((10.0285, 76.3215), stop("node/19", 10.0242, 76.3085))
        walk = 1500 * planner.DETOUR_FACTOR / (planner.ACCESS_MODES["walk"]["speed_kmh"] / 3.6)
        self.assertLess(near, walk)

    def test_no_route_service_keeps_the_old_way(self):
        with mock.patch.object(planner.router, "road_classes", lambda pts: [None] * len(pts)):
            self.assertIsNone(planner._ksrtc_points((10.0285, 76.3215), (9.79, 76.32), 26000,
                                                    [PLAIN], CFG))


if __name__ == "__main__":
    unittest.main()
